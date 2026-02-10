#!/usr/bin/env python3
"""
v28 전체 분석 파이프라인 실행 스크립트

Usage:
    python scripts/analysis/run_full_analysis.py [--run-id RUN_ID] [--strategy STRATEGY] [--use-openai]

Examples:
    # 모든 거래 분석
    python scripts/analysis/run_full_analysis.py

    # 특정 백테스트 실행 분석
    python scripts/analysis/run_full_analysis.py --run-id "20240101_backtest"

    # 특정 전략만 분석
    python scripts/analysis/run_full_analysis.py --strategy "EmaBouncScalp"

    # OpenAI 분석 포함
    python scripts/analysis/run_full_analysis.py --use-openai
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.backtesting.analysis.trade_analyzer import TradePatternAnalyzer
from src.backtesting.analysis.indicator_distribution import IndicatorDistributionAnalyzer
from src.backtesting.analysis.optimal_sl_tp import SLTPAnalyzer
from src.backtesting.analysis.pattern_extractor import PatternExtractor
from src.backtesting.models.database import BacktestTradeModel
from src.models.database import async_session


async def get_async_session():
    """비동기 세션 생성 - 기존 DB 세션 사용"""
    return async_session()


async def run_analysis(
    run_ids: list[str] | None = None,
    strategies: list[str] | None = None,
    use_openai: bool = False,
    output_dir: str = "reports/analysis",
) -> dict:
    """
    전체 분석 파이프라인 실행

    Args:
        run_ids: 분석할 백테스트 실행 ID 리스트
        strategies: 분석할 전략 리스트
        use_openai: OpenAI 분석 사용 여부
        output_dir: 결과 저장 디렉토리

    Returns:
        종합 분석 결과 딕셔너리
    """
    session = await get_async_session()

    try:
        print("=" * 60)
        print("v28 데이터 기반 전략 분석 시스템")
        print("=" * 60)

        # 1. 거래 패턴 분석
        print("\n[1/5] 거래 패턴 분석 중...")
        trade_analyzer = TradePatternAnalyzer(session)
        trade_result = await trade_analyzer.run_full_analysis(
            run_ids=run_ids,
            strategies=strategies,
        )

        if trade_result.total_trades == 0:
            print("분석할 거래가 없습니다.")
            return {"error": "no_trades"}

        print(f"  - 총 거래: {trade_result.total_trades}건")
        print(f"  - 승리/패배: {trade_result.winning_trades}/{trade_result.losing_trades}")
        print(f"  - 전체 승률: {trade_result.overall_win_rate:.1%}")

        # 2. 지표 분포 분석
        print("\n[2/5] 지표 분포 분석 중...")
        indicator_analyzer = IndicatorDistributionAnalyzer(trade_analyzer.trades)
        indicator_report = indicator_analyzer.to_report_dict()

        optimal_ranges = indicator_analyzer.find_optimal_ranges()
        print(f"  - 분석된 지표: {len(optimal_ranges)}개")

        if optimal_ranges:
            top_range = list(optimal_ranges.values())[0]
            print(f"  - 최고 개선 지표: {top_range.indicator_name} (+{top_range.improvement_vs_baseline:.1%})")

        # 3. SL/TP 분석
        print("\n[3/5] SL/TP 최적화 분석 중...")
        sl_tp_analyzer = SLTPAnalyzer(trade_analyzer.trades)
        sl_tp_result = sl_tp_analyzer.run_full_analysis()

        print(f"  - 최적 SL: {sl_tp_result.best_sl_tp.optimal_sl_pct:.1f}%")
        print(f"  - 최적 TP: {sl_tp_result.best_sl_tp.optimal_tp_pct:.1f}%")
        print(f"  - 예상 승률: {sl_tp_result.best_sl_tp.expected_win_rate:.1%}")
        print(f"  - 예상 평균 수익: {sl_tp_result.best_sl_tp.expected_avg_pnl_pct:.2f}%")

        # 4. 패턴 추출
        print("\n[4/5] 수익 패턴 추출 중...")
        pattern_extractor = PatternExtractor(session)
        extraction_result = await pattern_extractor.run_full_extraction(
            run_ids=run_ids,
            strategies=strategies,
            min_confidence=40.0,
        )

        print(f"  - 추출된 패턴: {len(extraction_result.patterns)}개")

        if extraction_result.patterns:
            top_pattern = extraction_result.patterns[0]
            print(f"  - 최고 패턴: {top_pattern.name} (신뢰도: {top_pattern.confidence_score:.1f})")

        # 5. OpenAI 분석 (선택)
        openai_report = None
        if use_openai:
            print("\n[5/5] OpenAI 분석 중...")
            try:
                from src.backtesting.analysis.openai_analyzer import OpenAIAnalyzer

                openai_analyzer = OpenAIAnalyzer()
                openai_result = await openai_analyzer.analyze_trade_patterns(
                    trade_analysis=trade_result,
                    indicator_ranges=optimal_ranges,
                    sl_tp_analysis=sl_tp_result,
                )
                openai_report = openai_analyzer.to_report_dict(openai_result)

                print(f"  - 사용 모델: {openai_result.model_used}")
                print(f"  - 토큰 사용: {openai_result.tokens_used}")
                print(f"  - 신뢰도: {openai_result.insight.confidence_level}")

            except Exception as e:
                print(f"  - OpenAI 분석 실패: {e}")
        else:
            print("\n[5/5] OpenAI 분석 건너뜀 (--use-openai 플래그 없음)")

        # 종합 보고서 생성
        print("\n" + "=" * 60)
        print("분석 완료! 보고서 생성 중...")

        report = {
            "generated_at": datetime.now().isoformat(),
            "analysis_params": {
                "run_ids": run_ids,
                "strategies": strategies,
            },
            "trade_pattern_analysis": trade_analyzer.to_report_dict(trade_result),
            "indicator_distribution_analysis": indicator_report,
            "sl_tp_analysis": sl_tp_analyzer.to_report_dict(sl_tp_result),
            "pattern_extraction": pattern_extractor.to_report_dict(extraction_result),
        }

        if openai_report:
            report["openai_analysis"] = openai_report

        # 전략 코드 저장
        if extraction_result.strategy_templates:
            strategy_code = extraction_result.strategy_templates[0].full_strategy_code
            report["generated_strategy_code"] = strategy_code

        # 보고서 저장
        output_path = Path(project_root) / output_dir
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = output_path / f"analysis_report_{timestamp}.json"

        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n보고서 저장: {report_file}")

        # 전략 코드 파일 저장
        if extraction_result.strategy_templates:
            strategy_file = output_path / f"generated_strategy_{timestamp}.py"
            with open(strategy_file, "w", encoding="utf-8") as f:
                f.write(strategy_code)
            print(f"전략 코드 저장: {strategy_file}")

        # 요약 출력
        print("\n" + "=" * 60)
        print("분석 요약")
        print("=" * 60)
        print(f"총 거래: {trade_result.total_trades}건")
        print(f"현재 승률: {trade_result.overall_win_rate:.1%}")
        print(f"최적 SL/TP: {sl_tp_result.best_sl_tp.optimal_sl_pct:.1f}% / {sl_tp_result.best_sl_tp.optimal_tp_pct:.1f}%")
        print(f"예상 개선 승률: {sl_tp_result.best_sl_tp.expected_win_rate:.1%}")

        if extraction_result.patterns:
            print(f"\n상위 3개 수익 패턴:")
            for i, p in enumerate(extraction_result.patterns[:3], 1):
                print(f"  {i}. {p.name}: 승률 {p.expected_win_rate:.1%}, 신뢰도 {p.confidence_score:.1f}")

        if openai_report and openai_report.get("insight"):
            print(f"\nOpenAI 핵심 인사이트:")
            print(f"  {openai_report['insight'].get('summary', 'N/A')}")

        return report

    finally:
        await session.close()


def main():
    """CLI 진입점"""
    parser = argparse.ArgumentParser(
        description="v28 데이터 기반 전략 분석 시스템",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--run-id",
        type=str,
        action="append",
        help="분석할 백테스트 실행 ID (여러 개 지정 가능)",
    )

    parser.add_argument(
        "--strategy",
        type=str,
        action="append",
        help="분석할 전략 이름 (여러 개 지정 가능)",
    )

    parser.add_argument(
        "--use-openai",
        action="store_true",
        help="OpenAI GPT-4 분석 사용 (OPENAI_API_KEY 환경변수 필요)",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default="reports/analysis",
        help="결과 저장 디렉토리 (기본: reports/analysis)",
    )

    args = parser.parse_args()

    asyncio.run(
        run_analysis(
            run_ids=args.run_id,
            strategies=args.strategy,
            use_openai=args.use_openai,
            output_dir=args.output_dir,
        )
    )


if __name__ == "__main__":
    main()
