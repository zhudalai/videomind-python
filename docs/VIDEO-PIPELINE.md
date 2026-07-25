# VideoMind 视频处理管线设计

> 下载 → 转码 → 分段 ASR → 关键帧 OCR → VideoContext 构建 → 入库索引
> 核心参考：vid-lens `internal/mq/consumer.go` (分段转写/片段级状态机/租约心跳) + free-video-downloader `downloader.py` `douyin.py`

---

## 1. 管线总览

```
用户上传/链接
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│                    IngestionPipeline                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │ Download │─▶│ Transcode│─▶│  ASR     │─▶│   OCR        │  │
│  │  Stage   │  │  Stage   │  │  Stage   │  │  Stage       │  │
│  └──────────┘  └──────────┘  └────┬─────┘  └──────┬───────┘  │
│                                    │             │           │
│                                    ▼             ▼           │
│                           ┌──────────────────────────────┐   │
│                           │     Build VideoContext       │   │
│                           │  (60s窗口合并 ASR+OCR+帧)     │   │
│                           └──────────────┬───────────────┘   │
│                                          │                   │
│                                          ▼                   │
│                           ┌──────────────────────────────┐   │
│                           │   Chunking + Embedding       │   │
│                           │  (5min摘要/关键词 + 向量入库)   │   │
│                           └──────────────┬───────────────┘   │
└──────────────────────────────────────────┼───────────────────┘
                                           │
                                           ▼
                                    MediaFile.status=READY
```

**关键特性**：
- **全异步**：Celery 任务链，每阶段独立重试、可观测
- **断点续传**：片段级状态机（vid-lens `TranscriptionChunk` 模式）
- **幂等去重**：内容哈希（MD5/SHA256）+ 目标哈希双键
- **GPU 错峰**：Whisper → OCR/Embedding → Ollama 串行独占显存

---

## 2. 阶段详细设计

### 2.1 下载阶段

**入口**：`DownloadStage.execute(media_id, url)`

```python
class DownloadStage:
    async def execute(self, ctx: IngestionContext) -> DownloadResult:
        # 1. 幂等检查：content_hash 是否已存在
        existing = await self.repo.find_by_hash(ctx.content_hash)
        if existing:
            return DownloadResult(media_id=existing.id, reused=True)

        # 2. 平台路由
        downloader = self.router.get_downloader(ctx.url)
        
        # 3. 下载执行（线程池避免阻塞事件循环）
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self.thread_pool,
            downloader.download,
            ctx.url,
            ctx.download_options
        )
        
        # 4. 存入 MinIO + 计算哈希
        minio_path, content_hash = await self.storage.put_video(
            result.local_path, 
            media_id=ctx.media_id
        )
        
        # 5. FFmpeg 探测元信息
        meta = await self.ffprobe.probe(minio_path)
        
        return DownloadResult(
            media_id=ctx.media_id,
            minio_path=minio_path,
            content_hash=content_hash,
            duration=meta.duration,
            width=meta.width,
            height=meta.height,
            fps=meta.fps,
            codec=meta.codec
        )
```

**下载器路由表**：

| 平台 | 实现类 | 特殊处理 |
|------|--------|----------|
| 通用 | `YtDlpDownloader` | yt-dlp 原生 1800+ 站点 |
| 抖音 | `DouyinDownloader` | 短链→重定向→video_id→公开 API/分享页解析→`playwm`→`play` 去水印 |
| B站 | `BilibiliDownloader` | 支持 BV/AV 号、番剧、合集、登录 Cookie |
| YouTube | `YtDlpDownloader` | 格式选择 `bestvideo+bestaudio`、字幕下载 |

> ⏳ **延后实现（Phase 2+，非首版）**：抖音 `DouyinDownloader`（无 Cookie 解析）与下方"无水印解析流程"均为后续规划，首版仅启用 yt-dlp 通用下载；字幕下载（`writesubtitles`/`writeautomaticsub`）本版关闭，文本来源以本地 ASR 为准。

