"""데이터베이스에서 사용 가능한 모든 심볼 조회"""

import asyncio

import structlog
from sqlalchemy import text

from src.models.database import async_session

logger = structlog.get_logger()


async def get_available_symbols(min_candles: int = 1000):
    """
    백테스트 데이터가 충분한 모든 심볼 조회

    Args:
        min_candles: 최소 캔들 수 (기본 1000개 = 5분봉 기준 약 3.5일)

    Returns:
        List of symbol strings
    """
    async with async_session() as db:
        query = text("""
            SELECT symbol, COUNT(*) as candle_count,
                   MIN(open_time) as first_candle,
                   MAX(open_time) as last_candle
            FROM backtest_candles
            WHERE interval = '5m'
            GROUP BY symbol
            HAVING COUNT(*) >= :min_candles
            ORDER BY COUNT(*) DESC
        """)

        result = await db.execute(query, {"min_candles": min_candles})
        rows = result.fetchall()

        print("\n" + "=" * 100)
        print(f"사용 가능한 심볼 ({len(rows)}개)")
        print("=" * 100 + "\n")

        symbols = []
        for row in rows:
            symbol, count, first, last = row
            symbols.append(symbol)

            # 날짜 범위 계산
            days = (last - first).days if first and last else 0

            print(
                f"{symbol:20s} | "
                f"{count:7,d} candles | "
                f"{days:4d} days | "
                f"{first.strftime('%Y-%m-%d') if first else 'N/A':10s} ~ "
                f"{last.strftime('%Y-%m-%d') if last else 'N/A':10s}"
            )

        print("\n" + "=" * 100)
        print(f"총 {len(symbols)}개 심볼")
        print("=" * 100 + "\n")

        print("Python 리스트 형식:")
        print("SYMBOLS = [")
        for i in range(0, len(symbols), 5):
            chunk = symbols[i:i+5]
            formatted = ", ".join([f'"{s}"' for s in chunk])
            print(f'    {formatted},')
        print("]")

        return symbols


async def main():
    symbols = await get_available_symbols(min_candles=1000)

    print(f"\n✅ 총 {len(symbols)}개 심볼 발견")
    print(f"최소 캔들 수: 1000개 (약 3.5일)\n")


if __name__ == "__main__":
    asyncio.run(main())
