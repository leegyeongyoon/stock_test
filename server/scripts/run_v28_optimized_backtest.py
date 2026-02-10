"""v28 데이터 기반 전략 - SL/TP 최적화 백테스트

다양한 SL/TP 조합으로 최적 파라미터 탐색
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

# SL/TP 조합 테스트
SL_TP_COMBINATIONS = [
    {"name": "원본", "sl": -0.050, "tp": 0.005},      # 원본: SL -5%, TP 0.5%
    {"name": "균형1", "sl": -0.020, "tp": 0.020},     # SL -2%, TP 2% (R:R 1:1)
    {"name": "균형2", "sl": -0.025, "tp": 0.015},     # SL -2.5%, TP 1.5%
    {"name": "보수적", "sl": -0.015, "tp": 0.010},    # SL -1.5%, TP 1%
    {"name": "공격적", "sl": -0.030, "tp": 0.025},    # SL -3%, TP 2.5%
]

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


async def run_backtest_with_params(sl_pct: float, tp_pct: float, days: int, name: str):
    """특정 SL/TP 조합으로 백테스트 실행"""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    params = {
        "stop_loss_pct": sl_pct,
        "take_profit_pct": tp_pct,
        "trailing_trigger_pct": tp_pct * 0.8,
        "trailing_stop_pct": tp_pct * 0.4,
    }

    config = BacktestConfig(
        strategy="DATA_DRIVEN_V28",
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

        signal_generator = get_signal_generator("DATA_DRIVEN_V28", params)
        exit_checker = create_pullback_exit_checker(
            stop_loss_pct=sl_pct,
            take_profit_pct=tp_pct,
            trailing_trigger_pct=tp_pct * 0.8,
            trailing_stop_pct=tp_pct * 0.4,
        )

        result = await engine.run(signal_generator, exit_checker)

        return {
            "name": name,
            "sl_pct": sl_pct * 100,
            "tp_pct": tp_pct * 100,
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
    print("\n" + "=" * 120)
    print("v28 데이터 기반 전략 - SL/TP 최적화 백테스트")
    print("=" * 120)
    print(f"테스트 조합: {len(SL_TP_COMBINATIONS)}개")
    print(f"심볼: {len(SYMBOLS)}개")
    print("=" * 120 + "\n")

    # 30일 백테스트로 비교
    days = 30
    all_results = []

    print(f"{'='*120}")
    print(f"  월간 백테스트 ({days}일)")
    print(f"{'='*120}\n")

    for combo in SL_TP_COMBINATIONS:
        try:
            print(f"  [{combo['name']}] SL {combo['sl']*100:.1f}% / TP {combo['tp']*100:.1f}% 실행 중...")
            result = await run_backtest_with_params(
                combo['sl'], combo['tp'], days, combo['name']
            )
            all_results.append(result)

            status = "+" if result['return_pct'] > 0 else "-"
            wr_status = "OK" if result['win_rate'] >= 55 else "LOW"

            print(
                f"  [{status}][{wr_status}] {combo['name']:6s} | "
                f"수익률: {result['return_pct']:+7.2f}% | "
                f"승률: {result['win_rate']:5.1f}% | "
                f"거래: {result['trades']:4d}건 | "
                f"PF: {result['pf']:5.2f} | "
                f"Avg W: {result['avg_win']:+.2f}% / L: {result['avg_loss']:+.2f}%\n"
            )
        except Exception as e:
            print(f"  FAIL {combo['name']}: {str(e)}\n")
            import traceback
            traceback.print_exc()

    # 결과 요약 (수익률 순)
    print("\n" + "=" * 120)
    print("  결과 요약 (수익률 순)")
    print("=" * 120)
    print(f"  {'설정':8s} | {'SL':>6s} | {'TP':>6s} | {'수익률':>10s} | {'승률':>7s} | {'거래':>6s} | {'PF':>6s} | {'Avg W':>8s} | {'Avg L':>8s}")
    print(f"  {'-'*8}-+-{'-'*6}-+-{'-'*6}-+-{'-'*10}-+-{'-'*7}-+-{'-'*6}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}")

    sorted_results = sorted(all_results, key=lambda x: x['return_pct'], reverse=True)
    for r in sorted_results:
        status = "BEST" if r == sorted_results[0] else "    "
        print(
            f"  {status} {r['name']:4s} | "
            f"{r['sl_pct']:+5.1f}% | "
            f"{r['tp_pct']:+5.1f}% | "
            f"{r['return_pct']:+9.2f}% | "
            f"{r['win_rate']:6.1f}% | "
            f"{r['trades']:5d} | "
            f"{r['pf']:5.2f} | "
            f"{r['avg_win']:+7.2f}% | "
            f"{r['avg_loss']:+7.2f}%"
        )

    # 최적 설정 출력
    best = sorted_results[0]
    print("\n" + "=" * 120)
    print("  최적 설정")
    print("=" * 120)
    print(f"  설정: {best['name']}")
    print(f"  SL: {best['sl_pct']:.1f}% / TP: {best['tp_pct']:.1f}%")
    print(f"  수익률: {best['return_pct']:+.2f}%")
    print(f"  승률: {best['win_rate']:.1f}%")
    print(f"  Profit Factor: {best['pf']:.2f}")

    # v27 대비 개선
    print("\n" + "=" * 120)
    print("  v27 대비 개선")
    print("=" * 120)
    print(f"  v27 (10개 전략): 수익률 -263.03% | 평균 승률 41.1%")
    print(f"  v28 원본:        수익률 -33.30% | 승률 71.0%")
    print(f"  v28 최적화:      수익률 {best['return_pct']:+.2f}% | 승률 {best['win_rate']:.1f}%")

    improvement = best['return_pct'] - (-263.03)
    print(f"\n  총 개선: {improvement:+.2f}%p (vs v27)")

    print(f"\n  완료!\n")


if __name__ == "__main__":
    asyncio.run(main())
