"""TimeStopPolicy - 조건부 Time Stop 정책

핵심 변경:
- R > 0 이면 Time Stop 하지 않음 (수익 구간)
- R <= 0 이고 지정 시간 경과 시에만 청산
- 변동성 기반 시간 조절

기존 문제:
- Time stop(45분) 기반 청산이 "노이즈 절단기"로 동작
- 모멘텀/추세가 발현되기 전에 비용만 내고 종료
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.position.schemas import ManagedPosition, PositionState

logger = structlog.get_logger()


@dataclass
class TimeStopConfig:
    """Time Stop 설정"""

    # WEAK 상태 설정
    weak_time_min: int = 20  # WEAK 상태에서 20분 후 체크
    weak_min_r: float = 0.0  # R <= 0 일 때만 청산

    # NORMAL 상태 설정
    normal_time_min: int = 45  # NORMAL 상태에서 45분 후 체크
    normal_min_r: float = 0.0  # R <= 0 일 때만 청산

    # 변동성 조절 활성화
    volatility_adjustment: bool = True

    # 변동성별 시간 승수
    high_vol_time_multiplier: float = 1.5  # 고변동성: 시간 연장 (더 기다림)
    low_vol_time_multiplier: float = 0.8  # 저변동성: 시간 단축

    # 추가 조건
    allow_time_stop_in_profit: bool = False  # 수익 중 Time Stop 허용 (기본 False)
    min_time_before_stop: int = 10  # 최소 10분은 대기


@dataclass
class TimeStopResult:
    """Time Stop 판단 결과"""

    should_stop: bool
    reason: str
    r_pnl: float
    time_in_trade: float
    adjusted_threshold: float


class TimeStopPolicy:
    """
    조건부 Time Stop 정책

    핵심 로직:
    1. R > 0 (수익 중)이면 Time Stop 하지 않음
    2. R <= 0 이고 지정 시간 경과 시에만 청산
    3. 변동성에 따라 시간 조절
    """

    def __init__(self, config: Optional[TimeStopConfig] = None) -> None:
        self._config = config or TimeStopConfig()

        # 심볼별 변동성 캐시 (percentile 0-1)
        self._volatility_cache: dict[str, float] = {}

    @property
    def config(self) -> TimeStopConfig:
        """현재 설정"""
        return self._config

    def update_volatility(self, symbol: str, volatility_percentile: float) -> None:
        """심볼별 변동성 업데이트 (0-1 범위)"""
        self._volatility_cache[symbol] = max(0.0, min(1.0, volatility_percentile))

    def get_volatility(self, symbol: str) -> float:
        """심볼별 변동성 조회 (기본 0.5 = 중간)"""
        return self._volatility_cache.get(symbol, 0.5)

    def should_time_stop(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        entry_time: datetime,
        current_price: float,
        current_state: str,
        initial_stop_distance: float,
    ) -> TimeStopResult:
        """
        Time Stop 여부 판단

        Args:
            symbol: 심볼
            side: "LONG" or "SHORT"
            entry_price: 진입가
            entry_time: 진입 시간
            current_price: 현재가
            current_state: "WEAK", "NORMAL", "STRONG", "EXTREME"
            initial_stop_distance: 초기 스탑 거리 (가격)

        Returns:
            TimeStopResult
        """
        cfg = self._config

        # 거래 시간 계산 (분)
        time_in_trade = (datetime.utcnow() - entry_time).total_seconds() / 60

        # R-multiple 계산
        if initial_stop_distance > 0:
            pnl_per_unit = current_price - entry_price
            if side == "SHORT":
                pnl_per_unit = -pnl_per_unit
            r_pnl = pnl_per_unit / initial_stop_distance
        else:
            # 스탑 거리 없으면 가격 기준으로 계산
            r_pnl = (current_price - entry_price) / entry_price * 100
            if side == "SHORT":
                r_pnl = -r_pnl

        # 최소 대기 시간 체크
        if time_in_trade < cfg.min_time_before_stop:
            return TimeStopResult(
                should_stop=False,
                reason=f"Min wait time: {time_in_trade:.0f}min < {cfg.min_time_before_stop}min",
                r_pnl=r_pnl,
                time_in_trade=time_in_trade,
                adjusted_threshold=0,
            )

        # 수익 구간이면 Time Stop 안함 (핵심 변경)
        if r_pnl > 0 and not cfg.allow_time_stop_in_profit:
            return TimeStopResult(
                should_stop=False,
                reason=f"Profitable (R={r_pnl:.2f}), no time stop",
                r_pnl=r_pnl,
                time_in_trade=time_in_trade,
                adjusted_threshold=0,
            )

        # 변동성 기반 시간 조절
        volatility_pct = self.get_volatility(symbol)
        if cfg.volatility_adjustment:
            if volatility_pct > 0.7:  # 상위 30% 변동성
                time_multiplier = cfg.high_vol_time_multiplier
            elif volatility_pct < 0.3:  # 하위 30% 변동성
                time_multiplier = cfg.low_vol_time_multiplier
            else:
                time_multiplier = 1.0
        else:
            time_multiplier = 1.0

        # 상태별 Time Stop 체크
        if current_state == "WEAK":
            threshold_time = cfg.weak_time_min * time_multiplier
            threshold_r = cfg.weak_min_r

            if time_in_trade >= threshold_time and r_pnl <= threshold_r:
                return TimeStopResult(
                    should_stop=True,
                    reason=f"WEAK time stop: {time_in_trade:.0f}min >= {threshold_time:.0f}min, R={r_pnl:.2f} <= {threshold_r}",
                    r_pnl=r_pnl,
                    time_in_trade=time_in_trade,
                    adjusted_threshold=threshold_time,
                )

        elif current_state == "NORMAL":
            threshold_time = cfg.normal_time_min * time_multiplier
            threshold_r = cfg.normal_min_r

            if time_in_trade >= threshold_time and r_pnl <= threshold_r:
                return TimeStopResult(
                    should_stop=True,
                    reason=f"NORMAL time stop: {time_in_trade:.0f}min >= {threshold_time:.0f}min, R={r_pnl:.2f} <= {threshold_r}",
                    r_pnl=r_pnl,
                    time_in_trade=time_in_trade,
                    adjusted_threshold=threshold_time,
                )

        elif current_state in ("STRONG", "EXTREME"):
            # STRONG/EXTREME 상태에서는 Time Stop 안함
            return TimeStopResult(
                should_stop=False,
                reason=f"{current_state} state: no time stop, R={r_pnl:.2f}",
                r_pnl=r_pnl,
                time_in_trade=time_in_trade,
                adjusted_threshold=0,
            )

        # 기본: Time Stop 안함
        return TimeStopResult(
            should_stop=False,
            reason=f"No time stop triggered (time={time_in_trade:.0f}min, R={r_pnl:.2f})",
            r_pnl=r_pnl,
            time_in_trade=time_in_trade,
            adjusted_threshold=0,
        )


# 싱글톤 인스턴스
_time_stop_policy: Optional[TimeStopPolicy] = None


def get_time_stop_policy() -> TimeStopPolicy:
    """TimeStopPolicy 싱글톤 조회"""
    global _time_stop_policy
    if _time_stop_policy is None:
        _time_stop_policy = TimeStopPolicy()
    return _time_stop_policy


def init_time_stop_policy(config: Optional[TimeStopConfig] = None) -> TimeStopPolicy:
    """TimeStopPolicy 초기화"""
    global _time_stop_policy
    _time_stop_policy = TimeStopPolicy(config=config)
    logger.info(
        "TimeStopPolicy initialized",
        weak_time_min=_time_stop_policy.config.weak_time_min,
        normal_time_min=_time_stop_policy.config.normal_time_min,
        allow_profit_time_stop=_time_stop_policy.config.allow_time_stop_in_profit,
    )
    return _time_stop_policy
