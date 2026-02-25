"""v4.1 실투자 시뮬레이션

마진 모드 OFF - 실제 현금 기반 매매 시뮬레이션
- 포지션 사이즈: 자산의 15% (equity 기반 복리)
- 현금 부족 시 진입 불가
- 수수료 0.05% + 슬리피지 5bps
- 최대 동시 포지션: 8개
- 일별 수익률 추적

실행: cd server && python -m scripts.run_v4_realistic
옵션: --days 30 (기본) | --days 90
      --capital 1000000 (기본)
"""

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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

# ─── 심볼 리스트 (top 113) ───

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

# ─── 실투자 파라미터 (v4.1 데이터 분석 기반 + 현실적 포지션) ───

STRATEGY_CONFIGS = {
    "VOLATILE_OVERSOLD_BOUNCE": {
        "signal_params": {
            "position_pct": 0.15,           # 실투자: 15% (마진모드 30% → 15%)
            "max_rsi": 25,
            "min_rvol": 2.5,
            "min_atr_pct": 1.3,
            "cooldown_minutes": 10,
            "blocked_hours_kst": [2, 3],
        },
        "exit_params": {
            "stop_loss_pct": -0.020,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.008,
            "time_stop_minutes": 180,
        },
    },
    "CRASH_RECOVERY": {
        "signal_params": {
            "position_pct": 0.15,           # 실투자: 15%
            "max_rsi": 25,
            "min_rvol": 2.5,
            "min_drop_pct": 0.03,
            "cooldown_minutes": 10,
            "blocked_hours_kst": [2, 3, 5, 20],
        },
        "exit_params": {
            "stop_loss_pct": -0.015,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.005,
            "time_stop_minutes": 120,
        },
    },
    "TRIPLE_BEARISH_REVERSAL": {
        "signal_params": {
            "position_pct": 0.15,           # 실투자: 15%
            "max_rsi": 25,
            "min_atr_pct": 1.3,
            "cooldown_minutes": 10,
            "blocked_hours_kst": [2, 8, 17, 19],
        },
        "exit_params": {
            "stop_loss_pct": -0.020,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.012,
            "trailing_stop_pct": 0.008,
            "time_stop_minutes": 90,
        },
    },
    "VOLUME_SURGE_REVERSAL": {
        "signal_params": {
            "position_pct": 0.15,           # 실투자: 15%
            "cooldown_minutes": 10,
            "min_rvol": 4.0,
            "min_drop_pct": 0.025,
            "min_close_position": 0.60,
            "max_close_position": 0.90,
            "blocked_hours_kst": [0, 2, 4, 20],
        },
        "exit_params": {
            "stop_loss_pct": -0.015,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.008,
            "time_stop_minutes": 60,
        },
    },
}


def print_header(text: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {text}")
    print(f"{'='*70}")


def print_strategy_breakdown(trades: list) -> None:
    """전략별 성과 분해"""
    by_strategy = defaultdict(list)
    for t in trades:
        by_strategy[t.strategy].append(t)

    print(f"\n  {'전략':<28} {'거래':>5} {'승률':>7} {'총수익':>12} {'PF':>6} {'평균보유':>7}")
    print(f"  {'-'*68}")

    for strategy in sorted(by_strategy.keys()):
        strades = by_strategy[strategy]
        total = len(strades)
        wins = sum(1 for t in strades if t.pnl > 0)
        wr = (wins / total * 100) if total > 0 else 0
        total_pnl = sum(t.pnl for t in strades)
        gp = sum(t.pnl for t in strades if t.pnl > 0)
        gl = abs(sum(t.pnl for t in strades if t.pnl < 0))
        pf = (gp / gl) if gl > 0 else float('inf')
        avg_hold = sum(t.holding_minutes or 0 for t in strades) / total if total > 0 else 0

        print(
            f"  {strategy:<28} {total:>5} {wr:>6.1f}% "
            f"{total_pnl:>+11,.0f} {pf:>5.2f} {avg_hold:>6.0f}m"
        )

    print(f"  {'-'*68}")
    total_all = len(trades)
    wins_all = sum(1 for t in trades if t.pnl > 0)
    wr_all = (wins_all / total_all * 100) if total_all > 0 else 0
    pnl_all = sum(t.pnl for t in trades)
    gp = sum(t.pnl for t in trades if t.pnl > 0)
    gl = abs(sum(t.pnl for t in trades if t.pnl < 0))
    pf_all = (gp / gl) if gl > 0 else float('inf')
    print(
        f"  {'TOTAL':<28} {total_all:>5} {wr_all:>6.1f}% "
        f"{pnl_all:>+11,.0f} {pf_all:>5.2f}"
    )


def print_exit_reasons(trades: list) -> None:
    """청산 사유별 분석"""
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t.exit_reason or "unknown"].append(t)

    print(f"\n  {'청산사유':<18} {'거래':>5} {'평균수익':>9} {'승률':>7}")
    print(f"  {'-'*42}")

    for reason in sorted(by_reason.keys()):
        rtrades = by_reason[reason]
        total = len(rtrades)
        avg_pnl = sum(t.pnl_pct for t in rtrades) / total * 100 if total > 0 else 0
        wins = sum(1 for t in rtrades if t.pnl > 0)
        wr = (wins / total * 100) if total > 0 else 0
        print(f"  {reason:<18} {total:>5} {avg_pnl:>+8.2f}% {wr:>6.1f}%")


