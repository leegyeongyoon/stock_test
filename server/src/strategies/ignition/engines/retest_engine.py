"""
Retest Engine - 리테스트 2파 진입 전용

목적: 1파 이후 되돌림에서 "안전하게" 2파를 탄다
핵심: 들어갈 때 이미 빨랐던 건 상관 없음. 대신 리테스트 구조가 나오면 진입

리테스트 정의 (둘 중 하나):
1. VWAP Retest: price가 vwap_5m ± 0.3*ATR 구간까지 되돌림
2. Breakout Level Retest: price가 breakout_level ± 0.25*ATR까지 되돌림

DD Tier 4+ 에서도 허용 (단, 사이징 축소)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

from src.config import get_settings
from src.data.candle_manager import Candle, CandleManager, get_candle_manager
from src.strategies.ignition.engines.base_engine import (
    BaseEngine,
    EngineSignal,
    EngineType,
)

logger = structlog.get_logger()


class RetestType(str):
    """리테스트 타입"""

    VWAP = "VWAP"  # VWAP 리테스트
    BREAKOUT = "BREAKOUT"  # 돌파레벨 리테스트
    BOTH = "BOTH"  # 둘 다


@dataclass
class RetestSignal(EngineSignal):
    """Retest Engine 신호"""

    # 리테스트 정보
    retest_type: str = RetestType.VWAP
    retest_zone_low: float = 0  # 리테스트 존 하단
    retest_zone_high: float = 0  # 리테스트 존 상단

    # 지지 확인 정보
    support_confirmed: bool = False
    bounce_bar_count: int = 0  # 반등봉 수
    volume_on_bounce: float = 0  # 반등 시 거래량 비율

    # 이전 고점 정보 (1파)
    previous_peak: float = 0
    pullback_depth_pct: float = 0  # 되돌림 깊이 (%)

    def __post_init__(self):
        self.engine_type = EngineType.RETEST


class RetestEngine(BaseEngine):
    """
    리테스트 2파 진입 엔진

    특징:
    - VWAP 또는 돌파레벨 근처로 되돌림 확인
    - 지지 확인봉 필요 (반등봉 종가가 위로 마감)
    - DD Tier 4+ 에서도 허용 (더 안전한 진입)
    - 손절이 더 넓으므로 사이징 축소 (×0.8)
    """

    # 리테스트 존 설정
    VWAP_ZONE_ATR_MULT = 0.3  # VWAP ± 0.3*ATR
    BREAKOUT_ZONE_ATR_MULT = 0.25  # Breakout ± 0.25*ATR

    # 지지 확인 조건
    MIN_BOUNCE_BARS = 1  # 최소 반등봉 수
    MIN_VOLUME_ON_BOUNCE = 1.0  # 반등 시 최소 거래량 비율

    def __init__(self) -> None:
        self._settings = get_settings()
        self._candle_manager = get_candle_manager()
        self._stats = {
            "signals_generated": 0,
            "signals_passed": 0,
            "blocked_by_no_retest": 0,
            "blocked_by_no_support": 0,
            "blocked_by_dd_tier": 0,
            "vwap_retests": 0,
            "breakout_retests": 0,
        }
        # 종목별 1파 고점 추적
        self._previous_peaks: dict[str, tuple[float, datetime]] = {}

    def get_engine_type(self) -> EngineType:
        return EngineType.RETEST

    def register_peak(self, symbol: str, peak_price: float) -> None:
        """1파 고점 등록 (Ignition Engine에서 호출)"""
        self._previous_peaks[symbol] = (peak_price, datetime.utcnow())
        logger.debug(
            "Peak registered for retest",
            symbol=symbol,
            peak_price=peak_price,
        )

    def detect_signal(
        self,
        symbol: str,
        market_data: dict,
        candles_1m: list[Candle],
    ) -> Optional[RetestSignal]:
        """
        리테스트 신호 감지

        조건:
        1. 이전에 급등(1파)이 있었음
        2. 현재 VWAP 또는 돌파레벨 근처로 되돌림
        3. 지지 확인봉 (반등 시작)
        """
        self._stats["signals_generated"] += 1

        current_price = market_data.get("price", 0)
        if current_price <= 0 or len(candles_1m) < 10:
            return None

        # === 구조 지표 계산 ===
        vwap_5m = self._calc_vwap_5m(candles_1m)
        breakout_level = self._get_breakout_level(candles_1m)
        atr_5m = self._calc_atr_5m(candles_1m)

        if atr_5m <= 0:
            atr_5m = current_price * 0.01  # 기본 1%

        # === 이전 고점 확인 ===
        previous_peak = self._get_previous_peak(symbol, candles_1m)
        if previous_peak <= breakout_level:
            # 1파가 없었음
            return None

        # === 리테스트 존 체크 ===
        retest_result = self._check_retest_zone(
            current_price=current_price,
            vwap_5m=vwap_5m,
            breakout_level=breakout_level,
            atr_5m=atr_5m,
        )

        if retest_result is None:
            self._stats["blocked_by_no_retest"] += 1
            return None

        retest_type, zone_low, zone_high = retest_result

        # === 지지 확인 ===
        support_confirmed, bounce_bars, volume_on_bounce = self._check_support_confirmation(
            candles_1m, zone_low, zone_high
        )

        if not support_confirmed:
            self._stats["blocked_by_no_support"] += 1
            return None

        # === 거리 계산 ===
        dist_breakout, dist_vwap, dist_atr = self.calculate_distances(
            current_price, breakout_level, vwap_5m, atr_5m
        )

        # === 되돌림 깊이 계산 ===
        pullback_depth_pct = (previous_peak - current_price) / previous_peak * 100

        # === 손절/목표가 계산 ===
        stop_loss = self.calculate_stop_loss(
            current_price, breakout_level, vwap_5m, atr_5m
        )
        tp1, tp2 = self.calculate_take_profits(current_price, stop_loss)

        # === 신뢰도 계산 ===
        confidence = self._calculate_confidence(
            retest_type=retest_type,
            pullback_depth_pct=pullback_depth_pct,
            bounce_bars=bounce_bars,
            volume_on_bounce=volume_on_bounce,
        )

        self._stats["signals_passed"] += 1
        if retest_type == RetestType.VWAP:
            self._stats["vwap_retests"] += 1
        else:
            self._stats["breakout_retests"] += 1

        signal = RetestSignal(
            symbol=symbol,
            engine_type=EngineType.RETEST,
            current_price=current_price,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            breakout_level=breakout_level,
            vwap_5m=vwap_5m,
            atr_5m=atr_5m,
            dist_breakout=dist_breakout,
            dist_vwap=dist_vwap,
            dist_atr=dist_atr,
            confidence=confidence,
            reason=f"retest_{retest_type.lower()}",
            retest_type=retest_type,
            retest_zone_low=zone_low,
            retest_zone_high=zone_high,
            support_confirmed=support_confirmed,
            bounce_bar_count=bounce_bars,
            volume_on_bounce=volume_on_bounce,
            previous_peak=previous_peak,
            pullback_depth_pct=pullback_depth_pct,
        )

        logger.info(
            "Retest signal detected",
            symbol=symbol,
            retest_type=retest_type,
            pullback_depth=f"{pullback_depth_pct:.2f}%",
            confidence=f"{confidence:.2f}",
        )

        return signal

    def can_enter(
        self,
        dd_tier: int,
        daily_loss_pct: float,
        is_aggressive_mode: bool,
    ) -> tuple[bool, str]:
        """
        Retest 진입 가능 여부

        - DD Tier 5까지 허용 (Ignition보다 완화)
        - daily_loss -1.5% 에서 HALT
        """
        max_dd_tier = getattr(self._settings, "retest_max_dd_tier", 5)

        if dd_tier > max_dd_tier:
            self._stats["blocked_by_dd_tier"] += 1
            return False, f"DD Tier {dd_tier} > {max_dd_tier}, Retest disabled"

        if daily_loss_pct <= -0.015:  # -1.5%
            return False, "Daily loss -1.5%, HALT mode"

        # -1.0% 에서도 Retest는 허용 (SAFE 모드에서 유일한 진입 가능)
        if daily_loss_pct <= -0.01:
            return True, "SAFE mode - Retest only (reduced size)"

        return True, "OK"

    def calculate_stop_loss(
        self,
        entry_price: float,
        breakout_level: float,
        vwap_5m: float,
        atr_5m: float,
    ) -> float:
        """
        리테스트 손절가 계산

        리테스트는 손절폭이 더 넓음 (지지 확인 후 진입이므로)
        - 리테스트 존 하단 아래로 손절
        """
        # 리테스트 존 하단
        vwap_zone_low = vwap_5m * (1 - self.VWAP_ZONE_ATR_MULT * atr_5m / vwap_5m)
        breakout_zone_low = breakout_level * (
            1 - self.BREAKOUT_ZONE_ATR_MULT * atr_5m / breakout_level
        )

        # 더 낮은 값 (더 넓은 손절)
        retest_low = min(vwap_zone_low, breakout_zone_low)

        # 손절 = 리테스트 존 하단 - 버퍼
        buffer = 0.005  # 0.5% 버퍼
        stop_loss = retest_low * (1 - buffer)

        # 최소/최대 손절폭 적용
        min_stop = entry_price * 0.99  # -1.0%
        max_stop = entry_price * 0.97  # -3.0%

        stop_loss = min(stop_loss, min_stop)
        stop_loss = max(stop_loss, max_stop)

        return stop_loss

    def _calc_vwap_5m(self, candles: list[Candle]) -> float:
        """5분 VWAP 계산"""
        if len(candles) < 5:
            return candles[-1].close if candles else 0

        recent = candles[-5:]
        total_pv = sum(c.close * c.volume for c in recent)
        total_v = sum(c.volume for c in recent)

        return total_pv / total_v if total_v > 0 else candles[-1].close

    def _get_breakout_level(self, candles: list[Candle]) -> float:
        """돌파 레벨 (최근 10분 고점 중 5분 전 고점)"""
        if len(candles) < 10:
            return candles[-5].high if len(candles) >= 5 else candles[-1].high

        # 5-10분 전 구간의 고점 (현재 급등 제외)
        return max(c.high for c in candles[-10:-3])

    def _calc_atr_5m(self, candles: list[Candle]) -> float:
        """5분 ATR 계산"""
        if len(candles) < 2:
            return 0

        true_ranges = []
        for i in range(1, min(6, len(candles))):
            tr = max(
                candles[i].high - candles[i].low,
                abs(candles[i].high - candles[i - 1].close),
                abs(candles[i].low - candles[i - 1].close),
            )
            true_ranges.append(tr)

        return sum(true_ranges) / len(true_ranges) if true_ranges else 0

    def _get_previous_peak(
        self, symbol: str, candles: list[Candle]
    ) -> float:
        """이전 고점 조회 (등록된 것 또는 캔들에서 추정)"""
        # 등록된 고점 확인
        if symbol in self._previous_peaks:
            peak_price, peak_time = self._previous_peaks[symbol]
            # 1시간 이내의 고점만 유효
            if (datetime.utcnow() - peak_time).total_seconds() < 3600:
                return peak_price

        # 캔들에서 추정 (최근 15봉 고점)
        if len(candles) >= 15:
            return max(c.high for c in candles[-15:-3])

        return 0

    def _check_retest_zone(
        self,
        current_price: float,
        vwap_5m: float,
        breakout_level: float,
        atr_5m: float,
    ) -> Optional[tuple[str, float, float]]:
        """
        리테스트 존 체크

        Returns:
            (retest_type, zone_low, zone_high) 또는 None
        """
        # VWAP 존
        vwap_zone_low = vwap_5m - self.VWAP_ZONE_ATR_MULT * atr_5m
        vwap_zone_high = vwap_5m + self.VWAP_ZONE_ATR_MULT * atr_5m

        # Breakout 존
        breakout_zone_low = breakout_level - self.BREAKOUT_ZONE_ATR_MULT * atr_5m
        breakout_zone_high = breakout_level + 0.4 * atr_5m  # 상단은 조금 더 넓게

        in_vwap_zone = vwap_zone_low <= current_price <= vwap_zone_high
        in_breakout_zone = breakout_zone_low <= current_price <= breakout_zone_high

        if in_vwap_zone and in_breakout_zone:
            return RetestType.BOTH, breakout_zone_low, vwap_zone_high
        elif in_vwap_zone:
            return RetestType.VWAP, vwap_zone_low, vwap_zone_high
        elif in_breakout_zone:
            return RetestType.BREAKOUT, breakout_zone_low, breakout_zone_high

        return None

    def _check_support_confirmation(
        self,
        candles: list[Candle],
        zone_low: float,
        zone_high: float,
    ) -> tuple[bool, int, float]:
        """
        지지 확인 체크

        조건:
        - 지지 구간에서 반등봉 종가가 위로 마감
        - 거래량이 유지

        Returns:
            (confirmed, bounce_bars, volume_ratio)
        """
        if len(candles) < 3:
            return False, 0, 0

        # 최근 3봉 중 지지 확인
        bounce_bars = 0
        volumes = []

        for i in range(-3, 0):
            candle = candles[i]

            # 저점이 존 내에 있었고, 종가가 그 위로 마감
            touched_zone = candle.low <= zone_high
            closed_above = candle.close > candle.open  # 양봉

            if touched_zone and closed_above:
                bounce_bars += 1
                volumes.append(candle.volume)

        # 최소 반등봉 수 확인
        if bounce_bars < self.MIN_BOUNCE_BARS:
            return False, bounce_bars, 0

        # 반등 시 거래량 확인
        avg_volume = sum(c.volume for c in candles[-10:-3]) / 7 if len(candles) >= 10 else 1
        volume_on_bounce = sum(volumes) / len(volumes) / avg_volume if volumes and avg_volume > 0 else 0

        confirmed = volume_on_bounce >= self.MIN_VOLUME_ON_BOUNCE

        return confirmed, bounce_bars, volume_on_bounce

    def _calculate_confidence(
        self,
        retest_type: str,
        pullback_depth_pct: float,
        bounce_bars: int,
        volume_on_bounce: float,
    ) -> float:
        """신뢰도 계산 (0-1)"""
        confidence = 0.5  # 기본

        # 리테스트 타입 (BOTH가 가장 강함)
        if retest_type == RetestType.BOTH:
            confidence += 0.15
        elif retest_type == RetestType.BREAKOUT:
            confidence += 0.10
        else:
            confidence += 0.05

        # 되돌림 깊이 (30-50%가 최적)
        if 30 <= pullback_depth_pct <= 50:
            confidence += 0.15
        elif 20 <= pullback_depth_pct < 30:
            confidence += 0.10
        elif pullback_depth_pct > 50:
            confidence -= 0.05  # 너무 깊은 되돌림

        # 반등봉 수
        if bounce_bars >= 2:
            confidence += 0.10
        elif bounce_bars == 1:
            confidence += 0.05

        # 반등 시 거래량
        if volume_on_bounce >= 1.5:
            confidence += 0.10

        return min(max(confidence, 0), 1.0)

    def get_stats(self) -> dict:
        """엔진 통계"""
        return {
            "engine_type": self.get_engine_type().value,
            **self._stats,
            "pass_rate": (
                self._stats["signals_passed"] / self._stats["signals_generated"]
                if self._stats["signals_generated"] > 0
                else 0
            ),
            "tracked_peaks": len(self._previous_peaks),
        }


# 싱글톤
_retest_engine: Optional[RetestEngine] = None


def get_retest_engine() -> RetestEngine:
    """RetestEngine 싱글톤"""
    global _retest_engine
    if _retest_engine is None:
        _retest_engine = RetestEngine()
    return _retest_engine
