"""고승률 10개 전략 (v27 개선) - SMA/EMA/RVOL/ATR 전용

Available indicators on HistoryProvider:
- calc_sma(symbol, interval, period)
- calc_ema(symbol, interval, period)
- calc_rvol(symbol, interval, period)
- calc_atr(symbol, interval, period)
- get_candles/closes/highs/lows/volumes

v27 개선사항:
- RVOL 조건 완화 (3.0~5.0 → 1.0~2.5)
- 딥/하락 기준 완화 (-2~3% → -0.5~1.5%)
- 스탑로스 확대 (-0.4~1.0% → -1.5~2.5%)
- 복합 조건 수 축소 (5개 → 2~3개)
- 쿨다운 강화 (1분 → 15~30분)
- 포지션 사이즈 축소 (0.20 → 0.10)
- SMA200 기반 시장 방향 필터 추가
"""

from datetime import datetime
from typing import Optional

from src.backtesting.data.data_loader import HistoryProvider


def check_market_direction_filter(
    history: HistoryProvider, symbol: str, price: float
) -> bool:
    """SMA200 기반 시장 방향 필터.

    하락장에서 LONG 진입 금지: price < SMA200 * 0.97 이면 False 반환
    """
    sma200 = history.calc_sma(symbol, "5m", 200)
    if sma200 and price < sma200 * 0.97:
        return False
    return True


class EmaBouncScalpSignalGenerator:
    """
    Strategy 1: EMA_BOUNCE_SCALP (v27 개선)

    Buy when price touches EMA20 from above during uptrend.
    - Price within 0.5% of EMA20 (완화: 기존 1%)
    - RVOL > 1.0x (완화: 기존 1.5x)
    - Previous candle was bullish (bounce starting)
    - SL: -1.5% / TP: +1.5%
    - 쿨다운: 15분 (기존 1분)
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.max_dip_below_ema = self.params.get("max_dip_below_ema", 0.005)  # 완화: 0.01→0.005
        self.min_rvol = self.params.get("min_rvol", 1.0)  # 완화: 1.5→1.0
        self.position_pct = self.params.get("position_pct", 0.10)  # 축소: 0.20→0.10
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 15)  # 강화: 1→15

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

        candles = history.get_candles(symbol, "5m", 210)  # SMA200용 확장
        if len(candles) < 50:
            return None

        price = candles[-1].close

        # 시장 방향 필터
        if not check_market_direction_filter(history, symbol, price):
            return None

        # EMA20
        ema20 = history.calc_ema(symbol, "5m", 20)
        if not ema20:
            return None

        # Shallow dip: price is 0~0.5% below EMA20 (완화된 조건)
        dip_pct = (ema20 - price) / ema20 if ema20 > 0 else 0
        if dip_pct < 0 or dip_pct > self.max_dip_below_ema:
            return None

        # Previous candle bullish (bounce starting)
        if len(candles) >= 2:
            prev = candles[-2]
            if prev.close <= prev.open:
                return None

        # RVOL (완화된 조건)
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # Score
        ema50 = history.calc_ema(symbol, "5m", 50)
        trend_strength = (ema20 - ema50) / ema50 * 100 if ema50 and ema50 > 0 else 0
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
                "dip_pct": dip_pct,
                "rvol": rvol,
            },
        }


class DoubleDipBuySignalGenerator:
    """
    Strategy 2: DOUBLE_DIP_BUY (v27 개선)

    Improved dip buy with relaxed confirmation.
    - 5m dip: -0.8% or worse (완화: 기존 -2.0%)
    - Within 5% of 24h low (완화: 기존 2%)
    - RVOL > 1.5x (완화: 기존 3.0x)
    - SL: -2.0% / TP: +1.5%
    - 쿨다운: 15분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_dip_pct = self.params.get("min_dip_pct", 0.008)  # 완화: 0.020→0.008
        self.support_tolerance = self.params.get("support_tolerance", 0.05)  # 완화: 0.02→0.05
        self.min_rvol = self.params.get("min_rvol", 1.5)  # 완화: 3.0→1.5
        self.position_pct = self.params.get("position_pct", 0.10)  # 축소: 0.20→0.10
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 15)  # 강화: 1→15

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

        # 시장 방향 필터
        if not check_market_direction_filter(history, symbol, price):
            return None

        # 5m dip check (완화된 조건)
        dip_pct = (price - prev_price) / prev_price if prev_price > 0 else 0
        if dip_pct > -self.min_dip_pct:
            return None

        # Within 5% of 24h low (완화된 조건)
        low_24h = min(c.low for c in candles_24h)
        distance_from_low = (price - low_24h) / low_24h if low_24h > 0 else 1
        if distance_from_low > self.support_tolerance:
            return None

        # RVOL (완화된 조건)
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
                "rvol": rvol,
            },
        }


