"""
패턴 추출기

이전 분석 결과들을 종합하여
수익 패턴을 추출하고 전략 코드 템플릿을 생성합니다.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from .trade_analyzer import TradePatternAnalyzer, PatternAnalysisResult
from .indicator_distribution import IndicatorDistributionAnalyzer, OptimalRangeResult
from .optimal_sl_tp import SLTPAnalyzer, SLTPAnalysisResult


@dataclass
class IndicatorCondition:
    """지표 조건"""

    indicator_name: str
    operator: str  # ">=", "<=", "between", "=="
    value: float
    upper_value: Optional[float] = None  # between 연산자용


@dataclass
class ProfitablePattern:
    """수익 패턴"""

    pattern_id: str
    name: str
    description: str
    conditions: list[IndicatorCondition]
    optimal_sl_pct: float
    optimal_tp_pct: float
    expected_win_rate: float
    expected_avg_pnl_pct: float
    trade_count: int
    confidence_score: float  # 0-100


@dataclass
class StrategyTemplate:
    """전략 템플릿"""

    strategy_name: str
    patterns: list[ProfitablePattern]
    entry_conditions_code: str
    exit_conditions_code: str
    full_strategy_code: str


@dataclass
class ExtractionResult:
    """패턴 추출 결과"""

    patterns: list[ProfitablePattern]
    strategy_templates: list[StrategyTemplate]
    summary: dict[str, Any]


class PatternExtractor:
    """패턴 추출기"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.trade_analysis: Optional[PatternAnalysisResult] = None
        self.indicator_analysis: Optional[dict[str, OptimalRangeResult]] = None
        self.sl_tp_analysis: Optional[SLTPAnalysisResult] = None

    async def extract_patterns(
        self,
        run_ids: Optional[list[str]] = None,
        strategies: Optional[list[str]] = None,
        min_confidence: float = 50.0,
    ) -> list[ProfitablePattern]:
        """
        수익 패턴 추출

        Args:
            run_ids: 백테스트 실행 ID
            strategies: 분석할 전략 리스트
            min_confidence: 최소 신뢰도

        Returns:
            수익 패턴 리스트
        """
        # 1. 거래 패턴 분석
        trade_analyzer = TradePatternAnalyzer(self.session)
        self.trade_analysis = await trade_analyzer.run_full_analysis(
            run_ids=run_ids, strategies=strategies
        )

        if self.trade_analysis.total_trades < 30:
            return []

        # 2. 지표 분포 분석
        indicator_analyzer = IndicatorDistributionAnalyzer(trade_analyzer.trades)
        self.indicator_analysis = indicator_analyzer.find_optimal_ranges()

        # 3. SL/TP 분석
        sl_tp_analyzer = SLTPAnalyzer(trade_analyzer.trades)
        self.sl_tp_analysis = sl_tp_analyzer.run_full_analysis()

        # 4. 패턴 생성
        patterns = self._generate_patterns(min_confidence)

        return patterns

    def _generate_patterns(
        self,
        min_confidence: float,
    ) -> list[ProfitablePattern]:
        """패턴 생성"""
        patterns: list[ProfitablePattern] = []

        if not self.indicator_analysis or not self.sl_tp_analysis:
            return patterns

        best_sl = self.sl_tp_analysis.best_sl_tp.optimal_sl_pct
        best_tp = self.sl_tp_analysis.best_sl_tp.optimal_tp_pct

        pattern_id = 0

        # 단일 지표 패턴
        for indicator_name, optimal_range in self.indicator_analysis.items():
            if optimal_range.win_rate < 0.5:
                continue

            # 신뢰도 계산: 승률 × 거래수 가중치
            confidence = self._calculate_confidence(
                optimal_range.win_rate,
                optimal_range.trade_count,
            )

            if confidence < min_confidence:
                continue

            pattern_id += 1
            condition = IndicatorCondition(
                indicator_name=indicator_name,
                operator="between",
                value=optimal_range.min_value,
                upper_value=optimal_range.max_value,
            )

            patterns.append(
                ProfitablePattern(
                    pattern_id=f"P{pattern_id:03d}",
                    name=f"{indicator_name}_range_filter",
                    description=(
                        f"{indicator_name}: "
                        f"{optimal_range.min_value:.4f} ~ {optimal_range.max_value:.4f}"
                    ),
                    conditions=[condition],
                    optimal_sl_pct=best_sl,
                    optimal_tp_pct=best_tp,
                    expected_win_rate=optimal_range.win_rate,
                    expected_avg_pnl_pct=optimal_range.avg_pnl_pct,
                    trade_count=optimal_range.trade_count,
                    confidence_score=confidence,
                )
            )

        # 수익 조건 기반 패턴 (trade_analysis에서)
        if self.trade_analysis:
            for condition in self.trade_analysis.profitable_conditions[:10]:
                if condition.win_rate < 0.5:
                    continue

                confidence = self._calculate_confidence(
                    condition.win_rate,
                    condition.trade_count,
                )

                if confidence < min_confidence:
                    continue

                pattern_id += 1

                # 조건 파싱
                parsed_conditions = self._parse_condition_name(condition.condition_name)

                patterns.append(
                    ProfitablePattern(
                        pattern_id=f"P{pattern_id:03d}",
                        name=condition.condition_name,
                        description=condition.description,
                        conditions=parsed_conditions,
                        optimal_sl_pct=best_sl,
                        optimal_tp_pct=best_tp,
                        expected_win_rate=condition.win_rate,
                        expected_avg_pnl_pct=condition.avg_pnl_pct,
                        trade_count=condition.trade_count,
                        confidence_score=confidence,
                    )
                )

        # 신뢰도 순 정렬
        patterns.sort(key=lambda x: x.confidence_score, reverse=True)

        return patterns

    def _calculate_confidence(
        self,
        win_rate: float,
        trade_count: int,
    ) -> float:
        """
        신뢰도 계산

        - 승률: 50% 이상이면 가산점
        - 거래 수: 많을수록 신뢰도 상승 (로그 스케일)
        """
        # 승률 점수 (0-50점)
        win_rate_score = min((win_rate - 0.5) * 100, 50) if win_rate > 0.5 else 0

        # 거래 수 점수 (0-50점)
        # 20거래=10점, 50거래=20점, 100거래=30점, 200거래=40점
        trade_score = min(np.log10(max(trade_count, 1)) * 20, 50)

        return min(win_rate_score + trade_score, 100)

    def _parse_condition_name(
        self,
        condition_name: str,
    ) -> list[IndicatorCondition]:
        """조건명 파싱"""
        conditions: list[IndicatorCondition] = []

        # 예: "rvol_high_75" → rvol >= 75th percentile
        # 예: "holding_lte_60m" → holding <= 60분

        if "_high_" in condition_name:
            parts = condition_name.rsplit("_high_", 1)
            indicator = parts[0]
            conditions.append(
                IndicatorCondition(
                    indicator_name=indicator,
                    operator=">=",
                    value=0,  # 실제 값은 description에서
                )
            )
        elif "_low_" in condition_name:
            parts = condition_name.rsplit("_low_", 1)
            indicator = parts[0]
            conditions.append(
                IndicatorCondition(
                    indicator_name=indicator,
                    operator="<=",
                    value=0,
                )
            )
        elif "_lte_" in condition_name:
            parts = condition_name.split("_lte_")
            if len(parts) == 2:
                indicator = parts[0]
                value_str = parts[1].replace("m", "")  # "60m" → "60"
                try:
                    value = float(value_str)
                    conditions.append(
                        IndicatorCondition(
                            indicator_name=indicator,
                            operator="<=",
                            value=value,
                        )
                    )
                except ValueError:
                    pass
        elif "_gt_" in condition_name:
            parts = condition_name.split("_gt_")
            if len(parts) == 2:
                indicator = parts[0]
                value_str = parts[1].replace("m", "")
                try:
                    value = float(value_str)
                    conditions.append(
                        IndicatorCondition(
                            indicator_name=indicator,
                            operator=">",
                            value=value,
                        )
                    )
                except ValueError:
                    pass
        elif "_gte_" in condition_name:
            parts = condition_name.split("_gte_")
            if len(parts) == 2:
                indicator = parts[0]
                value_str = parts[1]
                try:
                    value = float(value_str)
                    conditions.append(
                        IndicatorCondition(
                            indicator_name=indicator,
                            operator=">=",
                            value=value,
                        )
                    )
                except ValueError:
                    pass

        return conditions

    def generate_strategy_code(
        self,
        patterns: list[ProfitablePattern],
        strategy_name: str = "DataDrivenStrategy",
    ) -> str:
        """
        전략 코드 템플릿 생성

        Args:
            patterns: 적용할 패턴 리스트
            strategy_name: 전략 이름

        Returns:
            Python 코드 문자열
        """
        if not patterns:
            return ""

        # 최상위 패턴 선택 (상위 3개)
        top_patterns = patterns[:3]

        # 조건 코드 생성
        condition_lines: list[str] = []
        for pattern in top_patterns:
            for cond in pattern.conditions:
                if cond.operator == "between" and cond.upper_value is not None:
                    line = (
                        f"        {cond.value:.4f} <= indicators.get('{cond.indicator_name}', 0) "
                        f"<= {cond.upper_value:.4f}"
                    )
                elif cond.operator == ">=":
                    line = f"        indicators.get('{cond.indicator_name}', 0) >= {cond.value:.4f}"
                elif cond.operator == "<=":
                    line = f"        indicators.get('{cond.indicator_name}', 0) <= {cond.value:.4f}"
                elif cond.operator == ">":
                    line = f"        indicators.get('{cond.indicator_name}', 0) > {cond.value:.4f}"
                else:
                    continue
                condition_lines.append(line)

        # 최적 SL/TP
        sl_pct = top_patterns[0].optimal_sl_pct if top_patterns else -2.0
        tp_pct = top_patterns[0].optimal_tp_pct if top_patterns else 2.0

        conditions_code = " and\n".join(condition_lines) if condition_lines else "True"

        code = f'''"""
{strategy_name}

데이터 분석 기반 자동 생성된 전략
- 기대 승률: {top_patterns[0].expected_win_rate:.1%} (상위 패턴 기준)
- 최적 SL: {sl_pct:.1f}%, TP: {tp_pct:.1f}%

적용된 패턴:
{chr(10).join(f"- {p.name}: {p.description}" for p in top_patterns)}
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class {strategy_name}Config:
    """전략 설정"""

    sl_pct: float = {sl_pct}
    tp_pct: float = {tp_pct}
    position_pct: float = 0.1
    cooldown_minutes: int = 30


class {strategy_name}SignalGenerator:
    """데이터 기반 신호 생성기"""

    def __init__(self, config: Optional[{strategy_name}Config] = None):
        self.config = config or {strategy_name}Config()
        self.last_signal_time: dict[str, float] = {{}}

    def __call__(
        self,
        symbol: str,
        history_provider,
        current_time: float,
    ) -> Optional[dict]:
        """
        진입 신호 생성

        Args:
            symbol: 심볼
            history_provider: HistoryProvider 인스턴스
            current_time: 현재 시간 (timestamp)

        Returns:
            신호 딕셔너리 또는 None
        """
        # 쿨다운 체크
        last_time = self.last_signal_time.get(symbol, 0)
        if current_time - last_time < self.config.cooldown_minutes * 60:
            return None

        # 지표 계산
        indicators = self._calculate_indicators(symbol, history_provider)

        # 진입 조건 체크
        if self._check_entry_conditions(indicators):
            self.last_signal_time[symbol] = current_time

            return {{
                "symbol": symbol,
                "action": "buy",
                "strategy": "{strategy_name}",
                "score": {top_patterns[0].confidence_score:.1f},
                "position_pct": self.config.position_pct,
                "indicators": indicators,
                "sl_pct": self.config.sl_pct,
                "tp_pct": self.config.tp_pct,
            }}

        return None

    def _calculate_indicators(
        self,
        symbol: str,
        history_provider,
    ) -> dict:
        """지표 계산"""
        closes = history_provider.get_closes(symbol)
        if len(closes) < 50:
            return {{}}

        price = closes[-1]
        ema20 = history_provider.calc_ema(symbol, 20)
        ema50 = history_provider.calc_ema(symbol, 50)
        rvol = history_provider.calc_rvol(symbol)

        return {{
            "price": price,
            "ema20": ema20,
            "ema50": ema50,
            "rvol": rvol,
            "ema20_distance_pct": (price - ema20) / ema20 * 100 if ema20 else 0,
        }}

    def _check_entry_conditions(self, indicators: dict) -> bool:
        """
        진입 조건 체크

        데이터 분석으로 발견된 수익 조건들:
        """
        if not indicators:
            return False

        # 자동 생성된 조건
        return (
{conditions_code}
        )


def create_exit_checker(config: {strategy_name}Config):
    """청산 조건 체커 생성"""

    def check_exit(
        position,
        current_price: float,
        current_time: float,
    ) -> Optional[str]:
        """
        청산 조건 체크

        Returns:
            청산 사유 또는 None
        """
        entry_price = position.entry_price
        pnl_pct = (current_price - entry_price) / entry_price * 100

        # 손절
        if pnl_pct <= config.sl_pct:
            return "stop_loss"

        # 익절
        if pnl_pct >= config.tp_pct:
            return "take_profit"

        return None

    return check_exit
'''

        return code

    async def run_full_extraction(
        self,
        run_ids: Optional[list[str]] = None,
        strategies: Optional[list[str]] = None,
        min_confidence: float = 50.0,
    ) -> ExtractionResult:
        """
        전체 패턴 추출 실행

        Args:
            run_ids: 백테스트 실행 ID
            strategies: 분석할 전략
            min_confidence: 최소 신뢰도

        Returns:
            추출 결과
        """
        patterns = await self.extract_patterns(
            run_ids=run_ids,
            strategies=strategies,
            min_confidence=min_confidence,
        )

        # 전략 템플릿 생성
        templates: list[StrategyTemplate] = []

        if patterns:
            code = self.generate_strategy_code(patterns, "DataDrivenStrategyV28")

            templates.append(
                StrategyTemplate(
                    strategy_name="DataDrivenStrategyV28",
                    patterns=patterns[:3],
                    entry_conditions_code=code,
                    exit_conditions_code="",
                    full_strategy_code=code,
                )
            )

        summary = self._create_summary(patterns)

        return ExtractionResult(
            patterns=patterns,
            strategy_templates=templates,
            summary=summary,
        )

    def _create_summary(
        self,
        patterns: list[ProfitablePattern],
    ) -> dict[str, Any]:
        """요약 생성"""
        if not patterns:
            return {"total_patterns": 0, "message": "패턴 없음"}

        top_patterns = patterns[:5]

        return {
            "total_patterns": len(patterns),
            "top_patterns": [
                {
                    "id": p.pattern_id,
                    "name": p.name,
                    "win_rate": f"{p.expected_win_rate:.1%}",
                    "avg_pnl": f"{p.expected_avg_pnl_pct:.2f}%",
                    "confidence": f"{p.confidence_score:.1f}",
                    "trades": p.trade_count,
                }
                for p in top_patterns
            ],
            "best_sl_pct": patterns[0].optimal_sl_pct if patterns else -2.0,
            "best_tp_pct": patterns[0].optimal_tp_pct if patterns else 2.0,
            "trade_analysis": {
                "total_trades": self.trade_analysis.total_trades if self.trade_analysis else 0,
                "overall_win_rate": (
                    f"{self.trade_analysis.overall_win_rate:.1%}"
                    if self.trade_analysis
                    else "N/A"
                ),
            },
        }

    def to_report_dict(
        self,
        result: ExtractionResult,
    ) -> dict[str, Any]:
        """결과를 보고서용 딕셔너리로 변환"""
        return {
            "summary": result.summary,
            "patterns": [
                {
                    "id": p.pattern_id,
                    "name": p.name,
                    "description": p.description,
                    "conditions": [
                        {
                            "indicator": c.indicator_name,
                            "operator": c.operator,
                            "value": c.value,
                            "upper_value": c.upper_value,
                        }
                        for c in p.conditions
                    ],
                    "sl_pct": f"{p.optimal_sl_pct:.1f}%",
                    "tp_pct": f"{p.optimal_tp_pct:.1f}%",
                    "win_rate": f"{p.expected_win_rate:.1%}",
                    "avg_pnl": f"{p.expected_avg_pnl_pct:.2f}%",
                    "trades": p.trade_count,
                    "confidence": f"{p.confidence_score:.1f}",
                }
                for p in result.patterns[:20]
            ],
            "strategy_template_available": len(result.strategy_templates) > 0,
        }
