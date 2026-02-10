"""
지표 분포 분석기

승/패 거래별 지표 분포를 분석하고
각 지표의 최적 진입 범위를 도출합니다.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from scipy import stats

from .trade_analyzer import TradePattern


@dataclass
class IndicatorStats:
    """지표 통계"""

    mean: float
    std: float
    median: float
    min: float
    max: float
    percentile_25: float
    percentile_75: float
    count: int


@dataclass
class IndicatorDistribution:
    """지표 분포 정보"""

    indicator_name: str
    all_stats: IndicatorStats
    win_stats: IndicatorStats
    loss_stats: IndicatorStats
    histogram_data: dict[str, list]  # bins, win_counts, loss_counts
    optimal_range: Optional[tuple[float, float]]
    optimal_range_win_rate: float
    optimal_range_avg_pnl: float


@dataclass
class OptimalRangeResult:
    """최적 범위 분석 결과"""

    indicator_name: str
    min_value: float
    max_value: float
    win_rate: float
    avg_pnl_pct: float
    trade_count: int
    improvement_vs_baseline: float  # 기본 대비 승률 개선


class IndicatorDistributionAnalyzer:
    """지표 분포 분석기"""

    def __init__(self, trades: list[TradePattern]):
        self.trades = trades
        self.win_trades = [t for t in trades if t.is_win]
        self.loss_trades = [t for t in trades if not t.is_win]
        self.baseline_win_rate = (
            len(self.win_trades) / len(trades) if trades else 0
        )

    def _get_indicator_values(
        self,
        indicator_name: str,
        trades: list[TradePattern],
    ) -> list[float]:
        """특정 지표 값 추출"""
        values = []
        for trade in trades:
            value = trade.indicators.get(indicator_name)
            if value is not None and isinstance(value, (int, float)):
                values.append(float(value))
        return values

    def _calculate_stats(self, values: list[float]) -> Optional[IndicatorStats]:
        """통계 계산"""
        if len(values) < 5:
            return None

        arr = np.array(values)
        return IndicatorStats(
            mean=float(np.mean(arr)),
            std=float(np.std(arr)),
            median=float(np.median(arr)),
            min=float(np.min(arr)),
            max=float(np.max(arr)),
            percentile_25=float(np.percentile(arr, 25)),
            percentile_75=float(np.percentile(arr, 75)),
            count=len(values),
        )

    def _create_histogram_data(
        self,
        indicator_name: str,
        num_bins: int = 20,
    ) -> dict[str, list]:
        """히스토그램 데이터 생성"""
        all_values = self._get_indicator_values(indicator_name, self.trades)
        win_values = self._get_indicator_values(indicator_name, self.win_trades)
        loss_values = self._get_indicator_values(indicator_name, self.loss_trades)

        if not all_values:
            return {"bins": [], "win_counts": [], "loss_counts": []}

        # 동일 bin 범위 사용
        min_val = min(all_values)
        max_val = max(all_values)
        bins = np.linspace(min_val, max_val, num_bins + 1)

        win_counts, _ = np.histogram(win_values, bins=bins)
        loss_counts, _ = np.histogram(loss_values, bins=bins)

        # bin 중심값
        bin_centers = (bins[:-1] + bins[1:]) / 2

        return {
            "bins": bin_centers.tolist(),
            "win_counts": win_counts.tolist(),
            "loss_counts": loss_counts.tolist(),
        }

    def _find_optimal_range_for_indicator(
        self,
        indicator_name: str,
        step_count: int = 20,
    ) -> Optional[tuple[float, float, float, float, int]]:
        """
        단일 지표의 최적 범위 탐색 (그리드 서치)

        Returns:
            (min_val, max_val, win_rate, avg_pnl, trade_count) 또는 None
        """
        values = self._get_indicator_values(indicator_name, self.trades)

        if len(values) < 30:
            return None

        arr = np.array(values)
        min_val = np.percentile(arr, 5)  # 하위 5% 제외
        max_val = np.percentile(arr, 95)  # 상위 5% 제외

        steps = np.linspace(min_val, max_val, step_count)

        best_result = None
        best_score = 0  # win_rate * sqrt(trade_count)

        for i, low in enumerate(steps):
            for high in steps[i + 1 :]:
                matching_trades = [
                    t
                    for t in self.trades
                    if indicator_name in t.indicators
                    and isinstance(t.indicators[indicator_name], (int, float))
                    and low <= t.indicators[indicator_name] <= high
                ]

                if len(matching_trades) < 20:
                    continue

                win_count = sum(1 for t in matching_trades if t.is_win)
                win_rate = win_count / len(matching_trades)
                avg_pnl = np.mean([t.pnl_pct for t in matching_trades])

                # 승률과 거래 수를 고려한 점수
                score = win_rate * np.sqrt(len(matching_trades))

                # 승률이 기준치(50%) 이상인 경우만
                if win_rate >= 0.5 and score > best_score:
                    best_score = score
                    best_result = (low, high, win_rate, float(avg_pnl), len(matching_trades))

        return best_result

    def analyze_single_indicator(
        self,
        indicator_name: str,
    ) -> Optional[IndicatorDistribution]:
        """단일 지표 분포 분석"""
        all_values = self._get_indicator_values(indicator_name, self.trades)
        win_values = self._get_indicator_values(indicator_name, self.win_trades)
        loss_values = self._get_indicator_values(indicator_name, self.loss_trades)

        all_stats = self._calculate_stats(all_values)
        win_stats = self._calculate_stats(win_values)
        loss_stats = self._calculate_stats(loss_values)

        if not all_stats or not win_stats or not loss_stats:
            return None

        histogram_data = self._create_histogram_data(indicator_name)
        optimal_result = self._find_optimal_range_for_indicator(indicator_name)

        optimal_range = None
        optimal_win_rate = 0
        optimal_avg_pnl = 0

        if optimal_result:
            low, high, win_rate, avg_pnl, _ = optimal_result
            optimal_range = (low, high)
            optimal_win_rate = win_rate
            optimal_avg_pnl = avg_pnl

        return IndicatorDistribution(
            indicator_name=indicator_name,
            all_stats=all_stats,
            win_stats=win_stats,
            loss_stats=loss_stats,
            histogram_data=histogram_data,
            optimal_range=optimal_range,
            optimal_range_win_rate=optimal_win_rate,
            optimal_range_avg_pnl=optimal_avg_pnl,
        )

    def analyze_all_indicators(self) -> dict[str, IndicatorDistribution]:
        """모든 지표 분포 분석"""
        # 모든 지표 수집
        all_indicators: set[str] = set()
        for trade in self.trades:
            all_indicators.update(trade.indicators.keys())

        results: dict[str, IndicatorDistribution] = {}

        for indicator in sorted(all_indicators):
            distribution = self.analyze_single_indicator(indicator)
            if distribution:
                results[indicator] = distribution

        return results

    def find_optimal_ranges(self) -> dict[str, OptimalRangeResult]:
        """각 지표의 최적 범위 도출"""
        results: dict[str, OptimalRangeResult] = {}

        # 모든 지표 수집
        all_indicators: set[str] = set()
        for trade in self.trades:
            all_indicators.update(trade.indicators.keys())

        for indicator in sorted(all_indicators):
            optimal_result = self._find_optimal_range_for_indicator(indicator)

            if optimal_result:
                low, high, win_rate, avg_pnl, trade_count = optimal_result
                improvement = win_rate - self.baseline_win_rate

                results[indicator] = OptimalRangeResult(
                    indicator_name=indicator,
                    min_value=low,
                    max_value=high,
                    win_rate=win_rate,
                    avg_pnl_pct=avg_pnl,
                    trade_count=trade_count,
                    improvement_vs_baseline=improvement,
                )

        # 개선도 기준 정렬
        return dict(
            sorted(
                results.items(),
                key=lambda x: x[1].improvement_vs_baseline,
                reverse=True,
            )
        )

    def find_combined_optimal_ranges(
        self,
        top_n_indicators: int = 3,
    ) -> list[dict[str, Any]]:
        """
        여러 지표 조합의 최적 범위 탐색

        Args:
            top_n_indicators: 조합할 상위 지표 수

        Returns:
            조합 조건별 결과 리스트
        """
        single_ranges = self.find_optimal_ranges()

        if len(single_ranges) < 2:
            return []

        # 상위 지표 선택
        top_indicators = list(single_ranges.keys())[:top_n_indicators]

        combined_results: list[dict[str, Any]] = []

        # 2개 조합
        for i, ind1 in enumerate(top_indicators):
            for ind2 in top_indicators[i + 1 :]:
                range1 = single_ranges[ind1]
                range2 = single_ranges[ind2]

                matching_trades = [
                    t
                    for t in self.trades
                    if ind1 in t.indicators
                    and ind2 in t.indicators
                    and isinstance(t.indicators[ind1], (int, float))
                    and isinstance(t.indicators[ind2], (int, float))
                    and range1.min_value <= t.indicators[ind1] <= range1.max_value
                    and range2.min_value <= t.indicators[ind2] <= range2.max_value
                ]

                if len(matching_trades) >= 15:
                    win_rate = (
                        sum(1 for t in matching_trades if t.is_win)
                        / len(matching_trades)
                    )
                    avg_pnl = np.mean([t.pnl_pct for t in matching_trades])

                    combined_results.append({
                        "indicators": [ind1, ind2],
                        "conditions": {
                            ind1: (range1.min_value, range1.max_value),
                            ind2: (range2.min_value, range2.max_value),
                        },
                        "win_rate": win_rate,
                        "avg_pnl_pct": float(avg_pnl),
                        "trade_count": len(matching_trades),
                        "improvement": win_rate - self.baseline_win_rate,
                    })

        # 승률 기준 정렬
        return sorted(combined_results, key=lambda x: x["win_rate"], reverse=True)

    def to_report_dict(self) -> dict[str, Any]:
        """분석 결과를 보고서용 딕셔너리로 변환"""
        distributions = self.analyze_all_indicators()
        optimal_ranges = self.find_optimal_ranges()
        combined_ranges = self.find_combined_optimal_ranges()

        return {
            "summary": {
                "total_trades": len(self.trades),
                "baseline_win_rate": f"{self.baseline_win_rate:.1%}",
                "indicators_analyzed": len(distributions),
            },
            "indicator_distributions": {
                name: {
                    "all": {
                        "mean": f"{dist.all_stats.mean:.4f}",
                        "std": f"{dist.all_stats.std:.4f}",
                        "median": f"{dist.all_stats.median:.4f}",
                    },
                    "win": {
                        "mean": f"{dist.win_stats.mean:.4f}",
                        "std": f"{dist.win_stats.std:.4f}",
                        "median": f"{dist.win_stats.median:.4f}",
                    },
                    "loss": {
                        "mean": f"{dist.loss_stats.mean:.4f}",
                        "std": f"{dist.loss_stats.std:.4f}",
                        "median": f"{dist.loss_stats.median:.4f}",
                    },
                    "optimal_range": (
                        f"{dist.optimal_range[0]:.4f} ~ {dist.optimal_range[1]:.4f}"
                        if dist.optimal_range
                        else "N/A"
                    ),
                    "optimal_win_rate": f"{dist.optimal_range_win_rate:.1%}",
                }
                for name, dist in distributions.items()
            },
            "optimal_ranges": {
                name: {
                    "range": f"{r.min_value:.4f} ~ {r.max_value:.4f}",
                    "win_rate": f"{r.win_rate:.1%}",
                    "avg_pnl": f"{r.avg_pnl_pct:.2f}%",
                    "trades": r.trade_count,
                    "improvement": f"+{r.improvement_vs_baseline:.1%}",
                }
                for name, r in optimal_ranges.items()
            },
            "combined_conditions": [
                {
                    "indicators": c["indicators"],
                    "win_rate": f"{c['win_rate']:.1%}",
                    "avg_pnl": f"{c['avg_pnl_pct']:.2f}%",
                    "trades": c["trade_count"],
                    "improvement": f"+{c['improvement']:.1%}",
                }
                for c in combined_ranges[:10]  # 상위 10개
            ],
        }
