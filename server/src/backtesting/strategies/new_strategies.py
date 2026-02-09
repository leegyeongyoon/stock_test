"""고승률 10개 전략 (v27) - SMA/EMA/RVOL/ATR 전용

Available indicators on HistoryProvider:
- calc_sma(symbol, interval, period)
- calc_ema(symbol, interval, period)
- calc_rvol(symbol, interval, period)
- calc_atr(symbol, interval, period)
- get_candles/closes/highs/lows/volumes
"""

from datetime import datetime
from typing import Optional

from src.backtesting.data.data_loader import HistoryProvider


class EmaBouncScalpSignalGenerator:
    """
    Strategy 1: EMA_BOUNCE_SCALP (Target 68% WR)

    Buy when price touches EMA20 from above during uptrend.
    - Price crosses below EMA20 by < 1% (shallow dip)
    - EMA20 > EMA50 (uptrend confirmed)
    - RVOL > 1.5x
    - Previous candle was bullish (bounce starting)
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.max_dip_below_ema = self.params.get("max_dip_below_ema", 0.01)
        self.min_rvol = self.params.get("min_rvol", 1.5)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 1)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 60)
        if len(candles) < 50:
            return None

        price = candles[-1].close

        # EMA20, EMA50
        ema20 = history.calc_ema(symbol, "5m", 20)
        ema50 = history.calc_ema(symbol, "5m", 50)
        if not ema20 or not ema50:
            return None

        # Uptrend: EMA20 > EMA50
        if ema20 <= ema50:
            return None

        # Shallow dip: price is 0~1% below EMA20
        dip_pct = (ema20 - price) / ema20 if ema20 > 0 else 0
        if dip_pct < 0 or dip_pct > self.max_dip_below_ema:
            return None  # price is above EMA20 or too far below

        # Previous candle bullish (bounce starting)
        if len(candles) >= 2:
            prev = candles[-2]
            if prev.close <= prev.open:
                return None

        # RVOL
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # Score
        trend_strength = (ema20 - ema50) / ema50 * 100 if ema50 > 0 else 0
        score = 60 + min(trend_strength, 5) * 4 + min(rvol, 5) * 3

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "EMA_BOUNCE_SCALP",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "ema20": ema20,
                "ema50": ema50,
                "dip_pct": dip_pct,
                "rvol": rvol,
            },
        }


class DoubleDipBuySignalGenerator:
    """
    Strategy 2: DOUBLE_DIP_BUY (Target 65% WR)

    Improved dip buy with double confirmation.
    - 5m dip: -2.0% or worse
    - Within 2% of 24h low (tighter)
    - Price > EMA50 (not in downtrend)
    - RVOL > 3.0x
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_dip_pct = self.params.get("min_dip_pct", 0.020)
        self.support_tolerance = self.params.get("support_tolerance", 0.02)
        self.min_rvol = self.params.get("min_rvol", 3.0)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 1)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles_short = history.get_candles(symbol, "5m", 10)
        candles_24h = history.get_candles(symbol, "5m", 288)
        if len(candles_short) < 3 or len(candles_24h) < 100:
            return None

        price = candles_short[-1].close
        prev_price = candles_short[-2].close

        # 5m dip check
        dip_pct = (price - prev_price) / prev_price if prev_price > 0 else 0
        if dip_pct > -self.min_dip_pct:
            return None

        # Within 2% of 24h low
        low_24h = min(c.low for c in candles_24h)
        distance_from_low = (price - low_24h) / low_24h if low_24h > 0 else 1
        if distance_from_low > self.support_tolerance:
            return None

        # Price > EMA50 (not in downtrend)
        ema50 = history.calc_ema(symbol, "5m", 50)
        if not ema50 or price <= ema50:
            return None

        # RVOL
        rvol = history.calc_rvol(symbol, "5m", 10)
        if not rvol or rvol < self.min_rvol:
            return None

        score = 60 + abs(dip_pct) * 800 + (1 - distance_from_low / self.support_tolerance) * 20 + min(rvol, 6) * 3

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "DOUBLE_DIP_BUY",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "dip_pct": dip_pct,
                "low_24h": low_24h,
                "distance_from_low": distance_from_low,
                "ema50": ema50,
                "rvol": rvol,
            },
        }