**yt-dlp 统一配置** (`core/video/downloader.py`)：
```python
YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "extract_flat": False,
    "format": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "merge_output_format": "mp4",
    "writesubtitles": False,        # ⏳ 延后实现（Phase 2+，非首版）；字幕下载本版不做，文本来源以本地 ASR 为准
    "writeautomaticsub": False,    # ⏳ 延后实现（Phase 2+，非首版）
    "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "en"],
    "subtitlesformat": "vtt",
    "skip_download": False,  # 下载阶段为 True
}
```

> ⏳ **延后实现（Phase 2+，非首版）**：以下抖音无水印解析流程为后续规划，首版不做。

**Douyin 无水印解析流程**（移植自 free-video-downloader）：
```
短链接 (v.douyin.com/xxx)
    │
    ▼
HTTP HEAD 允许重定向 → 获取真实分享页 URL
    │
    ▼
提取 video_id (正则 /video/(\d+)/)
    │
    ▼
调用公开 API: https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={video_id}
    │
    ├─ 成功 → video.play_addr.url_list[0] (含水印) → 替换 playwm → play
    │
    └─ 失败 → 解析分享页 HTML → window._ROUTER_DATA JSON → 同路径提取
```

---

### 2.2 转码/分段阶段

**入口**：`TranscodeStage.execute(download_result)`

```python
class TranscodeStage:
    SEGMENT_DURATION = 60  # 秒，Whisper 单次推理窗口
    AUDIO_SAMPLE_RATE = 16000
    AUDIO_CHANNELS = 1
    
    async def execute(self, ctx: IngestionContext) -> TranscodeResult:
        minio_path = ctx.download_result.minio_path
        
        # 1. 下载到本地临时目录（GPU Worker 节点需共享存储或拉取）
        local_video = await self.storage.download_to_temp(minio_path)
        
        try:
            # 2. 提取音频 (16kHz mono mp3)
            audio_path = await self.ffmpeg.extract_audio(
                local_video,
                sample_rate=self.AUDIO_SAMPLE_RATE,
                channels=self.AUDIO_CHANNELS
            )
            
            # 3. 场景变化检测关键帧
            keyframes = await self.ffmpeg.detect_scene_changes(
                local_video,
                threshold=0.35,  # 感知哈希差异阈值
                min_interval=5.0,  # 最小间隔秒
                max_interval=30.0  # 兜底采样间隔
            )
            
            # 4. 音频切片 (60s/段，最后一段可能 < 60s)
            segments = await self.ffmpeg.split_audio(
                audio_path,
                segment_duration=self.SEGMENT_DURATION
            )
            
            # 4. 关键帧抽图（对应时间戳）
            frame_paths = await self.ffmpeg.extract_frames(
                local_video,
                timestamps=[kf.timestamp for kf in keyframes]
            )
            
            return TranscodeResult(
                audio_path=audio_path,
                segments=segments,  # [{index, start_ms, end_ms, path}]
                keyframes=[KeyFrame(ts=kf.timestamp, path=p) for kf, p in zip(keyframes, frame_paths)],
                duration_ms=ctx.download_result.duration * 1000
            )
        finally:
            # 清理临时文件（保留音频切片供 ASR 阶段复用）
            await self.cleanup_temp(local_video, keep=segments)
```

**FFmpeg 关键参数**：
```bash
# 提取音频
ffmpeg -i input.mp4 -vn -acodec libmp3lame -ar 16000 -ac 1 -f mp3 output.mp3

# 场景检测
ffmpeg -i input.mp4 -vf "select='gt(scene,0.35)',showinfo" -f null -

# 切片音频
ffmpeg -i audio.mp3 -f segment -segment_time 60 -c copy segment_%03d.mp3

# 抽帧
ffmpeg -i input.mp4 -vf "select='eq(n,<frame_num>)'" -vframes 1 -f image2 frame_<ts>.jpg
```

