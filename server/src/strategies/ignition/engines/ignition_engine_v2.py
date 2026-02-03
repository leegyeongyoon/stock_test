"""
Ignition Engine V2 - 초반 1파 진입 전용

목적: 막 시작된 점화를 "초반"에 잡는다
핵심: 진입은 빠르되, 추격은 금지 (즉시 쏘는 구간을 쫓아가면 손실)

진입 조건:
1. 점화 감지 (vol_first_expansion + range_break)
2. Anti-Chase 통과 (dist_atr <= 0.9)
3. 마이크로 풀백 또는 분할 진입만 허용

DD Tier 4+ 에서 비활성화됨
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


@dataclass
class IgnitionSignalV2(EngineSignal):
    """Ignition Engine V2 신호"""

    # 1분 변화율
    change_1m_pct: float = 0
    change_5m_pct: float = 0

    # 거래량
    volume_ratio: float = 0  # 현재 / 평균

    # 마이크로 풀백 여부
    is_micro_pullback: bool = False
    pullback_depth_pct: float = 0

    def __post_init__(self):
        self.engine_type = EngineType.IGNITION


class IgnitionEngineV2(BaseEngine):
    """
    초반 1파 진입 엔진

    특징:
    - 돌파 직후 진입
    - 마이크로 풀백에서만 진입 허용 (추격 금지)
    - dist_atr <= 0.9 필수
    - DD Tier 4+ 에서 OFF
    """

    # 진입 조건 임계값
    MIN_CHANGE_1M_PCT = 1.5  # 1분 최소 변화율 (%)
    MIN_VOLUME_RATIO = 3.0  # 최소 거래량 비율 (평균 대비)
    MAX_DIST_ATR = 0.9  # 최대 dist_atr (추격 금지)
    MAX_DIST_BREAKOUT_MULT = 0.6  # breakout 거리 * ATR% 상한

    # 마이크로 풀백 조건
    MICRO_PULLBACK_MAX_DEPTH = 0.3  # 돌파 후 최대 30% 되돌림까지 허용

    def __init__(self) -> None:
        self._settings = get_settings()
        self._candle_manager = get_candle_manager()
        self._stats = {
            "signals_generated": 0,
            "signals_passed": 0,
            "blocked_by_dist_atr": 0,
            "blocked_by_volume": 0,
            "blocked_by_change": 0,
            "blocked_by_dd_tier": 0,
        }

    def get_engine_type(self) -> EngineType:
        return EngineType.IGNITION

    def detect_signal(
        self,
        symbol: str,
        market_data: dict,
        candles_1m: list[Candle],
    ) -> Optional[IgnitionSignalV2]:
        """
        초반 1파 신호 감지

        조건:
        1. 1분 변화율 >= 1.5%
        2. 거래량 >= 평균 3배
        3. dist_atr <= 0.9 (추격 금지)
        4. 마이크로 풀백 패턴 (선택적)
        """
        self._stats["signals_generated"] += 1

        current_price = market_data.get("price", 0)
        if current_price <= 0 or len(candles_1m) < 5:
            return None

        # === 1분/5분 변화율 계산 ===
        price_1m_ago = candles_1m[-2].close if len(candles_1m) >= 2 else current_price
        price_5m_ago = candles_1m[-5].close if len(candles_1m) >= 5 else current_price

        change_1m_pct = (
            (current_price - price_1m_ago) / price_1m_ago * 100
            if price_1m_ago > 0
            else 0
        )
        change_5m_pct = (
            (current_price - price_5m_ago) / price_5m_ago * 100
            if price_5m_ago > 0
            else 0
        )

        # 조건 1: 1분 변화율
        if change_1m_pct < self.MIN_CHANGE_1M_PCT:
            return None

        # === 거래량 체크 ===
        volumes = [c.volume for c in candles_1m]
        if len(volumes) >= 5:
            recent_vol = volumes[-1]
            avg_vol = sum(volumes[:-1]) / len(volumes[:-1])
            volume_ratio = recent_vol / avg_vol if avg_vol > 0 else 0
        else:
            volume_ratio = 0

        # 조건 2: 거래량
        if volume_ratio < self.MIN_VOLUME_RATIO:
            self._stats["blocked_by_volume"] += 1
            return None

        # === 구조 지표 계산 ===
        vwap_5m = self._calc_vwap_5m(candles_1m)
        breakout_level = self._get_breakout_level(candles_1m)
        atr_5m = self._calc_atr_5m(candles_1m)

        # 거리 계산
        dist_breakout, dist_vwap, dist_atr = self.calculate_distances(
            current_price, breakout_level, vwap_5m, atr_5m
        )

        # === 조건 3: Anti-Chase (거리 기반) ===
        max_dist_atr = getattr(
            self._settings, "structure_chase_dist_atr_max", self.MAX_DIST_ATR
        )
        max_dist_breakout_mult = getattr(
            self._settings,
            "structure_chase_dist_breakout_max",
            self.MAX_DIST_BREAKOUT_MULT,
        )

        atr_pct = atr_5m / current_price if current_price > 0 else 0.01

        if dist_atr > max_dist_atr:
            self._stats["blocked_by_dist_atr"] += 1
            logger.debug(
                "Ignition blocked - dist_atr exceeded",
                symbol=symbol,
                dist_atr=f"{dist_atr:.2f}",
                max=max_dist_atr,
            )
            return None

        if dist_breakout > max_dist_breakout_mult * atr_pct:
            self._stats["blocked_by_dist_atr"] += 1
            logger.debug(
                "Ignition blocked - dist_breakout exceeded",
                symbol=symbol,
                dist_breakout=f"{dist_breakout:.4f}",
            )
            return None

        # === 마이크로 풀백 체크 (선택적 가산점) ===
        is_micro_pullback = self._check_micro_pullback(candles_1m, breakout_level)
        pullback_depth_pct = self._calc_pullback_depth(candles_1m, breakout_level)

        # === 손절/목표가 계산 ===
        stop_loss = self.calculate_stop_loss(
            current_price, breakout_level, vwap_5m, atr_5m
        )
        tp1, tp2 = self.calculate_take_profits(current_price, stop_loss)

        # === 신뢰도 계산 ===
        confidence = self._calculate_confidence(
            change_1m_pct=change_1m_pct,
            volume_ratio=volume_ratio,
            dist_atr=dist_atr,
            is_micro_pullback=is_micro_pullback,
        )

        self._stats["signals_passed"] += 1

        signal = IgnitionSignalV2(
            symbol=symbol,
            engine_type=EngineType.IGNITION,
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
            reason="ignition_detected",
            change_1m_pct=change_1m_pct,
            change_5m_pct=change_5m_pct,
            volume_ratio=volume_ratio,
            is_micro_pullback=is_micro_pullback,
            pullback_depth_pct=pullback_depth_pct,
        )

        logger.info(
            "Ignition signal detected",
            symbol=symbol,
            change_1m=f"{change_1m_pct:.2f}%",
            volume_ratio=f"{volume_ratio:.1f}x",
            dist_atr=f"{dist_atr:.2f}",
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
        Ignition 진입 가능 여부

        - DD Tier 4+ 에서 OFF
        - daily_loss -1.0% 이하에서 OFF
        """
        max_dd_tier = getattr(self._settings, "ignition_disable_dd_tier", 4)

        if dd_tier >= max_dd_tier:
            self._stats["blocked_by_dd_tier"] += 1
            return False, f"DD Tier {dd_tier} >= {max_dd_tier}, Ignition disabled"

        if daily_loss_pct <= -0.01:  # -1.0%
            return False, "Daily loss -1.0%, SAFE mode - Ignition disabled"

        if daily_loss_pct <= -0.015:  # -1.5%
            return False, "Daily loss -1.5%, HALT mode"

        return True, "OK"

    def calculate_stop_loss(
        self,
        entry_price: float,
        breakout_level: float,
        vwap_5m: float,
        atr_5m: float,
    ) -> float:
        """
        초반 1파 손절가 계산

        공식: stop = min(vwap_5m - k*ATR, breakout_level - buffer)
        초반 1파는 손절이 짧아야 비중이 커도 MDD를 지킬 수 있음
        """
        k = 0.5  # ATR 배수
        buffer = 0.003  # 0.3% 버퍼

        stop_vwap = vwap_5m - k * atr_5m
        stop_breakout = breakout_level * (1 - buffer)

        # 더 높은 손절가 선택 (손실 제한)
        stop_loss = max(stop_vwap, stop_breakout)

        # 최소/최대 손절폭 적용
        min_stop = entry_price * 0.995  # -0.5%
        max_stop = entry_price * 0.98  # -2.0%

        stop_loss = min(stop_loss, min_stop)  # 최소 0.5% 손절
        stop_loss = max(stop_loss, max_stop)  # 최대 2.0% 손절

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
        """돌파 레벨 (최근 5분 고점)"""
        if len(candles) < 5:
            return candles[-1].high if candles else 0

        return max(c.high for c in candles[-5:])

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

    def _check_micro_pullback(
        self, candles: list[Candle], breakout_level: float
    ) -> bool:
        """
        마이크로 풀백 패턴 체크

        돌파 직후 1~2개 봉 되돌림에서 진입하는 패턴
        """
        if len(candles) < 3:
            return False

        # 최근 3봉 중 돌파 후 약간 되돌림이 있었는지
        broke_above = candles[-3].high > breakout_level or candles[-2].high > breakout_level
        pulled_back = candles[-1].low < candles[-2].high

        return broke_above and pulled_back

    def _calc_pullback_depth(
        self, candles: list[Candle], breakout_level: float
    ) -> float:
        """되돌림 깊이 계산 (0-1)"""
        if len(candles) < 3:
            return 0

        # 돌파 후 고점
        peak = max(c.high for c in candles[-3:])
        current = candles[-1].close

        if peak <= breakout_level:
            return 0

        move = peak - breakout_level
        pullback = peak - current

        return pullback / move if move > 0 else 0

    def _calculate_confidence(
        self,
        change_1m_pct: float,
        volume_ratio: float,
        dist_atr: float,
        is_micro_pullback: bool,
    ) -> float:
        """신뢰도 계산 (0-1)"""
        confidence = 0.5  # 기본

        # 변화율 가산점 (1.5~3% 사이가 최적)
        if 1.5 <= change_1m_pct <= 3.0:
            confidence += 0.15
        elif change_1m_pct > 3.0:
            confidence += 0.05  # 너무 빠르면 감점

        # 거래량 가산점 (3~6배가 최적)
        if 3.0 <= volume_ratio <= 6.0:
            confidence += 0.15
        elif volume_ratio > 6.0:
            confidence += 0.05  # 너무 높으면 과열

        # dist_atr 가산점 (낮을수록 좋음)
        if dist_atr <= 0.5:
            confidence += 0.15
        elif dist_atr <= 0.7:
            confidence += 0.10

        # 마이크로 풀백 가산점
        if is_micro_pullback:
            confidence += 0.10

        return min(confidence, 1.0)

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
        }


# 싱글톤
_ignition_engine_v2: Optional[IgnitionEngineV2] = None


def get_ignition_engine_v2() -> IgnitionEngineV2:
    """IgnitionEngineV2 싱글톤"""
    global _ignition_engine_v2
    if _ignition_engine_v2 is None:
        _ignition_engine_v2 = IgnitionEngineV2()
    return _ignition_engine_v2
