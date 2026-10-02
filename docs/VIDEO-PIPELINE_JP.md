# VideoMind 動画処理パイプライン設計

> ダウンロード → トランスコード → セグメント単位 ASR → キーフレーム OCR → VideoContext 構築 → インデックス登録
> 主要参考：vid-lens `internal/mq/consumer.go`（セグメント単位転写／セグメント単位ステートマシン／リース・ハートビート）+ free-video-downloader `downloader.py` `douyin.py`

---

## 1. パイプライン概要

```
ユーザーによるアップロード／リンク
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
│                        ┌─────────────────────────────────┐   │
│                        │       Build VideoContext        │   │
│                        │  (60s窓でASR+OCR+フレーム統合)  │   │
│                        └────────────────┬────────────────┘   │
│                                          │                   │
│                                          ▼                   │
│                        ┌─────────────────────────────────┐   │
│                        │      Chunking + Embedding       │   │
│                        │(5分要約/キーワード+ベクトル登録)│   │
│                        └────────────────┬────────────────┘   │
└──────────────────────────────────────────┼───────────────────┘
                                           │
                                           ▼
                                    MediaFile.status=READY
```

**主な特徴**：
- **完全非同期**：Celery タスクチェーン。各段階が独立してリトライ可能・可観測
- **レジューム（分割転写の中断・再開）**：セグメント単位ステートマシン（vid-lens `TranscriptionChunk` パターン）
- **冪等性による重複排除**：コンテンツハッシュ（MD5/SHA256）+ ターゲットハッシュの二重キー
- **GPU オフピーク**：Whisper → OCR/Embedding → Ollama を直列実行し VRAM を独占

---

## 2. 各段階の詳細設計

### 2.1 ダウンロード段階

**入口**：`DownloadStage.execute(media_id, url)`

```python
class DownloadStage:
    async def execute(self, ctx: IngestionContext) -> DownloadResult:
        # 1. 冪等性チェック：content_hash が既に存在するか
        existing = await self.repo.find_by_hash(ctx.content_hash)
        if existing:
            return DownloadResult(media_id=existing.id, reused=True)

        # 2. プラットフォームルーティング
        downloader = self.router.get_downloader(ctx.url)
        
        # 3. ダウンロード実行（スレッドプールでイベントループのブロックを回避）
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self.thread_pool,
            downloader.download,
            ctx.url,
            ctx.download_options
        )
        
        # 4. MinIO へ保存 + ハッシュ計算
        minio_path, content_hash = await self.storage.put_video(
            result.local_path, 
            media_id=ctx.media_id
        )
        
        # 5. FFmpeg でメタ情報をプローブ
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

**ダウンローダのルーティングテーブル**：

| プラットフォーム | 実装クラス | 特殊処理 |
|------|--------|----------|
| 汎用 | `YtDlpDownloader` | yt-dlp が標準で 1800+ サイトに対応 |
| 抖音（Douyin） | `DouyinDownloader` | 短縮リンク→リダイレクト→video_id→公開 API／共有ページ解析→`playwm`→`play` の透かし除去 |
| Bilibili | `BilibiliDownloader` | BV/AV 番号、アニメ、コレクション、ログイン Cookie に対応 |
| YouTube | `YtDlpDownloader` | フォーマット選択 `bestvideo+bestaudio`、字幕ダウンロード |

> ⏳ **後回し実装（Phase 2+、初版では未対応）**：抖音の `DouyinDownloader`（Cookie なし解析）と下記の「透かし除去解析フロー」はいずれも後続の計画であり、初版では yt-dlp による汎用ダウンロードのみを有効化します。字幕ダウンロード（`writesubtitles`/`writeautomaticsub`）は本バージョンでは無効とし、テキストの取得元はローカル ASR を正とします。

**yt-dlp 統一設定** (`core/video/downloader.py`)：
```python
YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "extract_flat": False,
    "format": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "merge_output_format": "mp4",
    "writesubtitles": False,        # ⏳ 後回し実装（Phase 2+、初版では未対応）。字幕ダウンロードは本バージョンでは行わず、テキストの取得元はローカル ASR を正とする
    "writeautomaticsub": False,    # ⏳ 後回し実装（Phase 2+、初版では未対応）
    "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "en"],
    "subtitlesformat": "vtt",
    "skip_download": False,  # ダウンロード段階では True
}
```

> ⏳ **後回し実装（Phase 2+、初版では未対応）**：以下の抖音の透かし除去解析フローは後続の計画であり、初版では実施しません。

**Douyin の透かし除去解析フロー**（free-video-downloader から移植）：
```
短縮リンク (v.douyin.com/xxx)
    │
    ▼