class TightRangeBreakoutSignalGenerator:
    """
    Strategy 3: TIGHT_RANGE_BREAKOUT (Target 62% WR)

    Volatility squeeze breakout.
    - ATR(14) < ATR(50) * 0.7 (volatility compressed)
    - Price breaks above highest high of last 12 candles
    - RVOL > 2.5x
    - Breakout size > 1.0%
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.atr_squeeze_ratio = self.params.get("atr_squeeze_ratio", 0.7)
        self.min_breakout_pct = self.params.get("min_breakout_pct", 0.010)
        self.min_rvol = self.params.get("min_rvol", 2.5)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 1)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 60)
        if len(candles) < 51:
            return None

        price = candles[-1].close

        # ATR squeeze: ATR(14) < ATR(50) * 0.7
        atr14 = history.calc_atr(symbol, "5m", 14)
        # calc_atr needs period+1 candles, for period=50 we need 51
        # We compute ATR50 manually from candles
        atr_candles = candles[-51:]
        true_ranges_50 = []
        for i in range(1, len(atr_candles)):
            h = atr_candles[i].high
            lo = atr_candles[i].low
            pc = atr_candles[i - 1].close
            tr = max(h - lo, abs(h - pc), abs(lo - pc))
            true_ranges_50.append(tr)
        atr50 = sum(true_ranges_50[-50:]) / min(len(true_ranges_50), 50) if true_ranges_50 else None

        if not atr14 or not atr50 or atr50 <= 0:
            return None
        if atr14 >= atr50 * self.atr_squeeze_ratio:
            return None  # Not compressed enough

        # Breakout: price > highest high of last 12 candles (excluding current)
        recent_12 = candles[-13:-1]
        highest_12 = max(c.high for c in recent_12)
        breakout_pct = (price - highest_12) / highest_12 if highest_12 > 0 else 0
        if breakout_pct < self.min_breakout_pct:
            return None

        # RVOL
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        squeeze_ratio = atr14 / atr50 if atr50 > 0 else 1
        score = 55 + (1 - squeeze_ratio) * 40 + breakout_pct * 500 + min(rvol, 6) * 3

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "TIGHT_RANGE_BREAKOUT",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "atr14": atr14,
                "atr50": atr50,
                "squeeze_ratio": squeeze_ratio,
                "breakout_pct": breakout_pct,
                "rvol": rvol,
            },
        }


class SupportTouchBounceSignalGenerator:
    """
    Strategy 4: SUPPORT_TOUCH_BOUNCE (Target 70% WR)

    Buy exactly at support with tight entry.
    - Price within 0.5% of 24h low (precise support touch)
    - Current candle has long lower wick (>60% of range)
    - Close > Open (bullish reversal candle)
    - RVOL > 2.0x
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.support_tolerance = self.params.get("support_tolerance", 0.005)
        self.min_wick_ratio = self.params.get("min_wick_ratio", 0.60)
        self.min_rvol = self.params.get("min_rvol", 2.0)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 1)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles_short = history.get_candles(symbol, "5m", 10)
        candles_24h = history.get_candles(symbol, "5m", 288)
        if len(candles_short) < 3 or len(candles_24h) < 100:
            return None

        current = candles_short[-1]
        price = current.close

        # 24h low
        low_24h = min(c.low for c in candles_24h)
        distance_from_low = (price - low_24h) / low_24h if low_24h > 0 else 1

        if distance_from_low > self.support_tolerance:
            return None

        # Long lower wick (>60% of candle range)
        candle_range = current.high - current.low
        if candle_range <= 0:
            return None
        lower_wick = min(current.open, current.close) - current.low
        wick_ratio = lower_wick / candle_range
        if wick_ratio < self.min_wick_ratio:
            return None

        # Bullish candle
        if current.close <= current.open:
            return None

        # RVOL
        rvol = history.calc_rvol(symbol, "5m", 10)
        if not rvol or rvol < self.min_rvol:
            return None

        score = 70 + (1 - distance_from_low / self.support_tolerance) * 20 + wick_ratio * 10 + min(rvol, 5) * 3

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "SUPPORT_TOUCH_BOUNCE",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "low_24h": low_24h,
                "distance_from_low": distance_from_low,
                "wick_ratio": wick_ratio,
                "rvol": rvol,
            },
        }


