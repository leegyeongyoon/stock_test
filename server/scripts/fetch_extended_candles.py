"""확장 데이터 수집 스크립트

70개 심볼 x 3개월 x 5분봉 = ~180만 캔들 수집
기존 HistoricalCandleFetcher 재사용, upsert로 중복 방지

실행: cd server && python -m scripts.fetch_extended_candles
"""

import asyncio
import time
from datetime import datetime, timedelta

import structlog

from src.backtesting.data.candle_fetcher import HistoricalCandleFetcher
from src.models.database import async_session

logger = structlog.get_logger()

SYMBOLS = [
    "KRW-BTC", "KRW-ETH", "KRW-DOGE", "KRW-SHIB", "KRW-TRX",
    "KRW-ETC", "KRW-PEPE", "KRW-NEAR", "KRW-BCH", "KRW-HBAR",
    "KRW-SAND", "KRW-SEI", "KRW-ARB", "KRW-FIL", "KRW-UNI",
    "KRW-APT", "KRW-ATOM", "KRW-AAVE", "KRW-AVNT", "KRW-VIRTUAL",
    "KRW-USDT", "KRW-MOVE", "KRW-0G", "KRW-PENGU", "KRW-FLOW",
    "KRW-POL", "KRW-AXS", "KRW-LAYER", "KRW-WLD", "KRW-KAITO",
    "KRW-CTC", "KRW-ME", "KRW-BOUNTY", "KRW-BORA", "KRW-WET",
    "KRW-CRO", "KRW-TRUMP", "KRW-NOM", "KRW-ANIME", "KRW-BONK",
    "KRW-WAVES", "KRW-SONIC", "KRW-DOOD", "KRW-BLUR", "KRW-AXL",
    "KRW-BLAST", "KRW-NEO", "KRW-AERGO", "KRW-ALGO", "KRW-MOCA",
    "KRW-TRUST", "KRW-CELO", "KRW-AKT", "KRW-EGLD", "KRW-LSK",
    "KRW-WCT", "KRW-W", "KRW-ERA", "KRW-IN", "KRW-CARV",
    "KRW-AGLD", "KRW-PLUME", "KRW-ID", "KRW-G", "KRW-NXPC",
    "KRW-JTO", "KRW-DKA", "KRW-IOTA", "KRW-IP", "KRW-A",
]

INTERVAL = "5m"
DAYS = 90  # 3개월


def _progress_callback(fetched: int, total: int) -> None:
    """진행률 출력"""
    pct = (fetched / total * 100) if total > 0 else 0
    print(f"    {fetched:,}/{total:,} ({pct:.1f}%)", end="\r")


async def main():
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=DAYS)

    print(f"\n{'=' * 80}")
    print(f"  Extended Candle Data Fetcher")
    print(f"{'=' * 80}")
    print(f"  Symbols: {len(SYMBOLS)}")
    print(f"  Interval: {INTERVAL}")
    print(f"  Period: {start_date.date()} ~ {end_date.date()} ({DAYS} days)")
    print(f"  Estimated candles: ~{len(SYMBOLS) * DAYS * 288:,} ({DAYS}d x 288 candles/day x {len(SYMBOLS)} symbols)")
    print(f"{'=' * 80}\n")

    total_fetched = 0
    total_start = time.time()
    failed_symbols = []

    async with async_session() as session:
        async with HistoricalCandleFetcher(session) as fetcher:
            for i, symbol in enumerate(SYMBOLS, 1):
                print(f"  [{i:2d}/{len(SYMBOLS)}] {symbol:15s}", end=" ")
                sym_start = time.time()

                try:
                    # 기존 데이터 범위 확인
                    oldest, newest = await fetcher.get_stored_range(symbol, INTERVAL)

                    if oldest and newest:
                        # gap filling
                        fetched = await fetcher.fill_gaps(
                            symbol, INTERVAL, start_date, end_date
                        )
                        if fetched == 0:
                            elapsed = time.time() - sym_start
                            print(f"already complete ({elapsed:.1f}s)")
                            continue
                    else:
                        # 전체 수집
                        fetched = await fetcher.fetch_and_store(
                            symbol, INTERVAL, start_date, end_date,
                            progress_callback=_progress_callback,
                        )

                    elapsed = time.time() - sym_start
                    total_fetched += fetched
                    print(f"{fetched:>6,} candles ({elapsed:.1f}s)")

                except Exception as e:
                    elapsed = time.time() - sym_start
                    print(f"FAILED: {str(e)[:60]} ({elapsed:.1f}s)")
                    failed_symbols.append(symbol)
                    logger.error("Symbol fetch failed", symbol=symbol, error=str(e))

    total_elapsed = time.time() - total_start

    print(f"\n{'=' * 80}")
    print(f"  Complete!")
    print(f"  Total fetched: {total_fetched:,} candles")
    print(f"  Duration: {total_elapsed / 60:.1f} min")
    print(f"  Failed: {len(failed_symbols)} symbols")
    if failed_symbols:
        print(f"  Failed symbols: {', '.join(failed_symbols)}")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    asyncio.run(main())