HTTP HEAD でリダイレクトを許可 → 実際の共有ページ URL を取得
    │
    ▼
video_id を抽出（正規表現 /video/(\d+)/）
    │
    ▼
公開 API を呼び出し: https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={video_id}
    │
    ├─ 成功 → video.play_addr.url_list[0]（透かしあり）→ playwm を play に置換
    │
    └─ 失敗 → 共有ページ HTML を解析 → window._ROUTER_DATA JSON → 同経路で抽出
```

---

### 2.2 トランスコード／セグメント分割段階

**入口**：`TranscodeStage.execute(download_result)`

```python
class TranscodeStage:
    SEGMENT_DURATION = 60  # 秒。Whisper の 1 回の推論ウィンドウ
    AUDIO_SAMPLE_RATE = 16000
    AUDIO_CHANNELS = 1
    
    async def execute(self, ctx: IngestionContext) -> TranscodeResult:
        minio_path = ctx.download_result.minio_path
        
        # 1. ローカルの一時ディレクトリへダウンロード（GPU Worker ノードは共有ストレージかプルが必要）
        local_video = await self.storage.download_to_temp(minio_path)
        
        try:
            # 2. 音声を抽出（16kHz モノラル mp3）
            audio_path = await self.ffmpeg.extract_audio(
                local_video,
                sample_rate=self.AUDIO_SAMPLE_RATE,
                channels=self.AUDIO_CHANNELS
            )
            
            # 3. シーン変化でキーフレームを検出
            keyframes = await self.ffmpeg.detect_scene_changes(
                local_video,
                threshold=0.35,  # 知覚ハッシュ差分のしきい値
                min_interval=5.0,  # 最小間隔（秒）
                max_interval=30.0  # フォールバックのサンプリング間隔
            )
            
            # 4. 音声をスライス（60 秒／セグメント。最後のセグメントは 60 秒未満の場合あり）
            segments = await self.ffmpeg.split_audio(
                audio_path,
                segment_duration=self.SEGMENT_DURATION
            )
            
            # 4. キーフレームの画像を抽出（対応するタイムスタンプ）
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
            # 一時ファイルをクリーンアップ（音声スライスは ASR 段階で再利用するため保持）
            await self.cleanup_temp(local_video, keep=segments)
```

**FFmpeg 主要パラメータ**：
```bash
# 音声を抽出
ffmpeg -i input.mp4 -vn -acodec libmp3lame -ar 16000 -ac 1 -f mp3 output.mp3

# シーン検出
ffmpeg -i input.mp4 -vf "select='gt(scene,0.35)',showinfo" -f null -

# 音声を分割
ffmpeg -i audio.mp3 -f segment -segment_time 60 -c copy segment_%03d.mp3

# フレーム抽出
ffmpeg -i input.mp4 -vf "select='eq(n,<frame_num>)'" -vframes 1 -f image2 frame_<ts>.jpg
```

---

### 2.3 ASR 段階（核心：セグメント単位ステートマシン + レジューム）

**データモデル**（`TranscriptionChunk` テーブルに対応）：
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
    lease_expires_at: datetime | None  # リースの有効期限
    created_at: datetime
    updated_at: datetime
```

**実行フロー**（vid-lens `handleTranscribe` を移植）：

