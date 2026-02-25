"""v4 백테스트 거래 데이터 심층 분석

승리/패배 거래의 진입 시점 지표 패턴 비교 분석
→ 전략 고도화를 위한 데이터 근거 확보

실행: cd server && python -m scripts.analyze_v4_trades
"""

import asyncio
import json
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

# 상위 4개 전략만 분석
STRATEGY_CONFIGS = {
    "VOLATILE_OVERSOLD_BOUNCE": {
        "signal_params": {
            "position_pct": 0.30, "max_rsi": 30, "min_rvol": 2.5,
            "min_atr_pct": 1.0, "cooldown_minutes": 10,
        },
        "exit_params": {
            "stop_loss_pct": -0.020, "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015, "trailing_stop_pct": 0.008,
            "time_stop_minutes": 180,
        },
    },
    "CRASH_RECOVERY": {
        "signal_params": {
            "position_pct": 0.15, "max_rsi": 35, "min_rvol": 2.5,
            "min_drop_pct": 0.02, "cooldown_minutes": 10,
        },
        "exit_params": {
            "stop_loss_pct": -0.015, "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015, "trailing_stop_pct": 0.005,
            "time_stop_minutes": 120,
        },
    },
    "TRIPLE_BEARISH_REVERSAL": {
        "signal_params": {
            "position_pct": 0.30, "max_rsi": 30, "min_atr_pct": 1.0,
            "cooldown_minutes": 10,
        },
        "exit_params": {
            "stop_loss_pct": -0.020, "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.020, "trailing_stop_pct": 0.010,
            "time_stop_minutes": 180,
        },
    },
    "VOLUME_SURGE_REVERSAL": {
        "signal_params": {
            "position_pct": 0.30, "cooldown_minutes": 10,
            "min_rvol": 3.5, "min_drop_pct": 0.015,
            "min_close_position": 0.60,
        },
        "exit_params": {
            "stop_loss_pct": -0.015, "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015, "trailing_stop_pct": 0.008,
            "time_stop_minutes": 90,
        },
    },
}


def percentile(data, pct):
    """간단한 백분위 계산"""
    if not data:
        return 0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * pct / 100)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]


def analyze_indicator(name, win_vals, lose_vals):
    """지표 분석 (승/패 비교)"""
    if not win_vals or not lose_vals:
        return None

    win_avg = sum(win_vals) / len(win_vals)
    lose_avg = sum(lose_vals) / len(lose_vals)
    win_med = percentile(win_vals, 50)
    lose_med = percentile(lose_vals, 50)
    win_p25 = percentile(win_vals, 25)
    win_p75 = percentile(win_vals, 75)
    lose_p25 = percentile(lose_vals, 25)
    lose_p75 = percentile(lose_vals, 75)

    return {
        "name": name,
        "win_avg": win_avg, "lose_avg": lose_avg,
        "win_median": win_med, "lose_median": lose_med,
        "win_p25": win_p25, "win_p75": win_p75,
        "lose_p25": lose_p25, "lose_p75": lose_p75,
        "diff_pct": ((win_avg - lose_avg) / abs(lose_avg) * 100) if lose_avg != 0 else 0,
    }


