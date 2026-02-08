"""과거 캔들 데이터 수집기

Upbit API에서 과거 캔들 데이터를 수집하여 DB에 저장
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.backtesting.models.database import BacktestCandleModel

logger = structlog.get_logger()


class HistoricalCandleFetcher:
    """
    과거 캔들 데이터 수집기

    Features:
    - Upbit API에서 과거 데이터 수집
    - Rate limiting 준수 (초당 10회)
    - 중복 방지 (upsert)
    - 진행 상황 콜백
    """

    UPBIT_API_URL = "https://api.upbit.com/v1"

    # 인터벌 -> 분
    INTERVAL_MINUTES = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
    }

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request_time = 0.0
        self._min_interval = 0.11  # 110ms (초당 ~9회)

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    async def _rate_limit(self) -> None:
        """Rate limiting"""
        import time

        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    async def _fetch_candles(
        self,
        symbol: str,
        interval: str,
        to: Optional[datetime] = None,
        count: int = 200,
    ) -> list[dict]:
        """
        Upbit API에서 캔들 조회

        Args:
            symbol: 마켓 코드 (KRW-BTC)
            interval: 인터벌 (1m, 5m, 15m, 1h, 4h)
            to: 기준 시간 (이 시간 이전 캔들 조회)
            count: 조회 개수 (최대 200)
        """
        await self._rate_limit()

        minutes = self.INTERVAL_MINUTES.get(interval, 5)
        endpoint = f"{self.UPBIT_API_URL}/candles/minutes/{minutes}"

        params = {
            "market": symbol,
            "count": min(count, 200),
        }

        if to:
            # Upbit API는 KST 기준이므로 UTC -> KST 변환
            to_kst = to + timedelta(hours=9)
            params["to"] = to_kst.strftime("%Y-%m-%d %H:%M:%S")

        try:
            response = await self._client.get(endpoint, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to fetch candles", symbol=symbol, error=str(e))
            return []

    def _parse_candle(self, symbol: str, interval: str, data: dict) -> BacktestCandleModel:
        """Upbit 캔들 응답을 DB 모델로 변환"""
        # candle_date_time_kst를 UTC로 변환
        kst_str = data.get("candle_date_time_kst", "")
        kst_time = datetime.strptime(kst_str, "%Y-%m-%dT%H:%M:%S")
        utc_time = kst_time - timedelta(hours=9)

        return BacktestCandleModel(
            symbol=symbol,
            interval=interval,
            open_time=utc_time,
            open=float(data.get("opening_price", 0)),
            high=float(data.get("high_price", 0)),
            low=float(data.get("low_price", 0)),
            close=float(data.get("trade_price", 0)),
            volume=float(data.get("candle_acc_trade_volume", 0)),
            quote_volume=float(data.get("candle_acc_trade_price", 0)),
        )

    async def fetch_and_store(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: Optional[datetime] = None,
        progress_callback: Optional[callable] = None,
    ) -> int:
        """
        과거 캔들 데이터 수집 및 저장

        Args:
            symbol: 마켓 코드 (KRW-BTC)
            interval: 인터벌 (1m, 5m, 15m, 1h, 4h)
            start_date: 시작 일시 (UTC)
            end_date: 종료 일시 (UTC, None이면 현재)
            progress_callback: 진행 상황 콜백 (fetched_count, total_estimate)

        Returns:
            저장된 캔들 수
        """
        if end_date is None:
            end_date = datetime.utcnow()

        minutes = self.INTERVAL_MINUTES.get(interval, 5)
        total_minutes = (end_date - start_date).total_seconds() / 60
        total_candles_estimate = int(total_minutes / minutes)

        logger.info(
            "Starting historical candle fetch",
            symbol=symbol,
            interval=interval,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            estimated_candles=total_candles_estimate,
        )

        fetched_count = 0
        current_to = end_date
        batch_size = 200

        while current_to > start_date:
            # API 호출
            candles = await self._fetch_candles(symbol, interval, current_to, batch_size)

            if not candles:
                break

            # DB에 저장 (upsert)
            models = []
            oldest_time = current_to

            for c in candles:
                model = self._parse_candle(symbol, interval, c)
                if model.open_time < start_date:
                    continue
                models.append(model)
                if model.open_time < oldest_time:
                    oldest_time = model.open_time

            if models:
                await self._upsert_candles(models)
                fetched_count += len(models)

            # 진행 상황 콜백
            if progress_callback:
                progress_callback(fetched_count, total_candles_estimate)

            # 다음 페이지
            current_to = oldest_time - timedelta(minutes=minutes)

            # 마지막 배치가 200개 미만이면 종료
            if len(candles) < batch_size:
                break

        logger.info(
            "Historical candle fetch complete",
            symbol=symbol,
            interval=interval,
            total_fetched=fetched_count,
        )

        return fetched_count

    async def _upsert_candles(self, candles: list[BacktestCandleModel]) -> None:
        """캔들 데이터 upsert"""
        for candle in candles:
            stmt = pg_insert(BacktestCandleModel).values(
                symbol=candle.symbol,
                interval=candle.interval,
                open_time=candle.open_time,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                quote_volume=candle.quote_volume,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_candle",
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                    "quote_volume": stmt.excluded.quote_volume,
                },
            )
            await self._session.execute(stmt)

        await self._session.commit()

    async def get_stored_range(
        self, symbol: str, interval: str
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        """
        저장된 캔들 데이터 범위 조회

        Returns:
            (oldest_time, newest_time) or (None, None) if no data
        """
        from sqlalchemy import func

        stmt = select(
            func.min(BacktestCandleModel.open_time),
            func.max(BacktestCandleModel.open_time),
        ).where(
            BacktestCandleModel.symbol == symbol,
            BacktestCandleModel.interval == interval,
        )

        result = await self._session.execute(stmt)
        row = result.first()

        if row and row[0] and row[1]:
            return row[0], row[1]
        return None, None

    async def fill_gaps(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> int:
        """
        누락된 캔들 데이터 채우기

        Args:
            symbol: 마켓 코드
            interval: 인터벌
            start_date: 시작 일시
            end_date: 종료 일시

        Returns:
            채워진 캔들 수
        """
        # 현재 저장된 범위 확인
        oldest, newest = await self.get_stored_range(symbol, interval)

        filled = 0

        if oldest is None:
            # 데이터가 없으면 전체 수집
            filled = await self.fetch_and_store(symbol, interval, start_date, end_date)
        else:
            # 앞쪽 갭 채우기
            if start_date < oldest:
                filled += await self.fetch_and_store(
                    symbol, interval, start_date, oldest
                )

            # 뒤쪽 갭 채우기
            if end_date > newest:
                filled += await self.fetch_and_store(
                    symbol, interval, newest, end_date
                )

        return filled
