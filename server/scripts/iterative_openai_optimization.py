"""OpenAI 반복 최적화 시스템

OpenAI에게 결과를 피드백하고 반복적으로 전략을 개선하는 시스템
- 이전 결과와 문제점을 OpenAI에게 전달
- 새로운 개선안을 받아 백테스트 실행
- 결과를 다시 피드백하여 반복 개선
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from dataclasses import dataclass

from openai import AsyncOpenAI

# 환경 변수에서 API 키 로드
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 전략 코드 (DOUBLE_DIP_BUY)
STRATEGY_CODE = '''
class DoubleDipBuySignalGenerator:
    """
    Strategy: DOUBLE_DIP_BUY
    - 5분 내 일정 비율 이상 하락 시 진입
    - 24시간 저점 근처에서만 진입
    - RVOL 조건 충족 시 진입
    - SMA200 기반 시장 방향 필터
    """
    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_dip_pct = self.params.get("min_dip_pct", 0.008)
        self.support_tolerance = self.params.get("support_tolerance", 0.05)
        self.min_rvol = self.params.get("min_rvol", 1.5)
        self.position_pct = self.params.get("position_pct", 0.10)
        self._cooldown_minutes = self.params.get("cooldown_minutes", 15)

    def _evaluate_symbol(self, timestamp, history, symbol):
        # 쿨다운 체크
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles_short = history.get_candles(symbol, "5m", 10)
        candles_24h = history.get_candles(symbol, "5m", 288)
        if len(candles_short) < 3 or len(candles_24h) < 100:
            return None

        price = candles_short[-1].close
        prev_price = candles_short[-2].close

        # 시장 방향 필터: price < SMA200 * 0.97 이면 진입 금지
        sma200 = history.calc_sma(symbol, "5m", 200)
        if sma200 and price < sma200 * 0.97:
            return None

        # 진입 조건 1: 5분 내 일정 비율 이상 하락
        dip_pct = (price - prev_price) / prev_price
        if dip_pct > -self.min_dip_pct:
            return None

        # 진입 조건 2: 24시간 저점에서 일정 비율 이내
        low_24h = min(c.low for c in candles_24h)
        distance_from_low = (price - low_24h) / low_24h
        if distance_from_low > self.support_tolerance:
            return None

        # 진입 조건 3: RVOL 조건
        rvol = history.calc_rvol(symbol, "5m", 10)
        if not rvol or rvol < self.min_rvol:
            return None

        return {"symbol": symbol, "action": "buy", "strategy": "DOUBLE_DIP_BUY"}
'''


@dataclass
class BacktestResult:
    """백테스트 결과"""
    params: dict
    return_pct: float
    win_rate: float
    trades: int
    avg_win: float
    avg_loss: float
    mdd: float
    pf: float
    sharpe: float


async def run_backtest(params: dict) -> BacktestResult:
    """백테스트 실행"""
    import sys
    sys.path.insert(0, '/Users/keullaeseuting/Desktop/gylee/stock_test/server')

    from src.backtesting.data.data_loader import BacktestDataLoader
    from src.backtesting.engine.backtest_engine import BacktestEngine, create_pullback_exit_checker
    from src.backtesting.models.schemas import BacktestConfig
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

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30)

    config = BacktestConfig(
        strategy="DOUBLE_DIP_BUY",
        symbols=SYMBOLS,
        start_date=start_date,
        end_date=end_date,
        interval="5m",
        initial_capital=1_000_000,
        parameters=params.get("entry", {}),
    )

    async with async_session() as db:
        loader = BacktestDataLoader(db)
        engine = BacktestEngine(config, loader)

        signal_generator = get_signal_generator("DOUBLE_DIP_BUY", params.get("entry", {}))
        exit_checker = create_pullback_exit_checker(
            stop_loss_pct=params["exit"]["stop_loss_pct"],
            take_profit_pct=params["exit"]["take_profit_pct"],
            trailing_trigger_pct=params["exit"]["trailing_trigger_pct"],
            trailing_stop_pct=params["exit"]["trailing_stop_pct"],
        )

        result = await engine.run(signal_generator, exit_checker)

        return BacktestResult(
            params=params,
            return_pct=result.metrics.total_return_pct,
            win_rate=result.metrics.win_rate,
            trades=result.metrics.total_trades,
            avg_win=result.metrics.avg_win_pct or 0,
            avg_loss=result.metrics.avg_loss_pct or 0,
            mdd=result.metrics.max_drawdown_pct,
            pf=result.metrics.profit_factor or 0,
            sharpe=result.metrics.sharpe_ratio or 0,
        )


async def get_openai_recommendation(
    client: AsyncOpenAI,
    iteration: int,
    history: list[dict],
    best_result: BacktestResult | None,
) -> dict:
    """OpenAI에게 개선안 요청"""

    system_prompt = """당신은 암호화폐 트레이딩 전략 최적화 전문가입니다.