async def run_analysis():
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30)

    print("=" * 70)
    print("  v4 거래 데이터 심층 분석 (30일)")
    print("=" * 70)

    generators = []
    for strategy_name, config in STRATEGY_CONFIGS.items():
        gen = get_signal_generator(strategy_name, config["signal_params"])
        generators.append(gen)

    combined_signal = CombinedSignalGenerator(generators)
    exit_params = {name: config["exit_params"] for name, config in STRATEGY_CONFIGS.items()}
    combined_exit = CombinedExitChecker(exit_params)

    config = BacktestConfig(
        strategy="V4_ANALYSIS",
        symbols=SYMBOLS,
        start_date=start_date,
        end_date=end_date,
        interval="5m",
        initial_capital=1_000_000,
        parameters={"max_positions": 100, "multi_strategy": True, "margin_mode": True},
    )

    async with async_session() as session:
        data_loader = BacktestDataLoader(session)
        engine = BacktestEngine(config, data_loader)
        result = await engine.run(
            signal_generator=combined_signal,
            exit_checker=combined_exit,
        )

    trades = result.trades
    print(f"\n  총 거래: {len(trades)}건, 승률: {sum(1 for t in trades if t.pnl > 0)/len(trades)*100:.1f}%")

    # ─── 전략별 심층 분석 ───
    for strategy in STRATEGY_CONFIGS:
        strades = [t for t in trades if t.strategy == strategy]
        if not strades:
            continue

        wins = [t for t in strades if t.pnl > 0]
        losses = [t for t in strades if t.pnl <= 0]

        print(f"\n{'='*70}")
        print(f"  [{strategy}] 총 {len(strades)}건 | 승 {len(wins)} | 패 {len(losses)} | WR {len(wins)/len(strades)*100:.1f}%")
        print(f"{'='*70}")

        # 청산 사유별
        by_reason = defaultdict(list)
        for t in strades:
            by_reason[t.exit_reason].append(t)

        print(f"\n  청산사유별:")
        for reason, rtrades in sorted(by_reason.items()):
            avg_pnl = sum(t.pnl_pct for t in rtrades) / len(rtrades) * 100
            wr = sum(1 for t in rtrades if t.pnl > 0) / len(rtrades) * 100
            print(f"    {reason:<15} {len(rtrades):>4}건  avg {avg_pnl:>+6.2f}%  WR {wr:>5.1f}%")

        # 시간대별 분석
        by_hour = defaultdict(list)
        for t in strades:
            by_hour[t.entry_time.hour].append(t)

        print(f"\n  시간대별 (KST=UTC+9):")
        for hour in sorted(by_hour.keys()):
            htrades = by_hour[hour]
            wr = sum(1 for t in htrades if t.pnl > 0) / len(htrades) * 100 if htrades else 0
            avg_pnl = sum(t.pnl_pct for t in htrades) / len(htrades) * 100
            kst_hour = (hour + 9) % 24
            bar = "█" * max(1, int(wr / 5))
            print(f"    {kst_hour:02d}시  {len(htrades):>3}건  WR {wr:>5.1f}% {bar}  avg {avg_pnl:>+6.2f}%")

        # 진입 지표 분석 (승 vs 패)
        all_indicators = set()
        for t in strades:
            if t.entry_indicators:
                all_indicators.update(t.entry_indicators.keys())

        if all_indicators:
            print(f"\n  진입 지표 비교 (승리 vs 패배):")
            print(f"  {'지표':<18} {'승리 avg':>10} {'패배 avg':>10} {'승리 med':>10} {'패배 med':>10} {'차이':>8}")
            print(f"  {'-'*66}")

            for ind_name in sorted(all_indicators):
                if ind_name == "price":
                    continue
                win_vals = [t.entry_indicators[ind_name] for t in wins
                           if t.entry_indicators and ind_name in t.entry_indicators
                           and t.entry_indicators[ind_name] is not None]
                lose_vals = [t.entry_indicators[ind_name] for t in losses
                            if t.entry_indicators and ind_name in t.entry_indicators
                            and t.entry_indicators[ind_name] is not None]

                result_data = analyze_indicator(ind_name, win_vals, lose_vals)
                if result_data:
                    print(f"  {ind_name:<18} {result_data['win_avg']:>10.3f} {result_data['lose_avg']:>10.3f} "
                          f"{result_data['win_median']:>10.3f} {result_data['lose_median']:>10.3f} "
                          f"{result_data['diff_pct']:>+7.1f}%")

        # 보유시간별 성과
        print(f"\n  보유시간별 성과:")
        time_buckets = [(0, 15, "0-15분"), (15, 30, "15-30분"), (30, 60, "30-60분"),
                        (60, 120, "1-2시간"), (120, 999, "2시간+")]
        for lo, hi, label in time_buckets:
            bucket = [t for t in strades if t.holding_minutes and lo <= t.holding_minutes < hi]
            if bucket:
                wr = sum(1 for t in bucket if t.pnl > 0) / len(bucket) * 100
                avg_pnl = sum(t.pnl_pct for t in bucket) / len(bucket) * 100
                print(f"    {label:<10} {len(bucket):>4}건  WR {wr:>5.1f}%  avg {avg_pnl:>+6.2f}%")

        # TP 도달까지 걸린 시간 (승리 거래)
        tp_trades = [t for t in strades if t.exit_reason == "take_profit"]
        if tp_trades:
            tp_times = [t.holding_minutes for t in tp_trades if t.holding_minutes]
            if tp_times:
                avg_tp = sum(tp_times) / len(tp_times)
                med_tp = percentile(tp_times, 50)
                p25_tp = percentile(tp_times, 25)
                p75_tp = percentile(tp_times, 75)
                print(f"\n  TP 도달 시간: avg {avg_tp:.0f}분 | P25 {p25_tp}분 | med {med_tp}분 | P75 {p75_tp}분")

        # SL 도달까지 걸린 시간 (패배 거래)
        sl_trades = [t for t in strades if t.exit_reason == "stop_loss"]
        if sl_trades:
            sl_times = [t.holding_minutes for t in sl_trades if t.holding_minutes]
            if sl_times:
                avg_sl = sum(sl_times) / len(sl_times)
                med_sl = percentile(sl_times, 50)
                print(f"  SL 도달 시간: avg {avg_sl:.0f}분 | med {med_sl}분")

        # 최대 수익 도달 후 반전 분석 (SL 거래)
        if sl_trades:
            max_profits = [t.max_profit_pct * 100 for t in sl_trades]
            avg_max_profit = sum(max_profits) / len(max_profits)
            hit_1pct = sum(1 for p in max_profits if p >= 1.0)
            print(f"\n  SL 거래의 최대 수익 도달: avg {avg_max_profit:+.2f}% | 1%+ 도달 후 반전: {hit_1pct}건 ({hit_1pct/len(sl_trades)*100:.0f}%)")

    # ─── 전체 공통 패턴 분석 ───
    print(f"\n{'='*70}")
    print(f"  전체 공통 패턴 분석")
    print(f"{'='*70}")

    all_wins = [t for t in trades if t.pnl > 0 and t.entry_indicators]
    all_losses = [t for t in trades if t.pnl <= 0 and t.entry_indicators]

    # RSI 분포
    win_rsi = [t.entry_indicators.get("rsi") for t in all_wins if t.entry_indicators.get("rsi") is not None]
    lose_rsi = [t.entry_indicators.get("rsi") for t in all_losses if t.entry_indicators.get("rsi") is not None]
    if win_rsi and lose_rsi:
        print(f"\n  RSI 분포:")
        for lo, hi in [(0, 20), (20, 25), (25, 30), (30, 35), (35, 40), (40, 50)]:
            w = sum(1 for r in win_rsi if lo <= r < hi)
            l = sum(1 for r in lose_rsi if lo <= r < hi)
            total = w + l
            wr = w / total * 100 if total > 0 else 0
            print(f"    RSI {lo:>2}-{hi:<2}: {total:>4}건  WR {wr:>5.1f}%  {'█' * max(1, int(wr/5))}")

    # RVOL 분포
    win_rvol = [t.entry_indicators.get("rvol") for t in all_wins if t.entry_indicators.get("rvol") is not None]
    lose_rvol = [t.entry_indicators.get("rvol") for t in all_losses if t.entry_indicators.get("rvol") is not None]
    if win_rvol and lose_rvol:
        print(f"\n  RVOL 분포:")
        for lo, hi in [(1.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 6.0), (6.0, 10.0), (10.0, 100.0)]:
            w = sum(1 for r in win_rvol if lo <= r < hi)
            l = sum(1 for r in lose_rvol if lo <= r < hi)
            total = w + l
            wr = w / total * 100 if total > 0 else 0
            print(f"    RVOL {lo:>4.1f}-{hi:<4.1f}: {total:>4}건  WR {wr:>5.1f}%  {'█' * max(1, int(wr/5))}")

    # ATR% 분포
    win_atr = [t.entry_indicators.get("atr_pct") for t in all_wins if t.entry_indicators.get("atr_pct") is not None]
    lose_atr = [t.entry_indicators.get("atr_pct") for t in all_losses if t.entry_indicators.get("atr_pct") is not None]
    if win_atr and lose_atr:
        print(f"\n  ATR% 분포:")
        for lo, hi in [(0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 20.0)]:
            w = sum(1 for r in win_atr if lo <= r < hi)
            l = sum(1 for r in lose_atr if lo <= r < hi)
            total = w + l
            wr = w / total * 100 if total > 0 else 0
            print(f"    ATR {lo:>4.1f}-{hi:<4.1f}%: {total:>4}건  WR {wr:>5.1f}%  {'█' * max(1, int(wr/5))}")


if __name__ == "__main__":
    asyncio.run(run_analysis())
