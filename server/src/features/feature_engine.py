"""FeatureEngine - 기술적 지표 및 피처 계산 (CandleManager 통합)"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from src.data.candle_manager import CandleManager, get_candle_manager

logger = structlog.get_logger()


@dataclass
class Features:
    """계산된 피처"""

    symbol: str
    timestamp: datetime

    # 가격 기반 (1h)
    sma_20_1h: float = 0
    sma_50_1h: float = 0
    ema_12_1h: float = 0
    ema_26_1h: float = 0
    ma_50_1h: float = 0  # 1h 50이평 (레짐 필터용)

    # 가격 기반 (5m)
    sma_20_5m: float = 0
    ema_9_5m: float = 0
    vwap_5m: float = 0  # 당일 intraday VWAP

    # 모멘텀
    rsi_14: float = 50
    macd: float = 0
    macd_signal: float = 0
    macd_hist: float = 0

    # 변동성 (1h)
    atr_14_1h: float = 0
    atr_pct_1h: float = 0  # ATR / 현재가
    atr_avg_1h: float = 0  # ATR 14개 평균 (급변장 체크용)
    bb_upper: float = 0
    bb_lower: float = 0
    bb_width: float = 0

    # 변동성 (5m)
    atr_14_5m: float = 0
    atr_pct_5m: float = 0

    # 거래량 (5m)
    rvol_5m: float = 1.0  # 현재 5m 거래대금 / 최근 20개 평균
    volume_sma_20: float = 0
    obv: float = 0

    # 가격 위치
    close_pos: float = 0.5  # (close - low) / (high - low), 0~1

    # 돌파
    breakout_1h_up: bool = False  # 1h 20봉 고점 돌파
    breakout_1h_down: bool = False  # 1h 20봉 저점 돌파
    highest_20_1h: float = 0  # 최근 20봉 고점
    lowest_20_1h: float = 0  # 최근 20봉 저점

    # 5m 돌파 (Satellite용)
    breakout_12_5m_up: bool = False  # 최근 12개 5m 고점 돌파
    highest_12_5m: float = 0

    # 레짐
    regime: str = "NEUTRAL"  # BULLISH / NEUTRAL / BEARISH / VOLATILE

    # 급변장 플래그
    is_volatile: bool = False


class FeatureEngine:
    """
    FeatureEngine

    책임:
    1. CandleManager와 통합하여 OHLC 기반 지표 계산
    2. 다중 타임프레임 지원 (5m, 1h)
    3. 레짐 판단 (MA + ATR 기반)
    4. 피처 캐싱
    """

    def __init__(self, candle_manager: Optional[CandleManager] = None) -> None:
        self._candle_manager = candle_manager or get_candle_manager()
        self._features_cache: dict[str, Features] = {}

        # Legacy support (tick 기반)
        self._price_history: dict[str, list[float]] = {}
        self._volume_history: dict[str, list[float]] = {}

    @property
    def candle_manager(self) -> CandleManager:
        """CandleManager 접근자"""
        return self._candle_manager

    def add_price(self, symbol: str, price: float, volume: float = 0) -> None:
        """가격 데이터 추가 (legacy tick 기반)"""
        if symbol not in self._price_history:
            self._price_history[symbol] = []
            self._volume_history[symbol] = []

        self._price_history[symbol].append(price)
        self._volume_history[symbol].append(volume)

        # 최대 500개 유지
        if len(self._price_history[symbol]) > 500:
            self._price_history[symbol] = self._price_history[symbol][-500:]
            self._volume_history[symbol] = self._volume_history[symbol][-500:]

    def calculate_features(self, symbol: str) -> Optional[Features]:
        """피처 계산 (CandleManager 기반)"""
        cm = self._candle_manager

        # 캔들 데이터 확인
        candles_1h = cm.get_candles(symbol, "1h", 50)
        candles_5m = cm.get_candles(symbol, "5m", 50)

        # 캔들 데이터가 부족하면 legacy 방식 사용
        if len(candles_1h) < 20 or len(candles_5m) < 20:
            return self._calculate_features_legacy(symbol)

        features = Features(
            symbol=symbol,
            timestamp=datetime.utcnow(),
        )

        # === 1h 기반 지표 ===
        closes_1h = cm.get_closes(symbol, "1h", 50)
        highs_1h = cm.get_highs(symbol, "1h", 50)
        lows_1h = cm.get_lows(symbol, "1h", 50)

        if closes_1h:
            # SMA (1h)
            if len(closes_1h) >= 20:
                features.sma_20_1h = sum(closes_1h[-20:]) / 20
            if len(closes_1h) >= 50:
                features.sma_50_1h = sum(closes_1h[-50:]) / 50
                features.ma_50_1h = features.sma_50_1h

            # EMA (1h)
            features.ema_12_1h = self._ema(closes_1h, 12)
            features.ema_26_1h = self._ema(closes_1h, 26)

            # MACD (1h)
            features.macd = features.ema_12_1h - features.ema_26_1h

            # RSI (1h)
            features.rsi_14 = self._rsi(closes_1h, 14)

        # ATR (1h)
        atr_1h = cm.calc_atr(symbol, "1h", 14)
        if atr_1h:
            features.atr_14_1h = atr_1h
            # ATR% (현재가 대비)
            current_price = closes_1h[-1] if closes_1h else 0
            if current_price > 0:
                features.atr_pct_1h = atr_1h / current_price

        # ATR 평균 (급변장 체크용)
        atr_values_1h = self._calc_atr_series(candles_1h, 14)
        if len(atr_values_1h) >= 14:
            features.atr_avg_1h = sum(atr_values_1h[-14:]) / 14

        # 볼린저 밴드 (1h)
        if len(closes_1h) >= 20:
            std_20 = self._std(closes_1h[-20:])
            features.bb_upper = features.sma_20_1h + 2 * std_20
            features.bb_lower = features.sma_20_1h - 2 * std_20
            if features.sma_20_1h > 0:
                features.bb_width = (features.bb_upper - features.bb_lower) / features.sma_20_1h

        # 고점/저점 (1h)
        if highs_1h and lows_1h:
            features.highest_20_1h = max(highs_1h[-20:]) if len(highs_1h) >= 20 else max(highs_1h)
            features.lowest_20_1h = min(lows_1h[-20:]) if len(lows_1h) >= 20 else min(lows_1h)

            # 돌파 체크 (1h)
            if closes_1h:
                current_close = closes_1h[-1]
                historical_highs = highs_1h[:-1] if len(highs_1h) > 1 else highs_1h
                historical_lows = lows_1h[:-1] if len(lows_1h) > 1 else lows_1h

                if len(historical_highs) >= 20:
                    features.breakout_1h_up = current_close > max(historical_highs[-20:])
                if len(historical_lows) >= 20:
                    features.breakout_1h_down = current_close < min(historical_lows[-20:])

        # === 5m 기반 지표 ===
        closes_5m = cm.get_closes(symbol, "5m", 50)
        highs_5m = cm.get_highs(symbol, "5m", 50)

        if closes_5m:
            # SMA (5m)
            if len(closes_5m) >= 20:
                features.sma_20_5m = sum(closes_5m[-20:]) / 20

            # EMA (5m)
            features.ema_9_5m = self._ema(closes_5m, 9)

        # VWAP (5m)
        vwap = cm.calc_vwap(symbol, "5m", 20)
        if vwap:
            features.vwap_5m = vwap

        # RVOL (5m)
        rvol = cm.calc_rvol(symbol, "5m", 20)
        if rvol:
            features.rvol_5m = rvol

        # ATR (5m)
        atr_5m = cm.calc_atr(symbol, "5m", 14)
        if atr_5m:
            features.atr_14_5m = atr_5m
            current_price = closes_5m[-1] if closes_5m else 0
            if current_price > 0:
                features.atr_pct_5m = atr_5m / current_price

        # ClosePos (5m 현재 캔들)
        close_pos = cm.calc_close_position(symbol, "5m")
        if close_pos is not None:
            features.close_pos = close_pos

        # 5m 돌파 체크 (12개 = 1시간)
        if highs_5m and closes_5m and len(highs_5m) >= 12:
            features.highest_12_5m = max(highs_5m[-12:])
            historical_highs_5m = highs_5m[:-1] if len(highs_5m) > 1 else highs_5m
            if len(historical_highs_5m) >= 12:
                features.breakout_12_5m_up = closes_5m[-1] > max(historical_highs_5m[-12:])

        # 거래량 SMA
        volumes = cm.get_volumes(symbol, "5m", 20)
        if volumes and len(volumes) >= 20:
            features.volume_sma_20 = sum(volumes) / len(volumes)

        # === 레짐 판단 (1h MA + ATR 기반) ===
        features.regime, features.is_volatile = self._determine_regime_advanced(
            symbol, features, closes_1h
        )

        self._features_cache[symbol] = features
        return features

    def _calculate_features_legacy(self, symbol: str) -> Optional[Features]:
        """Legacy 피처 계산 (tick 기반, 캔들 데이터 부족 시)"""
        prices = self._price_history.get(symbol, [])
        volumes = self._volume_history.get(symbol, [])

        if len(prices) < 50:
            return None

        features = Features(
            symbol=symbol,
            timestamp=datetime.utcnow(),
        )

        # SMA
        features.sma_20_1h = sum(prices[-20:]) / 20
        features.sma_50_1h = sum(prices[-50:]) / 50
        features.ma_50_1h = features.sma_50_1h

        # EMA
        features.ema_12_1h = self._ema(prices, 12)
        features.ema_26_1h = self._ema(prices, 26)

        # RSI
        features.rsi_14 = self._rsi(prices, 14)

        # MACD
        features.macd = features.ema_12_1h - features.ema_26_1h

        # 볼린저 밴드
        std_20 = self._std(prices[-20:])
        features.bb_upper = features.sma_20_1h + 2 * std_20
        features.bb_lower = features.sma_20_1h - 2 * std_20
        if features.sma_20_1h > 0:
            features.bb_width = (features.bb_upper - features.bb_lower) / features.sma_20_1h

        # 거래량
        if volumes:
            features.volume_sma_20 = sum(volumes[-20:]) / min(20, len(volumes))
            if features.volume_sma_20 > 0:
                features.rvol_5m = volumes[-1] / features.volume_sma_20

        # 레짐 (간단)
        features.regime = self._determine_regime_simple(features, prices[-1])

        self._features_cache[symbol] = features
        return features

    def _determine_regime_advanced(
        self, symbol: str, features: Features, closes_1h: list[float]
    ) -> tuple[str, bool]:
        """
        레짐 판단 (1h MA + ATR 기반)

        Returns:
            (regime, is_volatile): 레짐과 급변장 여부
        """
        if not closes_1h:
            return "NEUTRAL", False

        current_close = closes_1h[-1]
        ma_50 = features.ma_50_1h
        atr_current = features.atr_14_1h
        atr_avg = features.atr_avg_1h

        # 급변장 체크: ATR이 평균의 1.8배 이상
        is_volatile = False
        if atr_avg > 0 and atr_current > atr_avg * 1.8:
            is_volatile = True
            return "VOLATILE", True

        # 추세 체크
        if ma_50 > 0:
            if current_close < ma_50:
                return "BEARISH", False
            elif current_close > ma_50 * 1.02:  # 2% 이상 위
                return "BULLISH", False

        return "NEUTRAL", False

    def _determine_regime_simple(self, features: Features, current_price: float) -> str:
        """간단한 레짐 판단 (legacy)"""
        bullish_signals = 0
        bearish_signals = 0

        # 가격 vs SMA
        if current_price > features.sma_50_1h:
            bullish_signals += 1
        else:
            bearish_signals += 1

        # SMA 골든크로스/데드크로스
        if features.sma_20_1h > features.sma_50_1h:
            bullish_signals += 1
        else:
            bearish_signals += 1

        # RSI
        if features.rsi_14 > 50:
            bullish_signals += 1
        else:
            bearish_signals += 1

        # MACD
        if features.macd > features.macd_signal:
            bullish_signals += 1
        else:
            bearish_signals += 1

        if bullish_signals >= 3:
            return "BULLISH"
        elif bearish_signals >= 3:
            return "BEARISH"
        else:
            return "NEUTRAL"

    def _ema(self, prices: list[float], period: int) -> float:
        """지수이동평균"""
        if len(prices) < period:
            return sum(prices) / len(prices) if prices else 0

        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period

        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        return ema

    def _rsi(self, prices: list[float], period: int = 14) -> float:
        """RSI 계산"""
        if len(prices) < period + 1:
            return 50

        changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [c if c > 0 else 0 for c in changes[-period:]]
        losses = [-c if c < 0 else 0 for c in changes[-period:]]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _std(self, values: list[float]) -> float:
        """표준편차"""
        if len(values) < 2:
            return 0

        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return variance ** 0.5

    def _calc_atr_series(self, candles: list, period: int = 14) -> list[float]:
        """ATR 시계열 계산 (급변장 감지용)"""
        if len(candles) < period + 1:
            return []

        atr_values = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i - 1].close

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            atr_values.append(tr)

        # Rolling ATR
        result = []
        for i in range(period - 1, len(atr_values)):
            atr = sum(atr_values[i - period + 1 : i + 1]) / period
            result.append(atr)

        return result

    def get_features(self, symbol: str) -> Optional[Features]:
        """캐시된 피처 조회"""
        return self._features_cache.get(symbol)

    def get_regime(self, symbol: str) -> str:
        """레짐 조회"""
        features = self._features_cache.get(symbol)
        return features.regime if features else "NEUTRAL"

    def is_volatile(self, symbol: str) -> bool:
        """급변장 여부 조회"""
        features = self._features_cache.get(symbol)
        return features.is_volatile if features else False

    # === Satellite 전략용 시그널 체크 ===

    def check_momentum_signal(
        self,
        symbol: str,
        rvol_threshold: float = 2.5,
        close_pos_threshold: float = 0.75,
    ) -> dict:
        """
        모멘텀 시그널 체크 (Satellite 전략용)

        Returns:
            {
                "has_signal": bool,
                "rvol": float,
                "close_pos": float,
                "breakout": bool,
                "above_vwap": bool,
            }
        """
        features = self.get_features(symbol)
        if not features:
            return {"has_signal": False}

        rvol = features.rvol_5m
        close_pos = features.close_pos
        breakout = features.breakout_12_5m_up

        # VWAP 위에 있는지
        cm = self._candle_manager
        closes_5m = cm.get_closes(symbol, "5m", 1)
        above_vwap = False
        if closes_5m and features.vwap_5m > 0:
            above_vwap = closes_5m[-1] >= features.vwap_5m

        has_signal = (
            rvol >= rvol_threshold
            and close_pos >= close_pos_threshold
            and breakout
            and above_vwap
        )

        return {
            "has_signal": has_signal,
            "rvol": rvol,
            "close_pos": close_pos,
            "breakout": breakout,
            "above_vwap": above_vwap,
            "highest_12_5m": features.highest_12_5m,
        }

    def check_btc_regime(self) -> dict:
        """
        BTC 레짐 체크 (1h 기준)

        Returns:
            {
                "regime": str,
                "is_volatile": bool,
                "ma_50": float,
                "current_price": float,
                "atr_ratio": float (현재ATR / 평균ATR),
            }
        """
        symbol = "BTCUSDT"
        features = self.get_features(symbol)

        if not features:
            return {
                "regime": "NEUTRAL",
                "is_volatile": False,
            }

        atr_ratio = 1.0
        if features.atr_avg_1h > 0:
            atr_ratio = features.atr_14_1h / features.atr_avg_1h

        cm = self._candle_manager
        closes_1h = cm.get_closes(symbol, "1h", 1)
        current_price = closes_1h[-1] if closes_1h else 0

        return {
            "regime": features.regime,
            "is_volatile": features.is_volatile,
            "ma_50": features.ma_50_1h,
            "current_price": current_price,
            "atr_ratio": atr_ratio,
        }
