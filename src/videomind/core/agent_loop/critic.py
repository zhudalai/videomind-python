"""Critic 质量评审器 + EvidenceVerifier 证据校验。

评审流程：
    1. 调用 LLM 进行质量审查（结构、覆盖度、幻觉风险）
    2. 调用 EvidenceVerifier 进行硬校验（时间戳范围 + 内容非空）
    3. 综合 LLM 结果和硬校验结果输出 CriticResult
"""

from __future__ import annotations

import json

import structlog

from videomind.core.agent_loop.types import AgentState, CriticResult
from videomind.core.agent_loop.verifier import EvidenceVerifier

logger = structlog.get_logger(__name__)

CRITIC_PROMPT = """你是一个分析结果质量评审器。请对以下分析结果进行评审。

## 输入
- 用户目标: {goal}
- 标题: {title}
- 结论数: {conclusion_count}
- 证据数: {evidence_count}
- 建议数: {suggestion_count}
- 结论内容: {conclusions}
- 建议内容: {suggestions}

## 评审要求
请输出严格的 JSON（不要 markdown 包装），包含以下字段：
- passed: bool, 是否通过（覆盖度 >= 0.6 且结构合理且无严重幻觉）
- feedback: str, 评审反馈（中文）
- required_timestamps: list[int], 需要补充证据的时间戳（不通过时才提供）
- coverage_score: float (0.0-1.0), 证据覆盖度评分
- structure_ok: bool, 结论结构是否合理
- evidence_verified: bool, 证据是否经过校验
- hallucination_risk: float (0.0-1.0), 幻觉风险评分
"""


class Critic:
    """质量评审器。

    组合 LLM 质量审查和 EvidenceVerifier 硬校验，
    输出综合的 CriticResult。
    """

    def __init__(self, llm, verifier: EvidenceVerifier):
        self._llm = llm
        self._verifier = verifier

    async def critique(self, state: AgentState) -> CriticResult:
        """对分析结果进行评审。

        Args:
            state: 当前代理状态，必须包含 result

        Returns:
            CriticResult: 评审结果
        """
        result = state.result
        if result is None:
            return CriticResult(
                passed=False,
                feedback="无分析结果可供评审",
                hallucination_risk=1.0,
            )

        # 步骤 1: LLM 质量审查
        try:
            # 提取结论摘要
            conclusion_summaries = [
                c.point for c in result.conclusions
            ]
            suggestion_summaries = result.suggestions

            prompt = CRITIC_PROMPT.format(
                goal=state.goal,
                title=result.title,
                conclusion_count=len(result.conclusions),
                evidence_count=len(result.evidence),
                suggestion_count=len(suggestion_summaries),
                conclusions=json.dumps(conclusion_summaries, ensure_ascii=False),
                suggestions=json.dumps(suggestion_summaries, ensure_ascii=False),
            )

            resp = await self._llm.chat(prompt)
            data = json.loads(resp.content)
        except Exception:
            logger.warning("Critic LLM 调用失败", exc_info=True)
            return CriticResult(
                passed=False,
                feedback="LLM 评审调用失败",
                hallucination_risk=1.0,
            )

        # 步骤 2: 硬性证据校验
        verified_passed, _ = self._verifier.verify_all(
            result.evidence, duration_ms=0
        )

        # 步骤 3: 综合结果
        llm_passed = data.get("passed", False)
        return CriticResult(
            passed=llm_passed and verified_passed,
            feedback=data.get("feedback", ""),
            required_timestamps=data.get("required_timestamps", []),
            coverage_score=data.get("coverage_score", 0.0),
            structure_ok=data.get("structure_ok", False),
            evidence_verified=verified_passed,
            hallucination_risk=data.get("hallucination_risk", 0.5),
        )