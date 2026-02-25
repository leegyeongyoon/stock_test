"""v4.1 멀티전략 복합 백테스트 (데이터 분석 기반 고도화)

4개 전략 동시 실행 (v3 고도화 3개 + v4 VSR 1개)
데이터 분석 인사이트 반영:
- RSI < 25 황금구간 (WR 66-69%) vs RSI 25-30 (53.6%) vs RSI 30+ (34.2%)
- ATR > 1.5% 핵심 (WR 77.3%) vs ATR 1.0-1.5% (54.9%)
- 02시(KST) 재앙 시간대 차단 (CR 7.1%, TBR 3.7%, VSR 14.3%)
- 빠른 청산 우위 (0-15분 > 30분+)

실행: cd server && python -m scripts.run_v4_backtest
옵션: --days 30 (기본) | --days 90
"""

import argparse
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

# ─── 심볼 리스트 (top 110) ───

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

# ─── v4.1 데이터 분석 기반 파라미터 ───

STRATEGY_CONFIGS = {
    # VOB: 76.8% WR → 고도화
    # 인사이트: RSI<25 황금구간, ATR>1.3% 승리자 평균, 02-03시 차단
    "VOLATILE_OVERSOLD_BOUNCE": {
        "signal_params": {
            "position_pct": 0.30,
            "max_rsi": 25,               # 30→25 (RSI 25-30: 53.6% WR → RSI<25: 67%)
            "min_rvol": 2.5,
            "min_atr_pct": 1.3,          # 1.0→1.3 (승리 avg 1.61%, 패배 1.33%)
            "cooldown_minutes": 10,
            "blocked_hours_kst": [2, 3],  # 02시 33%, 03시 50%
        },
        "exit_params": {
            "stop_loss_pct": -0.020,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.008,
            "time_stop_minutes": 180,
        },
    },
    # CR: 59.2% WR → 고도화
    # 인사이트: RSI<25 승리자, drop>3% 승리자, 02/05시 재앙
    "CRASH_RECOVERY": {
        "signal_params": {
            "position_pct": 0.25,        # 0.15→0.25 (조건 엄격화 → 높은 품질)
            "max_rsi": 25,               # 35→25 (승리 RSI 20.5, 패배 25.2)
            "min_rvol": 2.5,
            "min_drop_pct": 0.03,        # 0.02→0.03 (승리 drop -4%, 패배 -3.1%)
            "cooldown_minutes": 10,
            "blocked_hours_kst": [2, 3, 5, 20],  # 02시 7%, 05시 34%, 20시 33%
        },
        "exit_params": {
            "stop_loss_pct": -0.015,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.005,
            "time_stop_minutes": 120,
        },
    },
    # TBR: 61.1% WR, 429건 최다 → 고도화
    # 인사이트: RSI<25, ATR>1.3%, trailing 낮추기 (14% SL이 1%+ 도달 후 반전)
    # 30분+ 보유시 성과 급감 (72% → 43%)
    "TRIPLE_BEARISH_REVERSAL": {
        "signal_params": {
            "position_pct": 0.30,
            "max_rsi": 25,               # 30→25 (승리 RSI 19.8, 패배 21.5)
            "min_atr_pct": 1.3,          # 1.0→1.3 (승리 ATR 1.64%, 패배 1.52%)
            "cooldown_minutes": 10,
            "blocked_hours_kst": [2, 8, 17, 19],  # 02시 3.7%, 08시 0%, 17시 30%, 19시 36%
        },
        "exit_params": {
            "stop_loss_pct": -0.020,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.012,  # 0.020→0.012 (14%가 1%+ 도달 후 반전)
            "trailing_stop_pct": 0.008,     # 0.010→0.008 (더 타이트한 추적)
            "time_stop_minutes": 90,        # 180→90 (30분+ 보유시 성과 급감)
        },
    },
    # VSR: 54.8% WR → 고도화
    # 인사이트: 더 큰 하락(-2.5%+), close_position 상한 0.90 (가짜 반전 필터)
    "VOLUME_SURGE_REVERSAL": {
        "signal_params": {
            "position_pct": 0.30,
            "cooldown_minutes": 10,
            "min_rvol": 4.0,
            "min_drop_pct": 0.025,          # 0.020→0.025 (승리 -3.1%, 패배 -1.9%)
            "min_close_position": 0.60,
            "max_close_position": 0.90,     # 신규 (패배 avg 0.90, med 1.00 → 가짜 반전 차단)
            "blocked_hours_kst": [0, 2, 4, 20],  # 00시 12%, 02시 14%, 04시 0%, 20시 0%
        },
        "exit_params": {
            "stop_loss_pct": -0.015,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.008,
            "time_stop_minutes": 60,        # 90→60 (빠른 청산 우위)
        },
    },
}


