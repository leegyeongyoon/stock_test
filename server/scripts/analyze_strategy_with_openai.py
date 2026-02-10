"""전략 코드 + 백테스트 결과를 OpenAI에 전달하여 분석

전략 코드를 직접 전달해서 문제점과 개선안을 도출
"""

import asyncio
import os
from openai import AsyncOpenAI

# 전략 코드 (DOUBLE_DIP_BUY - 가장 높은 성과 전략)
DOUBLE_DIP_STRATEGY_CODE = '''
class DoubleDipBuySignalGenerator:
    """
    Strategy 2: DOUBLE_DIP_BUY (v27 개선)
    - 5m dip: -0.8% or worse (완화: 기존 -2.0%)
    - Within 5% of 24h low (완화: 기존 2%)
    - RVOL > 1.5x (완화: 기존 3.0x)
    - SL: -2.0% / TP: +1.5%
    - 쿨다운: 15분
    """
    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_dip_pct = self.params.get("min_dip_pct", 0.008)  # 완화: 0.020→0.008
        self.support_tolerance = self.params.get("support_tolerance", 0.05)  # 완화: 0.02→0.05
        self.min_rvol = self.params.get("min_rvol", 1.5)  # 완화: 3.0→1.5
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

        # 진입 조건 1: 5분 내 -0.8% 이상 하락
        dip_pct = (price - prev_price) / prev_price
        if dip_pct > -self.min_dip_pct:  # -0.8%
            return None

        # 진입 조건 2: 24시간 저점에서 5% 이내
        low_24h = min(c.low for c in candles_24h)
        distance_from_low = (price - low_24h) / low_24h
        if distance_from_low > self.support_tolerance:  # 5%
            return None

        # 진입 조건 3: RVOL > 1.5배
        rvol = history.calc_rvol(symbol, "5m", 10)
        if not rvol or rvol < self.min_rvol:  # 1.5x
            return None

        # 신호 반환
        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "DOUBLE_DIP_BUY",
            "indicators": {
                "price": price,
                "dip_pct": dip_pct,
                "low_24h": low_24h,
                "distance_from_low": distance_from_low,
                "rvol": rvol,
            },
        }
'''

# 백테스트 결과 (DOUBLE_DIP_BUY)
BACKTEST_RESULTS = '''
## DOUBLE_DIP_BUY 백테스트 결과 (30일)
- 총 거래: 3,466건
- 승률: 62.2%
- 월간 수익률: -0.95%
- MDD: 9.93%
- Profit Factor: 0.99
- 평균 승리: +1.21%
- 평균 패배: -1.99%

## 전략별 비교 (45,058건 분석)
| 전략 | 승률 | 평균 수익 | 총 수익 |
|------|------|----------|---------|
| DOUBLE_DIP_BUY | 60.1% | +0.01% | +67.58% |
| EMA_BOUNCE_SCALP | 45.9% | -0.28% | -2193.92% |
| ATR_EXPANSION_ENTRY | 46.9% | -0.42% | -4104.24% |
| DATA_DRIVEN_V28 | 67.8% | -0.40% | -1722.70% |

## 통계적으로 유의미한 지표 (승/패 차이)
- distance_from_low: 승리 0.0446 vs 패배 0.0329 (높을수록 좋음)
- dip_pct: 승리 -0.42% vs 패배 -0.34% (더 큰 하락에서 진입할수록 좋음)
- price: 승리 244K vs 패배 546K (저가 코인이 더 좋음)
- rvol: 승리 5.80 vs 패배 7.78 (낮을수록 좋음 - 예상과 반대!)

## 청산 조건 (현재)
- SL: -2.0%
- TP: +1.5%
- 트레일링 트리거: +1.0%
- 트레일링 스탑: 0.5%

## SL/TP 최적화 분석
- 최적 SL: -5.0%
- 최적 TP: +5.0%
- 예상 승률: 63.5%
'''


async def analyze_with_openai():
    """OpenAI로 전략 코드와 백테스트 결과 분석"""
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    system_prompt = """당신은 암호화폐 트레이딩 전략 전문가입니다.
제공된 전략 코드와 백테스트 결과를 분석하여 구체적인 개선안을 제시하세요.

응답 형식:
1. 문제점 분석 (왜 승률 62%인데 수익이 -0.95%인지)
2. 진입 조건 개선안 (코드로 제시)
3. 청산 조건 개선안 (코드로 제시)
4. 예상 효과

주의:
- 분석 결과에 기반한 구체적 수치 제시
- Python 코드로 수정안 제시
- 과적합 경고 포함
"""

    user_prompt = f"""다음 전략 코드와 백테스트 결과를 분석해주세요.

## 전략 코드
```python
{DOUBLE_DIP_STRATEGY_CODE}
```

{BACKTEST_RESULTS}

위 데이터를 바탕으로:
1. 승률 62%인데 수익이 -0.95%인 이유 분석
2. 진입 조건 개선안 (구체적 수치와 코드)
3. 청산 조건 개선안 (구체적 수치와 코드)
4. 개선 후 예상 효과

한국어로 답변해주세요."""

    print("=" * 80)
    print("OpenAI 분석 중... (전략 코드 + 백테스트 결과)")
    print("=" * 80 + "\n")

    response = await client.chat.completions.create(
        model="gpt-4-turbo-preview",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=3000,
    )

    analysis = response.choices[0].message.content
    tokens_used = response.usage.total_tokens if response.usage else 0

    print(analysis)
    print("\n" + "=" * 80)
    print(f"토큰 사용: {tokens_used}")
    print("=" * 80)

    # 파일로 저장
    with open("reports/analysis/openai_strategy_analysis.md", "w") as f:
        f.write(f"# DOUBLE_DIP_BUY 전략 OpenAI 분석\n\n")
        f.write(f"생성일시: {__import__('datetime').datetime.now()}\n\n")
        f.write(analysis)

    print(f"\n분석 결과 저장: reports/analysis/openai_strategy_analysis.md")


if __name__ == "__main__":
    asyncio.run(analyze_with_openai())
