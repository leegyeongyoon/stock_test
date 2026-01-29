"""MarketDataService - 시장 데이터 수집 및 관리"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from src.exchange.base import TickerData

logger = structlog.get_logger()


@dataclass
class OHLCVBar:
    """OHLCV 바"""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MarketSnapshot:
    """시장 스냅샷"""

    symbol: str
    spot_price: float
    perp_price: float
    funding_rate: float
    spot_bid: float
    spot_ask: float
    perp_bid: float
    perp_ask: float
    volume_24h: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def spread_pct(self) -> float:
        """스팟-선물 스프레드"""
        if self.spot_price <= 0:
            return 0
        return (self.spot_price - self.perp_price) / self.spot_price


class MarketDataService:
    """
    MarketDataService

    책임:
    1. 실시간 시세 수집
    2. OHLCV 데이터 관리
    3. 기술적 지표 계산 (VWAP, RVOL 등)
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, MarketSnapshot] = {}
        self._ohlcv_cache: dict[str, list[OHLCVBar]] = {}
        self._vwap_cache: dict[str, float] = {}
        self._volume_avg: dict[str, float] = {}

        self._running = False
        self._update_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """서비스 시작"""
        self._running = True
        logger.info("MarketDataService started")

    async def stop(self) -> None:
        """서비스 중지"""
        self._running = False
        if self._update_task:
            self._update_task.cancel()
        logger.info("MarketDataService stopped")

    def update_ticker(
        self,
        symbol: str,
        spot_ticker: Optional[TickerData],
        perp_ticker: Optional[TickerData],
        funding_rate: float = 0,
    ) -> None:
        """시세 업데이트"""
        if not spot_ticker and not perp_ticker:
            return

        self._snapshots[symbol] = MarketSnapshot(
            symbol=symbol,
            spot_price=spot_ticker.last if spot_ticker else 0,
            perp_price=perp_ticker.last if perp_ticker else 0,
            funding_rate=funding_rate,
            spot_bid=spot_ticker.bid if spot_ticker else 0,
            spot_ask=spot_ticker.ask if spot_ticker else 0,
            perp_bid=perp_ticker.bid if perp_ticker else 0,
            perp_ask=perp_ticker.ask if perp_ticker else 0,
            volume_24h=spot_ticker.volume_24h if spot_ticker else 0,
        )

    def get_snapshot(self, symbol: str) -> Optional[MarketSnapshot]:
        """스냅샷 조회"""
        return self._snapshots.get(symbol)

    def add_ohlcv_bar(self, symbol: str, bar: OHLCVBar) -> None:
        """OHLCV 바 추가"""
        if symbol not in self._ohlcv_cache:
            self._ohlcv_cache[symbol] = []

        self._ohlcv_cache[symbol].append(bar)

        # 최대 1000개 유지
        if len(self._ohlcv_cache[symbol]) > 1000:
            self._ohlcv_cache[symbol] = self._ohlcv_cache[symbol][-1000:]

        # VWAP 업데이트
        self._update_vwap(symbol)

    def _update_vwap(self, symbol: str) -> None:
        """VWAP 계산"""
        bars = self._ohlcv_cache.get(symbol, [])
        if not bars:
            return

        # 오늘 바만 사용 (간단화)
        total_pv = 0
        total_volume = 0

        for bar in bars[-100:]:  # 최근 100개
            typical_price = (bar.high + bar.low + bar.close) / 3
            total_pv += typical_price * bar.volume
            total_volume += bar.volume

        if total_volume > 0:
            self._vwap_cache[symbol] = total_pv / total_volume

    def get_vwap(self, symbol: str) -> float:
        """VWAP 조회"""
        return self._vwap_cache.get(symbol, 0)

    def get_rvol(self, symbol: str) -> float:
        """상대 거래량 (RVOL) 조회"""
        bars = self._ohlcv_cache.get(symbol, [])
        if len(bars) < 21:
            return 1.0

        # 최근 20개 평균 거래량
        recent_bars = bars[-21:-1]
        avg_volume = sum(b.volume for b in recent_bars) / len(recent_bars)

        current_volume = bars[-1].volume
        if avg_volume > 0:
            return current_volume / avg_volume

        return 1.0

    def get_high_low(self, symbol: str, lookback: int = 20) -> tuple[float, float]:
        """최근 N개 바의 고점/저점"""
        bars = self._ohlcv_cache.get(symbol, [])
        if len(bars) < lookback:
            return (0, 0)

        recent = bars[-lookback:]
        high = max(b.high for b in recent)
        low = min(b.low for b in recent)

        return (high, low)

    def get_market_data_for_strategy(self, symbol: str) -> dict:
        """전략용 시장 데이터 패키지"""
        snapshot = self.get_snapshot(symbol)
        high_20, low_20 = self.get_high_low(symbol, 20)

        return {
            "symbol": symbol,
            "spot_price": snapshot.spot_price if snapshot else 0,
            "perp_price": snapshot.perp_price if snapshot else 0,
            "funding_rate": snapshot.funding_rate if snapshot else 0,
            "price": snapshot.perp_price if snapshot else 0,  # 기본값은 perp
            "vwap": self.get_vwap(symbol),
            "rvol": self.get_rvol(symbol),
            "high_20": high_20,
            "low_20": low_20,
            "volume_24h": snapshot.volume_24h if snapshot else 0,
        }