```python
class ASRStage:
    def __init__(self, gpu_scheduler: GPUResourceManager):
        self.gpu = gpu_scheduler
        self.whisper_model = None  # 遅延ロード
    
    async def execute(self, ctx: IngestionContext) -> ASRResult:
        media_id = ctx.media_id
        segments = ctx.transcode_result.segments
        
        # 1. chunk レコードを初期化／復元
        await self.repo.ensure_chunks(media_id, segments)
        
        # 2. 処理対象 chunk を取得（pending + リース切れの running）
        pending_chunks = await self.repo.get_pending_chunks(media_id)
        
        # 3. 並行処理（GPU セマフォで制限）
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def process_chunk(chunk: TranscriptionChunk):
            async with semaphore:
                # 3.1 GPU リースを取得（VRAM を独占）
                async with self.gpu.acquire("asr", ttl=300) as gpu_ctx:
                    # 3.2 状態を running + リースに更新
                    await self.repo.claim_chunk(chunk.id, gpu_ctx.worker_id, lease_ttl=300)
                    
                    try:
                        # 3.3 音声セグメントをダウンロード
                        audio_bytes = await self.storage.get(chunk.audio_object)
                        
                        # 3.4 Whisper 推論（GPU）
                        result = await self.transcribe_audio(audio_bytes, gpu_ctx.device)
                        
                        # 3.5 completed に更新
                        await self.repo.complete_chunk(chunk.id, result.text)
                        
                        return ChunkResult(chunk.index, result.text, None)
                    except Exception as e:
                        # 3.6 失敗処理：リトライまたは failed としてマーク
                        await self.repo.fail_chunk(chunk.id, str(e))
                        return ChunkResult(chunk.index, None, e)
        
        # 4. すべての pending chunk を並行実行
        results = await asyncio.gather(*[process_chunk(c) for c in pending_chunks])
        
        # 5. すべて完了したか確認
        all_chunks = await self.repo.get_all_chunks(media_id)
        if any(c.status != "completed" for c in all_chunks):
            raise IncompleteASRError("一部のセグメントの転写に失敗しました。リトライが必要です")
        
        # 6. 全テキストをマージ
        full_text = " ".join(c.content for c in sorted(all_chunks, key=lambda x: x.chunk_index))
        
        # 7. transcription を登録（テーブル名。DATA-MODEL_JP.md の 2.2 節を参照）
        await self.repo.upsert_transcription(media_id, full_text)
        
        return ASRResult(full_text=full_text, chunks=all_chunks)
```

**Whisper 推論のラッパー**：
```python
async def transcribe_audio(self, audio_bytes: bytes, device: str) -> WhisperResult:
    loop = asyncio.get_event_loop()
    
    def _infer():
        # モデルを遅延ロード（初回呼び出し時）
        if self.whisper_model is None:
            self.whisper_model = whisper.load_model(
                "large-v3", 
                device=device,
                download_root=settings.WHISPER_MODEL_DIR
            )
        
        # 一時ファイルへ書き出し（Whisper はファイルパスを必要とする）
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name
        
        try:
            # 主要パラメータ
            result = self.whisper_model.transcribe(
                temp_path,
                language="zh",  # または auto
                task="transcribe",
                fp16=(device == "cuda"),
                verbose=False,
                word_timestamps=True,  # 単語レベルのタイムスタンプ
                condition_on_previous_text=True,
                temperature=0.0,  # 決定性
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

> **GPU の明示的管理**：`core/task.GPUResourceManager` に一括委譲します（詳細は TASK-ORCHESTRATION_JP.md の「GPU スケジューリング」）。Redis の分散ロック `gpu:lock` + ハートビートによるリース更新に基づき、Worker をまたいでも安全です。
> 本パイプラインは `acquire(task_id, stage) → GPUHandle` / `release(handle)` のセマンティクスのみを利用し、独自のローカル実装は持ちません。オフピーク戦略は下表を参照。

---

### 2.4 OCR 段階

```python
class OCRStage:
    def __init__(self, gpu_scheduler: GPUResourceManager):
        self.gpu = gpu_scheduler
        self.ocr_engine = None  # PaddleOCR を遅延ロード
    
    async def execute(self, ctx: IngestionContext) -> OCRResult:
        keyframes = ctx.transcode_result.keyframes
        
        # 1. FrameOCR レコードの存在を保証
        await self.repo.ensure_frames(ctx.media_id, keyframes)
        
        # 2. 処理対象フレームを取得
        pending = await self.repo.get_pending_frames(ctx.media_id)
        
        # 3. 並行 OCR（GPU セマフォで制御）
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
        
        # 4. 知覚ハッシュで重複排除（pHash、しきい値 ≤ 10）
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