class VolumeClimaxReversalSignalGenerator:
    """
    Strategy 5: VOLUME_CLIMAX_REVERSAL (Target 66% WR)

    Panic selling exhaustion.
    - RVOL > 5.0x (extreme volume spike)
    - Price drop > -3.0% (panic selling)
    - Current candle closes in top 30% of range (reversal starting)
    - Within 5% of 24h low
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_rvol = self.params.get("min_rvol", 5.0)
        self.min_drop_pct = self.params.get("min_drop_pct", 0.030)
        self.close_position_threshold = self.params.get("close_position_threshold", 0.30)
        self.max_distance_from_low = self.params.get("max_distance_from_low", 0.05)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 1)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles_short = history.get_candles(symbol, "5m", 10)
        candles_24h = history.get_candles(symbol, "5m", 288)
        if len(candles_short) < 3 or len(candles_24h) < 100:
            return None

        current = candles_short[-1]
        price = current.close
        prev_price = candles_short[-2].close

        # RVOL extreme
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # Price drop > -3%
        drop_pct = (price - prev_price) / prev_price if prev_price > 0 else 0
        if drop_pct > -self.min_drop_pct:
            return None

        # Close in top 30% of candle range
        candle_range = current.high - current.low
        if candle_range <= 0:
            return None
        close_position = (current.close - current.low) / candle_range
        if close_position < (1 - self.close_position_threshold):
            return None  # Close not in top 30%

        # Within 5% of 24h low
        low_24h = min(c.low for c in candles_24h)
        distance_from_low = (price - low_24h) / low_24h if low_24h > 0 else 1
        if distance_from_low > self.max_distance_from_low:
            return None

        score = 60 + abs(drop_pct) * 500 + min(rvol, 10) * 3 + close_position * 15

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "VOLUME_CLIMAX_REVERSAL",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "drop_pct": drop_pct,
                "rvol": rvol,
                "close_position": close_position,
                "distance_from_low": distance_from_low,
            },
        }


class EmaCrossoverMomentumSignalGenerator:
    """
    Strategy 6: EMA_CROSSOVER_MOMENTUM (Target 58% WR)

    Fast EMA crosses slow EMA with volume.
    - EMA10 crosses above EMA30 (golden cross)
    - Both EMAs sloping upward (momentum confirmed)
    - RVOL > 2.0x
    - Price > EMA50 (overall uptrend)
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_rvol = self.params.get("min_rvol", 2.0)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 1)
        # Store previous EMA values for cross detection
        self._prev_ema10: dict[str, float] = {}
        self._prev_ema30: dict[str, float] = {}

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 60)
        if len(candles) < 50:
            return None

        price = candles[-1].close

        # Current EMAs
        ema10 = history.calc_ema(symbol, "5m", 10)
        ema30 = history.calc_ema(symbol, "5m", 30)
        ema50 = history.calc_ema(symbol, "5m", 50)
        if not ema10 or not ema30 or not ema50:
            return None

        # Golden cross: EMA10 > EMA30 now
        if ema10 <= ema30:
            self._prev_ema10[symbol] = ema10
            self._prev_ema30[symbol] = ema30
            return None

        # Cross detection: previous EMA10 <= EMA30 (just crossed)
        prev_e10 = self._prev_ema10.get(symbol)
        prev_e30 = self._prev_ema30.get(symbol)
        self._prev_ema10[symbol] = ema10
        self._prev_ema30[symbol] = ema30

        # If no previous data or wasn't below before, check recent cross
        if prev_e10 is not None and prev_e30 is not None:
            if prev_e10 > prev_e30:
                return None  # Already crossed before, not a new cross
        else:
            # First time - check if EMA10 just barely above EMA30
            ema_diff = (ema10 - ema30) / ema30 if ema30 > 0 else 0
            if ema_diff > 0.005:  # If too far above, it's not a recent cross
                return None

        # Both EMAs sloping upward: approximate by checking EMA vs SMA
        sma10 = history.calc_sma(symbol, "5m", 10)
        sma30 = history.calc_sma(symbol, "5m", 30)
        if not sma10 or not sma30:
            return None
        if ema10 <= sma10 or ema30 <= sma30:
            return None  # Not sloping up

        # Price > EMA50
        if price <= ema50:
            return None

        # RVOL
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        ema_diff_pct = (ema10 - ema30) / ema30 * 100 if ema30 > 0 else 0
        score = 55 + ema_diff_pct * 20 + min(rvol, 5) * 4

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "EMA_CROSSOVER_MOMENTUM",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "ema10": ema10,
                "ema30": ema30,
                "ema50": ema50,
                "rvol": rvol,
            },
        }