이전 시도의 결과를 분석하고, 더 나은 파라미터를 제안해야 합니다.

중요한 규칙:
1. 실제 백테스트 결과를 기반으로 판단하세요
2. 이론적 최적화보다 실증적 결과를 중시하세요
3. 작은 변화로 점진적 개선을 시도하세요
4. 승률과 수익률 모두 고려하세요

응답 형식 (반드시 JSON으로):
{
    "analysis": "이전 결과 분석 (2-3문장)",
    "changes": "변경 사항 설명 (2-3문장)",
    "params": {
        "entry": {
            "min_dip_pct": 0.008,
            "support_tolerance": 0.05,
            "min_rvol": 1.5,
            "cooldown_minutes": 15
        },
        "exit": {
            "stop_loss_pct": -0.02,
            "take_profit_pct": 0.015,
            "trailing_trigger_pct": 0.01,
            "trailing_stop_pct": 0.005
        }
    },
    "expected_improvement": "예상 개선 효과 (1문장)"
}
"""

    # 히스토리 구성
    history_text = "\n\n".join([
        f"### 시도 {h['iteration']}\n"
        f"파라미터: {json.dumps(h['params'], indent=2)}\n"
        f"결과: 수익률 {h['return_pct']:+.2f}%, 승률 {h['win_rate']:.1f}%, "
        f"거래 {h['trades']}건, avg_win {h['avg_win']:+.2f}%, avg_loss {h['avg_loss']:+.2f}%"
        for h in history
    ])

    best_text = ""
    if best_result:
        best_text = f"""
### 현재 최고 성과
- 수익률: {best_result.return_pct:+.2f}%
- 승률: {best_result.win_rate:.1f}%
- 파라미터: {json.dumps(best_result.params, indent=2)}
"""

    user_prompt = f"""## DOUBLE_DIP_BUY 전략 최적화 - 반복 {iteration}

### 전략 코드
```python
{STRATEGY_CODE}
```

### 이전 시도 결과
{history_text}
{best_text}

### 핵심 인사이트
1. 타이트한 SL/TP (-2%/+1.5%)가 넓은 SL/TP (-5%/+5%)보다 효과적이었음
2. 넓은 SL은 MDD를 크게 증가시킴
3. 승률 62%에서 수익률이 -1.1%인 이유는 avg_win(+1.21%) < avg_loss(-1.99%)

### 요청
위 결과를 분석하고, 수익률을 양수로 만들 수 있는 파라미터를 제안해주세요.
작은 변화로 점진적 개선을 시도하세요.