class TightRangeBreakoutSignalGenerator:
    """
    Strategy 3: TIGHT_RANGE_BREAKOUT (v27 개선)

    Volatility squeeze breakout.
    - ATR(14) < ATR(50) * 0.85 (완화: 기존 0.7)
    - Price breaks above highest high of last 12 candles by 0.5%+ (완화: 기존 1.0%)
    - RVOL > 1.5x (완화: 기존 2.5x)
    - SL: -1.8% / TP: +2.0%
    - 쿨다운: 20분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.atr_squeeze_ratio = self.params.get("atr_squeeze_ratio", 0.85)  # 완화: 0.7→0.85
        self.min_breakout_pct = self.params.get("min_breakout_pct", 0.005)  # 완화: 0.010→0.005
        self.min_rvol = self.params.get("min_rvol", 1.5)  # 완화: 2.5→1.5
        self.position_pct = self.params.get("position_pct", 0.10)  # 축소: 0.20→0.10
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 20)  # 강화: 1→20

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

        candles = history.get_candles(symbol, "5m", 210)  # SMA200용 확장
        if len(candles) < 51:
            return None

        price = candles[-1].close

        # 시장 방향 필터
        if not check_market_direction_filter(history, symbol, price):
            return None

        # ATR squeeze: ATR(14) < ATR(50) * 0.85 (완화된 조건)
        atr14 = history.calc_atr(symbol, "5m", 14)
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
            return None

        # Breakout: price > highest high of last 12 candles (완화된 조건)
        recent_12 = candles[-13:-1]
        highest_12 = max(c.high for c in recent_12)
        breakout_pct = (price - highest_12) / highest_12 if highest_12 > 0 else 0
        if breakout_pct < self.min_breakout_pct:
            return None

        # RVOL (완화된 조건)
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
    Strategy 4: SUPPORT_TOUCH_BOUNCE (v27 개선)

    Buy near support with relaxed entry.
    - Price within 1.5% of 24h low (완화: 기존 0.5%)
    - Current candle has lower wick (>40% of range, 완화: 기존 60%)
    - RVOL > 1.2x (완화: 기존 2.0x)
    - SL: -1.5% / TP: +1.2%
    - 쿨다운: 15분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.support_tolerance = self.params.get("support_tolerance", 0.015)  # 완화: 0.005→0.015
        self.min_wick_ratio = self.params.get("min_wick_ratio", 0.40)  # 완화: 0.60→0.40
        self.min_rvol = self.params.get("min_rvol", 1.2)  # 완화: 2.0→1.2
        self.position_pct = self.params.get("position_pct", 0.10)  # 축소: 0.20→0.10
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 15)  # 강화: 1→15

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

        # 시장 방향 필터
        if not check_market_direction_filter(history, symbol, price):
            return None

        # 24h low (완화된 조건)
        low_24h = min(c.low for c in candles_24h)
        distance_from_low = (price - low_24h) / low_24h if low_24h > 0 else 1

        if distance_from_low > self.support_tolerance:
            return None

        # Lower wick (완화된 조건: >40% of candle range)
        candle_range = current.high - current.low
        if candle_range <= 0:
            return None
        lower_wick = min(current.open, current.close) - current.low
        wick_ratio = lower_wick / candle_range
        if wick_ratio < self.min_wick_ratio:
            return None

        # RVOL (완화된 조건)
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
    Strategy 5: VOLUME_CLIMAX_REVERSAL (v27 개선)

    Panic selling exhaustion.
    - RVOL > 2.5x (완화: 기존 5.0x)
    - Price drop > -1.5% (완화: 기존 -3.0%)
    - Current candle closes in top 40% of range (완화: 기존 30%)
    - SL: -2.0% / TP: +1.8%
    - 쿨다운: 20분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_rvol = self.params.get("min_rvol", 2.5)  # 완화: 5.0→2.5
        self.min_drop_pct = self.params.get("min_drop_pct", 0.015)  # 완화: 0.030→0.015
        self.close_position_threshold = self.params.get("close_position_threshold", 0.40)  # 완화: 0.30→0.40
        self.max_distance_from_low = self.params.get("max_distance_from_low", 0.05)
        self.position_pct = self.params.get("position_pct", 0.10)  # 축소: 0.20→0.10
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 20)  # 강화: 1→20

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

        # 시장 방향 필터
        if not check_market_direction_filter(history, symbol, price):
            return None

        # RVOL (완화된 조건)
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # Price drop (완화된 조건)
        drop_pct = (price - prev_price) / prev_price if prev_price > 0 else 0
        if drop_pct > -self.min_drop_pct:
            return None

        # Close in top 40% of candle range (완화된 조건)
        candle_range = current.high - current.low
        if candle_range <= 0:
            return None
        close_position = (current.close - current.low) / candle_range
        if close_position < (1 - self.close_position_threshold):
            return None

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
    Strategy 6: EMA_CROSSOVER_MOMENTUM (v27 개선)

    Fast EMA crosses slow EMA with volume (조건 축소).
    - EMA10 > EMA30 (골든크로스)
    - Price > EMA50 (overall uptrend)
    - RVOL > 1.2x (완화: 기존 2.0x)
    - 조건 축소: EMA vs SMA 비교 제거
    - SL: -2.0% / TP: +2.0%
    - 쿨다운: 30분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_rvol = self.params.get("min_rvol", 1.2)  # 완화: 2.0→1.2
        self.position_pct = self.params.get("position_pct", 0.10)  # 축소: 0.20→0.10
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 30)  # 강화: 1→30
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

        candles = history.get_candles(symbol, "5m", 210)  # SMA200용 확장
        if len(candles) < 50:
            return None

        price = candles[-1].close

        # 시장 방향 필터
        if not check_market_direction_filter(history, symbol, price):
            return None

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

        # Cross detection
        prev_e10 = self._prev_ema10.get(symbol)
        prev_e30 = self._prev_ema30.get(symbol)
        self._prev_ema10[symbol] = ema10
        self._prev_ema30[symbol] = ema30

        if prev_e10 is not None and prev_e30 is not None:
            if prev_e10 > prev_e30:
                return None
        else:
            ema_diff = (ema10 - ema30) / ema30 if ema30 > 0 else 0
            if ema_diff > 0.005:
                return None

        # Price > EMA50
        if price <= ema50:
            return None

        # RVOL (완화된 조건)
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
    Strategy 7: ATR_EXPANSION_ENTRY (v27 개선)

    Enter when volatility expands from low.
    - ATR(14) increases by >15% vs ATR(14) 3 candles ago (완화: 기존 30%)
    - Price > EMA20
    - RVOL > 1.5x (완화: 기존 2.5x)
    - 24h 신고가 조건 제거
    - SL: -2.0% / TP: +1.8%
    - 쿨다운: 20분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.atr_expansion_pct = self.params.get("atr_expansion_pct", 0.15)  # 완화: 0.30→0.15
        self.min_rvol = self.params.get("min_rvol", 1.5)  # 완화: 2.5→1.5
        self.position_pct = self.params.get("position_pct", 0.10)  # 축소: 0.20→0.10
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 20)  # 강화: 1→20

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

        # 시장 방향 필터
        if not check_market_direction_filter(history, symbol, price):
            return None

        # Current ATR(14)
        atr_now = history.calc_atr(symbol, "5m", 14)
        if not atr_now:
            return None

        # ATR 3 candles ago
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

        # RVOL (완화된 조건)
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # 24h 신고가 조건 제거됨

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
    Strategy 8: MICRO_PULLBACK_LONG (v27 개선) - 최악 성능 전략 대폭 수정

    Pullback in strong uptrend with SMA200 filter.
    - Price > EMA20 > EMA50 > SMA200 (추가: SMA200 필터)
    - Price pullback -1.0% to -2.5% from recent high (완화: 기존 -0.8%~-1.5%)
    - RVOL > 1.0x (완화: 기존 1.5x)
    - Bullish candle appears
    - SL: -2.0% / TP: +1.2%
    - 쿨다운: 30분 (대폭 강화: 기존 1분)
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_pullback_pct = self.params.get("min_pullback_pct", 0.010)  # 완화: 0.008→0.010
        self.max_pullback_pct = self.params.get("max_pullback_pct", 0.025)  # 완화: 0.015→0.025
        self.min_rvol = self.params.get("min_rvol", 1.0)  # 완화: 1.5→1.0
        self.position_pct = self.params.get("position_pct", 0.10)  # 축소: 0.20→0.10
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 30)  # 대폭 강화: 1→30

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

        candles = history.get_candles(symbol, "5m", 210)  # SMA200용 확장
        if len(candles) < 200:
            return None

        current = candles[-1]
        price = current.close

        # Strong uptrend: Price > EMA20 > EMA50 > SMA200 (추가된 조건)
        ema20 = history.calc_ema(symbol, "5m", 20)
        ema50 = history.calc_ema(symbol, "5m", 50)
        sma200 = history.calc_sma(symbol, "5m", 200)
        if not ema20 or not ema50 or not sma200:
            return None
        if not (price > ema20 > ema50 > sma200):
            return None

        # Recent high (last 20 candles)
        recent_20 = candles[-20:]
        recent_high = max(c.high for c in recent_20)

        # Pullback: -1.0% to -2.5% (완화된 조건)
        pullback_pct = (recent_high - price) / recent_high if recent_high > 0 else 0
        if pullback_pct < self.min_pullback_pct or pullback_pct > self.max_pullback_pct:
            return None

        # Bullish candle
        if current.close <= current.open:
            return None

        # RVOL (완화된 조건)
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
                "sma200": sma200,
                "pullback_pct": pullback_pct,
                "rvol": rvol,
            },
        }


class TripleConfirmationEntrySignalGenerator:
    """
    Strategy 9: TRIPLE_CONFIRMATION_ENTRY (v27 개선)

    Relaxed confirmation for higher trade frequency.
    - Price bounces from EMA50 (within 2% below, 완화: 기존 1%)
    - Within 5% of 24h low (완화: 기존 3%)
    - RVOL > 1.5x (완화: 기존 3.0x)
    - Last 1 candle bullish (완화: 기존 2개)
    - SL: -1.8% / TP: +1.4%
    - 쿨다운: 20분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.ema_bounce_tolerance = self.params.get("ema_bounce_tolerance", 0.02)  # 완화: 0.01→0.02
        self.support_tolerance = self.params.get("support_tolerance", 0.05)  # 완화: 0.03→0.05
        self.min_rvol = self.params.get("min_rvol", 1.5)  # 완화: 3.0→1.5
        self.min_atr_ratio = self.params.get("min_atr_ratio", 0.8)
        self.position_pct = self.params.get("position_pct", 0.10)  # 축소: 0.20→0.10
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 20)  # 강화: 1→20

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

        # 시장 방향 필터
        if not check_market_direction_filter(history, symbol, price):
            return None

        # Condition 1: Price bounces from EMA50 (within 2% below, 완화된 조건)
        ema50 = history.calc_ema(symbol, "5m", 50)
        if not ema50:
            return None
        ema_distance = (ema50 - price) / ema50 if ema50 > 0 else 0
        if ema_distance < 0 or ema_distance > self.ema_bounce_tolerance:
            return None

        # Condition 2: Within 5% of 24h low (완화된 조건)
        candles_for_low = candles[-288:] if len(candles) >= 288 else candles
        low_24h = min(c.low for c in candles_for_low)
        distance_from_low = (price - low_24h) / low_24h if low_24h > 0 else 1
        if distance_from_low > self.support_tolerance:
            return None

        # Condition 3: RVOL (완화된 조건)
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # Condition 4: Last 1 candle bullish (완화: 기존 2개)
        c1 = candles[-1]
        if c1.close <= c1.open:
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
            },
        }


