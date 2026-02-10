"""
최적 SL/TP 분석기

진입 후 가격 움직임을 분석하여
최적의 손절(SL)/익절(TP) 레벨을 도출합니다.
"""

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from scipy import optimize

from .trade_analyzer import TradePattern


@dataclass
class PriceMovementStats:
    """가격 움직임 통계"""

    avg_max_profit_pct: float
    avg_max_drawdown_pct: float
    median_max_profit_pct: float
    median_max_drawdown_pct: float
    percentile_25_profit: float
    percentile_75_profit: float
    percentile_25_drawdown: float
    percentile_75_drawdown: float


@dataclass
class BreakevenAnalysis:
    """손익분기점 분석"""

    avg_time_to_breakeven_minutes: float
    breakeven_rate: float  # 손익분기 도달 비율
    avg_drawdown_before_profit: float  # 수익 전 평균 드로다운


@dataclass
class SLTPOptimization:
    """SL/TP 최적화 결과"""

    optimal_sl_pct: float
    optimal_tp_pct: float
    expected_win_rate: float
    expected_avg_pnl_pct: float
    risk_reward_ratio: float
    trade_count: int


@dataclass
class TrailingStopResult:
    """트레일링 스탑 시뮬레이션 결과"""

    trailing_pct: float
    activation_pct: float  # 트레일링 스탑 활성화 수준
    win_rate: float
    avg_pnl_pct: float
    improvement_vs_fixed: float


@dataclass
class SLTPAnalysisResult:
    """SL/TP 분석 종합 결과"""

    price_movement_stats: PriceMovementStats
    breakeven_analysis: Optional[BreakevenAnalysis]
    grid_search_results: list[SLTPOptimization]
    best_sl_tp: SLTPOptimization
    trailing_stop_results: list[TrailingStopResult]
    best_trailing_stop: Optional[TrailingStopResult]


