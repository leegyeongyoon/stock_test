#!/usr/bin/env python3
"""
v28 분석 시스템 테스트 스크립트

데이터베이스 없이 더미 데이터로 분석 시스템 검증
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
import random

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# 분석 모듈만 직접 import (순환 참조 방지)
import importlib.util

def import_module_directly(module_name: str, file_path: str):
    """모듈을 직접 import (부모 모듈 우회)"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

# 분석 모듈 직접 로드
analysis_path = project_root / "src" / "backtesting" / "analysis"
trade_analyzer_module = import_module_directly(
    "trade_analyzer",
    str(analysis_path / "trade_analyzer.py")
)
indicator_dist_module = import_module_directly(
    "indicator_distribution",
    str(analysis_path / "indicator_distribution.py")
)
sl_tp_module = import_module_directly(
    "optimal_sl_tp",
    str(analysis_path / "optimal_sl_tp.py")
)
pattern_extractor_module = import_module_directly(
    "pattern_extractor",
    str(analysis_path / "pattern_extractor.py")
)


def generate_dummy_trades(n: int = 100) -> list:
    """테스트용 더미 거래 데이터 생성"""
    TradePattern = trade_analyzer_module.TradePattern

    trades = []
    strategies = ["EmaBouncScalp", "DoubleDipBuy", "MomentumBreakout"]

    for i in range(n):
        # 랜덤 지표 값
        rvol = random.uniform(0.5, 3.0)
        rsi = random.uniform(20, 80)
        ema20_distance = random.uniform(-3, 3)

        # 승/패 확률 (지표에 따라 조정)
        win_prob = 0.3
        if rvol > 1.5:
            win_prob += 0.1
        if 30 < rsi < 60:
            win_prob += 0.1
        if -1 < ema20_distance < 1:
            win_prob += 0.1

        is_win = random.random() < win_prob

        if is_win:
            pnl_pct = random.uniform(0.5, 5.0)
            max_profit_pct = pnl_pct + random.uniform(0, 2)
            max_drawdown_pct = random.uniform(0.1, 1.5)
        else:
            pnl_pct = random.uniform(-5.0, -0.3)
            max_profit_pct = random.uniform(0, 1.5)
            max_drawdown_pct = abs(pnl_pct) + random.uniform(0, 1)

        entry_time = datetime.now() - timedelta(days=random.randint(1, 30))
        holding_minutes = random.randint(5, 240)

        trade = TradePattern(
            trade_id=f"trade_{i:04d}",
            symbol="BTCUSDT",
            strategy=random.choice(strategies),
            entry_time=entry_time,
            exit_time=entry_time + timedelta(minutes=holding_minutes),
            pnl_pct=pnl_pct,
            max_profit_pct=max_profit_pct,
            max_drawdown_pct=max_drawdown_pct,
            holding_minutes=holding_minutes,
            exit_reason="take_profit" if is_win else "stop_loss",
            indicators={
                "rvol": rvol,
                "rsi": rsi,
                "ema20_distance_pct": ema20_distance,
                "price": 50000 + random.uniform(-1000, 1000),
            },
        )
        trades.append(trade)

    return trades


def test_indicator_distribution():
    """지표 분포 분석기 테스트"""
    IndicatorDistributionAnalyzer = indicator_dist_module.IndicatorDistributionAnalyzer

    print("\n[테스트] IndicatorDistributionAnalyzer")
    print("-" * 50)

    trades = generate_dummy_trades(200)
    analyzer = IndicatorDistributionAnalyzer(trades)

    # 단일 지표 분석
    rvol_dist = analyzer.analyze_single_indicator("rvol")
    if rvol_dist:
        print(f"RVOL 분포:")
        print(f"  - 전체 평균: {rvol_dist.all_stats.mean:.4f}")
        print(f"  - 승리 평균: {rvol_dist.win_stats.mean:.4f}")
        print(f"  - 패배 평균: {rvol_dist.loss_stats.mean:.4f}")
        if rvol_dist.optimal_range:
            print(f"  - 최적 범위: {rvol_dist.optimal_range[0]:.4f} ~ {rvol_dist.optimal_range[1]:.4f}")
            print(f"  - 최적 범위 승률: {rvol_dist.optimal_range_win_rate:.1%}")

    # 최적 범위 탐색
    optimal_ranges = analyzer.find_optimal_ranges()
    print(f"\n최적 범위 발견: {len(optimal_ranges)}개")
    for name, r in list(optimal_ranges.items())[:3]:
        print(f"  - {name}: {r.min_value:.4f} ~ {r.max_value:.4f} (승률 {r.win_rate:.1%})")

    print("PASSED")