def print_header(text: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def print_strategy_breakdown(trades: list, initial_capital: float) -> None:
    """전략별 성과 분해"""
    by_strategy = defaultdict(list)
    for t in trades:
        by_strategy[t.strategy].append(t)

    print(f"\n{'전략':<30} {'거래수':>6} {'승률':>7} {'수익':>12} {'PF':>6} {'평균보유':>8}")
    print("-" * 75)

    for strategy in sorted(by_strategy.keys()):
        strades = by_strategy[strategy]
        total = len(strades)
        wins = sum(1 for t in strades if t.pnl > 0)
        wr = (wins / total * 100) if total > 0 else 0
        total_pnl = sum(t.pnl for t in strades)
        gross_profit = sum(t.pnl for t in strades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in strades if t.pnl < 0))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
        avg_hold = sum(t.holding_minutes or 0 for t in strades) / total if total > 0 else 0

        print(
            f"  {strategy:<28} {total:>6} {wr:>6.1f}% "
            f"{total_pnl:>+11,.0f} {pf:>5.2f} {avg_hold:>7.0f}m"
        )

    print("-" * 75)
    total_all = len(trades)
    wins_all = sum(1 for t in trades if t.pnl > 0)
    wr_all = (wins_all / total_all * 100) if total_all > 0 else 0
    pnl_all = sum(t.pnl for t in trades)
    gp = sum(t.pnl for t in trades if t.pnl > 0)
    gl = abs(sum(t.pnl for t in trades if t.pnl < 0))
    pf_all = (gp / gl) if gl > 0 else float('inf')
    print(
        f"  {'TOTAL':<28} {total_all:>6} {wr_all:>6.1f}% "
        f"{pnl_all:>+11,.0f} {pf_all:>5.2f}"
    )


def print_exit_reason_breakdown(trades: list) -> None:
    """청산 사유별 분석"""
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t.exit_reason or "unknown"].append(t)

    print(f"\n{'청산사유':<20} {'거래수':>6} {'평균수익':>9} {'승률':>7}")
    print("-" * 45)

    for reason in sorted(by_reason.keys()):
        rtrades = by_reason[reason]
        total = len(rtrades)
        avg_pnl = sum(t.pnl_pct for t in rtrades) / total * 100 if total > 0 else 0
        wins = sum(1 for t in rtrades if t.pnl > 0)
        wr = (wins / total * 100) if total > 0 else 0
        print(f"  {reason:<18} {total:>6} {avg_pnl:>+8.2f}% {wr:>6.1f}%")