JSON 형식으로만 응답해주세요.
"""

    response = await client.chat.completions.create(
        model="gpt-4-turbo-preview",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=2000,
    )

    content = response.choices[0].message.content

    # JSON 파싱
    try:
        # JSON 블록 추출
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        return json.loads(content.strip())
    except json.JSONDecodeError as e:
        print(f"JSON 파싱 오류: {e}")
        print(f"원본 응답: {content}")
        # 기본값 반환
        return {
            "analysis": "파싱 오류",
            "changes": "기본값 사용",
            "params": {
                "entry": {
                    "min_dip_pct": 0.008,
                    "support_tolerance": 0.05,
                    "min_rvol": 1.5,
                    "cooldown_minutes": 15,
                },
                "exit": {
                    "stop_loss_pct": -0.02,
                    "take_profit_pct": 0.015,
                    "trailing_trigger_pct": 0.01,
                    "trailing_stop_pct": 0.005,
                },
            },
            "expected_improvement": "기본값",
        }


async def main():
    """메인 실행"""
    print("\n" + "=" * 100)
    print("OpenAI 반복 최적화 시스템")
    print("=" * 100)

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    # 초기 파라미터 (Original - 가장 좋았던 설정)
    initial_params = {
        "entry": {
            "min_dip_pct": 0.008,
            "support_tolerance": 0.05,
            "min_rvol": 1.5,
            "cooldown_minutes": 15,
        },
        "exit": {
            "stop_loss_pct": -0.02,
            "take_profit_pct": 0.015,
            "trailing_trigger_pct": 0.01,
            "trailing_stop_pct": 0.005,
        },
    }

    # 결과 히스토리
    history = []
    best_result = None
    all_results = []

    # 초기 백테스트 (baseline)
    print("\n📊 Iteration 0: Baseline 백테스트...")
    baseline_result = await run_backtest(initial_params)

    history.append({
        "iteration": 0,
        "params": initial_params,
        "return_pct": baseline_result.return_pct,
        "win_rate": baseline_result.win_rate,
        "trades": baseline_result.trades,
        "avg_win": baseline_result.avg_win,
        "avg_loss": baseline_result.avg_loss,
    })

    best_result = baseline_result
    all_results.append(("Baseline", baseline_result))

    print(f"   결과: 수익률 {baseline_result.return_pct:+.2f}%, 승률 {baseline_result.win_rate:.1f}%")

    # 5회 반복 최적화
    for iteration in range(1, 6):
        print(f"\n{'='*100}")
        print(f"📊 Iteration {iteration}: OpenAI 분석 및 최적화")
        print(f"{'='*100}")

        # OpenAI에게 개선안 요청
        print(f"\n   🤖 OpenAI에게 개선안 요청 중...")
        recommendation = await get_openai_recommendation(
            client, iteration, history, best_result
        )

        print(f"\n   📝 OpenAI 분석:")
        print(f"      {recommendation.get('analysis', 'N/A')}")
        print(f"\n   🔧 변경 사항:")
        print(f"      {recommendation.get('changes', 'N/A')}")
        print(f"\n   📈 예상 효과:")
        print(f"      {recommendation.get('expected_improvement', 'N/A')}")

        # 새 파라미터로 백테스트
        new_params = recommendation.get("params", initial_params)

        print(f"\n   🧪 새 파라미터로 백테스트 실행 중...")
        print(f"      Entry: min_dip={new_params['entry'].get('min_dip_pct', 0.008)}, "
              f"support_tol={new_params['entry'].get('support_tolerance', 0.05)}, "
              f"rvol={new_params['entry'].get('min_rvol', 1.5)}")
        print(f"      Exit: SL={new_params['exit'].get('stop_loss_pct', -0.02)*100:.1f}%, "
              f"TP={new_params['exit'].get('take_profit_pct', 0.015)*100:.1f}%, "
              f"Trail={new_params['exit'].get('trailing_trigger_pct', 0.01)*100:.1f}%/"
              f"{new_params['exit'].get('trailing_stop_pct', 0.005)*100:.1f}%")

        try:
            result = await run_backtest(new_params)

            # 히스토리에 추가
            history.append({
                "iteration": iteration,
                "params": new_params,
                "return_pct": result.return_pct,
                "win_rate": result.win_rate,
                "trades": result.trades,
                "avg_win": result.avg_win,
                "avg_loss": result.avg_loss,
            })

            all_results.append((f"Iteration {iteration}", result))

            # 결과 출력
            improvement = result.return_pct - best_result.return_pct
            wr_change = result.win_rate - best_result.win_rate

            print(f"\n   📊 결과:")
            print(f"      수익률: {result.return_pct:+.2f}% (변화: {improvement:+.2f}%p)")
            print(f"      승률: {result.win_rate:.1f}% (변화: {wr_change:+.1f}%p)")
            print(f"      거래 수: {result.trades}건")
            print(f"      평균 승리: {result.avg_win:+.2f}%, 평균 패배: {result.avg_loss:+.2f}%")
            print(f"      MDD: {result.mdd:.2f}%, Sharpe: {result.sharpe:.2f}")

            if result.return_pct > best_result.return_pct:
                print(f"\n   ✅ 개선됨! 새로운 최고 성과")
                best_result = result
            else:
                print(f"\n   ❌ 개선 안됨. 최고 성과 유지: {best_result.return_pct:+.2f}%")

        except Exception as e:
            print(f"\n   ❌ 백테스트 실패: {str(e)}")
            import traceback
            traceback.print_exc()

    # 최종 결과 요약
    print("\n" + "=" * 100)
    print("📈 최종 결과 요약")
    print("=" * 100)

    print(f"\n{'Iteration':<15} {'수익률':>10} {'승률':>10} {'거래수':>10} {'Avg Win':>10} {'Avg Loss':>10}")
    print("-" * 75)

    for name, result in all_results:
        print(f"{name:<15} {result.return_pct:>+9.2f}% {result.win_rate:>9.1f}% "
              f"{result.trades:>10} {result.avg_win:>+9.2f}% {result.avg_loss:>+9.2f}%")

    print("-" * 75)
    print(f"\n🏆 최고 성과:")
    print(f"   수익률: {best_result.return_pct:+.2f}%")
    print(f"   승률: {best_result.win_rate:.1f}%")
    print(f"   파라미터: {json.dumps(best_result.params, indent=6)}")

    # 결과 저장
    report = {
        "timestamp": datetime.now().isoformat(),
        "iterations": len(all_results),
        "best_result": {
            "return_pct": best_result.return_pct,
            "win_rate": best_result.win_rate,
            "trades": best_result.trades,
            "params": best_result.params,
        },
        "all_results": [
            {
                "name": name,
                "return_pct": r.return_pct,
                "win_rate": r.win_rate,
                "trades": r.trades,
                "avg_win": r.avg_win,
                "avg_loss": r.avg_loss,
                "params": r.params,
            }
            for name, r in all_results
        ],
    }

    with open("reports/analysis/iterative_optimization_results.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n📁 결과 저장: reports/analysis/iterative_optimization_results.json")
    print("\n" + "=" * 100)
    print("완료!")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
