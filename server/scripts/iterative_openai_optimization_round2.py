"""OpenAI 반복 최적화 시스템 - Round 2 (Iteration 6-10)

이전 라운드 최고 성과 (Iteration 2):
- 수익률: +2.45%
- 승률: 60.9%
- 파라미터: min_dip=0.01, support=0.04, rvol=1.7, SL=-1.8%, TP=+1.9%

Round 2: 이 최고 성과를 기준으로 5번 더 최적화
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from dataclasses import dataclass

from openai import AsyncOpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

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
        self.min_dip_pct = self.params.get("min_dip_pct", 0.01)
        self.support_tolerance = self.params.get("support_tolerance", 0.04)
        self.min_rvol = self.params.get("min_rvol", 1.7)
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
    system_prompt = """당신은 암호화폐 트레이딩 전략 최적화 전문가입니다.
이전 시도의 결과를 분석하고, 더 나은 파라미터를 제안해야 합니다.

중요한 규칙:
1. 실제 백테스트 결과를 기반으로 판단하세요
2. 이론적 최적화보다 실증적 결과를 중시하세요
3. 작은 변화로 점진적 개선을 시도하세요
4. 현재 최고 성과(+2.45%)를 기준으로 미세 조정하세요
5. 급격한 변화는 피하고, 파라미터를 ±10% 범위 내에서 조정하세요

응답 형식 (반드시 JSON으로):
{
    "analysis": "이전 결과 분석 (2-3문장)",
    "changes": "변경 사항 설명 (2-3문장)",
    "params": {
        "entry": {
            "min_dip_pct": 0.01,
            "support_tolerance": 0.04,
            "min_rvol": 1.7,
            "cooldown_minutes": 15
        },
        "exit": {
            "stop_loss_pct": -0.018,
            "take_profit_pct": 0.019,
            "trailing_trigger_pct": 0.01,
            "trailing_stop_pct": 0.005
        }
    },
    "expected_improvement": "예상 개선 효과 (1문장)"
}
"""

    history_text = "\n\n".join([
        f"### 시도 {h['iteration']}\n"
        f"파라미터: {json.dumps(h['params'], indent=2)}\n"
        f"결과: 수익률 {h['return_pct']:+.2f}%, 승률 {h['win_rate']:.1f}%, "
        f"거래 {h['trades']}건, avg_win {h['avg_win']:+.2f}%, avg_loss {h['avg_loss']:+.2f}%"
        for h in history[-6:]  # 최근 6개만
    ])

    best_text = ""
    if best_result:
        best_text = f"""
### 현재 최고 성과 (이것을 기준으로 미세 조정)
- 수익률: {best_result.return_pct:+.2f}%
- 승률: {best_result.win_rate:.1f}%
- 거래 수: {best_result.trades}건
- 평균 승리: {best_result.avg_win:+.2f}%
- 평균 패배: {best_result.avg_loss:+.2f}%
- 파라미터: {json.dumps(best_result.params, indent=2)}
"""

    user_prompt = f"""## DOUBLE_DIP_BUY 전략 최적화 - Round 2, Iteration {iteration}

### 전략 코드
```python
{STRATEGY_CODE}
```

### 이전 시도 결과 (최근 6개)
{history_text}
{best_text}

### Round 2 목표
현재 최고 성과 +2.45%를 더 개선하여 +5% 이상 달성

### 핵심 인사이트
1. SL -1.8%, TP +1.9%가 현재 최적
2. min_dip 1.0%, support_tolerance 4%가 효과적
3. 거래 수와 수익률 사이 균형이 중요
4. 너무 공격적인 변화는 성능 악화 (Iteration 4 참고: -4.28%)

### 요청
현재 최고 성과를 기준으로 **미세 조정**하여 수익률을 더 높일 수 있는 파라미터를 제안해주세요.
파라미터 변화는 ±10% 범위 내로 제한해주세요.

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

    try:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        return json.loads(content.strip())
    except json.JSONDecodeError as e:
        print(f"JSON 파싱 오류: {e}")
        return {
            "analysis": "파싱 오류",
            "changes": "기본값 사용",
            "params": {
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
            },
            "expected_improvement": "기본값",
        }


