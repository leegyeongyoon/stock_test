"""전략 앙상블 분석

전략 간 상관관계 분석 및 앙상블 조합 최적화.
상관관계 < 0.7인 전략만 조합.
"""

import math
from dataclasses import dataclass, field
from typing import Optional

import structlog

from src.ai_optimizer.schemas import StrategyCandidate, StrategyTypeAI

logger = structlog.get_logger()


@dataclass
class CorrelationPair:
    """전략 쌍 상관관계"""
    strategy_a: str
    strategy_b: str
    correlation: float
    overlap_pct: float = 0.0  # 동시 진입 비율


@dataclass
class EnsembleMember:
    """앙상블 멤버"""
    candidate: StrategyCandidate
    allocation_pct: float = 25.0  # 자본 배분 비율
    role: str = ""  # 앙상블 내 역할


@dataclass
class EnsembleResult:
    """앙상블 분석 결과"""
    members: list[EnsembleMember] = field(default_factory=list)
    correlations: list[CorrelationPair] = field(default_factory=list)
    max_correlation: float = 0.0
    avg_correlation: float = 0.0
    type_diversity: int = 0  # 서로 다른 전략 유형 수
    expected_return_pct: float = 0.0
    expected_sharpe: float = 0.0
    expected_max_dd: float = 0.0


