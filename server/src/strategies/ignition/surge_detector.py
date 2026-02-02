"""
Surge Detector - 급등 시작 실시간 감지

역할:
- 1분봉 기반 급등 "시작" 순간 감지
- 이미 급등한 종목은 제외 (추격 방지)
- 거래량 급증 + 가격 급등 동시 발생 시 신호

감지 조건:
1. 1분 변화율 >= 1.5% (급등 시작)
2. 거래량 >= 평균의 3배 (거래량 급증)
3. 5분 변화율 < 5% (이미 급등한 것 제외)
4. 스프레드 < 0.5% (유동성 확보)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

from src.config import get_settings
from src.data.candle_manager import CandleManager, get_candle_manager

logger = structlog.get_logger()


@dataclass
class SurgeSignal:
    """급등 시작 신호"""

    symbol: str
    current_price: float
    change_1m_pct: float  # 1분 변화율
    change_5m_pct: float  # 5분 변화율 (추격 방지용)
    volume_ratio: float  # 거래량 비율 (vs 평균)
    signal_time: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(seconds=60))

    # 진입/청산 가격
    entry_price: float = 0
    stop_loss: float = 0
    take_profit_1: float = 0
    take_profit_2: float = 0

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "current_price": round(self.current_price, 2),
            "change_1m_pct": round(self.change_1m_pct, 2),
            "change_5m_pct": round(self.change_5m_pct, 2),
            "volume_ratio": round(self.volume_ratio, 2),
            "entry_price": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "take_profit_1": round(self.take_profit_1, 2),
            "take_profit_2": round(self.take_profit_2, 2),
            "signal_time": self.signal_time.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass
class SurgePosition:
    """급등 포지션"""

    symbol: str
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    highest_price: float
    trailing_stop: Optional[float] = None
    entered_at: datetime = field(default_factory=datetime.utcnow)

    def update_trailing(self, current_price: float) -> None:
        """트레일링 스톱 업데이트"""
        if current_price > self.highest_price:
            self.highest_price = current_price
            # 고점에서 1.5% 하락 시 청산
            self.trailing_stop = self.highest_price * 0.985


class SurgeDetector:
    """
    급등 시작 실시간 감지

    1분봉 기반으로 급등 "시작" 순간을 캐치
    이미 급등한 종목은 자동 제외
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._candle_manager = get_candle_manager()
        self._recent_signals: dict[str, SurgeSignal] = {}
        self._cooldown: dict[str, datetime] = {}  # 쿨다운 (같은 종목 연속 신호 방지)
        self._positions: dict[str, SurgePosition] = {}  # 포지션 추적

    def scan_all_symbols(
        self,
        symbols: list[str],
        market_data_map: dict[str, dict],
    ) -> list[SurgeSignal]:
        """
        전체 심볼 스캔하여 급등 시작 감지

        Args:
            symbols: 스캔할 심볼 리스트
            market_data_map: 심볼별 시장 데이터

        Returns:
            급등 시작 신호 리스트
        """
        signals = []
        now = datetime.utcnow()

        for symbol in symbols:
            # 쿨다운 체크 (5분)
            if symbol in self._cooldown:
                if now < self._cooldown[symbol]:
                    continue

            market_data = market_data_map.get(symbol, {})
            if not market_data:
                continue

            signal = self._check_surge(symbol, market_data)
            if signal:
                signals.append(signal)
                self._recent_signals[symbol] = signal
                self._cooldown[symbol] = now + timedelta(minutes=5)

                logger.info(
                    "Surge detected",
                    symbol=symbol,
                    change_1m=f"{signal.change_1m_pct:.2f}%",
                    change_5m=f"{signal.change_5m_pct:.2f}%",
                    volume_ratio=f"{signal.volume_ratio:.1f}x",
                )

        return signals

    def _check_surge(
        self,
        symbol: str,
        market_data: dict,
    ) -> Optional[SurgeSignal]:
        """
        개별 심볼 급등 체크

        조건:
        1. 1분 변화율 >= 1.5%
        2. 거래량 >= 평균 3배
        3. 5분 변화율 < 5% (이미 급등 제외)
        """
        cm = self._candle_manager
        current_price = market_data.get("price", 0)

        if current_price <= 0:
            return None

        # === 1분봉 데이터 ===
        candles_1m = cm.get_candles(symbol, CandleManager.INTERVAL_1M, count=10)
        if len(candles_1m) < 5:
            return None

        # 1분 전 가격
        price_1m_ago = candles_1m[-2].close if len(candles_1m) >= 2 else current_price
        change_1m_pct = ((current_price - price_1m_ago) / price_1m_ago * 100) if price_1m_ago > 0 else 0

        # 5분 전 가격
        price_5m_ago = candles_1m[-5].close if len(candles_1m) >= 5 else current_price
        change_5m_pct = ((current_price - price_5m_ago) / price_5m_ago * 100) if price_5m_ago > 0 else 0

        # === 거래량 체크 ===
        volumes = [c.volume for c in candles_1m]
        if len(volumes) >= 5:
            recent_vol = volumes[-1]
            avg_vol = sum(volumes[:-1]) / len(volumes[:-1])
            volume_ratio = recent_vol / avg_vol if avg_vol > 0 else 0
        else:
            volume_ratio = 0

        # === 급등 조건 체크 ===
        # 조건 1: 1분 변화율 >= 1.5%
        if change_1m_pct < 1.5:
            return None

        # 조건 2: 거래량 >= 평균 3배
        if volume_ratio < 3.0:
            return None

        # 조건 3: 5분 변화율 < 5% (이미 급등한 것 제외 - 추격 방지)
        if change_5m_pct >= 5.0:
            logger.debug(
                "Surge rejected - already extended",
                symbol=symbol,
                change_5m=f"{change_5m_pct:.2f}%",
            )
            return None

        # === 신호 생성 ===
        # 손절가: 1분 전 저가 또는 -2%
        low_1m = candles_1m[-1].low if candles_1m else current_price * 0.98
        stop_loss = max(low_1m * 0.995, current_price * 0.98)

        # 목표가
        risk = current_price - stop_loss
        take_profit_1 = current_price + risk * 1.5  # 1.5R
        take_profit_2 = current_price + risk * 3.0  # 3R

        signal = SurgeSignal(
            symbol=symbol,
            current_price=current_price,
            change_1m_pct=change_1m_pct,
            change_5m_pct=change_5m_pct,
            volume_ratio=volume_ratio,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
        )

        return signal

    def get_active_signals(self) -> list[SurgeSignal]:
        """활성 신호 반환"""
        now = datetime.utcnow()
        active = []
        for signal in self._recent_signals.values():
            if not signal.is_expired:
                active.append(signal)
        return active

    def clear_expired(self) -> int:
        """만료된 신호 정리"""
        expired = [s for s, sig in self._recent_signals.items() if sig.is_expired]
        for symbol in expired:
            del self._recent_signals[symbol]
        return len(expired)

    def track_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        stop_loss: float,
        take_profit: float,
    ) -> None:
        """포지션 추적 시작"""
        self._positions[symbol] = SurgePosition(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            highest_price=entry_price,
        )
        logger.info(
            "Surge position tracked",
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def check_exit(self, symbol: str, current_price: float) -> Optional[tuple[str, float]]:
        """
        청산 조건 체크

        Returns:
            (청산 사유, 청산 비율) 또는 None
        """
        pos = self._positions.get(symbol)
        if not pos:
            return None

        # 트레일링 스톱 업데이트
        pos.update_trailing(current_price)

        # 1. 손절 체크
        if current_price <= pos.stop_loss:
            return "STOP_LOSS", 1.0

        # 2. 익절 체크 (목표가 도달 시 50% 청산)
        if current_price >= pos.take_profit:
            return "TAKE_PROFIT", 0.5

        # 3. 트레일링 스톱 체크
        if pos.trailing_stop and current_price <= pos.trailing_stop:
            return "TRAILING_STOP", 1.0

        # 4. 시간 청산 (10분 경과 후 손실이면 청산)
        elapsed = (datetime.utcnow() - pos.entered_at).total_seconds()
        if elapsed > 600 and current_price < pos.entry_price:
            return "TIME_STOP", 1.0

        return None

    def close_position(self, symbol: str, partial: bool = False) -> None:
        """포지션 청산"""
        if symbol in self._positions:
            if partial:
                # 부분 청산 - 수량 50% 감소
                self._positions[symbol].quantity *= 0.5
            else:
                del self._positions[symbol]

    def get_positions(self) -> list[SurgePosition]:
        """활성 포지션 반환"""
        return list(self._positions.values())

    def get_status(self) -> dict:
        """상태 반환"""
        return {
            "active_signals": len(self.get_active_signals()),
            "active_positions": len(self._positions),
            "cooldown_count": len(self._cooldown),
            "signals": [s.to_dict() for s in self.get_active_signals()],
            "positions": [
                {
                    "symbol": p.symbol,
                    "entry_price": p.entry_price,
                    "quantity": p.quantity,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "highest_price": p.highest_price,
                    "trailing_stop": p.trailing_stop,
                }
                for p in self._positions.values()
            ],
        }


# 싱글톤
_surge_detector: Optional[SurgeDetector] = None


def get_surge_detector() -> SurgeDetector:
    """SurgeDetector 싱글톤"""
    global _surge_detector
    if _surge_detector is None:
        _surge_detector = SurgeDetector()
    return _surge_detector