**PaddleOCR の初期化**：
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

### 2.5 VideoContext 構築段階

**中核データ構造**：
```python
@dataclass
class VideoSegment:
    segment_index: int
    start_ms: int
    end_ms: int
    transcript: str           # ASR テキスト
    ocr_texts: list[str]      # このウィンドウ内のキーフレーム OCR テキスト
    evidence_frames: list[EvidenceFrame]  # キーフレーム参照

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

**構築アルゴリズム**（60s スライディングウィンドウでマージ）：
```python
class VideoContextBuilder:
    WINDOW_MS = 60_000
    
    def build(self, media_id: UUID, asr_result: ASRResult, ocr_result: OCRResult) -> VideoContext:
        # 1. すべての ASR セグメントを取得（時間順）
        asr_segments = asr_result.chunks  # index でソート済み
        
        # 2. OCR フレームを取得（時間順）
        ocr_frames = sorted(ocr_result.frames, key=lambda f: f.timestamp_ms)
        
        # 3. スライディングウィンドウでマージ
        segments = []
        for i, asr_seg in enumerate(asr_segments):
            win_start = asr_seg.start_ms
            win_end = asr_seg.end_ms
            
            # ウィンドウ内の OCR を収集
            window_ocr = [
                f.ocr_text for f in ocr_frames 
                if win_start <= f.timestamp_ms < win_end
            ]
            
            # 証拠フレームを収集
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
        
        # 4. 補完：最後のセグメントは 60 秒未満の可能性があり、前段には空きウィンドウがあり得る
        #    ここでは簡易的に処理。実際には必要に応じて隣接する短いセグメントをマージ可能
        
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

### 2.6 インデックス登録段階（Chunking + Embedding → Qdrant + BM25）

