"""올바른 반복 최적화 시스템

로직:
1. OpenAI 제안 결과가 양수(+)면 → 그 전략 채택하고 더 개선 시도
2. OpenAI 제안 결과가 음수(-)면 → 왜 음수인지 분석, 개선안 받아서 양수될 때까지 재시도
3. 10번 반복

핵심: 음수가 나오면 스킵하지 않고, 분석 후 반드시 양수가 될 때까지 재시도
"""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import structlog
from openai import AsyncOpenAI

from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine, create_pullback_exit_checker
from src.backtesting.models.schemas import BacktestConfig
from src.backtesting.strategies.strategy_adapters import get_signal_generator
from src.models.database import async_session

logger = structlog.get_logger()

# OpenAI API 키
OPENAI_API_KEY = None  # 환경변수에서 로드

# 심볼 리스트
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


async def run_backtest(params: dict, days: int = 30) -> dict:
    """백테스트 실행"""
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
            "return_pct": result.metrics.total_return_pct,
            "win_rate": result.metrics.win_rate,
            "trades": result.metrics.total_trades,
            "avg_win": result.metrics.avg_win_pct or 0,
            "avg_loss": result.metrics.avg_loss_pct or 0,
            "profit_factor": result.metrics.profit_factor or 0,
            "params": params,
        }


