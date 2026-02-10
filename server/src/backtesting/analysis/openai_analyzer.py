"""
OpenAI 분석기

GPT-4를 활용하여 거래 패턴을 분석하고
인사이트와 전략 개선안을 도출합니다.
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from openai import AsyncOpenAI

from .trade_analyzer import PatternAnalysisResult, IndicatorComparison
from .indicator_distribution import IndicatorDistributionAnalyzer, OptimalRangeResult
from .optimal_sl_tp import SLTPAnalysisResult


@dataclass
class OpenAIInsight:
    """OpenAI 분석 인사이트"""

    summary: str
    key_findings: list[str]
    entry_conditions_suggestion: str
    exit_conditions_suggestion: str
    risk_management_suggestion: str
    strategy_improvement: str
    confidence_level: str  # low, medium, high


@dataclass
class OpenAIAnalysisResult:
    """OpenAI 분석 결과"""

    insight: OpenAIInsight
    raw_response: str
    model_used: str
    tokens_used: int


class OpenAIAnalyzer:
    """OpenAI 기반 분석기"""

    def __init__(self, api_key: Optional[str] = None):
        """
        초기화

        Args:
            api_key: OpenAI API 키 (없으면 환경변수에서 로드)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        if not self.api_key:
            raise ValueError(
                "OpenAI API 키가 필요합니다. "
                "OPENAI_API_KEY 환경변수를 설정하거나 api_key 파라미터를 전달하세요."
            )

        self.client = AsyncOpenAI(api_key=self.api_key)
        self.model = "gpt-4-turbo-preview"  # 최신 GPT-4 모델

    def _prepare_analysis_context(
        self,
        trade_analysis: Optional[PatternAnalysisResult] = None,
        indicator_ranges: Optional[dict[str, OptimalRangeResult]] = None,
        sl_tp_analysis: Optional[SLTPAnalysisResult] = None,
    ) -> str:
        """분석 컨텍스트 준비"""
        context_parts: list[str] = []

        if trade_analysis:
            context_parts.append(f"""
## 거래 패턴 분석 결과

- 총 거래 수: {trade_analysis.total_trades}
- 승리 거래: {trade_analysis.winning_trades}
- 패배 거래: {trade_analysis.losing_trades}
- 전체 승률: {trade_analysis.overall_win_rate:.1%}
- 평균 승리 수익: {trade_analysis.avg_win_pnl_pct:.2f}%
- 평균 패배 손실: {trade_analysis.avg_loss_pnl_pct:.2f}%

### 통계적으로 유의미한 지표 (승/패 간 차이가 있는 지표)
""")
            significant_indicators = [
                ic for ic in trade_analysis.indicator_comparisons if ic.is_significant
            ]

            for ic in significant_indicators[:10]:
                context_parts.append(
                    f"- {ic.indicator_name}: "
                    f"승리 평균 {ic.win_mean:.4f}, 패배 평균 {ic.loss_mean:.4f} "
                    f"(p-value: {ic.p_value:.4f}, 승리에 유리한 방향: {ic.win_favorable_direction})"
                )

            context_parts.append("\n### 전략별 성과")
            for strategy, data in trade_analysis.strategies_breakdown.items():
                context_parts.append(
                    f"- {strategy}: 승률 {data['win_rate']:.1%}, "
                    f"평균 수익 {data['avg_pnl_pct']:.2f}%, "
                    f"거래 수 {data['total_trades']}"
                )

        if indicator_ranges:
            context_parts.append("\n## 최적 지표 범위")
            for name, r in list(indicator_ranges.items())[:10]:
                context_parts.append(
                    f"- {name}: {r.min_value:.4f} ~ {r.max_value:.4f} "
                    f"(승률 {r.win_rate:.1%}, 개선 +{r.improvement_vs_baseline:.1%})"
                )

        if sl_tp_analysis:
            stats = sl_tp_analysis.price_movement_stats
            best = sl_tp_analysis.best_sl_tp

            context_parts.append(f"""
## 가격 움직임 및 SL/TP 분석

### 가격 움직임 통계
- 평균 최대 수익: {stats.avg_max_profit_pct:.2f}%
- 평균 최대 하락: {stats.avg_max_drawdown_pct:.2f}%
- 중앙값 최대 수익: {stats.median_max_profit_pct:.2f}%
- 중앙값 최대 하락: {stats.median_max_drawdown_pct:.2f}%

### 최적 SL/TP
- 손절(SL): {best.optimal_sl_pct:.1f}%
- 익절(TP): {best.optimal_tp_pct:.1f}%
- 예상 승률: {best.expected_win_rate:.1%}
- 예상 평균 수익: {best.expected_avg_pnl_pct:.2f}%
- 리스크/보상 비율: {best.risk_reward_ratio:.2f}
""")

            if sl_tp_analysis.best_trailing_stop:
                ts = sl_tp_analysis.best_trailing_stop
                context_parts.append(f"""
### 트레일링 스탑 분석
- 트레일링 %: {ts.trailing_pct:.1f}%
- 활성화 수준: {ts.activation_pct:.1f}%
- 고정 SL/TP 대비 개선: {ts.improvement_vs_fixed:+.2f}%
""")

        return "\n".join(context_parts)

    async def analyze_trade_patterns(
        self,
        trade_analysis: PatternAnalysisResult,
        indicator_ranges: Optional[dict[str, OptimalRangeResult]] = None,
        sl_tp_analysis: Optional[SLTPAnalysisResult] = None,
    ) -> OpenAIAnalysisResult:
        """
        GPT-4로 거래 패턴 분석

        Args:
            trade_analysis: 거래 패턴 분석 결과
            indicator_ranges: 최적 지표 범위
            sl_tp_analysis: SL/TP 분석 결과

        Returns:
            OpenAI 분석 결과
        """
        context = self._prepare_analysis_context(
            trade_analysis=trade_analysis,
            indicator_ranges=indicator_ranges,
            sl_tp_analysis=sl_tp_analysis,
        )

        system_prompt = """당신은 암호화폐 트레이딩 전문 퀀트 애널리스트입니다.
제공된 백테스트 데이터를 분석하여 실행 가능한 트레이딩 전략 개선안을 제시하세요.

응답은 다음 JSON 형식으로 제공하세요:
{
    "summary": "분석 요약 (2-3문장)",
    "key_findings": ["핵심 발견 1", "핵심 발견 2", ...],
    "entry_conditions_suggestion": "진입 조건 개선 제안",
    "exit_conditions_suggestion": "청산 조건 개선 제안",
    "risk_management_suggestion": "리스크 관리 제안",
    "strategy_improvement": "전략 개선 방향",
    "confidence_level": "분석 신뢰도 (low/medium/high)"
}

분석 시 다음을 고려하세요:
1. 통계적으로 유의미한 지표에 집중
2. 현실적으로 구현 가능한 조건만 제안
3. 과적합 위험 경고
4. 시장 상황 변화에 대한 고려
"""

        user_prompt = f"""다음 백테스트 분석 결과를 검토하고 전략 개선안을 제시해주세요.

{context}

위 데이터를 기반으로:
1. 현재 전략의 문제점
2. 개선 가능한 진입/청산 조건
3. 리스크 관리 방안
4. 구체적인 전략 개선 제안

을 분석해주세요."""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,  # 일관성 있는 분석을 위해 낮은 temperature
            max_tokens=2000,
        )

        raw_response = response.choices[0].message.content
        tokens_used = response.usage.total_tokens if response.usage else 0

        # JSON 파싱
        try:
            parsed = json.loads(raw_response)
            insight = OpenAIInsight(
                summary=parsed.get("summary", ""),
                key_findings=parsed.get("key_findings", []),
                entry_conditions_suggestion=parsed.get("entry_conditions_suggestion", ""),
                exit_conditions_suggestion=parsed.get("exit_conditions_suggestion", ""),
                risk_management_suggestion=parsed.get("risk_management_suggestion", ""),
                strategy_improvement=parsed.get("strategy_improvement", ""),
                confidence_level=parsed.get("confidence_level", "medium"),
            )
        except json.JSONDecodeError:
            insight = OpenAIInsight(
                summary=raw_response[:500],
                key_findings=[],
                entry_conditions_suggestion="",
                exit_conditions_suggestion="",
                risk_management_suggestion="",
                strategy_improvement="",
                confidence_level="low",
            )

        return OpenAIAnalysisResult(
            insight=insight,
            raw_response=raw_response,
            model_used=self.model,
            tokens_used=tokens_used,
        )

    async def suggest_entry_conditions(
        self,
        indicator_comparisons: list[IndicatorComparison],
        optimal_ranges: dict[str, OptimalRangeResult],
    ) -> dict[str, Any]:
        """
        GPT-4로 최적 진입 조건 제안

        Args:
            indicator_comparisons: 지표 비교 결과
            optimal_ranges: 최적 범위

        Returns:
            진입 조건 제안
        """
        context_parts: list[str] = []

        context_parts.append("## 승/패 거래간 유의미한 차이가 있는 지표들:")
        for ic in indicator_comparisons[:10]:
            if ic.is_significant:
                context_parts.append(
                    f"- {ic.indicator_name}: 승리 {ic.win_mean:.4f} vs 패배 {ic.loss_mean:.4f}"
                )

        context_parts.append("\n## 발견된 최적 범위:")
        for name, r in list(optimal_ranges.items())[:10]:
            context_parts.append(
                f"- {name}: {r.min_value:.4f} ~ {r.max_value:.4f} "
                f"(승률 {r.win_rate:.1%})"
            )

        system_prompt = """당신은 암호화폐 트레이딩 전략 개발자입니다.
제공된 지표 분석 결과를 바탕으로 Python으로 구현 가능한 진입 조건을 제안하세요.

응답은 다음 JSON 형식으로:
{
    "conditions": [
        {"indicator": "지표명", "operator": ">=", "value": 숫자, "rationale": "이유"},
        ...
    ],
    "combined_win_rate_estimate": 0.55,
    "implementation_notes": "구현 시 주의사항"
}
"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "\n".join(context_parts)},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=1000,
        )

        raw_response = response.choices[0].message.content

        try:
            return json.loads(raw_response)
        except json.JSONDecodeError:
            return {"error": "파싱 실패", "raw": raw_response}

    async def summarize_insights(
        self,
        trade_analysis: PatternAnalysisResult,
        sl_tp_analysis: SLTPAnalysisResult,
    ) -> str:
        """
        분석 인사이트 요약 (간단 버전)

        Returns:
            한국어 요약문
        """
        context = f"""