class AtrExpansionEntrySignalGenerator:
    """
    Strategy 7: ATR_EXPANSION_ENTRY (Target 64% WR)

    Enter when volatility expands from low.
    - ATR(14) increases by >30% vs ATR(14) 3 candles ago
    - Price breaks above EMA20
    - RVOL > 2.5x
    - Price making new 24h high
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.atr_expansion_pct = self.params.get("atr_expansion_pct", 0.30)
        self.min_rvol = self.params.get("min_rvol", 2.5)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 1)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 288)
        if len(candles) < 20:
            return None

        price = candles[-1].close

        # Current ATR(14)
        atr_now = history.calc_atr(symbol, "5m", 14)
        if not atr_now:
            return None

        # ATR 3 candles ago: compute from candles[:-3]
        older_candles = candles[:-3]
        if len(older_candles) < 15:
            return None
        true_ranges_old = []
        for i in range(1, len(older_candles)):
            h = older_candles[i].high
            lo = older_candles[i].low
            pc = older_candles[i - 1].close
            tr = max(h - lo, abs(h - pc), abs(lo - pc))
            true_ranges_old.append(tr)
        atr_old = sum(true_ranges_old[-14:]) / min(len(true_ranges_old), 14) if true_ranges_old else None

        if not atr_old or atr_old <= 0:
            return None

        atr_expansion = (atr_now - atr_old) / atr_old
        if atr_expansion < self.atr_expansion_pct:
            return None

        # Price > EMA20
        ema20 = history.calc_ema(symbol, "5m", 20)
        if not ema20 or price <= ema20:
            return None

        # RVOL
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # New 24h high
        highs_24h = [c.high for c in candles[-288:]] if len(candles) >= 288 else [c.high for c in candles]
        prev_high = max(highs_24h[:-1]) if len(highs_24h) > 1 else 0
        if price <= prev_high:
            return None

        score = 55 + atr_expansion * 40 + min(rvol, 6) * 3

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "ATR_EXPANSION_ENTRY",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "atr_now": atr_now,
                "atr_old": atr_old,
                "atr_expansion": atr_expansion,
                "ema20": ema20,
                "rvol": rvol,
            },
        }


class MicroPullbackLongSignalGenerator:
    """
    Strategy 8: MICRO_PULLBACK_LONG (Target 67% WR)

    Tiny pullback in strong uptrend.
    - Price > EMA20 > EMA50 (strong uptrend)
    - Price pullback -0.8% to -1.5% from recent high
    - RVOL > 1.5x
    - Bullish candle appears (close > open)
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_pullback_pct = self.params.get("min_pullback_pct", 0.008)
        self.max_pullback_pct = self.params.get("max_pullback_pct", 0.015)
        self.min_rvol = self.params.get("min_rvol", 1.5)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 1)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 60)
        if len(candles) < 50:
            return None

        current = candles[-1]
        price = current.close

        # Strong uptrend: Price > EMA20 > EMA50
        ema20 = history.calc_ema(symbol, "5m", 20)
        ema50 = history.calc_ema(symbol, "5m", 50)
        if not ema20 or not ema50:
            return None
        if not (price > ema20 > ema50):
            return None

        # Recent high (last 20 candles)
        recent_20 = candles[-20:]
        recent_high = max(c.high for c in recent_20)

        # Pullback: -0.8% to -1.5%
        pullback_pct = (recent_high - price) / recent_high if recent_high > 0 else 0
        if pullback_pct < self.min_pullback_pct or pullback_pct > self.max_pullback_pct:
            return None

        # Bullish candle
        if current.close <= current.open:
            return None

        # RVOL
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        trend_strength = (ema20 - ema50) / ema50 * 100 if ema50 > 0 else 0
        score = 60 + min(trend_strength, 3) * 5 + pullback_pct * 1000 + min(rvol, 4) * 3

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "MICRO_PULLBACK_LONG",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "ema20": ema20,
                "ema50": ema50,
                "pullback_pct": pullback_pct,
                "rvol": rvol,
            },
        }