---

### 2.3 ASR 阶段（核心：分段级状态机 + 断点续传）

**数据模型**（对应 `TranscriptionChunk` 表）：
```python
class TranscriptionChunk(Base):
    __tablename__ = "transcription_chunk"
    
    id: UUID
    media_id: UUID
    chunk_index: int
    start_ms: int
    end_ms: int
    status: Enum("pending", "running", "completed", "failed")
    content: str | None
    error_msg: str | None
    audio_object: str | None  # MinIO object path
    retry_count: int = 0
    max_retries: int = 3
    processing_node: str | None  # Worker hostname
    lease_expires_at: datetime | None  # 租约过期时间
    created_at: datetime
    updated_at: datetime
```

**执行流程**（vid-lens `handleTranscribe` 移植）：

```python
class ASRStage:
    def __init__(self, gpu_scheduler: GPUResourceManager):
        self.gpu = gpu_scheduler
        self.whisper_model = None  # 延迟加载
    
    async def execute(self, ctx: IngestionContext) -> ASRResult:
        media_id = ctx.media_id
        segments = ctx.transcode_result.segments
        
        # 1. 初始化/恢复 chunk 记录
        await self.repo.ensure_chunks(media_id, segments)
        
        # 2. 获取待处理 chunk（pending + 租约过期的 running）
        pending_chunks = await self.repo.get_pending_chunks(media_id)
        
        # 3. 并发处理（受 GPU 信号量限制）
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def process_chunk(chunk: TranscriptionChunk):
            async with semaphore:
                # 3.1 申请 GPU 租约（独占显存）
                async with self.gpu.acquire("asr", ttl=300) as gpu_ctx:
                    # 3.2 更新状态 running + 租约
                    await self.repo.claim_chunk(chunk.id, gpu_ctx.worker_id, lease_ttl=300)
                    
                    try:
                        # 3.3 下载音频片段
                        audio_bytes = await self.storage.get(chunk.audio_object)
                        
                        # 3.4 Whisper 推理（GPU）
                        result = await self.transcribe_audio(audio_bytes, gpu_ctx.device)
                        
                        # 3.5 更新 completed
                        await self.repo.complete_chunk(chunk.id, result.text)
                        
                        return ChunkResult(chunk.index, result.text, None)
                    except Exception as e:
                        # 3.6 失败处理：重试或标记 failed
                        await self.repo.fail_chunk(chunk.id, str(e))
                        return ChunkResult(chunk.index, None, e)
        
        # 4. 并发执行所有 pending chunk
        results = await asyncio.gather(*[process_chunk(c) for c in pending_chunks])
        
        # 5. 检查是否全部完成
        all_chunks = await self.repo.get_all_chunks(media_id)
        if any(c.status != "completed" for c in all_chunks):
            raise IncompleteASRError("部分片段转写失败，需重试")
        
        # 6. 合并全量文本
        full_text = " ".join(c.content for c in sorted(all_chunks, key=lambda x: x.chunk_index))
        
        # 7. 入库 transcription（表名，见 DATA-MODEL.md 章节 2.2）
        await self.repo.upsert_transcription(media_id, full_text)
        
        return ASRResult(full_text=full_text, chunks=all_chunks)
```

**Whisper 推理封装**：
```python
async def transcribe_audio(self, audio_bytes: bytes, device: str) -> WhisperResult:
    loop = asyncio.get_event_loop()
    
    def _infer():
        # 延迟加载模型（首次调用时）
        if self.whisper_model is None:
            self.whisper_model = whisper.load_model(
                "large-v3", 
                device=device,
                download_root=settings.WHISPER_MODEL_DIR
            )
        
        # 写临时文件（Whisper 需文件路径）
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name
        
        try:
            # 关键参数
            result = self.whisper_model.transcribe(
                temp_path,
                language="zh",  # 或 auto
                task="transcribe",
                fp16=(device == "cuda"),
                verbose=False,
                word_timestamps=True,  # 词级时间戳
                condition_on_previous_text=True,
                temperature=0.0,  # 确定性
                compression_ratio_threshold=2.4,
                logprob_threshold=-1.0,
                no_speech_threshold=0.6
            )
            return WhisperResult(
                text=result["text"].strip(),
                segments=result["segments"],  # [{start, end, text, words[]}]
                language=result["language"]
            )
        finally:
            os.unlink(temp_path)
    
    return await loop.run_in_executor(self.thread_pool, _infer)
```

