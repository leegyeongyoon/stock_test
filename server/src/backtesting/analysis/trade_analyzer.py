"""
거래 패턴 분석기

승리/패배 거래의 특성을 비교 분석하여
수익을 만드는 조건을 발견합니다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import numpy as np
from scipy import stats
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.backtesting.models.database import BacktestTradeModel


@dataclass
class TradePattern:
    """단일 거래 패턴 정보"""

    trade_id: str
    symbol: str
    strategy: str
    entry_time: datetime
    exit_time: Optional[datetime]
    pnl_pct: float
    max_profit_pct: float
    max_drawdown_pct: float
    holding_minutes: Optional[int]
    exit_reason: Optional[str]
    indicators: dict[str, float] = field(default_factory=dict)

    @property
    def is_win(self) -> bool:
        """승리 거래 여부"""
        return self.pnl_pct > 0


@dataclass
class IndicatorComparison:
    """지표별 승/패 비교 결과"""

    indicator_name: str
    win_mean: float
    win_std: float
    win_median: float
    loss_mean: float
    loss_std: float
    loss_median: float
    t_statistic: float
    p_value: float
    is_significant: bool  # p < 0.05
    win_favorable_direction: str  # "higher" or "lower"


@dataclass
class ProfitableCondition:
    """수익 조건"""

    condition_name: str
    win_rate: float
    avg_pnl_pct: float
    trade_count: int
    description: str


@dataclass
class PatternAnalysisResult:
    """패턴 분석 결과"""

    total_trades: int
    winning_trades: int
    losing_trades: int
    overall_win_rate: float
    avg_win_pnl_pct: float
    avg_loss_pnl_pct: float
    indicator_comparisons: list[IndicatorComparison]
    profitable_conditions: list[ProfitableCondition]
    strategies_breakdown: dict[str, dict]


class TradePatternAnalyzer:
    """승/패 거래 패턴 분석기"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.trades: list[TradePattern] = []

    async def load_trades(
        self,
        run_ids: Optional[list[str]] = None,
        strategies: Optional[list[str]] = None,
        min_trades: int = 30,
    ) -> list[TradePattern]:
        """
        DB에서 거래 데이터 로드

        Args:
            run_ids: 특정 백테스트 실행 ID (None이면 모든 거래)
            strategies: 분석할 전략 리스트
            min_trades: 최소 거래 수
        """
        query = select(BacktestTradeModel).where(
            BacktestTradeModel.exit_time.isnot(None)  # 청산된 거래만
        )

        if run_ids:
            query = query.where(BacktestTradeModel.run_id.in_(run_ids))

        if strategies:
            query = query.where(BacktestTradeModel.strategy.in_(strategies))

        result = await self.session.execute(query)
        db_trades = result.scalars().all()

        self.trades = []
        for trade in db_trades:
            indicators = trade.entry_indicators_json or {}
            pattern = TradePattern(
                trade_id=trade.trade_id,
                symbol=trade.symbol,
                strategy=trade.strategy,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                pnl_pct=trade.pnl_pct,
                max_profit_pct=trade.max_profit_pct,
                max_drawdown_pct=trade.max_drawdown_pct,
                holding_minutes=trade.holding_minutes,
                exit_reason=trade.exit_reason,
                indicators=indicators,
            )
            self.trades.append(pattern)

        return self.trades

    def compare_win_vs_loss(self) -> list[IndicatorComparison]:
        """
        승리/패배 거래의 지표 값 비교

        Returns:
            각 지표별 통계적 비교 결과
        """
        if not self.trades:
            return []

        win_trades = [t for t in self.trades if t.is_win]
        loss_trades = [t for t in self.trades if not t.is_win]

        if len(win_trades) < 5 or len(loss_trades) < 5:
            return []

        # 모든 지표 수집
        all_indicators: set[str] = set()
        for trade in self.trades:
            all_indicators.update(trade.indicators.keys())

        comparisons: list[IndicatorComparison] = []

        for indicator in sorted(all_indicators):
            win_values = [
                t.indicators.get(indicator)
                for t in win_trades
                if t.indicators.get(indicator) is not None
            ]
            loss_values = [
                t.indicators.get(indicator)
                for t in loss_trades
                if t.indicators.get(indicator) is not None
            ]

            # 숫자만 필터링
            win_values = [v for v in win_values if isinstance(v, (int, float))]
            loss_values = [v for v in loss_values if isinstance(v, (int, float))]

            if len(win_values) < 5 or len(loss_values) < 5:
                continue

            win_arr = np.array(win_values)
            loss_arr = np.array(loss_values)

            # t-검정
            t_stat, p_value = stats.ttest_ind(win_arr, loss_arr)

            win_mean = float(np.mean(win_arr))
            loss_mean = float(np.mean(loss_arr))

            comparison = IndicatorComparison(
                indicator_name=indicator,
                win_mean=win_mean,
                win_std=float(np.std(win_arr)),
                win_median=float(np.median(win_arr)),
                loss_mean=loss_mean,
                loss_std=float(np.std(loss_arr)),
                loss_median=float(np.median(loss_arr)),
                t_statistic=float(t_stat),
                p_value=float(p_value),
                is_significant=p_value < 0.05,
                win_favorable_direction="higher" if win_mean > loss_mean else "lower",
            )
            comparisons.append(comparison)

        # p-value 기준 정렬 (유의미한 지표 우선)
        return sorted(comparisons, key=lambda x: x.p_value)

    def find_profitable_conditions(self) -> list[ProfitableCondition]:
        """
        수익을 만드는 조건 조합 탐색

        Returns:
            수익 조건 리스트 (승률/수익률 순 정렬)
        """
        if not self.trades:
            return []

        conditions: list[ProfitableCondition] = []

        # 1. 지표별 범위 조건 탐색
        indicator_conditions = self._find_indicator_range_conditions()
        conditions.extend(indicator_conditions)

        # 2. 보유 시간 기반 조건
        holding_conditions = self._find_holding_time_conditions()
        conditions.extend(holding_conditions)

        # 3. max_profit/max_drawdown 비율 조건
        risk_reward_conditions = self._find_risk_reward_conditions()
        conditions.extend(risk_reward_conditions)

        # 승률 기준 정렬
        return sorted(
            conditions, key=lambda x: (x.win_rate, x.avg_pnl_pct), reverse=True
        )

    def _find_indicator_range_conditions(self) -> list[ProfitableCondition]:
        """지표 범위 기반 수익 조건 탐색"""
        conditions: list[ProfitableCondition] = []

        # 모든 지표 수집
        all_indicators: set[str] = set()
        for trade in self.trades:
            all_indicators.update(trade.indicators.keys())

        for indicator in all_indicators:
            values = [
                t.indicators.get(indicator)
                for t in self.trades
                if t.indicators.get(indicator) is not None
                and isinstance(t.indicators.get(indicator), (int, float))
            ]

            if len(values) < 20:
                continue

            arr = np.array(values)
            percentiles = [25, 50, 75]

            for pct in percentiles:
                threshold = np.percentile(arr, pct)

                # 상위 범위
                high_trades = [
                    t
                    for t in self.trades
                    if t.indicators.get(indicator) is not None
                    and isinstance(t.indicators.get(indicator), (int, float))
                    and t.indicators.get(indicator) >= threshold
                ]

                if len(high_trades) >= 10:
                    win_rate = (
                        sum(1 for t in high_trades if t.is_win) / len(high_trades)
                    )
                    avg_pnl = np.mean([t.pnl_pct for t in high_trades])

                    if win_rate > 0.5:
                        conditions.append(
                            ProfitableCondition(
                                condition_name=f"{indicator}_high_{pct}",
                                win_rate=win_rate,
                                avg_pnl_pct=float(avg_pnl),
                                trade_count=len(high_trades),
                                description=f"{indicator} >= {threshold:.4f} (상위 {100-pct}%)",
                            )
                        )

                # 하위 범위
                low_trades = [
                    t
                    for t in self.trades
                    if t.indicators.get(indicator) is not None
                    and isinstance(t.indicators.get(indicator), (int, float))
                    and t.indicators.get(indicator) < threshold
                ]

                if len(low_trades) >= 10:
                    win_rate = sum(1 for t in low_trades if t.is_win) / len(low_trades)
                    avg_pnl = np.mean([t.pnl_pct for t in low_trades])

                    if win_rate > 0.5:
                        conditions.append(
                            ProfitableCondition(
                                condition_name=f"{indicator}_low_{pct}",
                                win_rate=win_rate,
                                avg_pnl_pct=float(avg_pnl),
                                trade_count=len(low_trades),
                                description=f"{indicator} < {threshold:.4f} (하위 {pct}%)",
                            )
                        )

        return conditions

    def _find_holding_time_conditions(self) -> list[ProfitableCondition]:
        """보유 시간 기반 수익 조건 탐색"""
        conditions: list[ProfitableCondition] = []

        trades_with_holding = [t for t in self.trades if t.holding_minutes is not None]

        if len(trades_with_holding) < 20:
            return conditions

        holding_times = np.array([t.holding_minutes for t in trades_with_holding])
        thresholds = [15, 30, 60, 120, 240]  # 분 단위

        for threshold in thresholds:
            # 짧은 보유
            short_trades = [t for t in trades_with_holding if t.holding_minutes <= threshold]
            if len(short_trades) >= 10:
                win_rate = sum(1 for t in short_trades if t.is_win) / len(short_trades)
                avg_pnl = np.mean([t.pnl_pct for t in short_trades])

                conditions.append(
                    ProfitableCondition(
                        condition_name=f"holding_lte_{threshold}m",
                        win_rate=win_rate,
                        avg_pnl_pct=float(avg_pnl),
                        trade_count=len(short_trades),
                        description=f"보유 시간 <= {threshold}분",
                    )
                )

            # 긴 보유
            long_trades = [t for t in trades_with_holding if t.holding_minutes > threshold]
            if len(long_trades) >= 10:
                win_rate = sum(1 for t in long_trades if t.is_win) / len(long_trades)
                avg_pnl = np.mean([t.pnl_pct for t in long_trades])

                conditions.append(
                    ProfitableCondition(
                        condition_name=f"holding_gt_{threshold}m",
                        win_rate=win_rate,
                        avg_pnl_pct=float(avg_pnl),
                        trade_count=len(long_trades),
                        description=f"보유 시간 > {threshold}분",
                    )
                )

        return conditions

    def _find_risk_reward_conditions(self) -> list[ProfitableCondition]:
        """리스크/보상 비율 기반 조건 탐색"""
        conditions: list[ProfitableCondition] = []

        # max_profit / max_drawdown 비율 분석
        trades_with_rr = [
            t
            for t in self.trades
            if t.max_profit_pct > 0 and t.max_drawdown_pct > 0
        ]

        if len(trades_with_rr) < 20:
            return conditions

        for ratio_threshold in [0.5, 1.0, 1.5, 2.0]:
            # max_profit가 max_drawdown보다 ratio배 이상인 경우
            favorable_trades = [
                t
                for t in trades_with_rr
                if t.max_profit_pct / t.max_drawdown_pct >= ratio_threshold
            ]

            if len(favorable_trades) >= 10:
                win_rate = (
                    sum(1 for t in favorable_trades if t.is_win)
                    / len(favorable_trades)
                )
                avg_pnl = np.mean([t.pnl_pct for t in favorable_trades])

                conditions.append(
                    ProfitableCondition(
                        condition_name=f"rr_ratio_gte_{ratio_threshold}",
                        win_rate=win_rate,
                        avg_pnl_pct=float(avg_pnl),
                        trade_count=len(favorable_trades),
                        description=f"max_profit/max_drawdown >= {ratio_threshold}",
                    )
                )

        return conditions

    def analyze_strategies_breakdown(self) -> dict[str, dict[str, Any]]:
        """전략별 분석 결과"""
        if not self.trades:
            return {}

        strategies: dict[str, list[TradePattern]] = {}
        for trade in self.trades:
            if trade.strategy not in strategies:
                strategies[trade.strategy] = []
            strategies[trade.strategy].append(trade)

        breakdown: dict[str, dict[str, Any]] = {}
        for strategy, trades in strategies.items():
            wins = [t for t in trades if t.is_win]
            losses = [t for t in trades if not t.is_win]

            breakdown[strategy] = {
                "total_trades": len(trades),
                "winning_trades": len(wins),
                "losing_trades": len(losses),
                "win_rate": len(wins) / len(trades) if trades else 0,
                "avg_pnl_pct": float(np.mean([t.pnl_pct for t in trades])),
                "avg_win_pct": float(np.mean([t.pnl_pct for t in wins])) if wins else 0,
                "avg_loss_pct": float(np.mean([t.pnl_pct for t in losses])) if losses else 0,
                "total_pnl_pct": float(sum(t.pnl_pct for t in trades)),
                "avg_holding_minutes": float(
                    np.mean([t.holding_minutes for t in trades if t.holding_minutes])
                ) if any(t.holding_minutes for t in trades) else 0,
            }

        return breakdown

    async def run_full_analysis(
        self,
        run_ids: Optional[list[str]] = None,
        strategies: Optional[list[str]] = None,
    ) -> PatternAnalysisResult:
        """
        전체 패턴 분석 실행

        Args:
            run_ids: 백테스트 실행 ID 리스트
            strategies: 분석할 전략 리스트

        Returns:
            종합 분석 결과
        """
        await self.load_trades(run_ids=run_ids, strategies=strategies)

        if not self.trades:
            return PatternAnalysisResult(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                overall_win_rate=0,
                avg_win_pnl_pct=0,
                avg_loss_pnl_pct=0,
                indicator_comparisons=[],
                profitable_conditions=[],
                strategies_breakdown={},
            )

        win_trades = [t for t in self.trades if t.is_win]
        loss_trades = [t for t in self.trades if not t.is_win]

        return PatternAnalysisResult(
            total_trades=len(self.trades),
            winning_trades=len(win_trades),
            losing_trades=len(loss_trades),
            overall_win_rate=len(win_trades) / len(self.trades),
            avg_win_pnl_pct=float(np.mean([t.pnl_pct for t in win_trades]))
            if win_trades
            else 0,
            avg_loss_pnl_pct=float(np.mean([t.pnl_pct for t in loss_trades]))
            if loss_trades
            else 0,
            indicator_comparisons=self.compare_win_vs_loss(),
            profitable_conditions=self.find_profitable_conditions(),
            strategies_breakdown=self.analyze_strategies_breakdown(),
        )

    def to_report_dict(self, result: PatternAnalysisResult) -> dict[str, Any]:
        """분석 결과를 보고서용 딕셔너리로 변환"""
        return {
            "summary": {
                "total_trades": result.total_trades,
                "winning_trades": result.winning_trades,
                "losing_trades": result.losing_trades,
                "overall_win_rate": f"{result.overall_win_rate:.1%}",
                "avg_win_pnl_pct": f"{result.avg_win_pnl_pct:.2f}%",
                "avg_loss_pnl_pct": f"{result.avg_loss_pnl_pct:.2f}%",
            },
            "significant_indicators": [
                {
                    "name": ic.indicator_name,
                    "p_value": f"{ic.p_value:.4f}",
                    "win_mean": f"{ic.win_mean:.4f}",
                    "loss_mean": f"{ic.loss_mean:.4f}",
                    "favorable_direction": ic.win_favorable_direction,
                }
                for ic in result.indicator_comparisons
                if ic.is_significant
            ],
            "profitable_conditions": [
                {
                    "name": pc.condition_name,
                    "win_rate": f"{pc.win_rate:.1%}",
                    "avg_pnl_pct": f"{pc.avg_pnl_pct:.2f}%",
                    "trade_count": pc.trade_count,
                    "description": pc.description,
                }
                for pc in result.profitable_conditions[:20]  # 상위 20개
            ],
            "strategies_breakdown": result.strategies_breakdown,
        }
