"""v4.1 실투자 시뮬레이션 - 최적 파라미터 탐색

포지션 크기 / 최대동시 포지션 / 자본금 조합별 비교
핵심: 현금 병목 최소화 → 더 많은 시그널 포착

실행: cd server && python -m scripts.run_v4_realistic_compare
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

# 전략 시그널/청산 파라미터 (고정 - v4.1 데이터 분석 기반)
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


# 비교할 시나리오들
SCENARIOS = [
    # (이름, 자본금, 포지션%, 최대동시, 마진모드)
    ("A) 기본 (15% x 8)", 1_000_000, 0.15, 8, False),
    ("B) 분산 (8% x 12)", 1_000_000, 0.08, 12, False),
    ("C) 집중 (20% x 5)", 1_000_000, 0.20, 5, False),
    ("D) 소형분산 (5% x 20)", 1_000_000, 0.05, 20, False),
    ("E) 자본2x (15% x 8)", 2_000_000, 0.15, 8, False),
    ("F) 자본5x (15% x 8)", 5_000_000, 0.15, 8, False),
    ("G) 자본5x+분산 (8% x 15)", 5_000_000, 0.08, 15, False),
    ("H) 마진기준 (30% x 100)", 1_000_000, 0.30, 100, True),
]


async def run_scenario(
    name: str,
    capital: int,
    pos_pct: float,
    max_pos: int,
    margin: bool,
    days: int,
    session,
) -> dict:
    """단일 시나리오 실행"""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # 시그널 생성기
    generators = []
    for strategy_name, base_params in BASE_SIGNAL_PARAMS.items():
        params = {**base_params, "position_pct": pos_pct}
        gen = get_signal_generator(strategy_name, params)
        generators.append(gen)

    combined_signal = CombinedSignalGenerator(generators)
    combined_exit = CombinedExitChecker(BASE_EXIT_PARAMS)

    config = BacktestConfig(
        strategy="V4_COMPARE",
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
    metrics = result.metrics

    # 전략별
    by_strategy = defaultdict(list)
    for t in result.trades:
        by_strategy[t.strategy].append(t)

    return {
        "name": name,
        "capital": capital,
        "pos_pct": pos_pct,
        "max_pos": max_pos,
        "margin": margin,
        "final_equity": final_equity,
        "total_return": total_return,
        "net_profit": final_equity - capital,
        "trades": metrics.total_trades,
        "win_rate": metrics.win_rate,
        "profit_factor": metrics.profit_factor,
        "max_dd": metrics.max_drawdown_pct,
        "sharpe": metrics.sharpe_ratio,
        "avg_hold": metrics.avg_holding_minutes,
        "by_strategy": {
            s: {
                "trades": len(ts),
                "wr": sum(1 for t in ts if t.pnl > 0) / len(ts) * 100 if ts else 0,
                "pnl": sum(t.pnl for t in ts),
            }
            for s, ts in by_strategy.items()
        },
    }


async def main():
    days = 30

    print(f"\n{'='*80}")
    print(f"  v4.1 실투자 시뮬레이션 - 파라미터 최적화 비교 ({days}일)")
    print(f"{'='*80}")
    print(f"  시그널 조건: v4.1 데이터 분석 기반 (RSI<25, ATR>1.3%, 시간필터)")
    print(f"  수수료: 0.05% + 슬리피지 5bps")
    print()

    results = []

    async with async_session() as session:
        for i, (name, capital, pos_pct, max_pos, margin) in enumerate(SCENARIOS):
            print(f"  [{i+1}/{len(SCENARIOS)}] {name} 실행 중...", end="", flush=True)
            r = await run_scenario(name, capital, pos_pct, max_pos, margin, days, session)
            results.append(r)
            print(f" → {r['total_return']:+.1f}% ({r['trades']}건)")

    # ─── 결과 테이블 ───

    print(f"\n{'='*80}")
    print(f"  비교 결과 ({days}일)")
    print(f"{'='*80}")
    print()
    print(f"  {'시나리오':<28} {'자본':>10} {'수익률':>8} {'순수익':>12} {'거래':>5} {'승률':>6} {'PF':>5} {'MDD':>6}")
    print(f"  {'-'*84}")

    for r in results:
        cap_str = f"{r['capital']/1_000_000:.0f}M"
        mode = " *마진" if r["margin"] else ""
        print(
            f"  {r['name']:<28} {cap_str:>6}{mode:<4}"
            f" {r['total_return']:>+7.1f}%"
            f" {r['net_profit']:>+11,.0f}"
            f" {r['trades']:>5}"
            f" {r['win_rate']:>5.1f}%"
            f" {r['profit_factor']:>5.2f}"
            f" {r['max_dd']:>5.1f}%"
        )

    # ─── 최적 시나리오 (마진 제외) ───

    realistic = [r for r in results if not r["margin"]]
    best = max(realistic, key=lambda x: x["total_return"])

    print(f"\n  {'='*60}")
    print(f"  최적 실투자 시나리오: {best['name']}")
    print(f"  {'='*60}")
    print(f"  수익률: {best['total_return']:+.2f}%")
    print(f"  순수익: {best['net_profit']:+,.0f} KRW")
    print(f"  거래:   {best['trades']}건, 승률 {best['win_rate']:.1f}%")
    print(f"  PF:     {best['profit_factor']:.2f}")
    print(f"  MDD:    {best['max_dd']:.2f}%")
    print(f"  샤프:   {best['sharpe']:.2f}")

    # ─── 동일 자본(1M) 내 비교 ───

    same_cap = [r for r in results if r["capital"] == 1_000_000 and not r["margin"]]
    if same_cap:
        best_1m = max(same_cap, key=lambda x: x["total_return"])
        print(f"\n  100만원 기준 최적: {best_1m['name']}")
        print(f"  → 수익률 {best_1m['total_return']:+.2f}%, 순수익 {best_1m['net_profit']:+,.0f} KRW")

    # 90일도 최적 시나리오로 실행
    print(f"\n{'='*80}")
    print(f"  최적 시나리오로 90일 검증")
    print(f"{'='*80}")

    async with async_session() as session:
        r90 = await run_scenario(
            best["name"], best["capital"], best["pos_pct"],
            best["max_pos"], False, 90, session,
        )
        print(f"\n  90일 결과:")
        print(f"  수익률: {r90['total_return']:+.2f}%")
        print(f"  순수익: {r90['net_profit']:+,.0f} KRW")
        print(f"  거래:   {r90['trades']}건, 승률 {r90['win_rate']:.1f}%")
        print(f"  PF:     {r90['profit_factor']:.2f}")
        print(f"  MDD:    {r90['max_dd']:.2f}%")


if __name__ == "__main__":
    asyncio.run(main())