```python
class IndexStage:
    CHUNK_SIZE = 800      # tokens
    CHUNK_OVERLAP = 120
    CHUNK_DURATION_MS = 5 * 60 * 1000  # 5 分の論理 chunk（長尺動画の検索用）
    
    async def execute(self, ctx: IngestionContext) -> IndexResult:
        video_ctx = ctx.video_context
        
        # 1. 長尺動画のチャンク分割：5 分の要約 + キーワード + Embedding
        long_chunks = self._create_long_chunks(video_ctx)
        
        # 2. 要約／キーワード（LLM）と Embedding を並行生成
        enriched_chunks = await self._enrich_chunks(long_chunks)
        
        # 3. 二重書き込み：PostgreSQL の chunk テーブル + Qdrant ベクトル
        await self._dual_write(enriched_chunks)
        
        # 4. 短ウィンドウの BM25 インデックス（60 秒 segment 単位）
        await self._build_bm25_index(video_ctx.segments)
        
        # 5. Manifest SHA256 を計算（再現性のアンカー）
        manifest_sha = self._compute_manifest_sha(enriched_chunks)
        await self.repo.update_rag_index(media_id, status="indexed", manifest_sha=manifest_sha)
        
        return IndexResult(chunk_count=len(enriched_chunks), manifest_sha=manifest_sha)
    
    def _create_long_chunks(self, ctx: VideoContext) -> list[LongChunk]:
        """5 分単位で分割し、各ブロックは複数の 60 秒 segment を含む"""
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
            # テキストを連結
            text = "\n".join(
                f"[{ms_to_ts(s.start_ms)}-{ms_to_ts(s.end_ms)}] {s.transcript}"
                for s in chunk.segments
            )
            
            # LLM で要約 + キーワードを生成（並行）
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
                    "content": chunk.content[:1000],  # payload には先頭 1000 文字を格納
                    "summary": chunk.summary,
                    "keywords": chunk.keywords
                }
            )
            for orm, chunk in zip(orm_chunks, chunks)
        ]
        await self.qdrant.upsert(collection_name="video_chunks", points=points)
    
    def _compute_manifest_sha(self, chunks: list[EnrichedChunk]) -> str:
        """順序付き JSON シリアライズ + SHA256。整合性検証に使用"""
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

## 3. エラー処理とリトライ戦略

| 段階 | 失敗分類 | リトライ戦略 | デッドレター処理 |
|------|----------|----------|----------|
| ダウンロード | ネットワーク／403／アンチスクレイピング | 指数バックオフで 3 回。プロキシ／Cookie を変更 | `download_failed` としてマークし、人手で介入 |
| トランスコード | FFmpeg エラー／ファイル破損 | リトライしない（多くの場合ソースファイル側の問題） | `transcode_failed` としてマーク |
| ASR | OOM／タイムアウト／モデルエラー | セグメント単位で 3 回リトライ。GPU を解放してから再試行 | chunk を `failed` としてマークし、人手で再確認 |
| OCR | 画像デコード失敗／モデルエラー | 単一フレームを 2 回リトライ | フレームを `failed` としてマーク。パイプラインはブロックしない |
| インデックス | Embedding 失敗／Qdrant 書き込み失敗 | ブロック単位で 3 回リトライ | `index_failed` としてマーク。増分再構築に対応 |

**重複処理を防ぐリース機構**（vid-lens `ProcessingLease` を移植）：
```python
@asynccontextmanager
async def processing_lease(repo, entity_id: str, entity_type: str, ttl: int = 1800):
    """分散リース：SET NX + Lua による延長 + 自動解放"""
    lease_key = f"lease:{entity_type}:{entity_id}"
    acquired = await redis.set(lease_key, worker_id, nx=True, ex=ttl)
    if not acquired:
        raise LeaseAcquisitionError(f"Entity {entity_id} being processed by another worker")
    
    # ハートビートタスク
    heartbeat_task = asyncio.create_task(_renew_lease(lease_key, ttl))
    
    try:
        yield
    finally:
        heartbeat_task.cancel()
        # 依然として保持者である場合のみ解放
        lua = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        end
        return 0
        """
        await redis.eval(lua, 1, lease_key, worker_id)
```

---

## 4. 監視メトリクス

| メトリクス名 | 種別 | ラベル | 用途 |
|--------|------|------|------|
| `vm_pipeline_stage_duration_seconds` | Histogram | stage, status | 各段階の所要時間分布 |
| `vm_pipeline_stage_total` | Counter | stage, status | 成功／失敗のカウント |
| `vm_asr_chunk_duration_seconds` | Histogram | media_id | 単一セグメントの ASR 所要時間 |
| `vm_asr_chunk_retries_total` | Counter | media_id | リトライ回数 |
| `vm_gpu_memory_allocated_bytes` | Gauge | phase, worker | VRAM 使用量の監視 |
| `vm_ffmpeg_duration_seconds` | Histogram | operation | FFmpeg 各操作の所要時間 |
| `vm_minio_upload_bytes_total` | Counter | bucket | オブジェクトストレージへの書き込み量 |

---

## 5. 主要な設定パラメータ

```yaml
# config/video_pipeline.yaml
pipeline:
  download:
    timeout_seconds: 600
    max_retries: 3
    proxy_pool_enabled: true
    douyin_cookie_refresh_hours: 24   # ⏳ 後回し実装（Phase 2+、初版では未対応）
  
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
    max_concurrent_chunks: 1  # 単一 GPU を独占
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

> **関連ドキュメント**：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md) · [DATA-MODEL_JP.md](DATA-MODEL_JP.md) · [RAG-RETRIEVAL_JP.md](RAG-RETRIEVAL_JP.md) · [TASK-ORCHESTRATION_JP.md](TASK-ORCHESTRATION_JP.md) · [OBSERVABILITY_JP.md](OBSERVABILITY_JP.md)