> **GPU 显式管理**：统一委托 `core/task.GPUResourceManager`（详见 TASK-ORCHESTRATION.md「GPU 调度」），基于 Redis 分布式锁 `gpu:lock` + 心跳续租，跨 Worker 安全。
> 本管线只消费 `acquire(task_id, stage) → GPUHandle` / `release(handle)` 语义，不另起本地实现。错峰策略见下表。

---

### 2.4 OCR 阶段

```python
class OCRStage:
    def __init__(self, gpu_scheduler: GPUResourceManager):
        self.gpu = gpu_scheduler
        self.ocr_engine = None  # PaddleOCR 延迟加载
    
    async def execute(self, ctx: IngestionContext) -> OCRResult:
        keyframes = ctx.transcode_result.keyframes
        
        # 1. 确保 FrameOCR 记录存在
        await self.repo.ensure_frames(ctx.media_id, keyframes)
        
        # 2. 获取待处理帧
        pending = await self.repo.get_pending_frames(ctx.media_id)
        
        # 3. 并发 OCR（GPU 信号量控制）
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def ocr_frame(frame: FrameOCR):
            async with semaphore:
                async with self.gpu.acquire("ocr") as gpu_ctx:
                    await self.repo.claim_frame(frame.id)
                    try:
                        image_bytes = await self.storage.get(frame.minio_path)
                        result = await self._ocr_image(image_bytes, gpu_ctx.device)
                        await self.repo.complete_frame(frame.id, result.text, result.confidence)
                        return FrameResult(frame.timestamp_ms, result.text, result.confidence)
                    except Exception as e:
                        await self.repo.fail_frame(frame.id, str(e))
                        return FrameResult(frame.timestamp_ms, None, 0.0, error=e)
        
        results = await asyncio.gather(*[ocr_frame(f) for f in pending])
        
        # 4. 感知哈希去重（pHash，阈值 ≤ 10）
        unique_results = self._deduplicate_by_phash(results)
        
        return OCRResult(frames=unique_results)
    
    def _deduplicate_by_phash(self, results: list[FrameResult]) -> list[FrameResult]:
        seen_hashes = []
        unique = []
        for r in results:
            if r.error or not r.text:
                continue
            phash = imagehash.phash(Image.open(io.BytesIO(r.image_bytes)))
            if not any(phash - h <= 10 for h in seen_hashes):
                seen_hashes.append(phash)
                unique.append(r)
        return unique
```

**PaddleOCR 初始化**：
```python
def _init_ocr(self, device: str):
    if self.ocr_engine is None:
        self.ocr_engine = PaddleOCR(
            use_angle_cls=True,
            lang="ch",
            use_gpu=(device == "cuda"),
            gpu_mem=2048,  # MB
            enable_mkldnn=True,
            cpu_threads=4,
            det_model_dir=settings.PADDLE_DET_MODEL_DIR,
            rec_model_dir=settings.PADDLE_REC_MODEL_DIR,
            cls_model_dir=settings.PADDLE_CLS_MODEL_DIR
        )
```

---

### 2.5 VideoContext 构建阶段