async def main():
    print("\n" + "=" * 100)
    print("OpenAI 반복 최적화 시스템 - Round 2 (Iteration 6-10)")
    print("=" * 100)

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    # 이전 라운드 결과 로드
    with open("reports/analysis/iterative_optimization_results.json", "r") as f:
        prev_results = json.load(f)

    # 히스토리 복원
    history = []
    for i, r in enumerate(prev_results["all_results"]):
        history.append({
            "iteration": i,
            "params": r["params"],
            "return_pct": r["return_pct"],
            "win_rate": r["win_rate"],
            "trades": r["trades"],
            "avg_win": r["avg_win"],
            "avg_loss": r["avg_loss"],
        })

    # 최고 성과 파라미터로 시작 (Iteration 2)
    best_params = prev_results["best_result"]["params"]
    best_result = BacktestResult(
        params=best_params,
        return_pct=prev_results["best_result"]["return_pct"],
        win_rate=prev_results["best_result"]["win_rate"],
        trades=prev_results["best_result"]["trades"],
        avg_win=0, avg_loss=0, mdd=0, pf=0, sharpe=0
    )

    all_results = [(f"Round1-{r['name']}", BacktestResult(
        params=r["params"],
        return_pct=r["return_pct"],
        win_rate=r["win_rate"],
        trades=r["trades"],
        avg_win=r["avg_win"],
        avg_loss=r["avg_loss"],
        mdd=0, pf=0, sharpe=0
    )) for r in prev_results["all_results"]]

    print(f"\n📊 이전 라운드 최고 성과:")
    print(f"   수익률: {best_result.return_pct:+.2f}%")
    print(f"   승률: {best_result.win_rate:.1f}%")
    print(f"   파라미터: {json.dumps(best_params, indent=6)}")

    # 5회 추가 반복 최적화 (Iteration 6-10)
    for iteration in range(6, 11):
        print(f"\n{'='*100}")
        print(f"📊 Iteration {iteration}: OpenAI 분석 및 최적화")
        print(f"{'='*100}")

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

        new_params = recommendation.get("params", best_params)

        print(f"\n   🧪 새 파라미터로 백테스트 실행 중...")
        print(f"      Entry: min_dip={new_params['entry'].get('min_dip_pct', 0.01)}, "
              f"support_tol={new_params['entry'].get('support_tolerance', 0.04)}, "
              f"rvol={new_params['entry'].get('min_rvol', 1.7)}")
        print(f"      Exit: SL={new_params['exit'].get('stop_loss_pct', -0.018)*100:.1f}%, "
              f"TP={new_params['exit'].get('take_profit_pct', 0.019)*100:.1f}%, "
              f"Trail={new_params['exit'].get('trailing_trigger_pct', 0.01)*100:.1f}%/"
              f"{new_params['exit'].get('trailing_stop_pct', 0.005)*100:.1f}%")

        try:
            result = await run_backtest(new_params)

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

            improvement = result.return_pct - best_result.return_pct
            wr_change = result.win_rate - best_result.win_rate

            print(f"\n   📊 결과:")
            print(f"      수익률: {result.return_pct:+.2f}% (변화: {improvement:+.2f}%p)")
            print(f"      승률: {result.win_rate:.1f}% (변화: {wr_change:+.1f}%p)")
            print(f"      거래 수: {result.trades}건")
            print(f"      평균 승리: {result.avg_win:+.2f}%, 평균 패배: {result.avg_loss:+.2f}%")

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
    print("📈 Round 2 최종 결과 요약")
    print("=" * 100)

    print(f"\n{'Iteration':<20} {'수익률':>10} {'승률':>10} {'거래수':>10} {'Avg Win':>10} {'Avg Loss':>10}")
    print("-" * 80)

    for name, result in all_results:
        print(f"{name:<20} {result.return_pct:>+9.2f}% {result.win_rate:>9.1f}% "
              f"{result.trades:>10} {result.avg_win:>+9.2f}% {result.avg_loss:>+9.2f}%")

    print("-" * 80)
    print(f"\n🏆 최고 성과:")
    print(f"   수익률: {best_result.return_pct:+.2f}%")
    print(f"   승률: {best_result.win_rate:.1f}%")
    print(f"   파라미터: {json.dumps(best_result.params, indent=6)}")

    # 결과 저장
    report = {
        "timestamp": datetime.now().isoformat(),
        "round": 2,
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

    with open("reports/analysis/iterative_optimization_round2_results.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n📁 결과 저장: reports/analysis/iterative_optimization_round2_results.json")
    print("\n" + "=" * 100)
    print("완료!")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