def print_daily_equity(equity_curve: list, initial_capital: float) -> None:
    """일별 수익률 표시"""
    if not equity_curve:
        return

    # 일별 그룹핑 (KST 기준)
    daily = {}
    for point in equity_curve:
        kst = point.timestamp + timedelta(hours=9)
        date_key = kst.strftime("%m/%d")
        daily[date_key] = point.equity

    dates = list(daily.keys())
    if len(dates) <= 1:
        return

    print(f"\n  일별 자산 추이:")
    print(f"  {'날짜':<8} {'자산':>12} {'수익률':>8} {'그래프'}")
    print(f"  {'-'*55}")

    prev_equity = initial_capital
    for date_str in dates:
        equity = daily[date_str]
        total_return = (equity - initial_capital) / initial_capital * 100
        daily_return = (equity - prev_equity) / prev_equity * 100

        # 바 그래프
        bar_len = int(total_return / 2) if total_return > 0 else 0
        bar_len = min(bar_len, 25)
        bar = "█" * bar_len if total_return > 0 else ""

        print(f"  {date_str:<8} {equity:>11,.0f} {total_return:>+7.1f}% {bar}")
        prev_equity = equity


def print_weekly_trades(trades: list) -> None:
    """주별 거래 요약"""
    if not trades:
        return

    weekly = defaultdict(list)
    for t in trades:
        kst = t.entry_time + timedelta(hours=9)
        week_key = kst.strftime("%m/%d주")
        weekly[week_key].append(t)

    print(f"\n  주별 거래 요약:")
    print(f"  {'주차':<10} {'거래':>5} {'승률':>7} {'수익':>11} {'평균%':>8}")
    print(f"  {'-'*45}")

    for week in sorted(weekly.keys()):
        wtrades = weekly[week]
        total = len(wtrades)
        wins = sum(1 for t in wtrades if t.pnl > 0)
        wr = (wins / total * 100) if total > 0 else 0
        total_pnl = sum(t.pnl for t in wtrades)
        avg_pnl_pct = sum(t.pnl_pct for t in wtrades) / total * 100 if total > 0 else 0
        print(f"  {week:<10} {total:>5} {wr:>6.1f}% {total_pnl:>+10,.0f} {avg_pnl_pct:>+7.2f}%")


def print_risk_metrics(trades: list, equity_curve: list, initial_capital: float) -> None:
    """리스크 지표"""
    if not trades or not equity_curve:
        return

    # 최대 연속 손실
    max_consecutive_loss = 0
    current_streak = 0
    max_consecutive_win = 0
    current_win_streak = 0
    for t in trades:
        if t.pnl < 0:
            current_streak += 1
            max_consecutive_loss = max(max_consecutive_loss, current_streak)
            current_win_streak = 0
        else:
            current_win_streak += 1
            max_consecutive_win = max(max_consecutive_win, current_win_streak)
            current_streak = 0

    # 최대 단일 손실
    max_single_loss = min(t.pnl for t in trades) if trades else 0
    max_single_loss_pct = min(t.pnl_pct for t in trades) * 100 if trades else 0

    # 평균 손실 대비 평균 이익
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    avg_win = sum(t.pnl_pct for t in wins) / len(wins) * 100 if wins else 0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) * 100 if losses else 0

    # 총 수수료
    total_commission = sum(t.commission for t in trades)

    # 일평균 거래
    if trades:
        first = trades[0].entry_time
        last = trades[-1].entry_time
        days = max((last - first).days, 1)
        daily_trades = len(trades) / days
    else:
        daily_trades = 0

    print(f"\n  리스크 지표:")
    print(f"  {'-'*45}")
    print(f"  최대 연속 손실:    {max_consecutive_loss}회")
    print(f"  최대 연속 승리:    {max_consecutive_win}회")
    print(f"  최대 단일 손실:    {max_single_loss:,.0f} KRW ({max_single_loss_pct:+.2f}%)")
    print(f"  평균 승리:         {avg_win:+.2f}%")
    print(f"  평균 손실:         {avg_loss:+.2f}%")
    print(f"  손익비:            {abs(avg_win/avg_loss):.2f}x" if avg_loss != 0 else "  손익비:            N/A")
    print(f"  총 수수료:         {total_commission:,.0f} KRW")
    print(f"  일평균 거래:       {daily_trades:.1f}건")