**核心数据结构**：
```python
@dataclass
class VideoSegment:
    segment_index: int
    start_ms: int
    end_ms: int
    transcript: str           # ASR 文本
    ocr_texts: list[str]      # 该窗口内关键帧 OCR 文本
    evidence_frames: list[EvidenceFrame]  # 关键帧引用

@dataclass
class EvidenceFrame:
    timestamp_ms: int
    minio_path: str
    phash: str
    ocr_text: str | None

@dataclass
class VideoContext:
    media_id: UUID
    source: str               # "upload" | "url"
    user_goal: str
    segments: list[VideoSegment]
    duration_ms: int
    full_transcript: str
    created_at: datetime
```

**构建算法**（60s 滑窗合并）：
```python
class VideoContextBuilder:
    WINDOW_MS = 60_000
    
    def build(self, media_id: UUID, asr_result: ASRResult, ocr_result: OCRResult) -> VideoContext:
        # 1. 获取所有 ASR 段落（按时间排序）
        asr_segments = asr_result.chunks  # 已按 index 排序
        
        # 2. 获取 OCR 帧（按时间排序）
        ocr_frames = sorted(ocr_result.frames, key=lambda f: f.timestamp_ms)
        
        # 3. 滑窗合并
        segments = []
        for i, asr_seg in enumerate(asr_segments):
            win_start = asr_seg.start_ms
            win_end = asr_seg.end_ms
            
            # 收集窗口内 OCR
            window_ocr = [
                f.ocr_text for f in ocr_frames 
                if win_start <= f.timestamp_ms < win_end
            ]
            
            # 收集证据帧
            evidence = [
                EvidenceFrame(
                    timestamp_ms=f.timestamp_ms,
                    minio_path=f.minio_path,
                    phash=f.phash,
                    ocr_text=f.ocr_text
                )
                for f in ocr_frames
                if win_start <= f.timestamp_ms < win_end
            ]
            
            segments.append(VideoSegment(
                segment_index=i,
                start_ms=win_start,
                end_ms=win_end,
                transcript=asr_seg.content,
                ocr_texts=window_ocr,
                evidence_frames=evidence
            ))
        
        # 4. 补全：最后一段可能 < 60s，前段可能有空窗
        #    这里简单处理，实际可按需合并相邻短段
        
        return VideoContext(
            media_id=media_id,
            source=ctx.source,
            user_goal=ctx.user_goal,
            segments=segments,
            duration_ms=asr_result.chunks[-1].end_ms,
            full_transcript=asr_result.full_text,
            created_at=datetime.utcnow()
        )
```

---

### 2.6 入库索引阶段（Chunking + Embedding → Qdrant + BM25）