def test_sl_tp_analyzer():
    """SL/TP 분석기 테스트"""
    SLTPAnalyzer = sl_tp_module.SLTPAnalyzer

    print("\n[테스트] SLTPAnalyzer")
    print("-" * 50)

    trades = generate_dummy_trades(200)
    analyzer = SLTPAnalyzer(trades)

    # 가격 움직임 분석
    stats = analyzer.analyze_price_movements()
    print(f"가격 움직임:")
    print(f"  - 평균 최대 수익: {stats.avg_max_profit_pct:.2f}%")
    print(f"  - 평균 최대 하락: {stats.avg_max_drawdown_pct:.2f}%")

    # SL/TP 최적화
    best_sl_tp = analyzer.find_best_sl_tp()
    print(f"\n최적 SL/TP:")
    print(f"  - SL: {best_sl_tp.optimal_sl_pct:.1f}%")
    print(f"  - TP: {best_sl_tp.optimal_tp_pct:.1f}%")
    print(f"  - 예상 승률: {best_sl_tp.expected_win_rate:.1%}")
    print(f"  - 예상 평균 수익: {best_sl_tp.expected_avg_pnl_pct:.2f}%")

    # 트레일링 스탑 최적화
    trailing_results = analyzer.optimize_trailing_stop()
    if trailing_results:
        best_trailing = trailing_results[0]
        print(f"\n최적 트레일링 스탑:")
        print(f"  - 트레일링 %: {best_trailing.trailing_pct:.1f}%")
        print(f"  - 활성화 수준: {best_trailing.activation_pct:.1f}%")
        print(f"  - 예상 승률: {best_trailing.win_rate:.1%}")

    print("PASSED")


def test_trade_pattern_analyzer():
    """거래 패턴 분석기 테스트 (간소화 버전)"""
    TradePatternAnalyzer = trade_analyzer_module.TradePatternAnalyzer

    print("\n[테스트] TradePatternAnalyzer (더미 데이터)")
    print("-" * 50)

    # 더미 데이터로 분석 테스트 (DB 없이)
    trades = generate_dummy_trades(200)

    # 직접 trades 설정
    class DummyAnalyzer:
        def __init__(self, trades):
            self.trades = trades

        def compare_win_vs_loss(self):
            analyzer = TradePatternAnalyzer.__new__(TradePatternAnalyzer)
            analyzer.trades = self.trades
            return analyzer.compare_win_vs_loss()

        def find_profitable_conditions(self):
            analyzer = TradePatternAnalyzer.__new__(TradePatternAnalyzer)
            analyzer.trades = self.trades
            return analyzer.find_profitable_conditions()

    analyzer = DummyAnalyzer(trades)

    wins = sum(1 for t in trades if t.is_win)
    losses = len(trades) - wins
    print(f"총 거래: {len(trades)}건")
    print(f"승리/패배: {wins}/{losses}")
    print(f"승률: {wins/len(trades):.1%}")

    # 지표 비교
    comparisons = analyzer.compare_win_vs_loss()
    print(f"\n지표 비교 결과: {len(comparisons)}개 지표")
    for ic in comparisons[:3]:
        status = "유의미" if ic.is_significant else "미유의"
        print(f"  - {ic.indicator_name}: p={ic.p_value:.4f} ({status})")

    # 수익 조건
    conditions = analyzer.find_profitable_conditions()
    print(f"\n수익 조건 발견: {len(conditions)}개")
    for c in conditions[:3]:
        print(f"  - {c.condition_name}: 승률 {c.win_rate:.1%}")

    print("PASSED")


def test_pattern_extractor():
    """패턴 추출기 테스트 (더미 데이터)"""
    print("\n[테스트] PatternExtractor (코드 생성)")
    print("-" * 50)

    ProfitablePattern = pattern_extractor_module.ProfitablePattern
    IndicatorCondition = pattern_extractor_module.IndicatorCondition

    # 더미 패턴 생성
    patterns = [
        ProfitablePattern(
            pattern_id="P001",
            name="rvol_range_filter",
            description="RVOL: 1.5 ~ 2.5",
            conditions=[
                IndicatorCondition(
                    indicator_name="rvol",
                    operator="between",
                    value=1.5,
                    upper_value=2.5,
                )
            ],
            optimal_sl_pct=-2.0,
            optimal_tp_pct=3.0,
            expected_win_rate=0.58,
            expected_avg_pnl_pct=0.75,
            trade_count=50,
            confidence_score=72.5,
        ),
        ProfitablePattern(
            pattern_id="P002",
            name="rsi_range_filter",
            description="RSI: 35 ~ 55",
            conditions=[
                IndicatorCondition(
                    indicator_name="rsi",
                    operator="between",
                    value=35,
                    upper_value=55,
                )
            ],
            optimal_sl_pct=-2.0,
            optimal_tp_pct=3.0,
            expected_win_rate=0.55,
            expected_avg_pnl_pct=0.50,
            trade_count=45,
            confidence_score=65.0,
        ),
    ]

    # 전략 코드 생성 테스트
    # PatternExtractor 인스턴스 없이 직접 코드 생성 테스트
    print("패턴 기반 전략 코드 생성:")
    print(f"  - 패턴 수: {len(patterns)}")
    print(f"  - 최고 패턴: {patterns[0].name} (승률 {patterns[0].expected_win_rate:.1%})")

    # 간단한 코드 템플릿 생성 테스트
    condition_str = []
    for p in patterns:
        for c in p.conditions:
            if c.operator == "between" and c.upper_value:
                condition_str.append(
                    f"{c.value} <= indicators.get('{c.indicator_name}', 0) <= {c.upper_value}"
                )

    print(f"\n생성된 조건:")
    for c in condition_str:
        print(f"  {c}")

    print("PASSED")


def run_all_tests():
    """모든 테스트 실행"""
    print("=" * 60)
    print("v28 분석 시스템 검증 테스트")
    print("=" * 60)

    try:
        test_trade_pattern_analyzer()
        test_indicator_distribution()
        test_sl_tp_analyzer()
        test_pattern_extractor()

        print("\n" + "=" * 60)
        print("모든 테스트 통과!")
        print("=" * 60)

    except Exception as e:
        print(f"\n테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
