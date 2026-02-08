"""백테스트 데이터 로더

DB에서 캔들 데이터를 로드하여 백테스트 엔진에 제공
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.backtesting.models.database import BacktestCandleModel
from src.data.candle_manager import Candle

logger = structlog.get_logger()


@dataclass
class BacktestCandle:
    """백테스트용 캔들 데이터"""

    symbol: str
    interval: str
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float

    def to_candle(self) -> Candle:
        """CandleManager 호환 Candle 객체로 변환"""
        return Candle(
            symbol=self.symbol,
            interval=self.interval,
            open_time=self.open_time,
            close_time=self.open_time,  # 백테스트에서는 open_time만 사용
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            quote_volume=self.quote_volume,
        )


@dataclass
class MarketSnapshot:
    """특정 시점의 마켓 스냅샷 (여러 심볼)"""

    timestamp: datetime
    candles: dict[str, BacktestCandle] = field(default_factory=dict)

    def get_price(self, symbol: str) -> Optional[float]:
        """심볼의 현재가 (종가)"""
        if symbol in self.candles:
            return self.candles[symbol].close
        return None

    def get_candle(self, symbol: str) -> Optional[BacktestCandle]:
        """심볼의 캔들"""
        return self.candles.get(symbol)


class BacktestDataLoader:
    """
    백테스트 데이터 로더

    Features:
    - DB에서 캔들 데이터 로드
    - 시간순 이터레이션
    - 메모리 효율적 배치 로딩
    - 여러 심볼 동시 로드
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._cache: dict[str, list[BacktestCandle]] = {}

    async def load_candles(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> list[BacktestCandle]:
        """
        캔들 데이터 로드

        Args:
            symbol: 마켓 코드 (KRW-BTC)
            interval: 인터벌 (1m, 5m, 15m, 1h)
            start_date: 시작 일시 (UTC)
            end_date: 종료 일시 (UTC)

        Returns:
            시간순 정렬된 캔들 리스트
        """
        cache_key = f"{symbol}_{interval}_{start_date}_{end_date}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        stmt = (
            select(BacktestCandleModel)
            .where(
                BacktestCandleModel.symbol == symbol,
                BacktestCandleModel.interval == interval,
                BacktestCandleModel.open_time >= start_date,
                BacktestCandleModel.open_time <= end_date,
            )
            .order_by(BacktestCandleModel.open_time)
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        candles = [
            BacktestCandle(
                symbol=row.symbol,
                interval=row.interval,
                open_time=row.open_time,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                quote_volume=row.quote_volume,
            )
            for row in rows
        ]

        self._cache[cache_key] = candles

        logger.info(
            "Loaded candles for backtest",
            symbol=symbol,
            interval=interval,
            count=len(candles),
        )

        return candles

    async def load_multi_symbol(
        self,
        symbols: list[str],
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, list[BacktestCandle]]:
        """
        여러 심볼의 캔들 데이터 로드

        Args:
            symbols: 마켓 코드 리스트
            interval: 인터벌
            start_date: 시작 일시
            end_date: 종료 일시

        Returns:
            {symbol: candles} 딕셔너리
        """
        result = {}
        for symbol in symbols:
            result[symbol] = await self.load_candles(
                symbol, interval, start_date, end_date
            )
        return result

    async def iterate_snapshots(
        self,
        symbols: list[str],
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Iterator[MarketSnapshot]:
        """
        시간순 마켓 스냅샷 이터레이터

        Args:
            symbols: 마켓 코드 리스트
            interval: 인터벌
            start_date: 시작 일시
            end_date: 종료 일시

        Yields:
            각 타임스탬프의 MarketSnapshot
        """
        # 모든 심볼 데이터 로드
        all_candles = await self.load_multi_symbol(
            symbols, interval, start_date, end_date
        )

        # 심볼별 인덱스
        indices: dict[str, int] = {s: 0 for s in symbols}

        # 모든 타임스탬프 수집 및 정렬
        timestamps = set()
        for candles in all_candles.values():
            for c in candles:
                timestamps.add(c.open_time)

        sorted_timestamps = sorted(timestamps)

        # 각 타임스탬프에 대해 스냅샷 생성
        for ts in sorted_timestamps:
            snapshot = MarketSnapshot(timestamp=ts)

            for symbol, candles in all_candles.items():
                idx = indices[symbol]

                # 현재 타임스탬프에 해당하는 캔들 찾기
                while idx < len(candles) and candles[idx].open_time < ts:
                    idx += 1

                if idx < len(candles) and candles[idx].open_time == ts:
                    snapshot.candles[symbol] = candles[idx]
                    indices[symbol] = idx + 1

            yield snapshot

    async def get_candle_count(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> int:
        """저장된 캔들 수 조회"""
        from sqlalchemy import func

        stmt = (
            select(func.count())
            .select_from(BacktestCandleModel)
            .where(
                BacktestCandleModel.symbol == symbol,
                BacktestCandleModel.interval == interval,
                BacktestCandleModel.open_time >= start_date,
                BacktestCandleModel.open_time <= end_date,
            )
        )

        result = await self._session.execute(stmt)
        return result.scalar() or 0

    def clear_cache(self) -> None:
        """캐시 클리어"""
        self._cache.clear()


class HistoryProvider:
    """
    백테스트용 히스토리 프로바이더

    실시간 CandleManager와 유사한 인터페이스 제공
    전략에서 과거 캔들 데이터 조회용
    """

    def __init__(self) -> None:
        # 심볼 -> 인터벌 -> 캔들 리스트
        self._data: dict[str, dict[str, list[BacktestCandle]]] = {}
        # 심볼 -> 인터벌 -> 현재 인덱스
        self._current_idx: dict[str, dict[str, int]] = {}

    def load_data(
        self,
        symbol: str,
        interval: str,
        candles: list[BacktestCandle],
    ) -> None:
        """데이터 로드"""
        if symbol not in self._data:
            self._data[symbol] = {}
            self._current_idx[symbol] = {}

        self._data[symbol][interval] = candles
        self._current_idx[symbol][interval] = 0

    def set_time(self, timestamp: datetime) -> None:
        """현재 시간 설정 (해당 시간까지의 캔들만 접근 가능)"""
        for symbol in self._data:
            for interval in self._data[symbol]:
                candles = self._data[symbol][interval]
                idx = 0
                for i, c in enumerate(candles):
                    if c.open_time <= timestamp:
                        idx = i + 1
                    else:
                        break
                self._current_idx[symbol][interval] = idx

    def get_candles(
        self, symbol: str, interval: str, count: int = 50
    ) -> list[BacktestCandle]:
        """
        현재 시점까지의 캔들 조회

        Args:
            symbol: 마켓 코드
            interval: 인터벌
            count: 조회할 캔들 수

        Returns:
            최근 N개 캔들 (시간순)
        """
        if symbol not in self._data or interval not in self._data[symbol]:
            return []

        idx = self._current_idx.get(symbol, {}).get(interval, 0)
        candles = self._data[symbol][interval][:idx]

        return candles[-count:] if len(candles) > count else candles

    def get_closes(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        """종가 리스트"""
        candles = self.get_candles(symbol, interval, count)
        return [c.close for c in candles]

    def get_highs(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        """고가 리스트"""
        candles = self.get_candles(symbol, interval, count)
        return [c.high for c in candles]

    def get_lows(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        """저가 리스트"""
        candles = self.get_candles(symbol, interval, count)
        return [c.low for c in candles]

    def get_volumes(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        """거래량 리스트"""
        candles = self.get_candles(symbol, interval, count)
        return [c.quote_volume for c in candles]

    def get_current_price(self, symbol: str, interval: str = "5m") -> Optional[float]:
        """현재가"""
        candles = self.get_candles(symbol, interval, 1)
        if candles:
            return candles[-1].close
        return None

    def calc_sma(
        self, symbol: str, interval: str, period: int
    ) -> Optional[float]:
        """SMA 계산"""
        closes = self.get_closes(symbol, interval, period)
        if len(closes) < period:
            return None
        return sum(closes) / len(closes)

    def calc_ema(
        self, symbol: str, interval: str, period: int
    ) -> Optional[float]:
        """EMA 계산"""
        closes = self.get_closes(symbol, interval, period + 10)
        if len(closes) < period:
            return None

        multiplier = 2 / (period + 1)
        ema = closes[0]

        for close in closes[1:]:
            ema = (close - ema) * multiplier + ema

        return ema

    def calc_rvol(
        self, symbol: str, interval: str, period: int = 20
    ) -> Optional[float]:
        """RVOL 계산"""
        volumes = self.get_volumes(symbol, interval, period + 1)
        if len(volumes) < period + 1:
            return None

        current = volumes[-1]
        avg = sum(volumes[:-1]) / len(volumes[:-1])

        if avg <= 0:
            return None

        return current / avg

    def calc_atr(
        self, symbol: str, interval: str, period: int = 14
    ) -> Optional[float]:
        """ATR 계산"""
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
