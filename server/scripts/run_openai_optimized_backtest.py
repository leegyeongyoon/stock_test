"""OpenAI 분석 기반 DOUBLE_DIP_BUY 최적화 백테스트

OpenAI 권장 사항:
1. 진입 조건 강화:
   - min_dip_pct: 0.008 → 0.0085 (-0.85% 이상 하락)
   - support_tolerance: 0.05 → 0.04 (24시간 저점에서 4% 이내)

2. 청산 조건 최적화:
   - SL: -2.0% → -5.0%
   - TP: +1.5% → +5.0%
   - trailing_trigger: +1.0% → +3.0%
   - trailing_stop: 0.5% → 1.5%

예상 효과:
- 더 엄격한 진입으로 승률 소폭 상승
- 넓은 SL/TP로 평균 승리/패배 비율 개선
- avg_win +1.21% vs avg_loss -1.99% 문제 해결
"""

import asyncio
from datetime import datetime, timedelta

import structlog

from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.models.schemas import BacktestConfig
from src.backtesting.strategies.strategy_adapters import get_signal_generator
from src.backtesting.engine.backtest_engine import create_pullback_exit_checker
from src.models.database import async_session

logger = structlog.get_logger()

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

# 파라미터 설정
ORIGINAL_PARAMS = {
    "name": "Original",
    "entry": {
        "min_dip_pct": 0.008,
        "support_tolerance": 0.05,
    },
    "exit": {
        "stop_loss_pct": -0.020,
        "take_profit_pct": 0.015,
        "trailing_trigger_pct": 0.010,
        "trailing_stop_pct": 0.005,
    },
}

OPENAI_OPTIMIZED_PARAMS = {
    "name": "OpenAI Optimized",
    "entry": {
        "min_dip_pct": 0.0085,      # -0.85% (더 큰 하락에서만 진입)
        "support_tolerance": 0.04,   # 4% (더 저점 근처에서만 진입)
    },
    "exit": {
        "stop_loss_pct": -0.050,      # -5.0% (더 넓은 SL)
        "take_profit_pct": 0.050,     # +5.0% (더 넓은 TP)
        "trailing_trigger_pct": 0.030, # +3.0% (더 여유있는 트리거)
        "trailing_stop_pct": 0.015,   # 1.5% (더 여유있는 스탑)
    },
}


