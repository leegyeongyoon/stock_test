"""EdgeValidator - 전략 기대값 검증

최소 엣지(edge) 조건 검증:
- 기대수익(엣지) < 수수료+슬리피지 구조 방지
- 최소 기대값 0.3%
- 최소 Risk/Reward 1.5
- 히스토리 기반 승률 검증

진입 조건에 최소 기대수익/최소 변동성(ATR)/최소 오더북 깊이 같은 "엣지 하한" 추가
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

logger = structlog.get_logger()


@dataclass
class EdgeConfig:
    """엣지 검증 설정"""

    # 최소 기대값 (%)
    min_expected_edge_pct: float = 0.3  # 0.3%

    # 최소 Risk/Reward
    min_risk_reward: float = 1.5

    # 최소 승률 (히스토리 기반)
    min_win_rate: float = 0.45  # 45%

    # 히스토리 설정
    min_sample_size: int = 10  # 최소 표본 수
    lookback_days: int = 30  # 최근 30일

    # 기본 가정 승률 (히스토리 없을 때)
    assumed_win_rate: float = 0.50  # 50%

    # 수수료 + 슬리피지 예상 비용 (%)
    estimated_round_trip_cost_pct: float = 0.15  # 0.15% (왕복)


@dataclass
class TradeResult:
    """거래 결과 (기대값 계산용)"""

    symbol: str
    strategy_id: str
    pnl: float  # KRW
    pnl_pct: float  # %
    r_multiple: float  # R-multiple
    win: bool
    timestamp: datetime


@dataclass
class EdgeValidationResult:
    """엣지 검증 결과"""

    valid: bool
    reason: str
    expected_edge_pct: float
    risk_reward: float
    win_rate: float  # 사용된 승률 (히스토리 or 가정)
    sample_size: int  # 히스토리 표본 수


class EdgeValidator:
    """
    기대값 검증기

    책임:
    1. 진입 전 기대값 검증
    2. 히스토리 기반 승률/기대값 추적
    3. 기대값 미달 진입 차단
    """

    def __init__(self, config: Optional[EdgeConfig] = None) -> None:
        self._config = config or EdgeConfig()

        # 거래 히스토리: "symbol:strategy" -> list[TradeResult]
        self._trade_history: dict[str, list[TradeResult]] = {}

    @property
    def config(self) -> EdgeConfig:
        """현재 설정"""
        return self._config

    def validate_entry(
        self,
        symbol: str,
        strategy_id: str,
        stop_distance_pct: float,
        target_distance_pct: float,
    ) -> EdgeValidationResult:
        """
        진입 기대값 검증

        Args:
            symbol: 심볼
            strategy_id: 전략 ID
            stop_distance_pct: 스탑 거리 (%)
            target_distance_pct: 목표 거리 (%)

        Returns:
            EdgeValidationResult
        """
        cfg = self._config

        # Risk/Reward 계산
        risk_reward = 0.0
        if stop_distance_pct > 0:
            risk_reward = target_distance_pct / stop_distance_pct

        # R/R 검증
        if risk_reward < cfg.min_risk_reward:
            return EdgeValidationResult(
                valid=False,
                reason=f"Risk/Reward too low: {risk_reward:.2f} < {cfg.min_risk_reward:.2f}",
                expected_edge_pct=0,
                risk_reward=risk_reward,
                win_rate=0,
                sample_size=0,
            )

        # 히스토리 기반 승률 조회
        key = f"{symbol}:{strategy_id}"
        history = self._get_recent_history(key)
        sample_size = len(history)

        if sample_size >= cfg.min_sample_size:
            # 히스토리 기반 승률
            win_rate = sum(1 for t in history if t.win) / sample_size

            # 승률 검증
            if win_rate < cfg.min_win_rate:
                return EdgeValidationResult(
                    valid=False,
                    reason=f"Historical win rate too low: {win_rate*100:.1f}% < {cfg.min_win_rate*100:.1f}% (n={sample_size})",
                    expected_edge_pct=0,
                    risk_reward=risk_reward,
                    win_rate=win_rate,
                    sample_size=sample_size,
                )
        else:
            # 가정 승률 사용
            win_rate = cfg.assumed_win_rate

        # 기대값 계산
        # Edge = P(win) * Avg_win - P(loss) * Avg_loss - costs
        expected_edge = (
            win_rate * target_distance_pct
            - (1 - win_rate) * stop_distance_pct
            - cfg.estimated_round_trip_cost_pct
        )

        # 기대값 검증
        if expected_edge < cfg.min_expected_edge_pct:
            return EdgeValidationResult(
                valid=False,
                reason=f"Expected edge too low: {expected_edge:.2f}% < {cfg.min_expected_edge_pct:.2f}% (win_rate={win_rate*100:.0f}%)",
                expected_edge_pct=expected_edge,
                risk_reward=risk_reward,
                win_rate=win_rate,
                sample_size=sample_size,
            )

        # 검증 통과
        return EdgeValidationResult(
            valid=True,
            reason=f"Edge validated: {expected_edge:.2f}%, RR={risk_reward:.2f}, WR={win_rate*100:.0f}%",
            expected_edge_pct=expected_edge,
            risk_reward=risk_reward,
            win_rate=win_rate,
            sample_size=sample_size,
        )

    def validate_entry_simple(
        self,
        symbol: str,
        strategy_id: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
    ) -> EdgeValidationResult:
        """
        간단한 진입 기대값 검증 (가격 기반)

        Args:
            symbol: 심볼
            strategy_id: 전략 ID
            entry_price: 진입가
            stop_price: 스탑 가격
            target_price: 목표 가격

        Returns:
            EdgeValidationResult
        """
        if entry_price <= 0:
            return EdgeValidationResult(
                valid=False,
                reason="Invalid entry price",
                expected_edge_pct=0,
                risk_reward=0,
                win_rate=0,
                sample_size=0,
            )

        stop_distance_pct = abs(entry_price - stop_price) / entry_price * 100
        target_distance_pct = abs(target_price - entry_price) / entry_price * 100

        return self.validate_entry(
            symbol=symbol,
            strategy_id=strategy_id,
            stop_distance_pct=stop_distance_pct,
            target_distance_pct=target_distance_pct,
        )

    def record_trade_result(
        self,
        symbol: str,
        strategy_id: str,
        pnl: float,
        pnl_pct: float,
        r_multiple: float = 0.0,
    ) -> None:
        """
        거래 결과 기록

        Args:
            symbol: 심볼
            strategy_id: 전략 ID
            pnl: 손익 (KRW)
            pnl_pct: 손익률 (%)
            r_multiple: R-multiple
        """
        key = f"{symbol}:{strategy_id}"

        result = TradeResult(
            symbol=symbol,
            strategy_id=strategy_id,
            pnl=pnl,
            pnl_pct=pnl_pct,
            r_multiple=r_multiple,
            win=pnl > 0,
            timestamp=datetime.utcnow(),
        )

        if key not in self._trade_history:
            self._trade_history[key] = []

        self._trade_history[key].append(result)

        # 최근 N일만 유지
        cutoff = datetime.utcnow() - timedelta(days=self._config.lookback_days)
        self._trade_history[key] = [
            t for t in self._trade_history[key] if t.timestamp > cutoff
        ]

        logger.debug(
            "Trade result recorded for edge calculation",
            symbol=symbol,
            strategy=strategy_id,
            pnl=pnl,
            win=result.win,
            total_samples=len(self._trade_history[key]),
        )

    def _get_recent_history(self, key: str) -> list[TradeResult]:
        """최근 히스토리 조회"""
        if key not in self._trade_history:
            return []

        cutoff = datetime.utcnow() - timedelta(days=self._config.lookback_days)
        return [t for t in self._trade_history[key] if t.timestamp > cutoff]

    def get_strategy_stats(self, symbol: str, strategy_id: str) -> dict:
        """전략별 통계 조회"""
        key = f"{symbol}:{strategy_id}"
        history = self._get_recent_history(key)

        if not history:
            return {
                "symbol": symbol,
                "strategy_id": strategy_id,
                "sample_size": 0,
                "win_rate": None,
                "avg_pnl_pct": None,
                "avg_r_multiple": None,
                "total_pnl": 0,
            }

        wins = [t for t in history if t.win]
        losses = [t for t in history if not t.win]

        return {
            "symbol": symbol,
            "strategy_id": strategy_id,
            "sample_size": len(history),
            "win_rate": len(wins) / len(history) if history else 0,
            "win_count": len(wins),
            "loss_count": len(losses),
            "avg_pnl_pct": sum(t.pnl_pct for t in history) / len(history) if history else 0,
            "avg_win_pct": sum(t.pnl_pct for t in wins) / len(wins) if wins else 0,
            "avg_loss_pct": sum(t.pnl_pct for t in losses) / len(losses) if losses else 0,
            "avg_r_multiple": sum(t.r_multiple for t in history) / len(history) if history else 0,
            "total_pnl": sum(t.pnl for t in history),
        }

    def get_all_stats(self) -> dict:
        """모든 전략 통계 조회"""
        stats = {}
        for key in self._trade_history:
            parts = key.split(":")
            if len(parts) == 2:
                symbol, strategy = parts
                stats[key] = self.get_strategy_stats(symbol, strategy)
        return stats

    def get_recommended_targets(
        self,
        symbol: str,
        strategy_id: str,
        entry_price: float,
        stop_price: float,
    ) -> dict:
        """권장 목표가 계산"""
        cfg = self._config

        stop_distance = abs(entry_price - stop_price)
        stop_distance_pct = stop_distance / entry_price * 100

        # 최소 R/R을 만족하는 목표 거리
        min_target_distance = stop_distance * cfg.min_risk_reward
        min_target_price = entry_price + min_target_distance

        # 기대값 0.3%를 만족하는 목표 거리 (50% 승률 가정)
        # Edge = 0.5 * target - 0.5 * stop - 0.15 >= 0.3
        # target >= (0.3 + 0.15 + 0.5 * stop) / 0.5
        required_target_pct = (
            cfg.min_expected_edge_pct
            + cfg.estimated_round_trip_cost_pct
            + cfg.assumed_win_rate * stop_distance_pct
        ) / cfg.assumed_win_rate

        required_target_distance = entry_price * required_target_pct / 100
        required_target_price = entry_price + required_target_distance

        return {
            "entry_price": entry_price,
            "stop_price": stop_price,
            "stop_distance_pct": stop_distance_pct,
            "min_rr_target": {
                "price": min_target_price,
                "distance_pct": min_target_distance / entry_price * 100,
                "risk_reward": cfg.min_risk_reward,
            },
            "min_edge_target": {
                "price": required_target_price,
                "distance_pct": required_target_pct,
                "expected_edge_pct": cfg.min_expected_edge_pct,
            },
            "recommended_target": max(min_target_price, required_target_price),
        }


# 싱글톤 인스턴스
_edge_validator: Optional[EdgeValidator] = None


def get_edge_validator() -> EdgeValidator:
    """EdgeValidator 싱글톤 조회"""
    global _edge_validator
    if _edge_validator is None:
        _edge_validator = EdgeValidator()
    return _edge_validator


def init_edge_validator(config: Optional[EdgeConfig] = None) -> EdgeValidator:
    """EdgeValidator 초기화"""
    global _edge_validator
    _edge_validator = EdgeValidator(config=config)
    logger.info(
        "EdgeValidator initialized",
        min_expected_edge_pct=_edge_validator.config.min_expected_edge_pct,
        min_risk_reward=_edge_validator.config.min_risk_reward,
        min_win_rate=_edge_validator.config.min_win_rate,
    )
    return _edge_validator
