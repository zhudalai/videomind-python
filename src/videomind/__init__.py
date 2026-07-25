"""VideoMind — 视频理解 Agent 系统（Python 版）。

4 层架构：
    Interface        — FastAPI / SSE                    (src/videomind/interface)
    Application      — Task Orchestration / Celery      (src/videomind/application)
    Core Services    — Video Pipeline / RAG / Agent Loop (src/videomind/core)
    Infrastructure   — Storage / Media / Vector / Cache (src/videomind/infrastructure)

设计文档见 docs/ 与 AGENTS.md。
"""

__version__ = "0.1.0"
