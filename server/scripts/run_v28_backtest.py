"""v28 데이터 기반 전략 백테스트

분석 결과 기반 최적화된 전략 검증
"""

import asyncio
import json
from datetime import datetime, timedelta

import structlog

from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.models.database import BacktestResultModel, BacktestTradeModel
from src.backtesting.models.schemas import BacktestConfig
from src.backtesting.strategies.strategy_adapters import get_signal_generator, get_exit_checker
from src.models.database import async_session

logger = structlog.get_logger()

# v28 전략 파라미터 (OpenAI 분석 기반 - 타이트 트레일링)
STRATEGIES = {
    "DATA_DRIVEN_V28": {
        "stop_loss_pct": -0.015,        # -1.5% (더 타이트)
        "take_profit_pct": 0.030,       # +3.0% (적당한 TP)
        "trailing_trigger_pct": 0.003,  # 0.3% (OpenAI 권장)
        "trailing_stop_pct": 0.003,     # 0.3% (OpenAI 권장)
        # OpenAI 분석 기반 필터
        "sma200_min": 260.4825,        # 상위 75% (66.7% 승률)
        "sma200_max": 3830.75,         # 범위 필터
        "min_distance_from_low": 0.0412,  # 상위 25% (64.9% 승률)
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

        # 개별 거래도 저장
        for trade in result.trades:
            trade_model = BacktestTradeModel(
                run_id=result.run_id,
                trade_id=trade.trade_id,
                symbol=trade.symbol,
                strategy=trade.strategy,
                entry_time=trade.entry_time,
                entry_price=trade.entry_price,
                entry_quantity=trade.entry_quantity,
                entry_value=trade.entry_price * trade.entry_quantity,
                exit_time=trade.exit_time,
                exit_price=trade.exit_price,
                exit_reason=trade.exit_reason,
                pnl=trade.pnl,
                pnl_pct=trade.pnl_pct * 100,
                commission=trade.commission,
                slippage=0,
                holding_minutes=trade.holding_minutes,
                max_profit_pct=trade.max_profit_pct * 100,
                max_drawdown_pct=trade.max_drawdown_pct * 100,
                entry_indicators_json=trade.entry_indicators,
            )
            db.add(trade_model)

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
    print("\n" + "=" * 100)
    print("v28 데이터 기반 전략 백테스트 (OpenAI 최적화 버전)")
    print("=" * 100)
    print(f"전략: DATA_DRIVEN_V28 (OpenAI 분석 기반)")
    print(f"SL: -1.5% / TP: +3.0% / 트레일링: 0.3%")
    print(f"필터: SMA200 범위 260-3830, distance_from_low >= 4.12%")
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
            print(f"  DATA_DRIVEN_V28 실행 중...")
            result = await run_single_backtest("DATA_DRIVEN_V28", days, label)
            all_results.append(result)

            wr_emoji = "OK" if result['win_rate'] >= 55 else "LOW"
            ret_emoji = "+" if result['return_pct'] > 0 else "-"

            print(
                f"  [{wr_emoji}] DATA_DRIVEN_V28 | "
                f"수익률: {result['return_pct']:+7.2f}% | "
                f"승률: {result['win_rate']:5.1f}% | "
                f"거래: {result['trades']:4d}건 | "
                f"PF: {result['pf']:5.2f} | "
                f"Sharpe: {result['sharpe']:5.2f} | "
                f"MDD: {result['mdd']:5.2f}% | "
                f"Avg W: {result['avg_win']:+.2f}% / L: {result['avg_loss']:+.2f}%\n"
            )
        except Exception as e:
            print(f"  FAIL DATA_DRIVEN_V28: {str(e)}\n")
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
    print("  v27 vs v28 비교 (월간 기준)")
    print("=" * 100)

    monthly = next((r for r in all_results if r['label'] == '월간'), None)
    if monthly:
        print(f"  v27 (10개 전략): 수익률 -263.03% | 평균 승률 41.1%")
        print(f"  v28 (데이터기반): 수익률 {monthly['return_pct']:+.2f}% | 승률 {monthly['win_rate']:.1f}%")

        improvement = monthly['return_pct'] - (-263.03)
        print(f"\n  개선: {improvement:+.2f}%p")

    print(f"\n  완료!\n")


if __name__ == "__main__":
    asyncio.run(main())
