"""고승률 10개 전략 종합 백테스트 (v27)

SMA/EMA/RVOL/ATR 전용 전략 - 승률 60-70%+ 목표
모든 심볼 기준 1일/7일/30일 백테스트
"""

import asyncio
import json
from datetime import datetime, timedelta

import structlog

from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.models.database import BacktestResultModel
from src.backtesting.models.schemas import BacktestConfig
from src.backtesting.strategies.strategy_adapters import get_signal_generator, get_exit_checker
from src.models.database import async_session

logger = structlog.get_logger()

# v27: 고승률 10개 전략 청산 파라미터 (plan에서 정의된 R:R)
STRATEGIES = {
    "EMA_BOUNCE_SCALP": {
        "stop_loss_pct": -0.006,
        "take_profit_pct": 0.015,
        "trailing_trigger_pct": 0.010,
        "trailing_stop_pct": 0.004,
    },
    "DOUBLE_DIP_BUY": {
        "stop_loss_pct": -0.004,
        "take_profit_pct": 0.022,
        "trailing_trigger_pct": 0.015,
        "trailing_stop_pct": 0.003,
    },
    "TIGHT_RANGE_BREAKOUT": {
        "stop_loss_pct": -0.008,
        "take_profit_pct": 0.025,
        "trailing_trigger_pct": 0.018,
        "trailing_stop_pct": 0.005,
    },
    "SUPPORT_TOUCH_BOUNCE": {
        "stop_loss_pct": -0.005,
        "take_profit_pct": 0.012,
        "trailing_trigger_pct": 0.008,
        "trailing_stop_pct": 0.003,
    },
    "VOLUME_CLIMAX_REVERSAL": {
        "stop_loss_pct": -0.006,
        "take_profit_pct": 0.018,
        "trailing_trigger_pct": 0.012,
        "trailing_stop_pct": 0.004,
    },
    "EMA_CROSSOVER_MOMENTUM": {
        "stop_loss_pct": -0.010,
        "take_profit_pct": 0.030,
        "trailing_trigger_pct": 0.020,
        "trailing_stop_pct": 0.006,
    },
    "ATR_EXPANSION_ENTRY": {
        "stop_loss_pct": -0.008,
        "take_profit_pct": 0.024,
        "trailing_trigger_pct": 0.016,
        "trailing_stop_pct": 0.005,
    },
    "MICRO_PULLBACK_LONG": {
        "stop_loss_pct": -0.006,
        "take_profit_pct": 0.016,
        "trailing_trigger_pct": 0.010,
        "trailing_stop_pct": 0.004,
    },
    "TRIPLE_CONFIRMATION_ENTRY": {
        "stop_loss_pct": -0.005,
        "take_profit_pct": 0.014,
        "trailing_trigger_pct": 0.010,
        "trailing_stop_pct": 0.003,
    },
    "FAST_SCALP_REBOUND": {
        "stop_loss_pct": -0.005,
        "take_profit_pct": 0.015,
        "trailing_trigger_pct": 0.010,
        "trailing_stop_pct": 0.003,
    },
}