```python
class IndexStage:
    CHUNK_SIZE = 800      # tokens
    CHUNK_OVERLAP = 120
    CHUNK_DURATION_MS = 5 * 60 * 1000  # 5min 逻辑 chunk（长视频检索用）
    
    async def execute(self, ctx: IngestionContext) -> IndexResult:
        video_ctx = ctx.video_context
        
        # 1. 长视频分块：5min 摘要 + 关键词 + Embedding
        long_chunks = self._create_long_chunks(video_ctx)
        
        # 2. 并发生成摘要/关键词（LLM）+ Embedding
        enriched_chunks = await self._enrich_chunks(long_chunks)
        
        # 3. 双写：PostgreSQL chunk 表 + Qdrant 向量
        await self._dual_write(enriched_chunks)
        
        # 4. 短窗口 BM25 索引（60s segment 级）
        await self._build_bm25_index(video_ctx.segments)
        
        # 5. 计算 Manifest SHA256（可复现性锚点）
        manifest_sha = self._compute_manifest_sha(enriched_chunks)
        await self.repo.update_rag_index(media_id, status="indexed", manifest_sha=manifest_sha)
        
        return IndexResult(chunk_count=len(enriched_chunks), manifest_sha=manifest_sha)
    
    def _create_long_chunks(self, ctx: VideoContext) -> list[LongChunk]:
        """按 5min 切分，每块含多个 60s segment"""
        chunks = []
        current_chunk_segments = []
        current_start = 0
        
        for seg in ctx.segments:
            current_chunk_segments.append(seg)
            if seg.end_ms - current_start >= self.CHUNK_DURATION_MS:
                chunks.append(LongChunk(
                    chunk_index=len(chunks),
                    start_ms=current_start,
                    end_ms=seg.end_ms,
                    segments=current_chunk_segments.copy()
                ))
                current_chunk_segments.clear()
                current_start = seg.end_ms
        
        if current_chunk_segments:
            chunks.append(LongChunk(
                chunk_index=len(chunks),
                start_ms=current_start,
                end_ms=ctx.duration_ms,
                segments=current_chunk_segments
            ))
        
        return chunks
    
    async def _enrich_chunks(self, chunks: list[LongChunk]) -> list[EnrichedChunk]:
        async def enrich(chunk: LongChunk):
            # 拼接文本
            text = "\n".join(
                f"[{ms_to_ts(s.start_ms)}-{ms_to_ts(s.end_ms)}] {s.transcript}"
                for s in chunk.segments
            )
            
            # LLM 生成摘要 + 关键词（并发）
            summary, keywords = await asyncio.gather(
                self.llm.summarize(text),
                self.llm.extract_keywords(text)
            )
            
            # Embedding
            vector = await self.embedding.embed(text)
            
            return EnrichedChunk(
                chunk=chunk,
                content=text,
                summary=summary,
                keywords=keywords,
                vector=vector,
                token_count=count_tokens(text)
            )
        
        return await asyncio.gather(*[enrich(c) for c in chunks])
    
    async def _dual_write(self, chunks: list[EnrichedChunk]):
        # PostgreSQL
        orm_chunks = [
            Chunk(
                media_id=chunk.chunk.media_id,
                chunk_index=chunk.chunk.chunk_index,
                start_ms=chunk.chunk.start_ms,
                end_ms=chunk.chunk.end_ms,
                content=chunk.content,
                content_hash=sha256(chunk.content.encode()).hexdigest()[:12],
                summary=chunk.summary,
                keywords=chunk.keywords,
                token_count=chunk.token_count,
                qdrant_point_id=str(uuid5(NAMESPACE_DNS, f"{chunk.chunk.media_id}:{chunk.chunk.chunk_index}"))
            )
            for chunk in chunks
        ]
        await self.chunk_repo.bulk_upsert(orm_chunks)
        
        # Qdrant
        points = [
            PointStruct(
                id=orm.qdrant_point_id,
                vector=chunk.vector,
                payload={
                    "media_id": str(chunk.chunk.media_id),
                    "chunk_index": chunk.chunk.chunk_index,
                    "start_ms": chunk.chunk.start_ms,
                    "end_ms": chunk.chunk.end_ms,
                    "content": chunk.content[:1000],  # payload 存前 1000 字符
                    "summary": chunk.summary,
                    "keywords": chunk.keywords
                }
            )
            for orm, chunk in zip(orm_chunks, chunks)
        ]
        await self.qdrant.upsert(collection_name="video_chunks", points=points)
    
    def _compute_manifest_sha(self, chunks: list[EnrichedChunk]) -> str:
        """有序 JSON 序列化 + SHA256，用于一致性校验"""
        manifest = [
            {
                "chunk_index": c.chunk.chunk_index,
                "content_hash": c.chunk.content_hash,
                "vector_id": c.chunk.qdrant_point_id
            }
            for c in sorted(chunks, key=lambda x: x.chunk.chunk_index)
        ]
        return sha256(json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
```

---

## 3. 错误处理与重试策略

