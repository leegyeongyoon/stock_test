"""
Ignition Engine - 점화 감지

역할:
- Watchlist 종목을 1분봉 기준 실시간 모니터링
- 점화 조건 감지:
  1. Range High 돌파 (ATR 0.5배 이상)
  2. Volume Expansion (평균 대비 2배 이상)
  3. Anti-Chase 필터 통과
- 점화 신호 생성 및 관리
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.data.candle_manager import CandleManager, get_candle_manager
from src.strategies.ignition.setup_engine import SetupCandidate

logger = structlog.get_logger()


class IgnitionType(str, Enum):
    """점화 유형"""

    BREAKOUT = "BREAKOUT"  # 레인지 상단 돌파
    VOLUME_SURGE = "VOLUME_SURGE"  # 거래량 급증
    BOTH = "BOTH"  # 돌파 + 거래량


@dataclass
class IgnitionSignal:
    """점화 신호"""

    symbol: str
    ignition_type: IgnitionType
    trigger_price: float
    current_price: float
    range_high: float
    range_low: float
    atr_1h: float
    breakout_atr_mult: float  # 돌파 강도 (ATR 배수)
    volume_expansion_ratio: float  # 거래량 확장 비율
    entry_price: float  # 권장 진입가
    stop_loss: float  # 손절가
    r_target_1: float  # 1R 목표가
    r_target_2: float  # 2R 목표가
    signal_time: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(seconds=120))
    is_valid: bool = True
    invalidation_reason: Optional[str] = None

    @property
    def risk_per_share(self) -> float:
        """주당 리스크 (진입가 - 손절가)"""
        return abs(self.entry_price - self.stop_loss)

    @property
    def is_expired(self) -> bool:
        """신호 만료 여부"""
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "ignition_type": self.ignition_type.value,
            "trigger_price": round(self.trigger_price, 2),
            "current_price": round(self.current_price, 2),
            "range_high": round(self.range_high, 2),
            "range_low": round(self.range_low, 2),
            "atr_1h": round(self.atr_1h, 4),
            "breakout_atr_mult": round(self.breakout_atr_mult, 2),
            "volume_expansion_ratio": round(self.volume_expansion_ratio, 2),
            "entry_price": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "r_target_1": round(self.r_target_1, 2),
            "r_target_2": round(self.r_target_2, 2),
            "risk_per_share": round(self.risk_per_share, 4),
            "signal_time": self.signal_time.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "time_remaining_sec": max(
                0, (self.expires_at - datetime.utcnow()).total_seconds()
            ),
            "is_valid": self.is_valid,
            "invalidation_reason": self.invalidation_reason,
        }


class IgnitionEngine:
    """
    Ignition Engine

    역할:
    - Watchlist 종목 실시간 모니터링 (1분봉)
    - 점화 조건 감지 및 신호 생성
    - 신호 유효성 관리
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._candle_manager = get_candle_manager()
        self._active_signals: dict[str, IgnitionSignal] = {}
        self._signal_history: list[IgnitionSignal] = []
        self._last_check: Optional[datetime] = None

    def check_ignition(
        self,
        candidate: SetupCandidate,
        market_data: dict,
    ) -> Optional[IgnitionSignal]:
        """
        점화 조건 확인

        Args:
            candidate: Watchlist 후보 종목
            market_data: 실시간 시장 데이터

        Returns:
            점화 신호 (조건 미충족시 None)
        """
        settings = self._settings
        cm = self._candle_manager
        symbol = candidate.symbol

        # 이미 활성 신호가 있으면 스킵
        if symbol in self._active_signals:
            existing = self._active_signals[symbol]
            if not existing.is_expired and existing.is_valid:
                return None

        current_price = market_data.get("price", 0)
        if current_price <= 0:
            return None

        range_high = candidate.range_high
        range_low = candidate.range_low
        atr_1h = candidate.atr_1h

        # === 조건 1: Range High 돌파 확인 ===
        breakout_threshold = range_high + (atr_1h * settings.ignition_range_break_atr_mult)
        is_breakout = current_price >= breakout_threshold

        # 돌파 강도 (ATR 배수)
        if current_price > range_high:
            breakout_atr_mult = (current_price - range_high) / atr_1h if atr_1h > 0 else 0
        else:
            breakout_atr_mult = 0

        # === 조건 2: Volume Expansion 확인 ===
        volumes = cm.get_volumes(symbol, CandleManager.INTERVAL_1M, 20)
        if len(volumes) >= 20:
            recent_vol = volumes[-1] if volumes else 0
            avg_vol = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 1
            volume_expansion_ratio = recent_vol / avg_vol if avg_vol > 0 else 0
        else:
            volume_expansion_ratio = 0

        vol_expansion_min = settings.ignition_volume_expansion_mult
        is_volume_expansion = volume_expansion_ratio >= vol_expansion_min

        # 점화 유형 결정
        if is_breakout and is_volume_expansion:
            ignition_type = IgnitionType.BOTH
        elif is_breakout:
            ignition_type = IgnitionType.BREAKOUT
        elif is_volume_expansion and current_price >= range_high * 0.995:
            # 거래량 급증 + 레인지 근처
            ignition_type = IgnitionType.VOLUME_SURGE
        else:
            # 점화 조건 미충족
            return None

        # === 점화 신호 생성 ===
        entry_price = current_price

        # 손절가: Range Low 또는 ATR 기반 (더 타이트한 것 선택)
        atr_stop = entry_price - (atr_1h * 1.5)
        range_stop = range_low - (atr_1h * settings.ignition_stop_buffer_pct)
        stop_loss = max(atr_stop, range_stop)  # 더 높은 것 = 더 타이트한 손절

        # R 계산
        risk_per_share = entry_price - stop_loss
        if risk_per_share <= 0:
            risk_per_share = atr_1h  # fallback

        r_target_1 = entry_price + risk_per_share
        r_target_2 = entry_price + (risk_per_share * 2)

        # 신호 만료 시간
        entry_window_sec = settings.ignition_entry_window_sec
        expires_at = datetime.utcnow() + timedelta(seconds=entry_window_sec)

        signal = IgnitionSignal(
            symbol=symbol,
            ignition_type=ignition_type,
            trigger_price=breakout_threshold,
            current_price=current_price,
            range_high=range_high,
            range_low=range_low,
            atr_1h=atr_1h,
            breakout_atr_mult=breakout_atr_mult,
            volume_expansion_ratio=volume_expansion_ratio,
            entry_price=entry_price,
            stop_loss=stop_loss,
            r_target_1=r_target_1,
            r_target_2=r_target_2,
            expires_at=expires_at,
        )

        # 저장
        self._active_signals[symbol] = signal
        self._signal_history.append(signal)

        logger.info(
            "Ignition signal generated",
            symbol=symbol,
            ignition_type=ignition_type.value,
            entry_price=entry_price,
            stop_loss=stop_loss,
            breakout_atr_mult=breakout_atr_mult,
            volume_ratio=volume_expansion_ratio,
        )

        return signal

    def invalidate_signal(self, symbol: str, reason: str) -> bool:
        """
        점화 신호 무효화

        Args:
            symbol: 심볼
            reason: 무효화 사유

        Returns:
            무효화 성공 여부
        """
        if symbol not in self._active_signals:
            return False

        signal = self._active_signals[symbol]
        signal.is_valid = False
        signal.invalidation_reason = reason

        logger.info(
            "Ignition signal invalidated",
            symbol=symbol,
            reason=reason,
        )

        return True

    def get_signal(self, symbol: str) -> Optional[IgnitionSignal]:
        """특정 심볼의 활성 신호 반환"""
        signal = self._active_signals.get(symbol)
        if signal and signal.is_valid and not signal.is_expired:
            return signal
        return None

    def get_all_active_signals(self) -> list[IgnitionSignal]:
        """모든 활성 신호 반환"""
        active = []
        for signal in self._active_signals.values():
            if signal.is_valid and not signal.is_expired:
                active.append(signal)
        return active

    def cleanup_expired(self) -> int:
        """만료된 신호 정리"""
        expired_symbols = [
            s for s, sig in self._active_signals.items()
            if sig.is_expired or not sig.is_valid
        ]

        for symbol in expired_symbols:
            del self._active_signals[symbol]

        return len(expired_symbols)

    def check_over_extension(
        self,
        symbol: str,
        current_price: float,
    ) -> bool:
        """
        과도 확장 체크

        진입 후 너무 급등했으면 추가 진입 방지

        Args:
            symbol: 심볼
            current_price: 현재가

        Returns:
            과도 확장 여부
        """
        signal = self._active_signals.get(symbol)
        if not signal:
            return False

        settings = self._settings
        atr = signal.atr_1h
        max_extension = settings.ignition_max_extension_atr_mult

        # Range High 대비 확장 정도
        extension = (current_price - signal.range_high) / atr if atr > 0 else 0

        return extension > max_extension

    def get_status(self) -> dict:
        """현재 상태 반환"""
        active_signals = self.get_all_active_signals()

        return {
            "active_signal_count": len(active_signals),
            "total_signals_generated": len(self._signal_history),
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "active_signals": [s.to_dict() for s in active_signals],
        }


# 싱글톤 인스턴스
_ignition_engine: Optional[IgnitionEngine] = None


def get_ignition_engine() -> IgnitionEngine:
    """Ignition Engine 싱글톤"""
    global _ignition_engine
    if _ignition_engine is None:
        _ignition_engine = IgnitionEngine()
    return _ignition_engine
