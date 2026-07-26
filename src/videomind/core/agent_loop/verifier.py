"""EvidenceVerifier —— 证据硬校验器。

提供纯函数式硬校验：时间戳范围检查 + 内容非空检查。
不依赖 DB/ASR/OCR，只做代码层面的数据校验。

校验流程：
    verify_all(evidence, duration_ms) -> (passed: bool, failed: list[Evidence])
    逐条调用 _verify_one 检查每条证据。
"""

from __future__ import annotations

from videomind.core.agent_loop.types import Evidence


class EvidenceVerifier:
    """证据硬校验器。

    对证据列表进行逐条校验，检查：
    1. timestamp_ms 是否在 [0, duration_ms] 范围内
    2. content 是否非空
    """

    def verify_all(
        self,
        evidence: list[Evidence],
        duration_ms: int = 0,
    ) -> tuple[bool, list[Evidence]]:
        """逐条校验所有证据。

        Args:
            evidence: 待校验的证据列表
            duration_ms: 视频总时长（毫秒），用于范围检查

        Returns:
            (passed, failed): passed 为 True 表示全部通过；
            failed 为未通过校验的证据列表
        """
        failures: list[Evidence] = []
        for ev in evidence:
            if not self._verify_one(ev, duration_ms):
                failures.append(ev)
        return (len(failures) == 0, failures)

    @staticmethod
    def _verify_one(ev: Evidence, duration_ms: int) -> bool:
        """单条证据校验。

        Args:
            ev: 单条证据
            duration_ms: 视频总时长（毫秒）

        Returns:
            True 表示通过校验
        """
        if not (0 <= ev.timestamp_ms <= duration_ms):
            return False
        if len(ev.content) == 0:
            return False
        return True