# 모든 업비트 KRW 심볼 (113개)
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
        exit_checker = get_exit_checker(strategy, params)

        result = await engine.run(signal_generator, exit_checker)

        # DB 저장
        result_model = BacktestResultModel(
            run_id=result.run_id,
            strategy=result.strategy,
            symbols=json.dumps(result.symbols),
            interval=result.interval,
            start_date=result.start_date,
            end_date=result.end_date,
            initial_capital=result.initial_capital,
            final_equity=result.final_equity,
            total_return_pct=result.metrics.total_return_pct,
            max_drawdown_pct=result.metrics.max_drawdown_pct,
            sharpe_ratio=result.metrics.sharpe_ratio or 0,
            win_rate=result.metrics.win_rate,
            total_trades=result.metrics.total_trades,
            profit_factor=result.metrics.profit_factor or 0,
            metrics_json=result.metrics.model_dump(),
            parameters_json=result.parameters,
            equity_curve_json=[
                {
                    "timestamp": p.timestamp.isoformat(),
                    "equity": p.equity,
                    "drawdown_pct": p.drawdown_pct,
                }
                for p in result.equity_curve
            ],
        )

        db.add(result_model)
        await db.commit()

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
    print("\n" + "=" * 120)
    print("v27 고승률 10개 전략 종합 백테스트 (SMA/EMA/RVOL/ATR 전용, 113 심볼)")
    print("=" * 120 + "\n")

    test_periods = [
        (1, "1일"),
        (7, "주간"),
        (30, "월간"),
    ]

    all_results = []

    for days, label in test_periods:
        print(f"\n{'='*120}")
        print(f"  {label} 백테스트 ({days}일)")
        print(f"{'='*120}\n")

        period_results = []
        for strategy in STRATEGIES.keys():
            try:
                print(f"  {strategy} 실행 중...")
                result = await run_single_backtest(strategy, days, label)
                period_results.append(result)
                all_results.append(result)

                wr_emoji = "OK" if result['win_rate'] >= 60 else "LOW"
                ret_emoji = "+" if result['return_pct'] > 0 else "-"

                print(
                    f"  [{wr_emoji}] {strategy:30s} | "
                    f"수익률: {result['return_pct']:+7.2f}% | "
                    f"승률: {result['win_rate']:5.1f}% | "
                    f"거래: {result['trades']:4d}건 | "
                    f"PF: {result['pf']:5.2f} | "
                    f"Sharpe: {result['sharpe']:5.2f} | "
                    f"MDD: {result['mdd']:5.2f}% | "
                    f"Avg W: {result['avg_win']:+.2f}% / L: {result['avg_loss']:+.2f}%\n"
                )
            except Exception as e:
                print(f"  FAIL {strategy}: {str(e)}\n")

        # 기간별 요약
        if period_results:
            total_return = sum(r["return_pct"] for r in period_results)
            total_trades = sum(r["trades"] for r in period_results)
            avg_win_rate = sum(r["win_rate"] for r in period_results) / len(period_results)
            profitable = sum(1 for r in period_results if r["return_pct"] > 0)

            print(f"\n{'='*120}")
            print(f"  {label} 요약:")
            print(f"  총 수익률: {total_return:+.2f}% | 총 거래: {total_trades}건 | 평균 승률: {avg_win_rate:.1f}%")
            print(f"  수익 전략: {profitable}/10 | 목표 승률 60%+ 달성: {sum(1 for r in period_results if r['win_rate'] >= 60)}/10")
            print(f"{'='*120}\n")

    # 전체 요약
    print("\n" + "=" * 120)
    print("  전체 결과 요약")
    print("=" * 120)

    for label in ["1일", "주간", "월간"]:
        label_results = [r for r in all_results if r["label"] == label]
        if not label_results:
            continue

        print(f"\n  [{label}]")
        print(f"  {'Strategy':30s} | {'Return':>8s} | {'WinRate':>7s} | {'Trades':>6s} | {'PF':>5s} | {'Sharpe':>6s} | {'MDD':>6s}")
        print(f"  {'-'*30}-+-{'-'*8}-+-{'-'*7}-+-{'-'*6}-+-{'-'*5}-+-{'-'*6}-+-{'-'*6}")

        sorted_results = sorted(label_results, key=lambda x: x["return_pct"], reverse=True)
        for r in sorted_results:
            marker = "+" if r["return_pct"] > 0 else "-"
            wr_mark = "OK" if r["win_rate"] >= 60 else "!!"
            print(
                f"  {marker} {r['strategy']:28s} | "
                f"{r['return_pct']:+7.2f}% | "
                f"{wr_mark} {r['win_rate']:4.1f}% | "
                f"{r['trades']:5d} | "
                f"{r['pf']:5.2f} | "
                f"{r['sharpe']:5.2f} | "
                f"{r['mdd']:5.2f}%"
            )

        total_return = sum(r["return_pct"] for r in label_results)
        avg_wr = sum(r["win_rate"] for r in label_results) / len(label_results) if label_results else 0
        print(f"\n  TOTAL: {total_return:+.2f}% | AVG WR: {avg_wr:.1f}%")

    # 목표 달성 여부
    print("\n" + "=" * 120)
    print("  목표 달성 여부")
    print("=" * 120)

    daily_results = [r for r in all_results if r["label"] == "1일"]
    weekly_results = [r for r in all_results if r["label"] == "주간"]
    monthly_results = [r for r in all_results if r["label"] == "월간"]

    if daily_results:
        daily_total = sum(r["return_pct"] for r in daily_results)
        daily_avg_wr = sum(r["win_rate"] for r in daily_results) / len(daily_results)
        print(f"  1일  | 수익률: {daily_total:+.2f}% | 평균 승률: {daily_avg_wr:.1f}%")

    if weekly_results:
        weekly_total = sum(r["return_pct"] for r in weekly_results)
        weekly_avg_wr = sum(r["win_rate"] for r in weekly_results) / len(weekly_results)
        weekly_60plus = sum(1 for r in weekly_results if r["win_rate"] >= 60)
        print(f"  주간 | 수익률: {weekly_total:+.2f}% | 평균 승률: {weekly_avg_wr:.1f}% | 60%+ 달성: {weekly_60plus}/10")

    if monthly_results:
        monthly_total = sum(r["return_pct"] for r in monthly_results)
        monthly_avg_wr = sum(r["win_rate"] for r in monthly_results) / len(monthly_results)
        monthly_60plus = sum(1 for r in monthly_results if r["win_rate"] >= 60)
        monthly_profitable = sum(1 for r in monthly_results if r["return_pct"] > 0)
        print(f"  월간 | 수익률: {monthly_total:+.2f}% (목표 +20%) | 평균 승률: {monthly_avg_wr:.1f}% | 60%+ 달성: {monthly_60plus}/10 | 수익 전략: {monthly_profitable}/10")

        goal_met = monthly_total >= 20
        print(f"\n  월간 +20% 목표: {'PASS' if goal_met else 'FAIL'} ({monthly_total:+.2f}%)")

    print(f"\n  완료!\n")


if __name__ == "__main__":
    asyncio.run(main())