async def run_simulation(days: int = 30, initial_capital: int = 1_000_000) -> None:
    """실투자 시뮬레이션 실행"""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    print_header(f"v4.1 실투자 시뮬레이션 ({days}일)")
    print(f"  기간:     {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
    print(f"  초기자본: {initial_capital:>12,} KRW")
    print(f"  모드:     현금 기반 (마진 OFF)")
    print(f"  포지션:   자산의 15% × 최대 8개 동시")
    print(f"  수수료:   0.05% + 슬리피지 5bps")
    print(f"  전략:     {len(STRATEGY_CONFIGS)}개")

    # 시그널 생성기 조합
    generators = []
    for strategy_name, config in STRATEGY_CONFIGS.items():
        gen = get_signal_generator(strategy_name, config["signal_params"])
        generators.append(gen)

    combined_signal = CombinedSignalGenerator(generators)

    # 전략별 청산 파라미터
    exit_params = {name: config["exit_params"] for name, config in STRATEGY_CONFIGS.items()}
    combined_exit = CombinedExitChecker(exit_params)

    # 백테스트 설정 (실투자 모드)
    config = BacktestConfig(
        strategy="V4_REALISTIC",
        symbols=SYMBOLS,
        start_date=start_date,
        end_date=end_date,
        interval="5m",
        initial_capital=initial_capital,
        commission_rate=0.0005,      # 업비트 0.05%
        slippage_bps=5,              # 5bps 슬리피지
        parameters={
            "max_positions": 8,      # 최대 동시 8포지션
            "multi_strategy": True,  # 전략별 독립 포지션
            "margin_mode": False,    # 실투자: 현금 기반!
        },
    )

    # 실행
    print(f"\n  백테스트 실행 중...")
    async with async_session() as session:
        data_loader = BacktestDataLoader(session)
        engine = BacktestEngine(config, data_loader)

        def progress(current, total):
            pct = current / total * 100
            if int(pct) % 10 == 0:
                print(f"  진행: {pct:.0f}%", end="\r")

        result = await engine.run(
            signal_generator=combined_signal,
            exit_checker=combined_exit,
            progress_callback=progress,
        )

    # ─── 결과 출력 ───

    metrics = result.metrics
    final_equity = result.final_equity
    total_return = (final_equity - initial_capital) / initial_capital * 100

    print_header("실투자 시뮬레이션 결과")
    print(f"  초기 자본:  {initial_capital:>12,} KRW")
    print(f"  최종 자산:  {final_equity:>12,.0f} KRW")
    print(f"  순 수익:    {final_equity - initial_capital:>+12,.0f} KRW")
    print(f"  총 수익률:  {total_return:>+11.2f}%")
    print()
    print(f"  총 거래수:  {metrics.total_trades:>8}")
    print(f"  승률:       {metrics.win_rate:>7.1f}%")
    print(f"  수익팩터:   {metrics.profit_factor:>7.2f}")
    print(f"  최대낙폭:   {metrics.max_drawdown_pct:>7.2f}%")
    print(f"  샤프비율:   {metrics.sharpe_ratio:>7.2f}")
    print(f"  평균보유:   {metrics.avg_holding_minutes:>6.0f}분")

    # 전략별 성과
    print_header("전략별 성과")
    print_strategy_breakdown(result.trades)

    # 청산 사유별
    print_header("청산 사유별")
    print_exit_reasons(result.trades)

    # 리스크 지표
    print_header("리스크 분석")
    print_risk_metrics(result.trades, result.equity_curve, initial_capital)

    # 일별 자산 추이
    print_header("일별 자산 추이")
    print_daily_equity(result.equity_curve, initial_capital)

    # 주별 거래 요약
    print_header("주별 거래 요약")
    print_weekly_trades(result.trades)

    # 실투자 요약
    print_header("실투자 요약")
    monthly_return = total_return
    daily_avg_return = total_return / max(days, 1)
    print(f"  {days}일 수익률:    {monthly_return:+.2f}%")
    print(f"  일평균 수익률:  {daily_avg_return:+.3f}%")
    if total_return > 0:
        print(f"  원금 2배 예상:  약 {int(100/daily_avg_return)}일") if daily_avg_return > 0 else None
    print(f"  최대 위험:      {metrics.max_drawdown_pct:.2f}% 낙폭")


def main():
    parser = argparse.ArgumentParser(description="v4.1 실투자 시뮬레이션")
    parser.add_argument("--days", type=int, default=30, help="시뮬레이션 기간 (일)")
    parser.add_argument("--capital", type=int, default=1_000_000, help="초기 자본금 (KRW)")
    args = parser.parse_args()

    asyncio.run(run_simulation(days=args.days, initial_capital=args.capital))


if __name__ == "__main__":
    main()