- 총 거래: {trade_analysis.total_trades}건
- 현재 승률: {trade_analysis.overall_win_rate:.1%}
- 평균 수익 (승): {trade_analysis.avg_win_pnl_pct:.2f}%
- 평균 손실 (패): {trade_analysis.avg_loss_pnl_pct:.2f}%
- 최적 SL: {sl_tp_analysis.best_sl_tp.optimal_sl_pct:.1f}%
- 최적 TP: {sl_tp_analysis.best_sl_tp.optimal_tp_pct:.1f}%
- 예상 개선 승률: {sl_tp_analysis.best_sl_tp.expected_win_rate:.1%}
"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "백테스트 분석 결과를 한국어로 3줄 이내로 요약하세요.",
                },
                {"role": "user", "content": context},
            ],
            temperature=0.5,
            max_tokens=300,
        )

        return response.choices[0].message.content

    def to_report_dict(
        self,
        result: OpenAIAnalysisResult,
    ) -> dict[str, Any]:
        """결과를 보고서용 딕셔너리로 변환"""
        return {
            "model_used": result.model_used,
            "tokens_used": result.tokens_used,
            "insight": {
                "summary": result.insight.summary,
                "key_findings": result.insight.key_findings,
                "entry_conditions_suggestion": result.insight.entry_conditions_suggestion,
                "exit_conditions_suggestion": result.insight.exit_conditions_suggestion,
                "risk_management_suggestion": result.insight.risk_management_suggestion,
                "strategy_improvement": result.insight.strategy_improvement,
                "confidence_level": result.insight.confidence_level,
            },
        }
