"""전략 선별기

필터 기준으로 후보 전략 선별 및 랭킹.
과적합 방지를 위한 복잡도 페널티 적용.
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.ai_optimizer.schemas import StrategyCandidate, StrategyTypeAI

logger = structlog.get_logger()


@dataclass
class SelectionCriteria:
    """선별 기준"""
    min_return_pct: float = 5.0        # 최소 수익률 (3개월)
    min_sharpe: float = 0.5            # 최소 Sharpe
    max_drawdown_pct: float = 15.0     # 최대 MDD
    min_win_rate: float = 40.0         # 최소 승률 (%)
    min_profit_factor: float = 1.2     # 최소 PF
    min_trades: int = 50               # 최소 거래 수
    max_param_count: int = 8           # 최대 파라미터 수 (과적합 방지)
    require_wf_pass: bool = True       # Walk-forward 통과 필수
    max_same_type: int = 2             # 동일 유형 최대 개수


@dataclass
class ScoredCandidate:
    """점수가 매겨진 후보"""
    candidate: StrategyCandidate
    score: float = 0.0
    rank: int = 0
    rejection_reason: Optional[str] = None

    # 점수 구성
    return_score: float = 0.0
    sharpe_score: float = 0.0
    dd_score: float = 0.0
    trade_score: float = 0.0
    wf_score: float = 0.0
    complexity_penalty: float = 0.0
    diversity_bonus: float = 0.0


class StrategySelector:
    """전략 선별기"""

    def __init__(self, criteria: Optional[SelectionCriteria] = None):
        self.criteria = criteria or SelectionCriteria()

    def select(
        self,
        candidates: list[StrategyCandidate],
        max_results: int = 10,
    ) -> tuple[list[ScoredCandidate], list[ScoredCandidate]]:
        """
        후보 선별 및 랭킹.

        Returns:
            (selected, rejected): 선별된 후보와 탈락 후보
        """
        scored = [self._score_candidate(c) for c in candidates]

        # 필터링
        selected = []
        rejected = []

        for sc in scored:
            reason = self._check_criteria(sc.candidate)
            if reason:
                sc.rejection_reason = reason
                rejected.append(sc)
            else:
                selected.append(sc)

        # 다양성 보너스 적용
        selected = self._apply_diversity_bonus(selected)

        # 점수순 정렬
        selected.sort(key=lambda s: s.score, reverse=True)
        rejected.sort(key=lambda s: s.score, reverse=True)

        # 같은 유형 제한
        selected = self._enforce_type_limit(selected)

        # 상위 N개
        for i, s in enumerate(selected):
            s.rank = i + 1

        logger.info(
            "Strategy selection complete",
            total=len(candidates),
            selected=len(selected[:max_results]),
            rejected=len(rejected),
        )

        return selected[:max_results], rejected

    def _score_candidate(self, c: StrategyCandidate) -> ScoredCandidate:
        """후보 점수 계산"""
        sc = ScoredCandidate(candidate=c)

        # 수익률 점수 (0-30점)
        sc.return_score = min(c.return_pct / 20.0 * 30, 30)

        # Sharpe 점수 (0-25점)
        sc.sharpe_score = min(c.sharpe / 2.0 * 25, 25)

        # MDD 점수 (0-15점, 낮을수록 좋음)
        sc.dd_score = max(15 - c.max_dd, 0)

        # 거래 수 점수 (0-15점)
        sc.trade_score = min(c.total_trades / 100.0 * 15, 15)

        # Walk-forward 점수 (0-15점)
        if c.wf_passed:
            sc.wf_score = 15
        elif c.wf_oos_return is not None and c.wf_oos_return > 0:
            sc.wf_score = 8
        else:
            sc.wf_score = 0

        # 복잡도 페널티 (파라미터 8개 초과 시 감점)
        if c.param_count > self.criteria.max_param_count:
            sc.complexity_penalty = (c.param_count - self.criteria.max_param_count) * 2

        sc.score = (
            sc.return_score
            + sc.sharpe_score
            + sc.dd_score
            + sc.trade_score
            + sc.wf_score
            - sc.complexity_penalty
            + sc.diversity_bonus
        )

        return sc

    def _check_criteria(self, c: StrategyCandidate) -> Optional[str]:
        """필터 기준 체크. 탈락 시 사유 반환."""
        cr = self.criteria

        if c.return_pct < cr.min_return_pct:
            return f"Return {c.return_pct:.2f}% < {cr.min_return_pct}%"

        if c.sharpe < cr.min_sharpe:
            return f"Sharpe {c.sharpe:.2f} < {cr.min_sharpe}"

        if c.max_dd > cr.max_drawdown_pct:
            return f"MDD {c.max_dd:.2f}% > {cr.max_drawdown_pct}%"

        if c.win_rate < cr.min_win_rate:
            return f"WR {c.win_rate:.1f}% < {cr.min_win_rate}%"

        if c.profit_factor < cr.min_profit_factor:
            return f"PF {c.profit_factor:.2f} < {cr.min_profit_factor}"

        if c.total_trades < cr.min_trades:
            return f"Trades {c.total_trades} < {cr.min_trades}"

        if cr.require_wf_pass and not c.wf_passed:
            return "Walk-forward validation failed"

        return None

    def _apply_diversity_bonus(
        self, candidates: list[ScoredCandidate]
    ) -> list[ScoredCandidate]:
        """전략 유형 다양성 보너스"""
        type_counts: dict[StrategyTypeAI, int] = {}

        for sc in candidates:
            st = sc.candidate.strategy_type
            count = type_counts.get(st, 0)
            type_counts[st] = count + 1

            # 유형 내 첫 번째: +5점 보너스
            if count == 0:
                sc.diversity_bonus = 5.0
                sc.score += 5.0
            # 두 번째: 보너스 없음
            # 세 번째 이상: 페널티
            elif count >= 2:
                sc.diversity_bonus = -3.0
                sc.score -= 3.0

        return candidates

    def _enforce_type_limit(
        self, candidates: list[ScoredCandidate]
    ) -> list[ScoredCandidate]:
        """같은 유형 전략 수 제한"""
        type_counts: dict[StrategyTypeAI, int] = {}
        result = []

        for sc in candidates:
            st = sc.candidate.strategy_type
            count = type_counts.get(st, 0)

            if count < self.criteria.max_same_type:
                result.append(sc)
                type_counts[st] = count + 1
            else:
                sc.rejection_reason = f"Type limit: already {count} {st.value} strategies"

        return result
