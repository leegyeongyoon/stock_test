"""v4.1 공격적 실투자 시뮬레이션 - 수익률 극대화

현금 제약에서 수익률이 낮은 원인 분석:
1. 시그널 군집: 4개 전략이 동시에 시그널 (급락 반등 시) → 대부분 놓침
2. 현금 대기: 포지션 보유 중 현금 부족 → 새 시그널 진입 불가
3. 포지션 크기 제한: equity * 15% = 작은 포지션

해결: 포지션 크기 극대화 + 최소 동시포지션 + 빠른 자본 회전

실행: cd server && python -m scripts.run_v4_aggressive
"""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.models.schemas import BacktestConfig
from src.backtesting.strategies.combined_strategy import (
    CombinedSignalGenerator,
    CombinedExitChecker,
)
from src.backtesting.strategies.strategy_adapters import get_signal_generator
from src.models.database import async_session

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

BASE_EXIT_PARAMS = {
    "VOLATILE_OVERSOLD_BOUNCE": {
        "stop_loss_pct": -0.020, "take_profit_pct": 0.020,
        "trailing_trigger_pct": 0.015, "trailing_stop_pct": 0.008,
        "time_stop_minutes": 180,
    },
    "CRASH_RECOVERY": {
        "stop_loss_pct": -0.015, "take_profit_pct": 0.020,
        "trailing_trigger_pct": 0.015, "trailing_stop_pct": 0.005,
        "time_stop_minutes": 120,
    },
    "TRIPLE_BEARISH_REVERSAL": {
        "stop_loss_pct": -0.020, "take_profit_pct": 0.020,
        "trailing_trigger_pct": 0.012, "trailing_stop_pct": 0.008,
        "time_stop_minutes": 90,
    },
    "VOLUME_SURGE_REVERSAL": {
        "stop_loss_pct": -0.015, "take_profit_pct": 0.020,
        "trailing_trigger_pct": 0.015, "trailing_stop_pct": 0.008,
        "time_stop_minutes": 60,
    },
}

BASE_SIGNAL_PARAMS = {
    "VOLATILE_OVERSOLD_BOUNCE": {
        "max_rsi": 25, "min_rvol": 2.5, "min_atr_pct": 1.3,
        "cooldown_minutes": 10, "blocked_hours_kst": [2, 3],
    },
    "CRASH_RECOVERY": {
        "max_rsi": 25, "min_rvol": 2.5, "min_drop_pct": 0.03,
        "cooldown_minutes": 10, "blocked_hours_kst": [2, 3, 5, 20],
    },
    "TRIPLE_BEARISH_REVERSAL": {
        "max_rsi": 25, "min_atr_pct": 1.3,
        "cooldown_minutes": 10, "blocked_hours_kst": [2, 8, 17, 19],
    },
    "VOLUME_SURGE_REVERSAL": {
        "min_rvol": 4.0, "min_drop_pct": 0.025,
        "min_close_position": 0.60, "max_close_position": 0.90,
        "cooldown_minutes": 10, "blocked_hours_kst": [0, 2, 4, 20],
    },
}

# 공격적 시나리오
SCENARIOS = [
    # (이름, 자본금, 포지션%, 최대동시, 마진)
    ("기준: 15%x8", 1_000_000, 0.15, 8, False),
    ("공격1: 30%x4", 1_000_000, 0.30, 4, False),
    ("공격2: 40%x3", 1_000_000, 0.40, 3, False),
    ("공격3: 50%x2", 1_000_000, 0.50, 2, False),
    ("공격4: 60%x2", 1_000_000, 0.60, 2, False),
    ("공격5: 80%x1", 1_000_000, 0.80, 1, False),
    ("올인: 95%x1", 1_000_000, 0.95, 1, False),
    ("마진참고", 1_000_000, 0.30, 100, True),
]