class TripleConfirmationEntrySignalGenerator:
    """
    Strategy 9: TRIPLE_CONFIRMATION_ENTRY (Target 71% WR)

    Maximum confirmation for highest reliability.
    - Price bounces from EMA50 (< 1% below)
    - Within 3% of 24h low
    - RVOL > 3.0x
    - Last 2 candles both bullish
    - ATR(14) > ATR(50) * 0.8 (some volatility present)
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.ema_bounce_tolerance = self.params.get("ema_bounce_tolerance", 0.01)
        self.support_tolerance = self.params.get("support_tolerance", 0.03)
        self.min_rvol = self.params.get("min_rvol", 3.0)
        self.min_atr_ratio = self.params.get("min_atr_ratio", 0.8)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 1)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 288)
        if len(candles) < 51:
            return None

        price = candles[-1].close

        # Condition 1: Price bounces from EMA50 (within 1% below)
        ema50 = history.calc_ema(symbol, "5m", 50)
        if not ema50:
            return None
        ema_distance = (ema50 - price) / ema50 if ema50 > 0 else 0
        if ema_distance < 0 or ema_distance > self.ema_bounce_tolerance:
            return None  # Price above EMA50 or too far below

        # Condition 2: Within 3% of 24h low
        candles_for_low = candles[-288:] if len(candles) >= 288 else candles
        low_24h = min(c.low for c in candles_for_low)
        distance_from_low = (price - low_24h) / low_24h if low_24h > 0 else 1
        if distance_from_low > self.support_tolerance:
            return None

        # Condition 3: RVOL > 3.0x
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # Condition 4: Last 2 candles both bullish
        if len(candles) < 2:
            return None
        c1, c2 = candles[-2], candles[-1]
        if c1.close <= c1.open or c2.close <= c2.open:
            return None

        # Condition 5: ATR(14) > ATR(50) * 0.8
        atr14 = history.calc_atr(symbol, "5m", 14)
        # Compute ATR50 manually
        atr_candles = candles[-51:]
        true_ranges = []
        for i in range(1, len(atr_candles)):
            h = atr_candles[i].high
            lo = atr_candles[i].low
            pc = atr_candles[i - 1].close
            tr = max(h - lo, abs(h - pc), abs(lo - pc))
            true_ranges.append(tr)
        atr50 = sum(true_ranges[-50:]) / min(len(true_ranges), 50) if true_ranges else None

        if not atr14 or not atr50 or atr50 <= 0:
            return None
        if atr14 < atr50 * self.min_atr_ratio:
            return None

        score = 75 + (1 - distance_from_low / self.support_tolerance) * 15 + min(rvol, 6) * 3

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "TRIPLE_CONFIRMATION_ENTRY",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "ema50": ema50,
                "ema_distance": ema_distance,
                "low_24h": low_24h,
                "distance_from_low": distance_from_low,
                "rvol": rvol,
                "atr14": atr14,
                "atr50": atr50,
            },
        }


class FastScalpReboundSignalGenerator:
    """
    Strategy 10: FAST_SCALP_REBOUND (Target 63% WR)

    Quick 1-2% bounce from any dip.
    - Any -1.5% or worse dip in single candle
    - RVOL > 2.5x
    - Price > SMA50 (not in downtrend)
    - Close > Open (immediate bounce)
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_dip_pct = self.params.get("min_dip_pct", 0.015)
        self.min_rvol = self.params.get("min_rvol", 2.5)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 1)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 60)
        if len(candles) < 50:
            return None

        current = candles[-1]
        price = current.close

        # Dip check: current candle open-to-low or prev_close-to-close
        prev_close = candles[-2].close if len(candles) >= 2 else current.open
        dip_pct = (current.low - prev_close) / prev_close if prev_close > 0 else 0
        if dip_pct > -self.min_dip_pct:
            return None  # Not a significant dip

        # Immediate bounce: Close > Open
        if current.close <= current.open:
            return None

        # RVOL
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # Price > SMA50 (trend filter)
        sma50 = history.calc_sma(symbol, "5m", 50)
        if not sma50 or price <= sma50:
            return None

        # Recovery strength
        recovery = (current.close - current.low) / (current.high - current.low) if (current.high - current.low) > 0 else 0

        score = 55 + abs(dip_pct) * 600 + min(rvol, 6) * 3 + recovery * 10

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "FAST_SCALP_REBOUND",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "dip_pct": dip_pct,
                "rvol": rvol,
                "sma50": sma50,
                "recovery": recovery,
            },
        }