class EnsembleAnalyzer:
    """전략 앙상블 분석기"""

    MAX_CORRELATION = 0.7
    MAX_ENSEMBLE_SIZE = 4
    MIN_ENSEMBLE_SIZE = 2

    def analyze(
        self,
        candidates: list[StrategyCandidate],
        trade_logs: Optional[dict[str, list[dict]]] = None,
    ) -> EnsembleResult:
        """
        후보 전략들의 앙상블 최적 조합 분석.

        Args:
            candidates: 선별된 후보 전략 리스트
            trade_logs: {candidate_id: [trade_dicts]} 거래 로그 (상관관계 계산용)
        """
        if len(candidates) < self.MIN_ENSEMBLE_SIZE:
            logger.warning("Not enough candidates for ensemble", count=len(candidates))
            return EnsembleResult()

        # 1. 상관관계 계산
        correlations = self._compute_correlations(candidates, trade_logs)

        # 2. 최적 조합 탐색
        best_combo = self._find_best_combination(candidates, correlations)

        # 3. 자본 배분
        members = self._allocate_capital(best_combo)

        # 4. 앙상블 기대 성과 추정
        result = self._build_result(members, correlations)

        logger.info(
            "Ensemble analysis complete",
            members=len(result.members),
            types=result.type_diversity,
            expected_return=f"{result.expected_return_pct:+.2f}%",
            max_correlation=f"{result.max_correlation:.2f}",
        )

        return result

    def _compute_correlations(
        self,
        candidates: list[StrategyCandidate],
        trade_logs: Optional[dict[str, list[dict]]],
    ) -> list[CorrelationPair]:
        """전략 쌍별 상관관계 계산"""
        correlations = []

        for i, a in enumerate(candidates):
            for j, b in enumerate(candidates):
                if i >= j:
                    continue

                # 거래 로그 기반 상관관계
                corr = self._calc_trade_correlation(a, b, trade_logs)

                # 전략 유형 기반 추정 (거래 로그 없을 때)
                if corr is None:
                    corr = self._estimate_type_correlation(a.strategy_type, b.strategy_type)

                overlap = self._calc_overlap(a, b, trade_logs)

                correlations.append(CorrelationPair(
                    strategy_a=a.candidate_id,
                    strategy_b=b.candidate_id,
                    correlation=corr,
                    overlap_pct=overlap,
                ))

        return correlations

    def _calc_trade_correlation(
        self,
        a: StrategyCandidate,
        b: StrategyCandidate,
        trade_logs: Optional[dict[str, list[dict]]],
    ) -> Optional[float]:
        """거래 로그 기반 수익률 상관관계"""
        if not trade_logs:
            return None

        trades_a = trade_logs.get(a.candidate_id, [])
        trades_b = trade_logs.get(b.candidate_id, [])

        if len(trades_a) < 10 or len(trades_b) < 10:
            return None

        # 일별 수익률로 상관관계 계산
        daily_a = self._aggregate_daily_returns(trades_a)
        daily_b = self._aggregate_daily_returns(trades_b)

        common_dates = set(daily_a.keys()) & set(daily_b.keys())
        if len(common_dates) < 5:
            return None

        returns_a = [daily_a[d] for d in sorted(common_dates)]
        returns_b = [daily_b[d] for d in sorted(common_dates)]

        return self._pearson_correlation(returns_a, returns_b)

    @staticmethod
    def _aggregate_daily_returns(trades: list[dict]) -> dict[str, float]:
        """거래를 일별 수익률로 집계"""
        daily: dict[str, float] = {}
        for t in trades:
            entry_time = t.get("entry_time", "")
            date_str = str(entry_time)[:10] if entry_time else ""
            if date_str:
                daily[date_str] = daily.get(date_str, 0) + t.get("pnl_pct", 0)
        return daily

    @staticmethod
    def _pearson_correlation(x: list[float], y: list[float]) -> float:
        """피어슨 상관계수"""
        n = len(x)
        if n < 2:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
        std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) / n)
        std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y) / n)

        if std_x == 0 or std_y == 0:
            return 0.0

        return cov / (std_x * std_y)

    @staticmethod
    def _estimate_type_correlation(
        type_a: StrategyTypeAI, type_b: StrategyTypeAI
    ) -> float:
        """전략 유형 기반 상관관계 추정"""
        # 같은 유형: 높은 상관관계
        if type_a == type_b:
            return 0.8

        # 유사 유형
        similar_pairs = {
            frozenset({StrategyTypeAI.MOMENTUM, StrategyTypeAI.BREAKOUT}): 0.6,
            frozenset({StrategyTypeAI.MOMENTUM, StrategyTypeAI.TREND}): 0.7,
            frozenset({StrategyTypeAI.MEAN_REVERSION, StrategyTypeAI.VOLUME}): 0.5,
        }

        pair = frozenset({type_a, type_b})
        if pair in similar_pairs:
            return similar_pairs[pair]

        # 반대 유형: 음/낮은 상관관계
        opposite_pairs = {
            frozenset({StrategyTypeAI.MOMENTUM, StrategyTypeAI.MEAN_REVERSION}): 0.1,
            frozenset({StrategyTypeAI.TREND, StrategyTypeAI.MEAN_REVERSION}): 0.15,
        }

        if pair in opposite_pairs:
            return opposite_pairs[pair]

        # 기본: 중간 상관관계
        return 0.4

    def _calc_overlap(
        self,
        a: StrategyCandidate,
        b: StrategyCandidate,
        trade_logs: Optional[dict[str, list[dict]]],
    ) -> float:
        """동시 진입 비율"""
        if not trade_logs:
            return 0.0

        trades_a = trade_logs.get(a.candidate_id, [])
        trades_b = trade_logs.get(b.candidate_id, [])

        if not trades_a or not trades_b:
            return 0.0

        # 5분 단위로 동시 진입 체크
        timestamps_a = {str(t.get("entry_time", ""))[:16] for t in trades_a}
        timestamps_b = {str(t.get("entry_time", ""))[:16] for t in trades_b}

        overlap = len(timestamps_a & timestamps_b)
        total = len(timestamps_a | timestamps_b)

        return (overlap / total * 100) if total > 0 else 0.0

    def _find_best_combination(
        self,
        candidates: list[StrategyCandidate],
        correlations: list[CorrelationPair],
    ) -> list[StrategyCandidate]:
        """최적 조합 탐색 (탐욕 알고리즘)"""
        corr_map = {}
        for cp in correlations:
            corr_map[(cp.strategy_a, cp.strategy_b)] = cp.correlation
            corr_map[(cp.strategy_b, cp.strategy_a)] = cp.correlation

        # 수익률 높은 순으로 정렬
        sorted_candidates = sorted(candidates, key=lambda c: c.return_pct, reverse=True)

        selected = []
        for candidate in sorted_candidates:
            if len(selected) >= self.MAX_ENSEMBLE_SIZE:
                break

            # 기존 선택된 전략과 상관관계 체크
            compatible = True
            for existing in selected:
                corr = corr_map.get(
                    (candidate.candidate_id, existing.candidate_id), 0.5
                )
                if corr > self.MAX_CORRELATION:
                    compatible = False
                    break

            if compatible:
                selected.append(candidate)

        return selected

    def _allocate_capital(
        self, candidates: list[StrategyCandidate]
    ) -> list[EnsembleMember]:
        """자본 배분 (Sharpe 비례)"""
        if not candidates:
            return []

        total_sharpe = sum(max(c.sharpe, 0.1) for c in candidates)

        members = []
        for c in candidates:
            weight = max(c.sharpe, 0.1) / total_sharpe
            allocation = round(weight * 100, 1)

            role = self._determine_role(c)

            members.append(EnsembleMember(
                candidate=c,
                allocation_pct=allocation,
                role=role,
            ))

        return members

    @staticmethod
    def _determine_role(c: StrategyCandidate) -> str:
        """앙상블 내 역할 결정"""
        roles = {
            StrategyTypeAI.MOMENTUM: "Trend capture / momentum riding",
            StrategyTypeAI.BREAKOUT: "Range breakout capture",
            StrategyTypeAI.TREND: "Long-term trend following",
            StrategyTypeAI.MEAN_REVERSION: "Oversold bounce / mean reversion",
            StrategyTypeAI.VOLUME: "Volume-driven entry timing",
            StrategyTypeAI.MULTI_TIMEFRAME: "Multi-TF confirmation",
            StrategyTypeAI.BTC_CORRELATION: "BTC-altcoin divergence",
            StrategyTypeAI.REGIME_ADAPTIVE: "Market regime adaptation",
        }
        return roles.get(c.strategy_type, "General strategy")

    def _build_result(
        self,
        members: list[EnsembleMember],
        correlations: list[CorrelationPair],
    ) -> EnsembleResult:
        """앙상블 결과 빌드"""
        result = EnsembleResult(members=members, correlations=correlations)

        if correlations:
            member_ids = {m.candidate.candidate_id for m in members}
            relevant_corrs = [
                cp for cp in correlations
                if cp.strategy_a in member_ids and cp.strategy_b in member_ids
            ]
            if relevant_corrs:
                result.max_correlation = max(cp.correlation for cp in relevant_corrs)
                result.avg_correlation = sum(cp.correlation for cp in relevant_corrs) / len(relevant_corrs)

        # 유형 다양성
        types = {m.candidate.strategy_type for m in members}
        result.type_diversity = len(types)

        # 기대 성과 (가중 평균)
        if members:
            total_alloc = sum(m.allocation_pct for m in members)
            if total_alloc > 0:
                result.expected_return_pct = sum(
                    m.candidate.return_pct * m.allocation_pct / total_alloc
                    for m in members
                )
                result.expected_sharpe = sum(
                    m.candidate.sharpe * m.allocation_pct / total_alloc
                    for m in members
                )
                # MDD는 상관관계 고려하여 감소 추정
                avg_dd = sum(
                    m.candidate.max_dd * m.allocation_pct / total_alloc
                    for m in members
                )
                diversity_factor = max(0.5, 1.0 - result.avg_correlation * 0.3)
                result.expected_max_dd = avg_dd * diversity_factor

        return result