class SLTPAnalyzer:
    """최적 SL/TP 분석기"""

    def __init__(self, trades: list[TradePattern]):
        self.trades = [
            t for t in trades
            if t.max_profit_pct > 0 or t.max_drawdown_pct > 0
        ]

    def analyze_price_movements(self) -> PriceMovementStats:
        """가격 움직임 분석"""
        if not self.trades:
            return PriceMovementStats(
                avg_max_profit_pct=0,
                avg_max_drawdown_pct=0,
                median_max_profit_pct=0,
                median_max_drawdown_pct=0,
                percentile_25_profit=0,
                percentile_75_profit=0,
                percentile_25_drawdown=0,
                percentile_75_drawdown=0,
            )

        max_profits = np.array([t.max_profit_pct for t in self.trades])
        max_drawdowns = np.array([t.max_drawdown_pct for t in self.trades])

        return PriceMovementStats(
            avg_max_profit_pct=float(np.mean(max_profits)),
            avg_max_drawdown_pct=float(np.mean(max_drawdowns)),
            median_max_profit_pct=float(np.median(max_profits)),
            median_max_drawdown_pct=float(np.median(max_drawdowns)),
            percentile_25_profit=float(np.percentile(max_profits, 25)),
            percentile_75_profit=float(np.percentile(max_profits, 75)),
            percentile_25_drawdown=float(np.percentile(max_drawdowns, 25)),
            percentile_75_drawdown=float(np.percentile(max_drawdowns, 75)),
        )

    def calculate_breakeven_point(self) -> Optional[BreakevenAnalysis]:
        """손익분기점 분석"""
        # max_profit > 0인 거래 = 수익 영역에 도달한 거래
        breakeven_trades = [t for t in self.trades if t.max_profit_pct > 0]

        if len(breakeven_trades) < 10:
            return None

        breakeven_rate = len(breakeven_trades) / len(self.trades)

        # 수익 전 드로다운 (수익을 본 거래의 max_drawdown)
        avg_drawdown_before_profit = float(
            np.mean([t.max_drawdown_pct for t in breakeven_trades])
        )

        # 손익분기 도달 시간 (보유 시간의 일부로 추정)
        holding_times = [
            t.holding_minutes for t in breakeven_trades if t.holding_minutes
        ]
        avg_time = float(np.mean(holding_times)) if holding_times else 0

        return BreakevenAnalysis(
            avg_time_to_breakeven_minutes=avg_time,
            breakeven_rate=breakeven_rate,
            avg_drawdown_before_profit=avg_drawdown_before_profit,
        )

    def simulate_sl_tp(
        self,
        sl_pct: float,
        tp_pct: float,
    ) -> tuple[float, float, int]:
        """
        특정 SL/TP로 시뮬레이션

        Args:
            sl_pct: 손절 퍼센트 (음수 값)
            tp_pct: 익절 퍼센트 (양수 값)

        Returns:
            (win_rate, avg_pnl_pct, trade_count)
        """
        wins = 0
        total_pnl = 0
        count = 0

        for trade in self.trades:
            # SL에 먼저 걸리는지 TP에 먼저 걸리는지 판단
            # 실제로는 순서를 알 수 없으므로 max 값으로 추정

            hit_sl = trade.max_drawdown_pct >= abs(sl_pct)
            hit_tp = trade.max_profit_pct >= tp_pct

            if hit_tp and not hit_sl:
                # TP 도달, SL 미도달 → 익절
                wins += 1
                total_pnl += tp_pct
            elif hit_sl and not hit_tp:
                # SL 도달, TP 미도달 → 손절
                total_pnl += sl_pct  # 음수
            elif hit_sl and hit_tp:
                # 둘 다 도달 → 실제 결과 사용 (어느 것이 먼저인지 모름)
                if trade.is_win:
                    wins += 1
                    total_pnl += min(tp_pct, trade.pnl_pct)
                else:
                    total_pnl += max(sl_pct, trade.pnl_pct)
            else:
                # 둘 다 미도달 → 실제 결과 사용
                if trade.is_win:
                    wins += 1
                total_pnl += trade.pnl_pct

            count += 1

        win_rate = wins / count if count > 0 else 0
        avg_pnl = total_pnl / count if count > 0 else 0

        return win_rate, avg_pnl, count

    def optimize_sl_tp(
        self,
        sl_range: tuple[float, float] = (-5.0, -0.5),
        tp_range: tuple[float, float] = (0.5, 5.0),
        step: float = 0.5,
    ) -> list[SLTPOptimization]:
        """
        그리드 서치로 최적 SL/TP 탐색

        Args:
            sl_range: 손절 범위 (음수)
            tp_range: 익절 범위 (양수)
            step: 스텝 크기

        Returns:
            SL/TP 조합별 최적화 결과 리스트
        """
        results: list[SLTPOptimization] = []

        sl_values = np.arange(sl_range[0], sl_range[1] + step, step)
        tp_values = np.arange(tp_range[0], tp_range[1] + step, step)

        for sl in sl_values:
            for tp in tp_values:
                win_rate, avg_pnl, count = self.simulate_sl_tp(sl, tp)

                # 리스크/보상 비율 (TP/SL 절대값 비)
                rr_ratio = tp / abs(sl)

                results.append(
                    SLTPOptimization(
                        optimal_sl_pct=float(sl),
                        optimal_tp_pct=float(tp),
                        expected_win_rate=win_rate,
                        expected_avg_pnl_pct=avg_pnl,
                        risk_reward_ratio=rr_ratio,
                        trade_count=count,
                    )
                )

        # 기대 수익 기준 정렬
        return sorted(results, key=lambda x: x.expected_avg_pnl_pct, reverse=True)

    def find_best_sl_tp(self) -> SLTPOptimization:
        """최적 SL/TP 조합 찾기"""
        results = self.optimize_sl_tp()
        return results[0] if results else SLTPOptimization(
            optimal_sl_pct=-2.0,
            optimal_tp_pct=2.0,
            expected_win_rate=0,
            expected_avg_pnl_pct=0,
            risk_reward_ratio=1.0,
            trade_count=0,
        )

    def simulate_trailing_stop(
        self,
        trailing_pct: float,
        activation_pct: float = 0.5,
    ) -> tuple[float, float, int]:
        """
        트레일링 스탑 시뮬레이션

        Args:
            trailing_pct: 트레일링 스탑 퍼센트 (최고점에서 하락 허용치)
            activation_pct: 트레일링 활성화 수익률

        Returns:
            (win_rate, avg_pnl_pct, trade_count)
        """
        wins = 0
        total_pnl = 0
        count = 0

        for trade in self.trades:
            # 활성화 조건 충족 여부
            activated = trade.max_profit_pct >= activation_pct

            if activated:
                # 트레일링 스탑 시뮬레이션
                # 최고점에서 trailing_pct 하락 시 청산
                exit_pnl = trade.max_profit_pct - trailing_pct

                if exit_pnl > 0:
                    wins += 1
                total_pnl += exit_pnl
            else:
                # 활성화 안됨 → 실제 결과 사용
                if trade.is_win:
                    wins += 1
                total_pnl += trade.pnl_pct

            count += 1

        win_rate = wins / count if count > 0 else 0
        avg_pnl = total_pnl / count if count > 0 else 0

        return win_rate, avg_pnl, count

    def optimize_trailing_stop(
        self,
        trailing_range: tuple[float, float] = (0.3, 2.0),
        activation_range: tuple[float, float] = (0.3, 2.0),
        step: float = 0.3,
    ) -> list[TrailingStopResult]:
        """트레일링 스탑 최적화"""
        # 고정 SL/TP 기준 성능
        baseline = self.find_best_sl_tp()
        baseline_pnl = baseline.expected_avg_pnl_pct

        results: list[TrailingStopResult] = []

        trailing_values = np.arange(trailing_range[0], trailing_range[1] + step, step)
        activation_values = np.arange(
            activation_range[0], activation_range[1] + step, step
        )

        for trailing in trailing_values:
            for activation in activation_values:
                win_rate, avg_pnl, count = self.simulate_trailing_stop(
                    trailing, activation
                )

                results.append(
                    TrailingStopResult(
                        trailing_pct=float(trailing),
                        activation_pct=float(activation),
                        win_rate=win_rate,
                        avg_pnl_pct=avg_pnl,
                        improvement_vs_fixed=avg_pnl - baseline_pnl,
                    )
                )

        # 기대 수익 기준 정렬
        return sorted(results, key=lambda x: x.avg_pnl_pct, reverse=True)

    def run_full_analysis(self) -> SLTPAnalysisResult:
        """전체 SL/TP 분석 실행"""
        price_stats = self.analyze_price_movements()
        breakeven = self.calculate_breakeven_point()

        grid_results = self.optimize_sl_tp()
        best_sl_tp = grid_results[0] if grid_results else SLTPOptimization(
            optimal_sl_pct=-2.0,
            optimal_tp_pct=2.0,
            expected_win_rate=0,
            expected_avg_pnl_pct=0,
            risk_reward_ratio=1.0,
            trade_count=0,
        )

        trailing_results = self.optimize_trailing_stop()
        best_trailing = trailing_results[0] if trailing_results else None

        return SLTPAnalysisResult(
            price_movement_stats=price_stats,
            breakeven_analysis=breakeven,
            grid_search_results=grid_results[:20],  # 상위 20개
            best_sl_tp=best_sl_tp,
            trailing_stop_results=trailing_results[:20],
            best_trailing_stop=best_trailing,
        )

    def to_report_dict(self, result: SLTPAnalysisResult) -> dict[str, Any]:
        """분석 결과를 보고서용 딕셔너리로 변환"""
        report: dict[str, Any] = {
            "summary": {
                "total_trades": len(self.trades),
            },
            "price_movement_stats": {
                "avg_max_profit_pct": f"{result.price_movement_stats.avg_max_profit_pct:.2f}%",
                "avg_max_drawdown_pct": f"{result.price_movement_stats.avg_max_drawdown_pct:.2f}%",
                "median_max_profit_pct": f"{result.price_movement_stats.median_max_profit_pct:.2f}%",
                "median_max_drawdown_pct": f"{result.price_movement_stats.median_max_drawdown_pct:.2f}%",
            },
        }

        if result.breakeven_analysis:
            report["breakeven_analysis"] = {
                "breakeven_rate": f"{result.breakeven_analysis.breakeven_rate:.1%}",
                "avg_drawdown_before_profit": f"{result.breakeven_analysis.avg_drawdown_before_profit:.2f}%",
                "avg_time_to_breakeven": f"{result.breakeven_analysis.avg_time_to_breakeven_minutes:.0f}분",
            }

        report["best_sl_tp"] = {
            "sl_pct": f"{result.best_sl_tp.optimal_sl_pct:.1f}%",
            "tp_pct": f"{result.best_sl_tp.optimal_tp_pct:.1f}%",
            "expected_win_rate": f"{result.best_sl_tp.expected_win_rate:.1%}",
            "expected_avg_pnl": f"{result.best_sl_tp.expected_avg_pnl_pct:.2f}%",
            "risk_reward_ratio": f"{result.best_sl_tp.risk_reward_ratio:.2f}",
        }

        report["top_sl_tp_combinations"] = [
            {
                "sl": f"{r.optimal_sl_pct:.1f}%",
                "tp": f"{r.optimal_tp_pct:.1f}%",
                "win_rate": f"{r.expected_win_rate:.1%}",
                "avg_pnl": f"{r.expected_avg_pnl_pct:.2f}%",
            }
            for r in result.grid_search_results[:10]
        ]

        if result.best_trailing_stop:
            report["best_trailing_stop"] = {
                "trailing_pct": f"{result.best_trailing_stop.trailing_pct:.1f}%",
                "activation_pct": f"{result.best_trailing_stop.activation_pct:.1f}%",
                "win_rate": f"{result.best_trailing_stop.win_rate:.1%}",
                "avg_pnl": f"{result.best_trailing_stop.avg_pnl_pct:.2f}%",
                "improvement_vs_fixed": f"{result.best_trailing_stop.improvement_vs_fixed:+.2f}%",
            }

        return report