async def run_scenario(name, capital, pos_pct, max_pos, margin, days, session):
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    generators = []
    for strategy_name, base_params in BASE_SIGNAL_PARAMS.items():
        params = {**base_params, "position_pct": pos_pct}
        gen = get_signal_generator(strategy_name, params)
        generators.append(gen)

    combined_signal = CombinedSignalGenerator(generators)
    combined_exit = CombinedExitChecker(BASE_EXIT_PARAMS)

    config = BacktestConfig(
        strategy="V4_AGGRESSIVE",
        symbols=SYMBOLS,
        start_date=start_date,
        end_date=end_date,
        interval="5m",
        initial_capital=capital,
        commission_rate=0.0005,
        slippage_bps=5,
        parameters={
            "max_positions": max_pos,
            "multi_strategy": True,
            "margin_mode": margin,
        },
    )

    data_loader = BacktestDataLoader(session)
    engine = BacktestEngine(config, data_loader)
    result = await engine.run(
        signal_generator=combined_signal,
        exit_checker=combined_exit,
    )

    final_equity = result.final_equity
    total_return = (final_equity - capital) / capital * 100
    m = result.metrics

    # 전략별 통계
    by_strat = defaultdict(list)
    for t in result.trades:
        by_strat[t.strategy].append(t)

    # 최대 동시 포지션 추적 (equity curve에서 추정)
    max_concurrent = 0
    for point in result.equity_curve:
        # equity - cash 로 추정 (entry_price 기반이므로 근사치)
        pass

    return {
        "name": name,
        "capital": capital,
        "pos_pct": pos_pct,
        "max_pos": max_pos,
        "margin": margin,
        "final_equity": final_equity,
        "return": total_return,
        "profit": final_equity - capital,
        "trades": m.total_trades,
        "wr": m.win_rate,
        "pf": m.profit_factor,
        "mdd": m.max_drawdown_pct,
        "sharpe": m.sharpe_ratio,
        "avg_hold": m.avg_holding_minutes,
        "by_strat": {
            s: (len(ts), sum(1 for t in ts if t.pnl > 0) / len(ts) * 100, sum(t.pnl for t in ts))
            for s, ts in by_strat.items()
        },
    }


async def main():
    for days in [30, 90]:
        print(f"\n{'='*85}")
        print(f"  v4.1 공격적 시뮬레이션 비교 ({days}일)")
        print(f"  수수료 0.05% + 슬리피지 5bps, 현금기반(마진OFF)")
        print(f"{'='*85}\n")

        results = []
        async with async_session() as session:
            for i, (name, cap, pct, mx, mg) in enumerate(SCENARIOS):
                print(f"  [{i+1}/{len(SCENARIOS)}] {name}...", end="", flush=True)
                r = await run_scenario(name, cap, pct, mx, mg, days, session)
                results.append(r)
                print(f" {r['return']:+.1f}% | {r['trades']}건 | WR {r['wr']:.0f}% | MDD {r['mdd']:.1f}%")

        # 결과 테이블
        print(f"\n  {'시나리오':<18} {'수익률':>8} {'순수익':>12} {'거래':>5} {'승률':>6} {'PF':>5} {'MDD':>6} {'샤프':>6} {'보유':>5}")
        print(f"  {'-'*78}")

        for r in results:
            mg_tag = "*" if r["margin"] else " "
            print(
                f" {mg_tag}{r['name']:<17}"
                f" {r['return']:>+7.1f}%"
                f" {r['profit']:>+11,.0f}"
                f" {r['trades']:>5}"
                f" {r['wr']:>5.1f}%"
                f" {r['pf']:>5.2f}"
                f" {r['mdd']:>5.1f}%"
                f" {r['sharpe']:>5.1f}"
                f" {r['avg_hold']:>4.0f}m"
            )

        # 전략별 디테일 (최적 시나리오)
        realistic = [r for r in results if not r["margin"]]
        best = max(realistic, key=lambda x: x["return"])

        print(f"\n  최적: {best['name']} → {best['return']:+.1f}%")
        print(f"  전략별:")
        for s, (cnt, wr, pnl) in sorted(best["by_strat"].items()):
            print(f"    {s:<30} {cnt:>4}건 WR {wr:>5.1f}% PNL {pnl:>+10,.0f}")

        # 연 환산
        print(f"\n  {'시나리오':<18} {'월수익률':>8} {'연환산':>10} {'연수익(1M)':>14}")
        print(f"  {'-'*55}")
        for r in results:
            if r["margin"]:
                continue
            monthly = r["return"] / (days / 30) if days > 0 else 0
            annual = ((1 + monthly / 100) ** 12 - 1) * 100
            annual_profit = 1_000_000 * annual / 100
            print(
                f"  {r['name']:<18}"
                f" {monthly:>+7.1f}%"
                f" {annual:>+9.0f}%"
                f" {annual_profit:>+13,.0f}"
            )


if __name__ == "__main__":
    asyncio.run(main())