async def run_backtest(params: dict, days: int):
    """지정된 파라미터로 백테스트 실행"""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    config = BacktestConfig(
        strategy="DOUBLE_DIP_BUY",
        symbols=SYMBOLS,
        start_date=start_date,
        end_date=end_date,
        interval="5m",
        initial_capital=1_000_000,
        parameters=params["entry"],
    )

    async with async_session() as db:
        loader = BacktestDataLoader(db)
        engine = BacktestEngine(config, loader)

        signal_generator = get_signal_generator("DOUBLE_DIP_BUY", params["entry"])
        exit_checker = create_pullback_exit_checker(
            stop_loss_pct=params["exit"]["stop_loss_pct"],
            take_profit_pct=params["exit"]["take_profit_pct"],
            trailing_trigger_pct=params["exit"]["trailing_trigger_pct"],
            trailing_stop_pct=params["exit"]["trailing_stop_pct"],
        )

        result = await engine.run(signal_generator, exit_checker)

        return {
            "name": params["name"],
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
    print("OpenAI 분석 기반 DOUBLE_DIP_BUY 최적화 백테스트")
    print("=" * 100)

    print("\n📊 파라미터 비교:")
    print("-" * 80)
    print(f"{'설정':<20} {'Original':<25} {'OpenAI Optimized':<25}")
    print("-" * 80)
    print(f"{'min_dip_pct':<20} {ORIGINAL_PARAMS['entry']['min_dip_pct']:<25} {OPENAI_OPTIMIZED_PARAMS['entry']['min_dip_pct']:<25}")
    print(f"{'support_tolerance':<20} {ORIGINAL_PARAMS['entry']['support_tolerance']:<25} {OPENAI_OPTIMIZED_PARAMS['entry']['support_tolerance']:<25}")
    print(f"{'SL':<20} {ORIGINAL_PARAMS['exit']['stop_loss_pct']*100:.1f}%{'':<21} {OPENAI_OPTIMIZED_PARAMS['exit']['stop_loss_pct']*100:.1f}%")
    print(f"{'TP':<20} {ORIGINAL_PARAMS['exit']['take_profit_pct']*100:.1f}%{'':<21} {OPENAI_OPTIMIZED_PARAMS['exit']['take_profit_pct']*100:.1f}%")
    print(f"{'Trailing Trigger':<20} {ORIGINAL_PARAMS['exit']['trailing_trigger_pct']*100:.1f}%{'':<21} {OPENAI_OPTIMIZED_PARAMS['exit']['trailing_trigger_pct']*100:.1f}%")
    print(f"{'Trailing Stop':<20} {ORIGINAL_PARAMS['exit']['trailing_stop_pct']*100:.1f}%{'':<21} {OPENAI_OPTIMIZED_PARAMS['exit']['trailing_stop_pct']*100:.1f}%")
    print("-" * 80)

    days = 30
    print(f"\n🔄 {days}일 백테스트 실행 중...")

    # 두 설정으로 백테스트 실행
    results = []

    for params in [ORIGINAL_PARAMS, OPENAI_OPTIMIZED_PARAMS]:
        print(f"\n  [{params['name']}] 실행 중...")
        try:
            result = await run_backtest(params, days)
            results.append(result)
            print(f"    완료: 수익률 {result['return_pct']:+.2f}%, 승률 {result['win_rate']:.1f}%")
        except Exception as e:
            print(f"    실패: {str(e)}")
            import traceback
            traceback.print_exc()

    # 결과 비교
    print("\n" + "=" * 100)
    print("📈 결과 비교")
    print("=" * 100)

    print(f"\n{'지표':<20} {'Original':<30} {'OpenAI Optimized':<30} {'차이':<20}")
    print("-" * 100)

    if len(results) == 2:
        orig, opt = results[0], results[1]

        metrics = [
            ("수익률", "return_pct", "%", 1),
            ("승률", "win_rate", "%", 1),
            ("거래 수", "trades", "건", 0),
            ("Profit Factor", "pf", "", 2),
            ("Sharpe Ratio", "sharpe", "", 2),
            ("MDD", "mdd", "%", 2),
            ("평균 승리", "avg_win", "%", 2),
            ("평균 패배", "avg_loss", "%", 2),
        ]

        for name, key, unit, decimals in metrics:
            orig_val = orig[key]
            opt_val = opt[key]
            diff = opt_val - orig_val

            if decimals == 0:
                print(f"{name:<20} {orig_val:<30.0f} {opt_val:<30.0f} {diff:+.0f} {unit}")
            else:
                fmt = f"{{:+.{decimals}f}}"
                print(f"{name:<20} {orig_val:<30.{decimals}f} {opt_val:<30.{decimals}f} {fmt.format(diff)} {unit}")

        print("-" * 100)

        # 종합 평가
        print("\n📋 종합 평가:")

        improvement_return = opt["return_pct"] - orig["return_pct"]
        improvement_wr = opt["win_rate"] - orig["win_rate"]

        if improvement_return > 0:
            print(f"  ✅ 수익률: {improvement_return:+.2f}%p 개선")
        else:
            print(f"  ❌ 수익률: {improvement_return:+.2f}%p 악화")

        if improvement_wr > 0:
            print(f"  ✅ 승률: {improvement_wr:+.1f}%p 개선")
        else:
            print(f"  ❌ 승률: {improvement_wr:+.1f}%p 악화")

        # Risk/Reward 비율 개선 확인
        orig_rr = abs(orig["avg_win"] / orig["avg_loss"]) if orig["avg_loss"] != 0 else 0
        opt_rr = abs(opt["avg_win"] / opt["avg_loss"]) if opt["avg_loss"] != 0 else 0

        print(f"\n  📊 Risk/Reward 비율:")
        print(f"     Original: {orig_rr:.2f} (avg_win {orig['avg_win']:+.2f}% / avg_loss {orig['avg_loss']:+.2f}%)")
        print(f"     Optimized: {opt_rr:.2f} (avg_win {opt['avg_win']:+.2f}% / avg_loss {opt['avg_loss']:+.2f}%)")

        if opt_rr > orig_rr:
            print(f"  ✅ R/R 비율 개선: {opt_rr - orig_rr:+.2f}")
        else:
            print(f"  ❌ R/R 비율 악화: {opt_rr - orig_rr:+.2f}")

        # 기대값 계산
        orig_ev = (orig["win_rate"]/100 * orig["avg_win"]) + ((100-orig["win_rate"])/100 * orig["avg_loss"])
        opt_ev = (opt["win_rate"]/100 * opt["avg_win"]) + ((100-opt["win_rate"])/100 * opt["avg_loss"])

        print(f"\n  📈 거래당 기대값:")
        print(f"     Original: {orig_ev:+.4f}%")
        print(f"     Optimized: {opt_ev:+.4f}%")

        if opt_ev > orig_ev:
            print(f"  ✅ 기대값 개선: {opt_ev - orig_ev:+.4f}%p")
        else:
            print(f"  ❌ 기대값 악화: {opt_ev - orig_ev:+.4f}%p")

    print("\n" + "=" * 100)
    print("완료!")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
