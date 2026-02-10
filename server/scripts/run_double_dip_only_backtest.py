"""DOUBLE_DIP_BUY 전략 단독 백테스트

OpenAI 분석 결과: DOUBLE_DIP_BUY가 유일하게 양의 총 수익 (+67.58%)
승률 60.1%, 가장 높은 성과
"""

import asyncio
import json
from datetime import datetime, timedelta

import structlog

from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.models.database import BacktestResultModel, BacktestTradeModel
from src.backtesting.models.schemas import BacktestConfig
from src.backtesting.strategies.strategy_adapters import get_signal_generator
from src.backtesting.engine.backtest_engine import create_pullback_exit_checker
from src.models.database import async_session

logger = structlog.get_logger()

# DOUBLE_DIP_BUY 전략 파라미터 (v27 기본값)
STRATEGIES = {
    "DOUBLE_DIP_BUY": {
        "stop_loss_pct": -0.020,       # -2.0%
        "take_profit_pct": 0.015,      # +1.5%
        "trailing_trigger_pct": 0.010,
        "trailing_stop_pct": 0.005,
    },
}

# 심볼 리스트 (전체 113개)
SYMBOLS = [
    "KRW-ETH", "KRW-IP", "KRW-DOGE", "KRW-USDT", "KRW-SHIB",
    "KRW-VIRTUAL", "KRW-TRX", "KRW-AVNT", "KRW-ETC", "KRW-PEPE",
    "KRW-SAND", "KRW-NEAR", "KRW-BCH", "KRW-MOVE", "KRW-0G",
    "KRW-PENGU", "KRW-HBAR", "KRW-FLOW", "KRW-POL", "KRW-AXS",
    "KRW-LAYER", "KRW-SEI", "KRW-WLD", "KRW-WLFI", "KRW-KAITO",
    "KRW-CTC", "KRW-ME", "KRW-ARB", "KRW-BOUNTY", "KRW-BORA",
    "KRW-FIL", "KRW-WET", "KRW-UNI", "KRW-APT", "KRW-CRO",
    "KRW-TRUMP", "KRW-NOM", "KRW-ANIME", "KRW-A", "KRW-BONK",
    "KRW-WAVES", "KRW-SONIC", "KRW-ATOM", "KRW-DOOD", "KRW-BLUR",
    "KRW-AXL", "KRW-BLAST", "KRW-NEO", "KRW-AERGO", "KRW-ALGO",
    "KRW-MOCA", "KRW-TRUST", "KRW-AAVE", "KRW-CELO", "KRW-AKT",
    "KRW-EGLD", "KRW-LSK", "KRW-WCT", "KRW-W", "KRW-ERA",
    "KRW-IN", "KRW-CARV", "KRW-AGLD", "KRW-PLUME", "KRW-ID",
    "KRW-G", "KRW-NXPC", "KRW-JTO", "KRW-DKA", "KRW-IOTA",
    "KRW-2Z", "KRW-F", "KRW-FF", "KRW-BAT", "KRW-PENDLE",
    "KRW-BIO", "KRW-ARK", "KRW-BEAM", "KRW-CVC", "KRW-ARDR",
    "KRW-JUP", "KRW-THETA", "KRW-WAXP", "KRW-USDC", "KRW-AQT",
    "KRW-CYBER", "KRW-JST", "KRW-TREE", "KRW-WAL", "KRW-IOST",
    "KRW-API3", "KRW-NEWT", "KRW-RVN", "KRW-AWE", "KRW-IQ",
    "KRW-LA", "KRW-LPT", "KRW-HP", "KRW-TOKAMAK", "KRW-T",
    "KRW-ANKR", "KRW-ARKM", "KRW-YGG", "KRW-PUNDIX", "KRW-POWR",
    "KRW-AHT", "KRW-HUNT", "KRW-ALT", "KRW-AERO", "KRW-USDE",
    "KRW-USD1", "KRW-COW", "KRW-BTC",
]


