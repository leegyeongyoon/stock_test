"""AI 전략 최적화 실행 스크립트

전체 파이프라인 실행:
1. 데이터 확인 (3개월 5분봉)
2. 15라운드 AI 최적화 실행
3. Walk-forward 검증
4. 앙상블 분석
5. 결과 저장

실행: cd server && python -m scripts.run_ai_optimizer
옵션:
  --rounds N    : 라운드 수 (기본 15)
  --symbols N   : 심볼 수 (기본 70)
  --quick       : 빠른 테스트 (5라운드, 20심볼, 30일)
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# .env 로드
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.ai_optimizer.discovery_engine import DEFAULT_SYMBOLS, DiscoveryEngine
from src.models.database import async_session


def parse_args():
    parser = argparse.ArgumentParser(description="AI Strategy Optimization Engine")
    parser.add_argument("--rounds", type=int, default=15, help="Number of optimization rounds")
    parser.add_argument("--symbols", type=int, default=70, help="Number of symbols to use")
    parser.add_argument("--quick", action="store_true", help="Quick test mode (5 rounds, 20 symbols)")
    parser.add_argument("--capital", type=float, default=1_000_000, help="Initial capital (KRW)")
    return parser.parse_args()


async def check_data_availability(session, symbols: list[str]) -> int:
    """데이터 가용성 확인"""
    from datetime import datetime, timedelta

    from sqlalchemy import func, select

    from src.backtesting.models.database import BacktestCandleModel

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=90)

    stmt = (
        select(
            BacktestCandleModel.symbol,
            func.count().label("count"),
        )
        .where(
            BacktestCandleModel.symbol.in_(symbols),
            BacktestCandleModel.interval == "5m",
            BacktestCandleModel.open_time >= start_date,
            BacktestCandleModel.open_time <= end_date,
        )
        .group_by(BacktestCandleModel.symbol)
    )

    result = await session.execute(stmt)
    rows = result.all()

    symbols_with_data = {row[0]: row[1] for row in rows}
    total_candles = sum(symbols_with_data.values())

    print(f"\n  Data check: {len(symbols_with_data)}/{len(symbols)} symbols have data")
    print(f"  Total candles: {total_candles:,}")

    min_candles = 1000  # 최소 ~3.5일
    ready_symbols = [s for s, c in symbols_with_data.items() if c >= min_candles]
    print(f"  Symbols with enough data (>{min_candles} candles): {len(ready_symbols)}")

    if len(ready_symbols) < 10:
        print(f"\n  WARNING: Not enough data. Run fetch_extended_candles first:")
        print(f"  cd server && python -m scripts.fetch_extended_candles\n")
        return 0

    return len(ready_symbols)


async def main():
    args = parse_args()

    # OpenAI API 키 확인
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in environment or .env file")
        sys.exit(1)

    # 설정
    if args.quick:
        total_rounds = 5
        symbols = DEFAULT_SYMBOLS[:20]
        print("  Quick mode: 5 rounds, 20 symbols")
    else:
        total_rounds = args.rounds
        symbols = DEFAULT_SYMBOLS[:args.symbols]

    async with async_session() as session:
        # 데이터 확인
        ready_count = await check_data_availability(session, symbols)
        if ready_count == 0:
            return

    # 엔진 실행 (각 백테스트마다 자체 DB 세션 생성)
    engine = DiscoveryEngine(
        openai_api_key=api_key,
        symbols=symbols[:ready_count] if ready_count < len(symbols) else symbols,
        total_rounds=total_rounds,
        initial_capital=args.capital,
    )

    results = await engine.run()

    # 요약 출력
    selected = results.get("selected_strategies", [])
    if selected:
        print(f"\n  Next steps:")
        print(f"  1. Review: reports/analysis/ai_optimization_results.json")
        print(f"  2. Strategies: src/strategies/generated/")
        print(f"  3. Convert to live: Update v4_live.py with best strategies")
    else:
        print(f"\n  No strategies passed all criteria.")
        print(f"  Try adjusting selection criteria or running more rounds.")


if __name__ == "__main__":
    asyncio.run(main())