class FastScalpReboundSignalGenerator:
    """
    Strategy 10: FAST_SCALP_REBOUND (v27 개선)

    Quick bounce from small dip.
    - Any -0.8% or worse dip in single candle (완화: 기존 -1.5%)
    - RVOL > 1.5x (완화: 기존 2.5x)
    - Price > SMA50 (trend filter)
    - Close > Open (immediate bounce)
    - SL: -1.5% / TP: +1.0%
    - 쿨다운: 15분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_dip_pct = self.params.get("min_dip_pct", 0.008)  # 완화: 0.015→0.008
        self.min_rvol = self.params.get("min_rvol", 1.5)  # 완화: 2.5→1.5
        self.position_pct = self.params.get("position_pct", 0.10)  # 축소: 0.20→0.10
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 15)  # 강화: 1→15

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

        candles = history.get_candles(symbol, "5m", 210)  # SMA200용 확장
        if len(candles) < 50:
            return None

        current = candles[-1]
        price = current.close

        # 시장 방향 필터
        if not check_market_direction_filter(history, symbol, price):
            return None

        # Dip check (완화된 조건)
        prev_close = candles[-2].close if len(candles) >= 2 else current.open
        dip_pct = (current.low - prev_close) / prev_close if prev_close > 0 else 0
        if dip_pct > -self.min_dip_pct:
            return None

        # Immediate bounce: Close > Open
        if current.close <= current.open:
            return None

        # RVOL (완화된 조건)
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


class DataDrivenStrategyV28SignalGenerator:
    """
    Strategy V28: 데이터 분석 기반 전략 (OpenAI 최적화 버전)

    45,058건 거래 + OpenAI GPT-4 분석 결과:
    - SMA200 범위 필터: 260.4825 ~ 3830.75 (66.7% 승률)
    - 24h 저점 대비 거리 >= 4.12% (상위 25%, 64.9% 승률)
    - 트레일링 스탑 0.3% (91.7% 승률, +0.76% 평균수익)
    - 최적 SL: -1.5%, TP: +3.0%
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        # OpenAI 분석 기반 파라미터
        self.sma200_min = self.params.get("sma200_min", 260.4825)   # 상위 75%
        self.sma200_max = self.params.get("sma200_max", 3830.75)    # 범위 상한
        self.min_distance_from_low = self.params.get("min_distance_from_low", 0.0412)  # 상위 25%
        self.position_pct = self.params.get("position_pct", 0.10)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 30)

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
        # 쿨다운 체크
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 300)
        if len(candles) < 210:
            return None

        price = candles[-1].close

        # 패턴 1: SMA200 범위 필터 (260.48 ~ 3830.75, 66.7% 승률)
        sma200 = history.calc_sma(symbol, "5m", 200)
        if not sma200:
            return None
        if not (self.sma200_min <= sma200 <= self.sma200_max):
            return None

        # 패턴 2: 24h 저점 대비 거리 >= 4.12% (상위 25%, 64.9% 승률)
        candles_24h = candles[-288:] if len(candles) >= 288 else candles
        low_24h = min(c.low for c in candles_24h)
        distance_from_low = (price - low_24h) / low_24h if low_24h > 0 else 0
        if distance_from_low < self.min_distance_from_low:
            return None

        # 추가 필터: 현재 캔들 양봉 (모멘텀 확인)
        current = candles[-1]
        if current.close <= current.open:
            return None

        # EMA20/50 계산 (추세 확인)
        ema20 = history.calc_ema(symbol, "5m", 20)
        ema50 = history.calc_ema(symbol, "5m", 50)
        if not ema20 or not ema50:
            return None

        # 상승 추세 확인: EMA20 > EMA50
        if ema20 <= ema50:
            return None

        # RVOL 체크 (최소 1.0x)
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < 1.0:
            return None

        # 점수 계산
        trend_strength = (ema20 - ema50) / ema50 * 100 if ema50 > 0 else 0
        score = 57.8 + min(trend_strength, 3) * 2 + min(rvol - 1, 2) * 2

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "DATA_DRIVEN_V28",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "sma200": sma200,
                "ema20": ema20,
                "ema50": ema50,
                "low_24h": low_24h,
                "distance_from_low": distance_from_low,
                "rvol": rvol,
            },
        }