async def run_backtest(days: int = 30) -> None:
    """v4.1 복합 백테스트 실행"""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    initial_capital = 1_000_000

    print_header(f"v4.1 멀티전략 복합 백테스트 ({days}일) - 데이터 분석 기반")
    print(f"  기간: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
    print(f"  초기자본: {initial_capital:,.0f} KRW")
    print(f"  전략 수: {len(STRATEGY_CONFIGS)}개")
    print(f"  심볼 수: {len(SYMBOLS)}개")

    # 시그널 생성기 조합
    generators = []
    for strategy_name, config in STRATEGY_CONFIGS.items():
        gen = get_signal_generator(strategy_name, config["signal_params"])
        generators.append(gen)
        blocked = config["signal_params"].get("blocked_hours_kst", [])
        blocked_str = f", 차단시간(KST)={blocked}" if blocked else ""
        print(f"  + {strategy_name} (pos={config['signal_params'].get('position_pct', 0.10)*100:.0f}%{blocked_str})")

    combined_signal = CombinedSignalGenerator(generators)

    # 전략별 청산 파라미터 조합
    exit_params = {name: config["exit_params"] for name, config in STRATEGY_CONFIGS.items()}
    combined_exit = CombinedExitChecker(exit_params)

    # 백테스트 설정
    config = BacktestConfig(
        strategy="V4_COMBINED",
        symbols=SYMBOLS,
        start_date=start_date,
        end_date=end_date,
        interval="5m",
        initial_capital=initial_capital,
        commission_rate=0.0005,
        slippage_bps=5,
        parameters={"max_positions": 100, "multi_strategy": True, "margin_mode": True},
    )

    # 실행
    print(f"\n  데이터 로딩 및 백테스트 실행 중...")
    async with async_session() as session:
        data_loader = BacktestDataLoader(session)
        engine = BacktestEngine(config, data_loader)

        def progress(current, total):
            pct = current / total * 100
            print(f"  진행: {pct:.0f}% ({current}/{total})", end="\r")

        result = await engine.run(
            signal_generator=combined_signal,
            exit_checker=combined_exit,
            progress_callback=progress,
        )

    # ─── 결과 출력 ───

    print_header("결과 요약")
    metrics = result.metrics
    final_equity = result.final_equity
    total_return = (final_equity - initial_capital) / initial_capital * 100

    print(f"  최종 자산: {final_equity:,.0f} KRW")
    print(f"  총 수익률: {total_return:+.2f}%")
    print(f"  총 거래수: {metrics.total_trades}")
    print(f"  승률: {metrics.win_rate:.1f}%")
    print(f"  수익팩터: {metrics.profit_factor:.2f}")
    print(f"  최대낙폭: {metrics.max_drawdown_pct:.2f}%")
    print(f"  샤프비율: {metrics.sharpe_ratio:.2f}")
    print(f"  평균보유: {metrics.avg_holding_minutes:.0f}분")
    print(f"  최고거래: {metrics.best_trade_pct*100:+.2f}%")
    print(f"  최악거래: {metrics.worst_trade_pct*100:+.2f}%")

    # 전략별 분해
    print_header("전략별 성과")
    print_strategy_breakdown(result.trades, initial_capital)

    # 청산 사유별
    print_header("청산 사유별 분석")
    print_exit_reason_breakdown(result.trades)

    # 목표 달성 여부
    print_header("목표 달성 여부")
    target_return = 80
    target_trades = 400
    print(f"  수익률: {total_return:+.2f}% (목표: {target_return}%+) {'OK' if total_return >= target_return else 'MISS'}")
    print(f"  거래수: {metrics.total_trades} (목표: {target_trades}+) {'OK' if metrics.total_trades >= target_trades else 'MISS'}")
    print(f"  PF: {metrics.profit_factor:.2f} (목표: 1.3+) {'OK' if metrics.profit_factor >= 1.3 else 'MISS'}")

    # 0 거래 전략 체크
    strategy_counts = defaultdict(int)
    for t in result.trades:
        strategy_counts[t.strategy] += 1
    zero_strategies = [s for s in STRATEGY_CONFIGS if strategy_counts[s] == 0]
    if zero_strategies:
        print(f"\n  WARNING: 0 거래 전략: {', '.join(zero_strategies)}")
    else:
        print(f"\n  모든 전략 활성 (거래 발생)")


def main():
    parser = argparse.ArgumentParser(description="v4.1 멀티전략 복합 백테스트")
    parser.add_argument("--days", type=int, default=30, help="백테스트 기간 (일)")
    args = parser.parse_args()

    asyncio.run(run_backtest(days=args.days))


if __name__ == "__main__":
    main()
