"""异常分级 —— 防御性编程基础（可重试 / 不可重试 二分法）。

设计要点：
1. Celery 阶段任务原先 `autoretry_for=(Exception,)` 无差别重试 3 次，
   确定性失败（404 视频、私享视频、格式损坏、上下文契约违规）也被重试，
   纯浪费算力且拖慢"失败可见"时间。本模块给出显式二分：
   - RetryableError：暂时性故障（网络抖动、对端 5xx、磁盘暂满、模型下载失败），
     退避重试有意义。
   - NonRetryableError：确定性失败（资源不存在、参数/契约错误、格式损坏、
     依赖缺失），重试必然同样结果，应立即终止并落终态。
2. `BaseVideoTask.autoretry_for` 只收 RetryableError + 标准库暂时性异常
   （ConnectionError / TimeoutError / OSError）+ 第三方基础设施瞬态错误；
   各阶段把确定性失败显式翻译成 NonRetryableError。
3. 分类边界在"错误发生的现场"（download/ffmpeg/asr 各自最了解错误语义），
   不在任务层靠字符串猜测。
"""

from __future__ import annotations


class VideoMindError(Exception):
    """项目内业务异常基类（便于按命名空间统一捕获）。"""


class RetryableError(VideoMindError):
    """暂时性故障：重试（含退避）有意义。

    例：网络超时、对端 5xx、磁盘空间暂不足、模型权重下载中断。
    """


class NonRetryableError(VideoMindError):
    """确定性失败：重试必然同样结果，应立即终止并落终态。

    例：视频 404/私享/被删、URL 不受支持、音频格式损坏、
    上下文契约违规（download_result missing）、必需配置缺失。
    """


__all__ = ["VideoMindError", "RetryableError", "NonRetryableError"]
