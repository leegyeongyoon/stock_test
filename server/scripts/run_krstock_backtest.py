#!/usr/bin/env python3
"""
한국 주식 백테스트 실행 스크립트

사용법:
    python scripts/run_krstock_backtest.py --strategy all --period 60d
    python scripts/run_krstock_backtest.py --strategy MOMENTUM_BREAKOUT --period 30d
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.krstock.market import MarketType, is_trading_day
from src.strategies.krstock import (
    create_all_strategies,
    create_strategy_by_name,
    get_strategy_info,
    STRATEGY_MAP,
)
from src.backtesting.engine.krstock_backtest_engine import (
    KRStockBacktestEngine,
    KRBacktestConfig,
    print_backtest_summary,
)


def print_strategy_list() -> None:
    """전략 목록 출력"""
    print("\n📋 사용 가능한 전략:")
    print("-" * 60)

    for i, info in enumerate(get_strategy_info(), 1):
        print(f"{i:2}. {info['name']:<25} ({info['type']})")
        print(f"     손절: {info['stop_loss_pct']:+.1f}% | 익절: {info['take_profit_pct']:+.1f}%")
        print(f"     최대보유: {info['max_holding_days']}일 | 포지션: {info['max_position_pct']:.0f}%")
        print()


def run_demo_backtest(strategy_name: str = "all", period_days: int = 60) -> None:
    """
    데모 백테스트 실행

    실제 데이터 없이 구조만 테스트
    """
    print("\n" + "=" * 60)
    print("🚀 한국 주식 자동매매 시스템 - 백테스트")
    print("=" * 60)

    # 전략 생성
    if strategy_name.lower() == "all":
        strategies = create_all_strategies()
        print(f"\n📊 {len(strategies)}개 전략 테스트")
    else:
        strategies = [create_strategy_by_name(strategy_name)]
        print(f"\n📊 전략: {strategy_name}")

    # 백테스트 설정
    config = KRBacktestConfig(
        initial_capital=100_000_000,  # 1억원
        market=MarketType.KOSPI,
        commission_rate=0.00015,
        tax_rate=0.0023,
        slippage_pct=0.1,
        max_positions=10,
        max_single_position_pct=10.0,
    )

    print(f"\n💰 초기 자본: {config.initial_capital:,.0f}원")
    print(f"📈 시장: {config.market.value}")
    print(f"💸 수수료: {config.commission_rate*100:.3f}%")
    print(f"💸 거래세: {config.tax_rate*100:.3f}%")

    # 기간 설정
    end_date = date.today()
    start_date = end_date - timedelta(days=period_days)

    print(f"\n📅 기간: {start_date} ~ {end_date} ({period_days}일)")

    # 각 전략별 정보 출력
    print("\n" + "-" * 60)
    print("전략별 손실 방지 필터 설정:")
    print("-" * 60)

    for strategy in strategies:
        config = strategy.config
        filters = config.filter_config

        print(f"\n🎯 {strategy.name}")
        print(f"   유형: {config.strategy_type}")
        print(f"   손절: {config.stop_loss_pct:+.1f}% | 익절: {config.take_profit_pct:+.1f}%")
        print(f"   추적손절: {config.trailing_trigger_pct}% 수익 시 → 고점 -{config.trailing_stop_pct}%")
        print(f"   최대보유: {config.max_holding_days}일")

        # 필터 설정
        tech = filters.technical
        sd = filters.supply_demand

        filter_info = []
        if tech.require_above_sma_20:
            filter_info.append("20일선↑필수")
        if tech.require_macd_above_signal:
            filter_info.append("MACD골든크로스")
        if sd.require_foreign_positive:
            filter_info.append("외국인순매수")
        if sd.require_inst_positive:
            filter_info.append("기관순매수")

        if filter_info:
            print(f"   필터: {', '.join(filter_info)}")

    print("\n" + "=" * 60)
    print("⚠️  실제 백테스트 실행을 위해서는 캔들 데이터가 필요합니다.")
    print("    키움 API를 통해 데이터를 수집한 후 백테스트를 실행하세요.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="한국 주식 백테스트 실행",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예제:
  python scripts/run_krstock_backtest.py --list
  python scripts/run_krstock_backtest.py --strategy all --period 60
  python scripts/run_krstock_backtest.py --strategy MOMENTUM_BREAKOUT --period 30
        """
    )

    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="사용 가능한 전략 목록 출력"
    )

    parser.add_argument(
        "--strategy", "-s",
        type=str,
        default="all",
        help="전략 이름 (기본: all)"
    )

    parser.add_argument(
        "--period", "-p",
        type=int,
        default=60,
        help="백테스트 기간 (일, 기본: 60)"
    )

    args = parser.parse_args()

    if args.list:
        print_strategy_list()
        return

    # 전략 이름 검증
    if args.strategy.lower() != "all" and args.strategy not in STRATEGY_MAP:
        print(f"❌ 알 수 없는 전략: {args.strategy}")
        print(f"   사용 가능: {', '.join(STRATEGY_MAP.keys())}")
        sys.exit(1)

    run_demo_backtest(args.strategy, args.period)


if __name__ == "__main__":
    main()
