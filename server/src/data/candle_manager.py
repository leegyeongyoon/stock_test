"""캔들 데이터 매니저 - 5m, 1h 캔들 수집 및 캐싱"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

logger = structlog.get_logger()


@dataclass
class Candle:
    """OHLCV 캔들 데이터"""

    symbol: str
    interval: str  # "5m", "1h"
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float  # base volume
    quote_volume: float  # USDT volume
    trades: int = 0


@dataclass
class CandleBuffer:
    """심볼별 캔들 버퍼"""

    symbol: str
    interval: str
    candles: list[Candle] = field(default_factory=list)
    max_size: int = 100

    def add(self, candle: Candle) -> None:
        """캔들 추가 (중복 방지, 정렬 유지)"""
        # 동일 시간 캔들 업데이트
        for i, existing in enumerate(self.candles):
            if existing.open_time == candle.open_time:
                self.candles[i] = candle
                return

        self.candles.append(candle)
        self.candles.sort(key=lambda c: c.open_time)

        # 최대 크기 유지
        if len(self.candles) > self.max_size:
            self.candles = self.candles[-self.max_size :]

    def get_latest(self, count: int = 1) -> list[Candle]:
        """최근 N개 캔들 반환"""
        return self.candles[-count:] if self.candles else []

    def get_closes(self, count: int = 50) -> list[float]:
        """최근 N개 종가 반환"""
        return [c.close for c in self.candles[-count:]]

    def get_highs(self, count: int = 50) -> list[float]:
        """최근 N개 고가 반환"""
        return [c.high for c in self.candles[-count:]]

    def get_lows(self, count: int = 50) -> list[float]:
        """최근 N개 저가 반환"""
        return [c.low for c in self.candles[-count:]]

    def get_volumes(self, count: int = 50) -> list[float]:
        """최근 N개 거래량(quote) 반환"""
        return [c.quote_volume for c in self.candles[-count:]]


class CandleManager:
    """
    캔들 데이터 매니저

    기능:
    - REST로 히스토리 로드
    - 실시간 캔들 업데이트
    - 심볼/인터벌별 캔들 캐싱
    """

    # Binance Kline intervals
    INTERVAL_5M = "5m"
    INTERVAL_1H = "1h"

    def __init__(self) -> None:
        # 심볼 -> 인터벌 -> CandleBuffer
        self._buffers: dict[str, dict[str, CandleBuffer]] = {}
        self._last_update: dict[str, datetime] = {}
        self._running = False

    def get_buffer(self, symbol: str, interval: str) -> Optional[CandleBuffer]:
        """캔들 버퍼 조회"""
        if symbol not in self._buffers:
            return None
        return self._buffers[symbol].get(interval)

    def get_candles(self, symbol: str, interval: str, count: int = 50) -> list[Candle]:
        """캔들 조회"""
        buffer = self.get_buffer(symbol, interval)
        if not buffer:
            return []
        return buffer.get_latest(count)

    def get_closes(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        """종가 리스트 조회"""
        buffer = self.get_buffer(symbol, interval)
        if not buffer:
            return []
        return buffer.get_closes(count)

    def get_highs(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        """고가 리스트 조회"""
        buffer = self.get_buffer(symbol, interval)
        if not buffer:
            return []
        return buffer.get_highs(count)

    def get_lows(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        """저가 리스트 조회"""
        buffer = self.get_buffer(symbol, interval)
        if not buffer:
            return []
        return buffer.get_lows(count)

    def get_volumes(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        """거래량 리스트 조회"""
        buffer = self.get_buffer(symbol, interval)
        if not buffer:
            return []
        return buffer.get_volumes(count)

    async def load_history(
        self,
        exchange,
        symbol: str,
        interval: str,
        limit: int = 100,
    ) -> bool:
        """
        REST API로 캔들 히스토리 로드

        Args:
            exchange: BinanceSpotExchange 또는 BinancePerpExchange
            symbol: 심볼 (e.g., "BTCUSDT")
            interval: 인터벌 ("5m", "1h")
            limit: 로드할 캔들 수
        """
        try:
            # 버퍼 초기화
            if symbol not in self._buffers:
                self._buffers[symbol] = {}

            if interval not in self._buffers[symbol]:
                self._buffers[symbol][interval] = CandleBuffer(
                    symbol=symbol,
                    interval=interval,
                    max_size=limit,
                )

            # REST API로 캔들 로드
            klines = await exchange.get_klines(symbol, interval, limit)

            if not klines:
                logger.warning(
                    "No klines returned",
                    symbol=symbol,
                    interval=interval,
                )
                return False

            buffer = self._buffers[symbol][interval]

            for kline in klines:
                candle = self._parse_kline(symbol, interval, kline)
                if candle:
                    buffer.add(candle)

            logger.info(
                "Loaded candle history",
                symbol=symbol,
                interval=interval,
                count=len(buffer.candles),
            )

            self._last_update[f"{symbol}_{interval}"] = datetime.utcnow()
            return True

        except Exception as e:
            logger.error(
                "Failed to load candle history",
                symbol=symbol,
                interval=interval,
                error=str(e),
            )
            return False

    def update_candle(self, symbol: str, interval: str, kline: dict) -> None:
        """
        실시간 캔들 업데이트 (WebSocket에서 호출)

        Args:
            symbol: 심볼
            interval: 인터벌
            kline: Binance kline 데이터
        """
        if symbol not in self._buffers:
            self._buffers[symbol] = {}

        if interval not in self._buffers[symbol]:
            self._buffers[symbol][interval] = CandleBuffer(
                symbol=symbol,
                interval=interval,
            )

        candle = self._parse_kline(symbol, interval, kline)
        if candle:
            self._buffers[symbol][interval].add(candle)
            self._last_update[f"{symbol}_{interval}"] = datetime.utcnow()

    def _parse_kline(
        self, symbol: str, interval: str, kline: dict | list
    ) -> Optional[Candle]:
        """Binance kline 데이터 파싱"""
        try:
            # REST API 응답 (list 형태)
            if isinstance(kline, list):
                return Candle(
                    symbol=symbol,
                    interval=interval,
                    open_time=datetime.utcfromtimestamp(kline[0] / 1000),
                    close_time=datetime.utcfromtimestamp(kline[6] / 1000),
                    open=float(kline[1]),
                    high=float(kline[2]),
                    low=float(kline[3]),
                    close=float(kline[4]),
                    volume=float(kline[5]),
                    quote_volume=float(kline[7]),
                    trades=int(kline[8]),
                )

            # WebSocket 응답 (dict 형태)
            if isinstance(kline, dict):
                k = kline.get("k", kline)  # kline 이벤트 또는 직접 데이터
                return Candle(
                    symbol=symbol,
                    interval=interval,
                    open_time=datetime.utcfromtimestamp(k.get("t", 0) / 1000),
                    close_time=datetime.utcfromtimestamp(k.get("T", 0) / 1000),
                    open=float(k.get("o", 0)),
                    high=float(k.get("h", 0)),
                    low=float(k.get("l", 0)),
                    close=float(k.get("c", 0)),
                    volume=float(k.get("v", 0)),
                    quote_volume=float(k.get("q", 0)),
                    trades=int(k.get("n", 0)),
                )

            return None

        except Exception as e:
            logger.error("Failed to parse kline", error=str(e))
            return None

    # === 지표 계산 헬퍼 ===

    def calc_sma(self, symbol: str, interval: str, period: int) -> Optional[float]:
        """SMA 계산"""
        closes = self.get_closes(symbol, interval, period)
        if len(closes) < period:
            return None
        return sum(closes) / len(closes)

    def calc_ema(self, symbol: str, interval: str, period: int) -> Optional[float]:
        """EMA 계산"""
        closes = self.get_closes(symbol, interval, period + 10)  # 충분한 데이터
        if len(closes) < period:
            return None

        multiplier = 2 / (period + 1)
        ema = closes[0]

        for close in closes[1:]:
            ema = (close - ema) * multiplier + ema

        return ema

    def calc_atr(self, symbol: str, interval: str, period: int = 14) -> Optional[float]:
        """ATR (Average True Range) 계산"""
        candles = self.get_candles(symbol, interval, period + 1)
        if len(candles) < period + 1:
            return None

        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i - 1].close

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        if not true_ranges:
            return None

        return sum(true_ranges[-period:]) / min(len(true_ranges), period)

    def calc_atr_percent(
        self, symbol: str, interval: str, period: int = 14
    ) -> Optional[float]:
        """ATR% (ATR / 현재가) 계산"""
        atr = self.calc_atr(symbol, interval, period)
        candles = self.get_candles(symbol, interval, 1)

        if atr is None or not candles:
            return None

        current_price = candles[-1].close
        if current_price <= 0:
            return None

        return atr / current_price

    def calc_highest(
        self, symbol: str, interval: str, period: int = 20
    ) -> Optional[float]:
        """최근 N봉 고점"""
        highs = self.get_highs(symbol, interval, period)
        if not highs:
            return None
        return max(highs)

    def calc_lowest(
        self, symbol: str, interval: str, period: int = 20
    ) -> Optional[float]:
        """최근 N봉 저점"""
        lows = self.get_lows(symbol, interval, period)
        if not lows:
            return None
        return min(lows)

    def calc_rvol(self, symbol: str, interval: str, period: int = 20) -> Optional[float]:
        """
        RVOL (Relative Volume) 계산
        = 현재 캔들 거래대금 / 최근 N개 평균 거래대금
        """
        volumes = self.get_volumes(symbol, interval, period + 1)
        if len(volumes) < period + 1:
            return None

        current_volume = volumes[-1]
        avg_volume = sum(volumes[:-1]) / len(volumes[:-1])

        if avg_volume <= 0:
            return None

        return current_volume / avg_volume

    def calc_vwap(self, symbol: str, interval: str, period: int = 20) -> Optional[float]:
        """
        VWAP (Volume Weighted Average Price) 계산
        = sum(typical_price * volume) / sum(volume)
        """
        candles = self.get_candles(symbol, interval, period)
        if not candles:
            return None

        total_pv = 0.0
        total_volume = 0.0

        for candle in candles:
            typical_price = (candle.high + candle.low + candle.close) / 3
            total_pv += typical_price * candle.quote_volume
            total_volume += candle.quote_volume

        if total_volume <= 0:
            return None

        return total_pv / total_volume

    def calc_close_position(self, symbol: str, interval: str) -> Optional[float]:
        """
        ClosePos = (close - low) / (high - low)
        현재 캔들에서 종가의 상대적 위치 (0~1)
        """
        candles = self.get_candles(symbol, interval, 1)
        if not candles:
            return None

        candle = candles[-1]
        range_hl = candle.high - candle.low

        if range_hl <= 0:
            return 0.5  # 변동 없으면 중립

        return (candle.close - candle.low) / range_hl

    def is_breakout(
        self, symbol: str, interval: str, period: int = 20, direction: str = "up"
    ) -> bool:
        """
        돌파 여부 확인

        Args:
            direction: "up" (고점 돌파) 또는 "down" (저점 돌파)
        """
        candles = self.get_candles(symbol, interval, period + 1)
        if len(candles) < period + 1:
            return False

        current = candles[-1]
        historical = candles[:-1]

        if direction == "up":
            highest = max(c.high for c in historical)
            return current.close > highest
        else:
            lowest = min(c.low for c in historical)
            return current.close < lowest


# 싱글톤 인스턴스
_candle_manager: Optional[CandleManager] = None


def get_candle_manager() -> CandleManager:
    """캔들 매니저 싱글톤"""
    global _candle_manager
    if _candle_manager is None:
        _candle_manager = CandleManager()
    return _candle_manager