| 阶段 | 失败分类 | 重试策略 | 死信处理 |
|------|----------|----------|----------|
| 下载 | 网络/403/反爬 | 指数退避 3 次，更换代理/Cookie | 标记 `download_failed`，人工介入 |
| 转码 | FFmpeg 错误/文件损坏 | 不重试（通常是源文件问题） | 标记 `transcode_failed` |
| ASR | OOM/超时/模型错误 | 片段级重试 3 次，释放 GPU 后重试 | 标记 chunk `failed`，人工复核 |
| OCR | 图片解码失败/模型错误 | 单帧重试 2 次 | 标记帧 `failed`，不阻塞管线 |
| 索引 | Embedding 失败/Qdrant 写入失败 | 整块重试 3 次 | 标记 `index_failed`，支持增量重建 |

**租约机制防止重复处理**（vid-lens `ProcessingLease` 移植）：
```python
@asynccontextmanager
async def processing_lease(repo, entity_id: str, entity_type: str, ttl: int = 1800):
    """分布式租约：SET NX + Lua 延期 + 自动释放"""
    lease_key = f"lease:{entity_type}:{entity_id}"
    acquired = await redis.set(lease_key, worker_id, nx=True, ex=ttl)
    if not acquired:
        raise LeaseAcquisitionError(f"Entity {entity_id} being processed by another worker")
    
    # 心跳任务
    heartbeat_task = asyncio.create_task(_renew_lease(lease_key, ttl))
    
    try:
        yield
    finally:
        heartbeat_task.cancel()
        # 仅当仍是持有者时释放
        lua = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        end
        return 0
        """
        await redis.eval(lua, 1, lease_key, worker_id)
```

---

## 4. 监控指标

| 指标名 | 类型 | 标签 | 用途 |
|--------|------|------|------|
| `vm_pipeline_stage_duration_seconds` | Histogram | stage, status | 各阶段耗时分布 |
| `vm_pipeline_stage_total` | Counter | stage, status | 成功/失败计数 |
| `vm_asr_chunk_duration_seconds` | Histogram | media_id | 单片段 ASR 耗时 |
| `vm_asr_chunk_retries_total` | Counter | media_id | 重试次数 |
| `vm_gpu_memory_allocated_bytes` | Gauge | phase, worker | 显存占用监控 |
| `vm_ffmpeg_duration_seconds` | Histogram | operation | FFmpeg 各操作耗时 |
| `vm_minio_upload_bytes_total` | Counter | bucket | 对象存储写入量 |

---

## 5. 关键配置参数

```yaml
# config/video_pipeline.yaml
pipeline:
  download:
    timeout_seconds: 600
    max_retries: 3
    proxy_pool_enabled: true
    douyin_cookie_refresh_hours: 24   # ⏳ 延后实现（Phase 2+，非首版）
  
  transcode:
    segment_duration_seconds: 60
    audio_sample_rate: 16000
    audio_channels: 1
    scene_threshold: 0.35
    keyframe_min_interval: 5
    keyframe_max_interval: 30
  
  asr:
    model: "large-v3"
    device: "cuda"
    batch_size: 1
    language: "zh"
    word_timestamps: true
    max_concurrent_chunks: 1  # 单卡独占
    chunk_retry_max: 3
    lease_ttl_seconds: 300
  
  ocr:
    engine: "paddleocr"
    lang: "ch"
    use_gpu: true
    gpu_mem_mb: 2048
    max_concurrent_frames: 2
    phash_dedup_threshold: 10
  
  context:
    window_ms: 60000
    long_chunk_duration_ms: 300000  # 5min
  
  indexing:
    chunk_size_tokens: 800
    chunk_overlap_tokens: 120
    embedding_model: "BAAI/bge-m3"
    qdrant_collection: "video_chunks"
    bm25_index_path: "/data/bm25_indexes"
  
  gpu_scheduler:
    phases: ["asr", "ocr", "embedding", "llm"]
    gpu_count: 1
    exclusive_per_phase: true
```

---

> **关联文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [DATA-MODEL.md](DATA-MODEL.md) · [RAG-RETRIEVAL.md](RAG-RETRIEVAL.md) · [TASK-ORCHESTRATION.md](TASK-ORCHESTRATION.md) · [OBSERVABILITY.md](OBSERVABILITY.md)