async def run_single_backtest(strategy: str, days: int, label: str):
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    params = STRATEGIES[strategy]

    config = BacktestConfig(
        strategy=strategy,
        symbols=SYMBOLS,
        start_date=start_date,
        end_date=end_date,
        interval="5m",
        initial_capital=1_000_000,
        parameters=params,
    )

    async with async_session() as db:
        loader = BacktestDataLoader(db)
        engine = BacktestEngine(config, loader)

        signal_generator = get_signal_generator(strategy, params)
        exit_checker = create_pullback_exit_checker(
            stop_loss_pct=params["stop_loss_pct"],
            take_profit_pct=params["take_profit_pct"],
            trailing_trigger_pct=params["trailing_trigger_pct"],
            trailing_stop_pct=params["trailing_stop_pct"],
        )

        result = await engine.run(signal_generator, exit_checker)

        return {
            "strategy": strategy,
            "label": label,
            "days": days,
            "run_id": result.run_id,
            "return_pct": result.metrics.total_return_pct,
            "win_rate": result.metrics.win_rate,
            "trades": result.metrics.total_trades,
            "pf": result.metrics.profit_factor or 0,
            "sharpe": result.metrics.sharpe_ratio or 0,
            "mdd": result.metrics.max_drawdown_pct,
            "avg_win": result.metrics.avg_win_pct or 0,
            "avg_loss": result.metrics.avg_loss_pct or 0,
        }


async def main():
    print("\n" + "=" * 100)
    print("DOUBLE_DIP_BUY 전략 단독 백테스트 (OpenAI 분석 결과 최고 성과 전략)")
    print("=" * 100)
    print(f"전략: DOUBLE_DIP_BUY (60.1% 승률, +67.58% 총수익)")
    print(f"SL: -2.0% / TP: +1.5%")
    print(f"심볼: {len(SYMBOLS)}개")
    print("=" * 100 + "\n")

    test_periods = [
        (1, "1일"),
        (7, "주간"),
        (30, "월간"),
    ]

    all_results = []

    for days, label in test_periods:
        print(f"\n{'='*100}")
        print(f"  {label} 백테스트 ({days}일)")
        print(f"{'='*100}\n")

        try:
            print(f"  DOUBLE_DIP_BUY 실행 중...")
            result = await run_single_backtest("DOUBLE_DIP_BUY", days, label)
            all_results.append(result)

            wr_emoji = "OK" if result['win_rate'] >= 55 else "LOW"
            ret_emoji = "+" if result['return_pct'] > 0 else "-"

            print(
                f"  [{wr_emoji}] DOUBLE_DIP_BUY | "
                f"수익률: {result['return_pct']:+7.2f}% | "
                f"승률: {result['win_rate']:5.1f}% | "
                f"거래: {result['trades']:4d}건 | "
                f"PF: {result['pf']:5.2f} | "
                f"Sharpe: {result['sharpe']:5.2f} | "
                f"MDD: {result['mdd']:5.2f}% | "
                f"Avg W: {result['avg_win']:+.2f}% / L: {result['avg_loss']:+.2f}%\n"
            )
        except Exception as e:
            print(f"  FAIL DOUBLE_DIP_BUY: {str(e)}\n")
            import traceback
            traceback.print_exc()

    # 결과 요약
    print("\n" + "=" * 100)
    print("  결과 요약")
    print("=" * 100)

    for r in all_results:
        status = "PASS" if r['return_pct'] > 0 and r['win_rate'] >= 55 else "FAIL"
        print(
            f"  [{status}] {r['label']:4s} | "
            f"수익률: {r['return_pct']:+7.2f}% | "
            f"승률: {r['win_rate']:5.1f}% | "
            f"거래: {r['trades']:4d}건"
        )

    # 기존 v27 결과와 비교
    print("\n" + "=" * 100)
    print("  v27 vs DOUBLE_DIP_BUY 비교 (월간 기준)")
    print("=" * 100)

    monthly = next((r for r in all_results if r['label'] == '월간'), None)
    if monthly:
        print(f"  v27 (10개 전략): 수익률 -263.03% | 평균 승률 41.1%")
        print(f"  DOUBLE_DIP_BUY: 수익률 {monthly['return_pct']:+.2f}% | 승률 {monthly['win_rate']:.1f}%")

        improvement = monthly['return_pct'] - (-263.03)
        print(f"\n  개선: {improvement:+.2f}%p")

    print(f"\n  완료!\n")


if __name__ == "__main__":
    asyncio.run(main())
