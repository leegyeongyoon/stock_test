"""v3 라이브 전략 모듈 (v3.2 OpenAI 최적화 파라미터)

백테스트 +12.49%/30일 달성 3개 전략을 라이브 엔진용으로 변환.
candle_manager 기반 실시간 지표 계산.

전략:
1. VOLATILE_OVERSOLD_BOUNCE: ATR>1% + RSI<30 + RVOL>2.5 + 양봉
2. CRASH_RECOVERY: 30분-2%하락 + RSI<35 + RVOL>2.5 + 양봉
3. TRIPLE_BEARISH_REVERSAL: 3연속음봉 + RSI<30 + ATR>1%
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

logger = structlog.get_logger()


@dataclass
class V3Signal:
    """v3 전략 시그널"""
    symbol: str
    strategy: str
    score: float
    entry_price: float
    position_pct: float  # 자본 대비 비중 (0.10 = 10%)
    indicators: dict = field(default_factory=dict)


@dataclass
class V3Position:
    """v3 전략 포지션 추적"""
    symbol: str
    strategy: str
    entry_price: float
    quantity: float
    entry_time: datetime
    stop_loss_pct: float
    take_profit_pct: float
    trail_trigger_pct: float
    trail_stop_pct: float
    time_stop_minutes: int
    highest_price: float = 0.0
    trailing_active: bool = False


class V3BaseStrategy:
    """v3 전략 공통 베이스"""

    def __init__(self, name: str, cooldown_minutes: int):
        self.name = name
        self.cooldown_minutes = cooldown_minutes
        self._last_entry: dict[str, datetime] = {}
        self._positions: dict[str, V3Position] = {}

    def _check_cooldown(self, symbol: str, now: datetime) -> bool:
        """쿨다운 체크. True면 진입 가능."""
        if symbol in self._last_entry:
            elapsed = (now - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self.cooldown_minutes:
                return False
        return True

    def record_entry(self, symbol: str, now: datetime) -> None:
        self._last_entry[symbol] = now

    def track_position(self, pos: V3Position) -> None:
        self._positions[pos.symbol] = pos

    def get_position(self, symbol: str) -> Optional[V3Position]:
        return self._positions.get(symbol)

    def close_position(self, symbol: str) -> Optional[V3Position]:
        return self._positions.pop(symbol, None)

    def get_all_positions(self) -> dict[str, V3Position]:
        return dict(self._positions)

    @staticmethod
    def calc_rsi(closes: list[float], period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(-period, 0):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        return 100 - (100 / (1 + avg_gain / avg_loss))

    @staticmethod
    def calc_atr_pct(highs: list[float], lows: list[float], closes: list[float],
                     period: int = 14) -> Optional[float]:
        """ATR% 계산 (candle_manager closes/highs/lows 사용)"""
        n = len(closes)
        if n < period + 1:
            return None
        trs = []
        for i in range(-period, 0):
            h = highs[n + i]
            lo = lows[n + i]
            pc = closes[n + i - 1]
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        atr = sum(trs) / len(trs)
        price = closes[-1]
        return (atr / price * 100) if price > 0 else None

    def check_exit(self, symbol: str, current_price: float, now: datetime) -> Optional[dict]:
        """SL/TP/trailing/time_stop 통합 청산 체크"""
        pos = self._positions.get(symbol)
        if not pos:
            return None

        entry = pos.entry_price
        if entry <= 0:
            return None

        pnl_pct = (current_price - entry) / entry

        # 최고가 업데이트
        if current_price > pos.highest_price:
            pos.highest_price = current_price

        # 1. Stop Loss
        if pnl_pct <= pos.stop_loss_pct:
            return {"action": "FULL", "reason": f"SL hit ({pnl_pct:.2%})", "exit_type": "stop_loss"}

        # 2. Take Profit
        if pnl_pct >= pos.take_profit_pct:
            return {"action": "FULL", "reason": f"TP hit ({pnl_pct:.2%})", "exit_type": "take_profit"}

        # 3. Trailing Stop
        if pnl_pct >= pos.trail_trigger_pct:
            pos.trailing_active = True

        if pos.trailing_active and pos.highest_price > 0:
            drop_from_high = (pos.highest_price - current_price) / pos.highest_price
            if drop_from_high >= pos.trail_stop_pct:
                return {
                    "action": "FULL",
                    "reason": f"Trail stop ({drop_from_high:.2%} from high)",
                    "exit_type": "trailing",
                }

        # 4. Time Stop
        hold_minutes = (now - pos.entry_time).total_seconds() / 60
        if hold_minutes >= pos.time_stop_minutes:
            return {"action": "FULL", "reason": f"Time stop ({hold_minutes:.0f}min)", "exit_type": "time_stop"}

        return None


class VolatileOversoldBounce(V3BaseStrategy):
    """ATR>1% + RSI<30 + RVOL>2.5 + 양봉"""

    # v3.2 최적화 파라미터
    SL_PCT = float(os.environ.get("V3_VOB_SL_PCT", "-0.02"))
    TP_PCT = float(os.environ.get("V3_VOB_TP_PCT", "0.02"))
    TRAIL_TRIGGER = 0.015
    TRAIL_STOP = 0.008
    TIME_STOP_MIN = 180
    MIN_ATR_PCT = 1.0
    MAX_RSI = 30
    MIN_RVOL = 2.5
    POSITION_PCT = 0.10

    def __init__(self):
        super().__init__("VOLATILE_OVERSOLD_BOUNCE", cooldown_minutes=90)

    def scan(self, symbol: str, market_data: dict, candle_manager, now: datetime) -> Optional[V3Signal]:
        if not self._check_cooldown(symbol, now):
            return None

        price = market_data.get("price", 0)
        if price <= 0:
            return None

        # 5분봉 데이터
        closes = candle_manager.get_closes(symbol, "5m", 20) if candle_manager else []
        if len(closes) < 15:
            return None

        # 양봉 체크 (현재 봉)
        candles = candle_manager.get_candles(symbol, "5m", 2) if candle_manager else []
        if not candles or len(candles) < 1:
            return None
        last_candle = candles[-1]
        if hasattr(last_candle, 'close') and hasattr(last_candle, 'open'):
            if last_candle.close <= last_candle.open:
                return None
        elif isinstance(last_candle, dict):
            if last_candle.get("close", 0) <= last_candle.get("open", 0):
                return None

        # RSI
        rsi = self.calc_rsi(closes, 14)
        if rsi is None or rsi >= self.MAX_RSI:
            return None

        # ATR%
        atr_pct = candle_manager.calc_atr_percent(symbol, "5m", 14) if candle_manager else None
        if atr_pct is None or atr_pct < self.MIN_ATR_PCT:
            return None

        # RVOL
        rvol = market_data.get("rvol", 1.0)
        if rvol < self.MIN_RVOL:
            return None

        score = 60 + min(atr_pct, 3) * 5 + (30 - rsi) * 0.5 + min(rvol, 5) * 2

        return V3Signal(
            symbol=symbol,
            strategy=self.name,
            score=score,
            entry_price=price,
            position_pct=self.POSITION_PCT,
            indicators={"rsi": rsi, "atr_pct": atr_pct, "rvol": rvol},
        )


class CrashRecovery(V3BaseStrategy):
    """30분-2%하락 + RSI<35 + RVOL>2.5 + 양봉"""

    SL_PCT = float(os.environ.get("V3_CR_SL_PCT", "-0.015"))
    TP_PCT = float(os.environ.get("V3_CR_TP_PCT", "0.02"))
    TRAIL_TRIGGER = 0.015
    TRAIL_STOP = 0.005
    TIME_STOP_MIN = 120
    MIN_DROP_PCT = 0.02
    MAX_RSI = 35
    MIN_RVOL = 2.5
    POSITION_PCT = 0.10

    def __init__(self):
        super().__init__("CRASH_RECOVERY", cooldown_minutes=60)

    def scan(self, symbol: str, market_data: dict, candle_manager, now: datetime) -> Optional[V3Signal]:
        if not self._check_cooldown(symbol, now):
            return None

        price = market_data.get("price", 0)
        if price <= 0:
            return None

        # 5분봉 데이터
        closes = candle_manager.get_closes(symbol, "5m", 20) if candle_manager else []
        if len(closes) < 8:
            return None

        # 양봉 체크
        candles = candle_manager.get_candles(symbol, "5m", 2) if candle_manager else []
        if not candles or len(candles) < 1:
            return None
        last_candle = candles[-1]
        if hasattr(last_candle, 'close') and hasattr(last_candle, 'open'):
            if last_candle.close <= last_candle.open:
                return None
        elif isinstance(last_candle, dict):
            if last_candle.get("close", 0) <= last_candle.get("open", 0):
                return None

        # 30분(6봉) -2% 이상 하락
        if len(closes) < 7:
            return None
        price_6_ago = closes[-7]
        if price_6_ago <= 0:
            return None
        change_6 = (price - price_6_ago) / price_6_ago
        if change_6 > -self.MIN_DROP_PCT:
            return None

        # RSI
        rsi = self.calc_rsi(closes, 14)
        if rsi is not None and rsi >= self.MAX_RSI:
            return None

        # RVOL
        rvol = market_data.get("rvol", 1.0)
        if rvol < self.MIN_RVOL:
            return None

        drop_strength = min(abs(change_6) / 0.04, 1.0)
        score = 60 + drop_strength * 20 + min(rvol - 1, 5) * 3

        return V3Signal(
            symbol=symbol,
            strategy=self.name,
            score=score,
            entry_price=price,
            position_pct=self.POSITION_PCT,
            indicators={"change_6": change_6, "rsi": rsi, "rvol": rvol},
        )


class TripleBearishReversal(V3BaseStrategy):
    """3연속음봉 + RSI<30 + ATR>1%"""

    SL_PCT = float(os.environ.get("V3_TBR_SL_PCT", "-0.02"))
    TP_PCT = float(os.environ.get("V3_TBR_TP_PCT", "0.02"))
    TRAIL_TRIGGER = 0.020
    TRAIL_STOP = 0.010
    TIME_STOP_MIN = 180
    MAX_RSI = 30
    MIN_ATR_PCT = 1.0
    POSITION_PCT = 0.10

    def __init__(self):
        super().__init__("TRIPLE_BEARISH_REVERSAL", cooldown_minutes=30)

    def scan(self, symbol: str, market_data: dict, candle_manager, now: datetime) -> Optional[V3Signal]:
        if not self._check_cooldown(symbol, now):
            return None

        price = market_data.get("price", 0)
        if price <= 0:
            return None

        # 5분봉 데이터
        candles = candle_manager.get_candles(symbol, "5m", 20) if candle_manager else []
        if len(candles) < 5:
            return None

        # 직전 3봉 연속 음봉
        bearish_count = 0
        for j in range(1, 4):
            idx = len(candles) - 1 - j
            if idx < 0:
                break
            c = candles[idx]
            c_close = c.close if hasattr(c, 'close') else c.get("close", 0)
            c_open = c.open if hasattr(c, 'open') else c.get("open", 0)
            if c_close < c_open:
                bearish_count += 1
        if bearish_count < 3:
            return None

        # RSI
        closes = candle_manager.get_closes(symbol, "5m", 20) if candle_manager else []
        if len(closes) < 15:
            return None
        rsi = self.calc_rsi(closes, 14)
        if rsi is None or rsi >= self.MAX_RSI:
            return None

        # ATR%
        atr_pct = candle_manager.calc_atr_percent(symbol, "5m", 14) if candle_manager else None
        if atr_pct is None or atr_pct < self.MIN_ATR_PCT:
            return None

        score = 60 + bearish_count * 5 + (30 - rsi) * 0.5 + min(atr_pct, 3) * 5

        return V3Signal(
            symbol=symbol,
            strategy=self.name,
            score=score,
            entry_price=price,
            position_pct=self.POSITION_PCT,
            indicators={"rsi": rsi, "atr_pct": atr_pct, "bearish_count": bearish_count},
        )


# === Singleton access ===

_v3_strategies: Optional[list[V3BaseStrategy]] = None


def get_v3_strategies() -> list[V3BaseStrategy]:
    """v3 전략 인스턴스 목록 반환 (싱글톤)"""
    global _v3_strategies
    if _v3_strategies is None:
        _v3_strategies = [
            VolatileOversoldBounce(),
            CrashRecovery(),
            TripleBearishReversal(),
        ]
    return _v3_strategies
