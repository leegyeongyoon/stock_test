"""v3 데이터 기반 전략 (DB 역분석 결과)

30일 5분봉 데이터 245,656건 역분석 → 수수료 포함 기대수익 양수인 패턴만 전략화.

핵심 발견:
- TP/SL 1:1 (2%/2%) 또는 4:3 (2%/1.5%) + 긴 보유시간(120~180분)이 최적
- 급락 + 과매도 + 양봉 확인이 가장 강력한 엣지
- ATR(변동성) > 1%가 수익의 핵심 조건
"""

import math
from datetime import datetime
from typing import Optional

from src.backtesting.data.data_loader import HistoryProvider


class VolatileOversoldBounceSignalGenerator:
    """
    전략 1: VOLATILE_OVERSOLD_BOUNCE
    데이터 근거: ATR>1.0% + RSI<35 + RVOL>1.5 + 양봉 → WR 53.8%, 기대수익 +0.25%
    + SMA20 -2%아래 + RVOL>2 + 양봉 → WR 56.0%, 기대수익 +0.34% (부분 겹침)

    진입: 고변동성 + 과매도 + 거래량 폭발 + 양봉 확인
    최적: TP +2.0% | SL -2.0% | 타임스탑 180분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_atr_pct = self.params.get("min_atr_pct", 1.0)
        self.max_rsi = self.params.get("max_rsi", 35)
        self.min_rvol = self.params.get("min_rvol", 1.5)
        self.position_pct = self.params.get("position_pct", 0.10)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 60)

    def __call__(self, timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            signal = self._evaluate(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate(self, timestamp, history, symbol) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 220)
        if len(candles) < 200:
            return None

        current = candles[-1]
        price = current.close
        if price <= 0:
            return None

        # 1. 양봉 필수
        if current.close <= current.open:
            return None

        # 2. RSI < 35
        closes = [c.close for c in candles]
        rsi = self._calc_rsi(closes, 14)
        if rsi is None or rsi >= self.max_rsi:
            return None

        # 3. ATR% >= 1.0%
        atr_pct = self._calc_atr_pct(candles, 14, price)
        if atr_pct is None or atr_pct < self.min_atr_pct:
            return None

        # 4. RVOL >= 1.5
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        score = 60 + min(atr_pct, 3) * 5 + (35 - rsi) * 0.5 + min(rvol, 5) * 2

        self._last_entry[symbol] = timestamp
        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "VOLATILE_OVERSOLD_BOUNCE",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price, "rsi": rsi, "atr_pct": atr_pct, "rvol": rvol,
            },
        }

    @staticmethod
    def _calc_rsi(closes, period=14):
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
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))

    @staticmethod
    def _calc_atr_pct(candles, period, price):
        if len(candles) < period + 1:
            return None
        trs = []
        for i in range(-period, 0):
            h, lo, pc = candles[i].high, candles[i].low, candles[i - 1].close
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        atr = sum(trs) / len(trs)
        return (atr / price * 100) if price > 0 else None


class CrashRecoverySignalGenerator:
    """
    전략 2: CRASH_RECOVERY
    데이터 근거: 30분-2%하락 + RVOL>2 + 양봉 + RSI<40 → WR 48.6%, 기대수익 +0.30%
    + 30분-2%하락 + RSI<30 + 양봉 → WR 45.7%, 기대수익 +0.19%

    진입: 30분 내 급락 + 거래량 폭발 + 과매도 + 양봉 반전
    최적: TP +2.0% | SL -1.5% | 타임스탑 120분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_drop_pct = self.params.get("min_drop_pct", 0.02)
        self.max_rsi = self.params.get("max_rsi", 40)
        self.min_rvol = self.params.get("min_rvol", 2.0)
        self.position_pct = self.params.get("position_pct", 0.10)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 45)

    def __call__(self, timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            signal = self._evaluate(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate(self, timestamp, history, symbol) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 30)
        if len(candles) < 8:
            return None

        current = candles[-1]
        price = current.close
        if price <= 0:
            return None

        # 1. 양봉 필수
        if current.close <= current.open:
            return None

        # 2. 30분(6봉) -2% 이상 하락
        price_6_ago = candles[-7].close
        if price_6_ago <= 0:
            return None
        change_6 = (price - price_6_ago) / price_6_ago
        if change_6 > -self.min_drop_pct:
            return None

        # 3. RSI < 40
        closes = [c.close for c in candles]
        rsi = self._calc_rsi(closes, 14)
        if rsi is not None and rsi >= self.max_rsi:
            return None

        # 4. RVOL >= 2.0
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        drop_strength = min(abs(change_6) / 0.04, 1.0)
        score = 60 + drop_strength * 20 + min(rvol - 1, 5) * 3

        self._last_entry[symbol] = timestamp
        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "CRASH_RECOVERY",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price, "change_6": change_6, "rsi": rsi, "rvol": rvol,
            },
        }

    @staticmethod
    def _calc_rsi(closes, period=14):
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
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))


class TripleBearishReversalSignalGenerator:
    """
    전략 3: TRIPLE_BEARISH_REVERSAL
    데이터 근거: 3봉연속음봉 + RSI<30 + ATR>1% → WR 52.4%, 기대수익 +0.17%

    진입: 3봉 연속 하락 후 과매도 + 고변동성 상태
    최적: TP +2.0% | SL -2.0% | 타임스탑 180분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.max_rsi = self.params.get("max_rsi", 30)
        self.min_atr_pct = self.params.get("min_atr_pct", 1.0)
        self.min_bearish = self.params.get("min_bearish", 3)
        self.position_pct = self.params.get("position_pct", 0.10)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 30)

    def __call__(self, timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            signal = self._evaluate(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate(self, timestamp, history, symbol) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 220)
        if len(candles) < 20:
            return None

        current = candles[-1]
        price = current.close
        if price <= 0:
            return None

        # 1. 직전 3봉 연속 음봉
        if len(candles) < 4:
            return None
        bearish_count = 0
        for j in range(1, 4):
            if candles[-1 - j].close < candles[-1 - j].open:
                bearish_count += 1
        if bearish_count < self.min_bearish:
            return None

        # 2. RSI < 30
        closes = [c.close for c in candles]
        rsi = self._calc_rsi(closes, 14)
        if rsi is None or rsi >= self.max_rsi:
            return None

        # 3. ATR% >= 1.0%
        atr_pct = self._calc_atr_pct(candles, 14, price)
        if atr_pct is None or atr_pct < self.min_atr_pct:
            return None

        score = 60 + bearish_count * 5 + (30 - rsi) * 0.5 + min(atr_pct, 3) * 5

        self._last_entry[symbol] = timestamp
        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "TRIPLE_BEARISH_REVERSAL",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price, "rsi": rsi, "atr_pct": atr_pct,
                "bearish_count": bearish_count,
            },
        }

    @staticmethod
    def _calc_rsi(closes, period=14):
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
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))

    @staticmethod
    def _calc_atr_pct(candles, period, price):
        if len(candles) < period + 1:
            return None
        trs = []
        for i in range(-period, 0):
            h, lo, pc = candles[i].high, candles[i].low, candles[i - 1].close
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        atr = sum(trs) / len(trs)
        return (atr / price * 100) if price > 0 else None