async def get_improvement_suggestion(
    client: AsyncOpenAI,
    current_params: dict,
    current_result: dict,
    history: list,
    is_negative: bool,
) -> dict:
    """OpenAI에게 개선안 요청"""

    if is_negative:
        # 음수인 경우: 왜 음수인지 분석하고 양수가 되도록 개선안 요청
        prompt = f"""
당신은 암호화폐 트레이딩 전략 최적화 전문가입니다.

현재 DOUBLE_DIP_BUY 전략이 **음수 수익률**을 기록했습니다. 반드시 양수(+)가 되도록 개선해야 합니다.

## 현재 결과 (문제)
- 수익률: {current_result['return_pct']:+.2f}% ❌ 음수
- 승률: {current_result['win_rate']:.1f}%
- 거래 수: {current_result['trades']}건
- 평균 승리: {current_result['avg_win']:+.2f}%
- 평균 패배: {current_result['avg_loss']:+.2f}%

## 현재 파라미터
Entry:
- min_dip_pct: {current_params['entry']['min_dip_pct']} (진입 전 최소 하락폭)
- support_tolerance: {current_params['entry']['support_tolerance']} (지지선 허용 범위)
- min_rvol: {current_params['entry']['min_rvol']} (최소 상대 거래량)
- cooldown_minutes: {current_params['entry']['cooldown_minutes']}

Exit:
- stop_loss_pct: {current_params['exit']['stop_loss_pct']} (손절)
- take_profit_pct: {current_params['exit']['take_profit_pct']} (익절)
- trailing_trigger_pct: {current_params['exit']['trailing_trigger_pct']}
- trailing_stop_pct: {current_params['exit']['trailing_stop_pct']}

## 이전 시도 이력
{json.dumps(history, indent=2, ensure_ascii=False)}

## 요청사항
1. **왜 음수가 나왔는지** 분석해주세요
2. **양수(+)가 되도록** 파라미터를 수정해주세요
3. 이전 시도에서 양수였던 파라미터 조합을 참고해주세요

## 응답 형식 (JSON)
{{
    "analysis": "음수가 된 원인 분석",
    "solution": "양수로 만들기 위한 해결책",
    "params": {{
        "entry": {{
            "min_dip_pct": 숫자,
            "support_tolerance": 숫자,
            "min_rvol": 숫자,
            "cooldown_minutes": 15
        }},
        "exit": {{
            "stop_loss_pct": 숫자 (음수),
            "take_profit_pct": 숫자 (양수),
            "trailing_trigger_pct": 숫자,
            "trailing_stop_pct": 숫자
        }}
    }},
    "expected_improvement": "예상 개선 효과"
}}
"""
    else:
        # 양수인 경우: 더 좋은 성과를 위한 개선안 요청
        prompt = f"""
당신은 암호화폐 트레이딩 전략 최적화 전문가입니다.

현재 DOUBLE_DIP_BUY 전략이 **양수 수익률**을 기록했습니다. 이를 **더 개선**해주세요.

## 현재 결과 (양수 ✓)
- 수익률: {current_result['return_pct']:+.2f}% ✓
- 승률: {current_result['win_rate']:.1f}%
- 거래 수: {current_result['trades']}건
- 평균 승리: {current_result['avg_win']:+.2f}%
- 평균 패배: {current_result['avg_loss']:+.2f}%

## 현재 파라미터
Entry:
- min_dip_pct: {current_params['entry']['min_dip_pct']}
- support_tolerance: {current_params['entry']['support_tolerance']}
- min_rvol: {current_params['entry']['min_rvol']}
- cooldown_minutes: {current_params['entry']['cooldown_minutes']}

Exit:
- stop_loss_pct: {current_params['exit']['stop_loss_pct']}
- take_profit_pct: {current_params['exit']['take_profit_pct']}
- trailing_trigger_pct: {current_params['exit']['trailing_trigger_pct']}
- trailing_stop_pct: {current_params['exit']['trailing_stop_pct']}

## 이전 시도 이력
{json.dumps(history, indent=2, ensure_ascii=False)}

## 요청사항
1. 현재 양수인 상태를 유지하면서 **더 높은 수익률**을 달성할 수 있는 방법을 제안해주세요
2. 리스크를 크게 늘리지 않으면서 개선해주세요
3. 미세 조정 위주로 안전하게 개선해주세요

## 응답 형식 (JSON)
{{
    "analysis": "현재 전략의 강점과 개선 가능한 부분",
    "solution": "더 나은 성과를 위한 조정 방안",
    "params": {{
        "entry": {{
            "min_dip_pct": 숫자,
            "support_tolerance": 숫자,
            "min_rvol": 숫자,
            "cooldown_minutes": 15
        }},
        "exit": {{
            "stop_loss_pct": 숫자 (음수),
            "take_profit_pct": 숫자 (양수),
            "trailing_trigger_pct": 숫자,
            "trailing_stop_pct": 숫자
        }}
    }},
    "expected_improvement": "예상 개선 효과"
}}
"""

    response = await client.chat.completions.create(
        model="gpt-4-turbo-preview",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    return json.loads(response.choices[0].message.content)


async def main():
    import os
    global OPENAI_API_KEY
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

    if not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        return

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    print("\n" + "=" * 100)
    print("올바른 반복 최적화 시스템")
    print("=" * 100)
    print("\n로직:")
    print("  - 양수(+) 결과 → 채택하고 더 개선 시도")
    print("  - 음수(-) 결과 → 원인 분석 후 양수가 될 때까지 재시도")
    print("  - 목표: 10번의 성공적인 개선 반복\n")

    # 시작 파라미터 (이전 최적화에서 찾은 베스트)
    current_params = {
        "entry": {
            "min_dip_pct": 0.01,
            "support_tolerance": 0.04,
            "min_rvol": 1.7,
            "cooldown_minutes": 15,
        },
        "exit": {
            "stop_loss_pct": -0.018,
            "take_profit_pct": 0.019,
            "trailing_trigger_pct": 0.01,
            "trailing_stop_pct": 0.005,
        },
    }

    history = []  # 모든 시도 기록
    successful_iterations = []  # 성공적인 반복만 기록
    best_result = None
    best_params = None

    # 먼저 시작 파라미터로 베이스라인 테스트
    print("📊 베이스라인 테스트 중...")
    baseline_result = await run_backtest(current_params)
    print(f"   수익률: {baseline_result['return_pct']:+.2f}%, 승률: {baseline_result['win_rate']:.1f}%")

    history.append({
        "iteration": "Baseline",
        "return_pct": baseline_result["return_pct"],
        "win_rate": baseline_result["win_rate"],
        "trades": baseline_result["trades"],
        "status": "positive" if baseline_result["return_pct"] > 0 else "negative",
        "params": current_params,
    })

    if baseline_result["return_pct"] > 0:
        best_result = baseline_result
        best_params = current_params.copy()
        successful_iterations.append({
            "name": "Baseline",
            **baseline_result,
        })

    iteration = 0
    max_iterations = 10  # 10번의 성공적인 개선
    max_retries_per_iteration = 5  # 음수일 때 최대 재시도 횟수

    while len(successful_iterations) < max_iterations + 1:  # +1 for baseline
        iteration += 1
        print(f"\n{'=' * 100}")
        print(f"📊 Iteration {iteration}")
        print("=" * 100)

        # 현재 결과 기반으로 개선안 요청
        current_result = successful_iterations[-1] if successful_iterations else baseline_result
        is_negative = current_result["return_pct"] <= 0

        retry_count = 0
        success = False

        while not success and retry_count < max_retries_per_iteration:
            retry_count += 1

            if retry_count > 1:
                print(f"\n   🔄 재시도 {retry_count}/{max_retries_per_iteration} (음수 결과 개선 시도)")

            print(f"\n   🤖 OpenAI에게 {'양수 달성을 위한' if is_negative else '추가 개선'} 제안 요청 중...")

            try:
                suggestion = await get_improvement_suggestion(
                    client,
                    current_params,
                    current_result,
                    history[-10:],  # 최근 10개 이력만 전달
                    is_negative,
                )

                print(f"\n   📝 분석: {suggestion.get('analysis', 'N/A')[:100]}...")
                print(f"   💡 해결책: {suggestion.get('solution', 'N/A')[:100]}...")

                new_params = suggestion["params"]

                # 파라미터 출력
                print(f"\n   🧪 새 파라미터로 백테스트:")
                print(f"      Entry: dip={new_params['entry']['min_dip_pct']}, "
                      f"support={new_params['entry']['support_tolerance']}, "
                      f"rvol={new_params['entry']['min_rvol']}")
                print(f"      Exit: SL={new_params['exit']['stop_loss_pct']*100:.1f}%, "
                      f"TP={new_params['exit']['take_profit_pct']*100:.1f}%")

                # 백테스트 실행
                new_result = await run_backtest(new_params)

                print(f"\n   📊 결과:")
                print(f"      수익률: {new_result['return_pct']:+.2f}%")
                print(f"      승률: {new_result['win_rate']:.1f}%")
                print(f"      거래 수: {new_result['trades']}건")

                # 이력에 추가
                history.append({
                    "iteration": f"Iter{iteration}-Try{retry_count}",
                    "return_pct": new_result["return_pct"],
                    "win_rate": new_result["win_rate"],
                    "trades": new_result["trades"],
                    "status": "positive" if new_result["return_pct"] > 0 else "negative",
                    "params": new_params,
                    "analysis": suggestion.get("analysis", ""),
                    "solution": suggestion.get("solution", ""),
                })

                if new_result["return_pct"] > 0:
                    # 양수 달성!
                    print(f"\n   ✅ 양수 달성! +{new_result['return_pct']:.2f}%")

                    successful_iterations.append({
                        "name": f"Iteration {iteration}",
                        **new_result,
                    })

                    current_params = new_params
                    current_result = new_result

                    # 베스트 업데이트
                    if best_result is None or new_result["return_pct"] > best_result["return_pct"]:
                        best_result = new_result
                        best_params = new_params.copy()
                        print(f"   🏆 새로운 최고 성과!")

                    success = True
                    is_negative = False
                else:
                    # 아직 음수
                    print(f"\n   ❌ 아직 음수 ({new_result['return_pct']:+.2f}%). 재시도 필요...")
                    is_negative = True
                    current_result = new_result
                    current_params = new_params

            except Exception as e:
                print(f"\n   ⚠️ 오류 발생: {e}")
                import traceback
                traceback.print_exc()

        if not success:
            print(f"\n   ⚠️ {max_retries_per_iteration}번 재시도 후에도 양수 달성 실패. 다음 iteration으로...")
            # 이전 성공한 파라미터로 복귀
            if successful_iterations:
                current_params = successful_iterations[-1]["params"]
                current_result = successful_iterations[-1]

        # 안전장치: 너무 많은 iteration 방지
        if iteration > 30:
            print("\n⚠️ 최대 반복 횟수(30) 도달. 종료합니다.")
            break

    # 최종 결과 출력
    print("\n" + "=" * 100)
    print("📈 최종 결과 요약")
    print("=" * 100)

    print(f"\n{'Iteration':<20} {'수익률':>10} {'승률':>10} {'거래수':>10} {'Avg Win':>10} {'Avg Loss':>10}")
    print("-" * 80)

    for result in successful_iterations:
        print(f"{result['name']:<20} {result['return_pct']:>+9.2f}% {result['win_rate']:>9.1f}% "
              f"{result['trades']:>10} {result['avg_win']:>+9.2f}% {result['avg_loss']:>+9.2f}%")

    print("-" * 80)

    print(f"\n🏆 최고 성과:")
    print(f"   수익률: {best_result['return_pct']:+.2f}%")
    print(f"   승률: {best_result['win_rate']:.1f}%")
    print(f"   거래 수: {best_result['trades']}건")
    print(f"\n   최적 파라미터:")
    print(json.dumps(best_params, indent=6, ensure_ascii=False))

    # 결과 저장
    output_dir = Path("reports/analysis")
    output_dir.mkdir(parents=True, exist_ok=True)

    final_results = {
        "timestamp": datetime.now().isoformat(),
        "total_iterations": iteration,
        "successful_iterations": len(successful_iterations),
        "best_result": {
            "return_pct": best_result["return_pct"],
            "win_rate": best_result["win_rate"],
            "trades": best_result["trades"],
            "avg_win": best_result["avg_win"],
            "avg_loss": best_result["avg_loss"],
            "params": best_params,
        },
        "all_successful": successful_iterations,
        "full_history": history,
    }

    output_path = output_dir / "proper_optimization_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_results, f, indent=2, ensure_ascii=False)

    print(f"\n📁 결과 저장: {output_path}")
    print("\n" + "=" * 100)
    print("완료!")
    print("=" * 100 + "\n")

    return final_results


if __name__ == "__main__":
    asyncio.run(main())
