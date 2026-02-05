# 트레이딩 알고리즘 구조 문서

> **버전**: 5.2 (Dynamic Exit Strategy)
> **최종 수정**: 2026-02-04
> **목표**: 소자본(1,000만원 이하) 안정적 성장, MDD 5% 제약
> **거래소**: Upbit (KRW 현물 전용)

---

## 목차

1. [시스템 개요](#시스템-개요)
2. [급등 근접 종목 시스템](#급등-근접-종목-시스템)
3. [Ignition 전략](#ignition-전략)
4. [Structure Anti-Chase 필터](#structure-anti-chase-필터)
5. [**v5.0 분봉 급등 듀얼 트리거**](#v50-분봉-급등-듀얼-트리거)
6. [**v5.2 과열도 기반 동적 청산**](#v52-과열도-기반-동적-청산)
7. [Dip Scalper 급락 스캘퍼](#dip-scalper-급락-스캘퍼)
8. [Pullback 눌림목 매수 전략](#pullback-눌림목-매수-전략)
9. [MDD 방어 시스템](#mdd-방어-시스템)
10. [Surge 자본 재배분 시스템](#surge-자본-재배분-시스템)
11. [리스크 관리](#리스크-관리)
12. [설정 파라미터](#설정-파라미터)
13. [모니터링 API](#모니터링-api)
14. [파일 구조](#파일-구조)
15. [변경 이력](#변경-이력)

---

## 시스템 개요

### 투자 철학

```
"이미 급등한 주식이 아닌, 급등 시작하는 주식을 찾아라"

기존 문제:
- 급등 감지 후 추격 매수 → 고점 물림 → 손절 반복

v4.4 해결책:
- 6개 조건 점수화 → 급등 "근접" 종목 미리 감지
- Structure Anti-Chase → 거리 기반 추격 방지
- 분산 투자 (최대 10개 포지션)
```

### 전략 구성

| 전략 | 역할 | 포지션 배분 |
|------|------|------------|
| Ignition | 급등 초반 점화 포착 | 최대 50% |
| Pullback | 눌림목 매수 (분산) | 5~10% × 10개 |

### 핵심 수치 요약

| 항목 | Ignition | Pullback |
|------|----------|----------|
| 진입 트리거 | 1분 +1.5% | Score 55+ |
| 거래량 요구 | 3.0x 이상 | - |
| 과열 차단 | 8.0x 초과 | - |
| 손절 | -2% | -3% |
| 익절 | 1.5R (50%) | +5% (50%) |
| 트레일링 | 고점 -1.5% | 고점 -2% |
| 타임스톱 | 10분 | 4시간 |
| 최대 포지션 | - | 10개 |

---

## 급등 근접 종목 시스템

### 개요

급등 조건에 "근접"한 종목을 6개 지표로 점수화하여 모니터링하는 시스템.
대시보드에서 실시간으로 확인 가능.

### 6개 조건 점수 체계

| 조건 | 가중치 | 목표 | 설명 |
|------|--------|------|------|
| 📈 1분 상승률 | 25% | 1.5% | 급등 시작 감지 |
| 📊 거래량 배수 | 20% | 3.0x | 평균 대비 거래량 |
| ⚡ 거래량 급증 | 15% | 2.0x | 직전 1분 대비 급증 |
| 🚀 거래량 가속 | 10% | 1.5x | 최근 3분 vs 이전 3분 |
| 🌡️ 과열 체크 | 10% | 8.0x 미만 | 너무 늦은 진입 방지 |
| 🛡️ 추격매수 방지 | 20% | ATR 0.9 이내 | 고점 추격 방지 |

### 점수 계산 공식

```python
# 1분 상승률 (25%)
change_1m_score = min(100, (change_1m_pct / 1.5) * 100)

# 거래량 배수 (20%)
volume_score = min(100, (volume_ratio / 3.0) * 100)

# 거래량 급증 - 직전 1분 대비 (15%)
volume_spike = current_1m_vol / prev_1m_vol
volume_spike_score = min(100, (volume_spike / 2.0) * 100)

# 거래량 가속 - 최근 3분 vs 이전 3분 (10%)
volume_accel = avg(recent_3m) / avg(prior_3m)
volume_accel_score = min(100, (volume_accel / 1.5) * 100)

# 과열 체크 (10%) - 8배 초과 시 0점
vol_overheat_score = 100 if vol_ratio <= 8.0 else 0

# 추격매수 방지 (20%) - dist_atr 기반
anti_chase_score = 100 if dist_atr <= 0.9 else 0

# 총점 (가중 평균)
total_score = (
    change_1m_score * 0.25 +
    volume_score * 0.20 +
    volume_spike_score * 0.15 +
    volume_accel_score * 0.10 +
    vol_overheat_score * 0.10 +
    anti_chase_score * 0.20
)
```

### 필터 조건

- **최소 변화율**: 0.1% 미만 제외 (하락/횡보 종목 필터링)
- **표시 기준**: 총점 70점 이상만 대시보드에 표시

### 대시보드 UI

```
┌─────────────────────────────────────────┐
│ 🎯 급등 근접 종목                 5개 감지 │
├─────────────────────────────────────────┤
│ 🏆 TOP 5 (급등 조건에 가장 근접)          │
│                                         │
│ ┌─────────────────────────────────────┐ │
│ │ ① BTC 비트코인              85점   │ │
│ │                           4/6 충족  │ │
│ │ ₩97,500,000              +1.23%    │ │
│ │ ✓ 상승률 1.2%  ✓ 거래량 3.5배       │ │
│ │ ✓ 급증 2.1배   ✗ 가속 0.8배         │ │
│ │ ✓ 과열 OK      ✓ 추격 A            │ │
│ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

---

## Ignition 전략

### 전략 철학

```
Ignition = "점화" 전략
급등 초반 점화 순간에만 진입하는 전략

핵심 원리:
1. Setup Score로 "폭발 직전" 종목 선별
2. 1분봉 실시간 감시로 점화 순간 포착
3. Anti-Chase 필터로 과추격 방지
```

### Setup Score (S1~S5 전조 패턴)

5가지 전조 패턴으로 "폭발 직전" 종목 선별 (총 100점)

| 패턴 | 이름 | 최대 점수 | 기준 |
|------|------|----------|------|
| S1 | 변동성 수축 | 20점 | BB Width <= 20% |
| S2 | 거래량 건조 | 20점 | Vol <= 평균 50% |
| S3 | 매집 구조 | 25점 | Higher Lows 3회+, Close Pos 55%+ |
| S4 | 상대 강도 | 20점 | BTC 대비 +10% |
| S5 | 레인지 압박 | 15점 | 상단 30% 위치, 터치 3회+ |

**Watchlist 진입 기준**:
- Setup Score >= 70점 → Watchlist 추가
- 최대 20개 종목
- TTL: 2시간

### 진입 조건 (3가지 필수)

| 조건 | 기준 | 목적 |
|------|------|------|
| 1분 변화율 | >= 1.5% | 급등 시작 감지 |
| 거래량 비율 | >= 3.0x | 진짜 급등 확인 |
| Anti-Chase | Type A/B | 과추격 방지 |

### VolOverheat Guard (과열 차단)

```
거래량 기반 진입 타이밍 필터:

vol_ratio < 2.0x  → 초기 단계 (허용)
vol_ratio 2.0~5.0x → 최적 구간 (적극 진입)
vol_ratio 5.0~8.0x → 주의 구간 (신중)
vol_ratio > 8.0x  → 과열 (차단!)

점수 계산:
vol_overheat_score = max(0, (8.0 - vol_ratio) / 6.0 * 100)
```

### 전일급등주 정책 (HotYesterdayPolicy)

전일 +10% 이상 급등한 종목에 대한 강화 조건:

| 항목 | 일반 종목 | 전일급등주 |
|------|----------|-----------|
| 거래량 요구 | 3.0x | 3.75x (+25%) |
| 포지션 사이즈 | 100% | 60% (-40%) |
| 타임스톱 | 5분 | 3분 (-2분) |

### 청산 조건

| 유형 | 조건 | 청산 비율 |
|------|------|----------|
| 손절 | -2% 또는 1분 저가 -0.5% | 100% |
| 익절 | 1.5R 도달 | 50% |
| 트레일링 | 고점 -1.5% | 100% |
| 타임스톱 | 10분 + 손실 | 100% |

```python
# 트레일링 스톱 동작
trailing_stop = highest_price * 0.985  # 고점에서 1.5% 하락 시 청산
```

---

## Structure Anti-Chase 필터

### 개요 (v4.2)

기존 "5분 변화율 < 5%" 방식을 대체하는 거리 기반 추격 방지 시스템.

### 3가지 거리 지표

```python
# 돌파 레벨 대비 거리
dist_breakout = (current_price - breakout_level) / breakout_level

# VWAP 대비 거리
dist_vwap = (current_price - vwap) / vwap

# ATR 정규화 거리 (핵심 지표)
dist_atr = dist_vwap / (atr / current_price)
```

### Type A/B/X 분류

| Type | 조건 | 의미 | 사이징 |
|------|------|------|--------|
| **A** | dist_atr <= 0.9 | 첫 점화 직후 | 100% |
| **B** | 리테스트 패턴 | 되돌림 후 재진입 | 70% |
| **X** | dist_atr > 0.9 | 과추격 | 차단 |

### Type A (첫 점화) 조건

```python
# dist_atr 0.9 이하 = 아직 VWAP에서 멀리 안 감
if dist_atr <= 0.9:
    entry_type = "A"
    position_mult = 1.0
```

### Type B (리테스트) 조건

```python
# VWAP 근처 (±0.3*atr_pct) 또는 돌파레벨 근처 (±0.25*atr_pct)
if abs(dist_vwap) <= 0.3 * atr_pct:
    entry_type = "B"
    position_mult = 0.7  # 70% 사이징
```

### Type X (차단) 조건

```python
# 이미 너무 멀리 감
if dist_atr > 0.9:
    entry_type = "X"
    return None  # 진입 차단
```

---

## v5.0 분봉 급등 듀얼 트리거

### 개요

v5.0에서 도입된 분봉 기반 급등 감지 시스템. 기존 Anti-Chase Gate가 급등주를 너무 일찍 차단하는 문제를 해결.

```
문제: 급등주(+10%+)가 Anti-Chase Gate에 의해 차단됨
→ 급등주를 잡으려는 알고리즘이 실제 급등주를 놓침

해결책:
1. Anti-Chase Gate 완화: 10% → 20%
2. 분봉 급등 듀얼 트리거: 1분봉/5분봉 급등 감지로 보너스 점수
```

### Anti-Chase Gate 완화

| 버전 | 임계값 | 동작 |
|------|--------|------|
| v4.x | 10% | 일일 +10% 이상 → 완전 차단 |
| **v5.0** | **20%** | 일일 +20% 이상 → 완전 차단 |

```python
# v5.0: Anti-Chase Gate 완화
anti_chase_threshold = settings.anti_chase_gate_threshold  # 기본 0.20 (20%)

if change_rate >= anti_chase_threshold:
    return ScoreResult(
        score=0.0,
        level=0,
        gate="anti_chase",
        reason=f"일일 +{change_rate*100:.1f}% - Anti-Chase Gate 차단",
        components=[],
    )
```

### 분봉 급등 듀얼 트리거 (Candle Surge Bonus)

두 가지 조건 중 하나만 충족해도 보너스 점수 획득:

| 트리거 | 조건1 (분봉 변화율) | 조건2 (RVOL) | 보너스 |
|--------|---------------------|--------------|--------|
| **1분봉 극단 급등** | +7% 이상 | 3.0x 이상 | **+30점** |
| **5분봉 급등** | +4% 이상 | 2.0x 이상 | **+20점** |

```python
def _calc_candle_surge_bonus(self, market_data: dict) -> ScoreComponent:
    """
    v5.0: 분봉 급등 보너스 (0~30)

    조건1: 1분봉 극단 급등 (+7%, RVOL 3x) → +30점
    조건2: 5분봉 급등 (+4%, RVOL 2x) → +20점
    """
    # 1분봉 데이터
    change_1m = market_data.get("change_1m", 0)
    rvol_1m = market_data.get("rvol_1m", 1.0)

    # 5분봉 데이터
    change_5m = market_data.get("change_5m", 0)
    rvol_5m = market_data.get("rvol_5m", 1.0)

    # 조건1: 1분봉 극단 급등 (OR)
    if change_1m >= 0.07 and rvol_1m >= 3.0:
        return 30  # 최대 보너스

    # 조건2: 5분봉 급등 (OR)
    elif change_5m >= 0.04 and rvol_5m >= 2.0:
        return 20

    return 0
```

### RVOL 계산 (분봉 기준)

RVOL은 **해당 분봉의 평균 거래량** 대비 현재 거래량:

```python
# 1분봉 RVOL = 현재 1분봉 거래량 / 최근 20개 1분봉 평균
rvol_1m = current_1m_volume / avg(last_20_1m_volumes)

# 5분봉 RVOL = 현재 5분봉 거래량 / 최근 12개 5분봉 평균
rvol_5m = current_5m_volume / avg(last_12_5m_volumes)
```

### 점수 체계 변경

| 컴포넌트 | v4.x | v5.0 | v5.2 |
|---------|------|------|------|
| Breakout Strength | 0~25 | 0~25 | 0~25 |
| Volume Expansion | 0~25 | 0~25 | 0~25 |
| Trend Context | 0~20 | 0~15 | 0~15 |
| Orderbook Quality | 0~15 | 0~10 | 0~10 |
| Overheat Penalty | -15~0 | -15~0 | **0** (진입 제외) |
| Candle Surge Bonus | - | 0~30 | 0~30 |
| **총점** | 0~85 | 0~105 | 0~105 → 클램프 0~100 |

> **v5.2 변경**: Overheat Penalty가 **진입 점수에서 제외**됨 (0점 고정).
> 대신 청산 전략에서 과열도에 따라 **동적 TP/SL** 적용 (아래 섹션 참조).

### 예상 결과

```
Before (v4.x):
ZORA: 일일 +14.1% → ❌ Anti-Chase Gate 차단
G:    일일 +20.0% → ❌ Anti-Chase Gate 차단

After (v5.0):
ZORA: 일일 +14.1% → ✅ 진입 가능 (< 20%)
      1분봉 +8% + RVOL 5x → ✅ +30점 보너스
      총점: 75점 → Level 1 진입!

G:    5분봉 +5% + RVOL 3x → ✅ +20점 보너스
      총점: 70점 → Level 1 진입!
```

### v5.1 Candle Surge Anti-Chase 우회

v5.1에서 Candle Surge 감지 시 Anti-Chase Gate를 우회:

```python
if change_rate >= 0.20:  # 일일 +20% 이상
    if surge_triggered:
        # 급등 감지됨 → Anti-Chase 우회하고 진입 허용
        pass
    else:
        # 급등 감지 안 됨 → 차단
        return None
```

### 설정 파라미터

```bash
# v5.0 Anti-Chase Gate 완화
ANTI_CHASE_GATE_THRESHOLD=0.20

# v5.0 분봉 급등 트리거
CANDLE_SURGE_1M_CHANGE=0.07    # 1분봉 변화율 임계값
CANDLE_SURGE_1M_RVOL=3.0       # 1분봉 RVOL 임계값
CANDLE_SURGE_5M_CHANGE=0.04    # 5분봉 변화율 임계값
CANDLE_SURGE_5M_RVOL=2.0       # 5분봉 RVOL 임계값
CANDLE_SURGE_BONUS_1M=30       # 1분봉 보너스 점수
CANDLE_SURGE_BONUS_5M=20       # 5분봉 보너스 점수
```

---

## v5.2 과열도 기반 동적 청산

### 개요

v5.2에서 과열 패널티(Overheat Penalty)가 **진입 점수에서 제외**되고, 대신 **청산 전략에서만** 활용됨.

```
기존 문제:
- 과열 패널티가 진입을 막음 → 이미 많이 오른 종목 진입 불가
- 하지만 급등은 초반이 중요, 과열 자체가 진입 차단 이유는 아님

v5.2 해결책:
- 진입: 과열 무시 (점수 0점)
- 청산: 과열도에 따라 동적 TP/SL
  - 과열 높음 → 빨리 익절 (이미 많이 올랐으니 빨리 먹고 나오기)
  - 과열 낮음 → 오래 홀드 (초기 단계니까 더 먹기)
```

### 과열도 계산 (raw_penalty)

과열 정보는 여전히 계산되지만 진입 점수에 반영되지 않음:

| 조건 | 과열 점수 |
|------|----------|
| 전일 대비 +15% 이상 | -15 |
| 전일 대비 +10% 이상 | -12 |
| 전일 대비 +8% 이상 | -8 |
| 전일 대비 +5% 이상 | -5 |
| 1시간 +10% 이상 | -5 추가 |
| RVOL 8배 이상 | -2 추가 |

### 동적 청산 파라미터

| 과열도 | 트레일링 활성화 | 트레일링 폭 | 타임스톱 | 연장 임계값 |
|--------|----------------|------------|---------|------------|
| **높음** (≤-10) | 0.8R / 1.5% | 손절거리 70% | 30분 | +1% 이상 |
| **중간** (-10~-5) | 1.2R / 2% | 손절거리 50% | 45분 | +0.5% 이상 |
| **낮음** (>-5) | 1.5R / 2.5% | 손절거리 30% | 60분 | +0.3% 이상 |

### 동작 예시

```
예시 1: 과열 높음 (overheat = -12)
- 진입: 일일 +12% 상승 종목, 과열 패널티 0점 → 진입 가능
- 청산: 1.5% 수익 시 트레일링 활성화
- 트레일링: 고점에서 -2.1% (손절 3% × 0.7) 하락 시 청산
- 타임스톱: 30분 (빠르게 정리)

예시 2: 과열 낮음 (overheat = -3)
- 진입: 일일 +3% 상승 종목, 과열 패널티 0점 → 진입 가능
- 청산: 2.5% 수익 시 트레일링 활성화
- 트레일링: 고점에서 -0.9% (손절 3% × 0.3) 하락 시 청산
- 타임스톱: 60분 (오래 홀드)
```

### 코드 구현

```python
def _get_dynamic_exit_params(self, overheat_score: float) -> dict:
    """과열도 기반 동적 청산 파라미터"""
    if overheat_score <= -10:
        # 과열 심함: 빠른 익절
        return {
            "trail_trigger_r": 0.8,
            "trail_trigger_pct": 0.015,
            "trailing_mult": 0.7,
            "time_stop_min": 30,
        }
    elif overheat_score <= -5:
        # 과열 중간: 기본값
        return {
            "trail_trigger_r": 1.2,
            "trail_trigger_pct": 0.02,
            "trailing_mult": 0.5,
            "time_stop_min": 45,
        }
    else:
        # 과열 낮음: 오래 홀드
        return {
            "trail_trigger_r": 1.5,
            "trail_trigger_pct": 0.025,
            "trailing_mult": 0.3,
            "time_stop_min": 60,
        }
```

---

## Dip Scalper 급락 스캘퍼

### 개요 (v2.0)

급락 종목의 기술적 반등을 노리는 초단타 전략.

```
"떨어지는 칼날을 잡지 않는다"

v2.0 핵심 변경:
1. BTC 안정성 체크 - BTC 급락 중이면 매수 안함
2. 거래량 급증 체크 (RVOL) - 거래량 없는 급락은 피함
3. 호가창 지지 체크 - 매수벽이 약하면 진입 안함
```

### 진입 조건 (5가지)

| 조건 | 기준 | 목적 |
|------|------|------|
| 급락폭 | -2% ~ -8% | 적정 낙폭 |
| ATR 배수 | 1.2× ATR 이상 | 유의미한 움직임 |
| 거래대금 | 1억 이상 | 유동성 확보 |
| **BTC 안정** | BTC -3% 이내 | 시장 급락 방지 |
| **RVOL** | 2.0x 이상 | 거래량 급증 |
| **호가창 지지** | Bid 비율 40%+ | 매수벽 확인 |

### v2.0 필터 상세

#### 1. BTC 안정성 체크

```python
def _check_btc_stability(self) -> tuple[bool, str]:
    """BTC 1분 변화율 확인"""
    if self._btc_change_1m <= -0.03:  # -3% 이하
        return False, f"BTC 급락 중: {self._btc_change_1m:.2%}"
    return True, "BTC 안정"
```

#### 2. 거래량 급증 체크 (RVOL)

```python
def _check_volume_spike(self, rvol: float) -> tuple[bool, str]:
    """RVOL 2.0x 이상"""
    if rvol < 2.0:
        return False, f"거래량 부족: RVOL {rvol:.1f}"
    return True, f"거래량 급증: RVOL {rvol:.1f}"
```

#### 3. 호가창 지지 체크

```python
def _check_orderbook_support(self, bid_volume, ask_volume) -> tuple[bool, float, str]:
    """매수벽 비율 40% 이상"""
    bid_ratio = bid_volume / (bid_volume + ask_volume)
    if bid_ratio < 0.4:
        return False, bid_ratio, f"매수벽 약함: {bid_ratio:.0%}"
    return True, bid_ratio, f"매수벽 확인: {bid_ratio:.0%}"
```

### 청산 조건

| 유형 | 조건 | 비율 |
|------|------|------|
| 익절 | +0.8% | 100% |
| 손절 | -1.2% | 100% |
| 타임스톱 | 10분 | 100% |

### 설정 파라미터

```bash
# Dip Scalper v2.0
DIP_SCALPER_ENABLED=true
DIP_MIN_DROP_PCT=-0.02        # 최소 -2%
DIP_MAX_DROP_PCT=-0.08        # 최대 -8%
DIP_ATR_MULTIPLIER=1.2        # ATR 배수
DIP_BUY_AMOUNT_KRW=100000     # 매수 금액
DIP_MAX_POSITIONS=5           # 최대 포지션
DIP_TAKE_PROFIT_PCT=0.008     # 익절 +0.8%
DIP_STOP_LOSS_PCT=-0.012      # 손절 -1.2%
DIP_TIME_STOP_MINUTES=10      # 타임스톱

# v2.0 안전 필터
DIP_MIN_RVOL=2.0              # 최소 RVOL
DIP_BTC_CRASH_THRESHOLD=-0.03 # BTC 급락 임계값
DIP_MIN_BID_RATIO=0.4         # 최소 매수벽 비율
```

---

## Pullback 눌림목 매수 전략

### 전략 철학

```
"급등을 쫓지 말고, 급등 후 눌림을 사라"

1. 최근 급등한 종목이
2. 적절히 조정 받고
3. 지지선 근처에서
4. 매수세가 축적되면
5. 분할 매수
```

### 진입 조건

| 조건 | 기준 | 설명 |
|------|------|------|
| 최근 급등 | 24h 변동 5%+ | 급등 이력 필수 |
| 눌림 깊이 | 고점 -3~10% | 적절한 조정 |
| 지지선 | VWAP/SMA 근처 | 반등 예상 구간 |
| 축적 신호 | Bid > Ask | 매수세 우위 |
| 반등 조짐 | 양봉/안정화 | 확인 신호 |

### Pullback Score (5개 컴포넌트)

| 컴포넌트 | 최대점 | 세부 기준 |
|---------|--------|----------|
| **Recent Surge** | 20 | 24h 범위 15%=12pt, 전일 +10%=8pt |
| **Pullback Depth** | 25 | 3~5% 눌림=25pt (이상적) |
| **Support Level** | 20 | VWAP ±1%=8pt, SMA20 ±1%=7pt |
| **Accumulation** | 20 | Bid/Ask 2배=12pt, RVOL<0.8=5pt |
| **Reversal Sign** | 15 | 안정화=6pt, 양봉 2개=4pt |

### Level별 배분

| Level | 점수 | 배분 | 모드 제한 |
|-------|------|------|----------|
| L3 | >= 85 | 10% | SAFE 이상 |
| L2 | 70~84 | 8% | NORMAL 이상 |
| L1 | 55~69 | 5% | AGGRESSIVE만 |
| L0 | < 55 | - | 진입 불가 |

### 청산 조건

| 유형 | 조건 | 청산 비율 |
|------|------|----------|
| 손절 | -3% | 100% |
| 익절 | +5% | 50% |
| 트레일링 발동 | +3% | 활성화 |
| 트레일링 스톱 | 고점 -2% | 100% |
| 타임스톱 | 4시간 | 100% |

### 포지션 관리

```python
max_positions = 10       # 최대 동시 포지션
cooldown_min = 60        # 동일 종목 재진입 쿨다운
```

---

## MDD 방어 시스템

### DD 6-Tier 시스템

| Tier | DD 범위 | 배수 | 모드 | 주요 조치 |
|------|---------|------|------|----------|
| 1 | 0~1% | 1.0x | 정상 | 전체 운영 |
| 2 | 1~2% | 0.8x | 주의 | 감시 강화 |
| 3 | 2~3.5% | 0.6x | SAFE | L3 진입 금지 |
| 4 | 3.5~4.5% | 0.3x | Soft DD | Satellite 신규 OFF |
| 5 | 4.5~5% | 0.1x | 위험 | 헤지 준비 |
| 6 | >= 5% | 0.0x | Hard DD | 분할청산 개시 |

### 일손실 기반 모드 전환

| 조건 | 모드 전환 | 조치 |
|------|----------|------|
| 일손실 >= -1.0% | SAFE | 리스크 감소 |
| 일손실 >= -1.5% | HALT | 신규진입 금지 |
| 주간손실 >= -5% | Satellite OFF | Attack/Core만 |

### Hard DD (>= 5%) 분할청산

```
Phase 1: 손실 포지션 50% 축소
Phase 2: 헤지 추가 (선택적)
Phase 3: DD >= 6% 시 전량 청산
```

---

## Surge 자본 재배분 시스템

### 개요 (v4.5)

Surge/Ignition 신호 발생 시 자본이 부족하면 Satellite 포지션을 자동 청산하여 자본 확보.

```
"급등주에 집중 투자하기 위해 분산 포지션을 정리한다"

원리:
1. Surge 신호 발생 → 필요 자본 계산 (자산의 20%)
2. 가용 현금 부족 → Satellite 포지션 청산 대상 선정
3. 우선순위에 따라 청산 → 자본 확보
4. Surge 매수 실행
```

### 청산 우선순위

| 순위 | 조건 | 설명 |
|------|------|------|
| 1 | **수익 포지션** | PnL% 높은 것부터 (이익 확정) |
| 2 | **소폭 손실** | -2% 이내 (손실 감수 가능) |
| - | 큰 손실 제외 | -2% 초과 (손실 확정 방지) |

### 작동 흐름

```
Surge 신호: KRW-BTC +2.3% (1분)
    │
    ▼
필요 금액: ₩200,000 (자산의 20%)
가용 현금: ₩50,000
부족 금액: ₩150,000
    │
    ▼
Satellite 청산 대상 선정:
  ① KRW-ETH  +3.2%  ₩80,000  → 청산 (이익)
  ② KRW-XRP  +1.5%  ₩60,000  → 청산 (이익)
  ③ KRW-SOL  -0.5%  ₩40,000  → 청산 (소폭 손실)
    │
    ▼
확보 금액: ₩180,000
    │
    ▼
Surge 매수 실행 (조정된 금액)
```

### 코드 구현

```python
# SatelliteStrategy.get_positions_for_liquidation()
def get_positions_for_liquidation(
    self,
    market_data: dict,
    required_amount: float,
    max_loss_pct: float = -0.02,  # 최대 -2% 손실까지만 청산
) -> list[tuple[str, float, float, float]]:
    """
    청산 대상 포지션 선택
    Returns: [(symbol, quantity, current_price, pnl_pct), ...]
    """
    # 1. PnL 계산
    # 2. max_loss_pct 이상인 것만 필터
    # 3. PnL 내림차순 정렬 (수익 큰 것 우선)
    # 4. 필요 금액만큼 선택
```

### 이벤트 로그

청산 시 다음 이벤트가 기록됨:

```json
{
  "event_type": "SATELLITE_EXIT",
  "message": "Satellite liquidated for Surge: KRW-ETH",
  "details": {
    "reason": "SURGE_CAPITAL_REALLOCATION",
    "quantity": 0.5,
    "price": 4200000,
    "pnl_pct": 0.032,
    "realized_pnl": 67200
  }
}
```

### Slack 알림

```
💰 *Satellite 청산 (Surge 자본 재배분)*
> 심볼: `KRW-ETH`
> 수량: 0.5000
> 가격: ₩4,200,000
> 수익률: 3.2%
> 손익: ₩67,200
```

---

## 리스크 관리

### 단일 포지션 최대 손실

```python
# Ignition (50% 배분, -2% 손절)
max_loss_ignition = 50% × 2% = 1.0%

# Pullback L1 (5% 배분, -3% 손절)
max_loss_pullback_l1 = 5% × 3% = 0.15%

# 최악 시나리오: Pullback 10개 전부 손절
max_loss_pullback_all = 10 × 7% × 3% = 2.1%
```

### 리스크 비교

| 항목 | Ignition | Pullback |
|------|----------|----------|
| 단일 손실 | 최대 1.0% | 0.15~0.3% |
| 집중도 | 높음 | 낮음 (분산) |
| 연속 손실 위험 | 중간 | 낮음 |

---

## 설정 파라미터

### 환경 변수 (.env)

```bash
# ===== 거래소 =====
EXCHANGE_TYPE=upbit
UPBIT_ACCESS_KEY=your_key
UPBIT_SECRET_KEY=your_secret

# ===== 운영 모드 =====
ENABLE_LIVE_TRADING=false

# ===== Ignition 설정 =====
IGNITION_MODE=NORMAL
IGNITION_CHANGE_1M_MIN=1.5
IGNITION_VOLUME_RATIO_MIN=3.0
IGNITION_VOL_OVERHEAT_MAX=8.0
IGNITION_STOP_LOSS_PCT=-0.02
IGNITION_TRAIL_PCT=-0.015
IGNITION_TIME_STOP_MIN=10

# ===== Pullback 설정 =====
PULLBACK_MODE=NORMAL
PULLBACK_SCORE_L1=55
PULLBACK_SCORE_L2=70
PULLBACK_SCORE_L3=85
PULLBACK_ALLOC_L1=0.05
PULLBACK_ALLOC_L2=0.08
PULLBACK_ALLOC_L3=0.10
PULLBACK_MAX_POSITIONS=10
PULLBACK_STOP_LOSS_PCT=-0.03
PULLBACK_TAKE_PROFIT_PCT=0.05
PULLBACK_TIME_STOP_HOURS=4

# ===== Risk 설정 =====
DAILY_LOSS_LIMIT_SAFE=-0.01
DAILY_LOSS_LIMIT_HALT=-0.015
```

### 주요 임계값 표

| 파라미터 | 값 | 설명 |
|---------|-----|------|
| `change_1m_min` | 1.5% | 급등 시작 기준 |
| `volume_ratio_min` | 3.0x | 거래량 배수 기준 |
| `volume_spike_target` | 2.0x | 직전 대비 급증 목표 |
| `volume_accel_target` | 1.5x | 가속도 목표 |
| `vol_overheat_max` | 8.0x | 과열 차단 기준 |
| `dist_atr_max` | 0.9 | 추격 차단 기준 |
| `setup_score_min` | 70 | Watchlist 진입 |
| `pullback_score_l1` | 55 | Pullback L1 |

---

## 모니터링 API

### 엔드포인트 목록

```bash
# 헬스 체크
GET /api/health

# 요약 정보
GET /api/summary

# 포지션 조회
GET /api/positions

# 급등 근접 종목
GET /api/surge/candidates?min_score=70&limit=20

# Pullback 상태
GET /api/pullback/status

# Pullback Score 조회
GET /api/pullback/score/{symbol}
```

### 급등 근접 종목 응답 예시

```json
{
  "candidates": [
    {
      "symbol": "KRW-BTC",
      "current_price": 97500000,
      "korean_name": "비트코인",
      "change_1m": {"score": 80, "value": 1.2, "target": 1.5},
      "volume": {"score": 100, "ratio": 3.5, "target": 3.0},
      "volume_spike": {"score": 100, "ratio": 2.1, "target": 2.0},
      "volume_accel": {"score": 53, "ratio": 0.8, "target": 1.5},
      "vol_overheat": {"score": 100, "reason": ""},
      "anti_chase": {"score": 100, "entry_type": "A", "dist_atr": 0.53},
      "total_score": 85,
      "is_hot_yesterday": false
    }
  ],
  "total_scanned": 236,
  "filtered_count": 5
}
```

---

## 파일 구조

```
server/src/
├── config.py                    # 전역 설정
├── api/
│   └── routes.py                # API 라우트
│
├── strategies/
│   ├── ignition/                # Ignition 전략
│   │   ├── surge_detector.py    # 급등 근접 점수 계산
│   │   ├── setup_score.py       # Setup Score (S1~S5)
│   │   ├── setup_engine.py      # Watchlist 관리
│   │   ├── ignition_engine.py   # 점화 감지
│   │   ├── ignition_strategy.py # 통합 전략
│   │   └── filters/
│   │       ├── structure_anti_chase.py  # Type A/B/X
│   │       ├── vol_overheat_guard.py    # 과열 차단
│   │       └── hot_yesterday_policy.py  # 전일급등주
│   │
│   ├── pullback_strategy.py     # Pullback 전략
│   └── pullback_score.py        # Pullback Score 계산
│
├── risk/
│   ├── risk_overlay.py          # 리스크 오버레이
│   ├── risk_engine.py           # DD 6-Tier 시스템
│   └── config.py                # 리스크 설정
│
├── engine/
│   └── core.py                  # 트레이딩 엔진
│
├── exchange/
│   └── upbit.py                 # Upbit 거래소
│
└── data/
    ├── candle_manager.py        # 캔들 데이터
    └── symbol_manager.py        # 종목 정보 (한글명)
```

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| **5.2** | **2026-02-04** | **과열도 기반 동적 청산 전략** |
| | | - Overheat Penalty 진입 점수에서 제외 (0점 고정) |
| | | - 과열도에 따라 동적 TP/SL 적용 |
| | | - 과열 높음: 0.8R/1.5%, 30분 타임스톱 (빠른 익절) |
| | | - 과열 낮음: 1.5R/2.5%, 60분 타임스톱 (오래 홀드) |
| | | - 트레일링 폭 동적 조정 (0.3~0.7× 손절거리) |
| **5.1** | **2026-02-04** | **Candle Surge Anti-Chase 우회** |
| | | - Candle Surge 감지 시 Anti-Chase Gate 우회 |
| | | - 일일 +20% 이상이어도 급등 감지되면 진입 허용 |
| | | - Dip Scalper v2.0 (BTC/RVOL/호가창 필터) |
| **5.0** | **2026-02-04** | **분봉 급등 듀얼 트리거** |
| | | - Anti-Chase Gate 완화: 10% → 20% |
| | | - 1분봉 극단 급등 트리거 (+7%, RVOL 3x → +30점) |
| | | - 5분봉 급등 트리거 (+4%, RVOL 2x → +20점) |
| | | - market_data에 change_1m/5m, rvol_1m/5m 추가 |
| | | - 점수 체계 재조정 (총점 105 → 클램프 100) |
| 4.5 | 2026-02-03 | Surge 자본 재배분 시스템 |
| | | - Satellite 포지션 자동 청산으로 자본 확보 |
| | | - 수익 포지션 우선 청산 (이익 확정) |
| | | - 큰 손실 포지션 보호 (-2% 초과 제외) |
| | | - Slack 알림 및 이벤트 로깅 |
| 4.4 | 2026-02-03 | Surge Proximity 6개 조건 체계 |
| | | - 거래량 급증 (Spike) 지표 추가 |
| | | - 거래량 가속 (Accel) 지표 추가 |
| | | - 가중치 재조정 |
| | | - 대시보드 UI 한글화 및 개선 |
| | | - 조건별 충족/미충족 표시 |
| 4.3 | 2026-02-02 | Structure Anti-Chase 도입 |
| | | - Type A/B/X 분류 체계 |
| | | - dist_atr 거리 기반 추격 방지 |
| | | - VolOverheat Guard |
| 4.2 | 2026-02-02 | Pullback 전략 도입 |
| | | - 5개 Score 컴포넌트 |
| | | - 분산 투자 (최대 10개) |
| 4.1 | 2026-02-02 | Ignition 전략 기본 구조 |
| 4.0 | 2026-02-02 | Upbit 거래소 전환 |
| 3.x | 2026-01-30 | MDD 5% 방어 시스템 |

---

## Quick Reference

```
┌─────────────────────────────────────────────────────────┐
│            v5.2 트레이딩 시스템 요약                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  v5.2 동적 청산 전략 (NEW):                             │
│  ┌─────────────────────────────────────────────┐        │
│  │ 🔥 과열 높음: 0.8R/1.5% → 30분 (빠른 익절)  │        │
│  │ 📊 과열 중간: 1.2R/2.0% → 45분 (기본값)     │        │
│  │ 🚀 과열 낮음: 1.5R/2.5% → 60분 (오래 홀드)  │        │
│  │ ※ Overheat Penalty 진입 점수에서 제외 (0점) │        │
│  └─────────────────────────────────────────────┘        │
│                                                         │
│  v5.0 분봉 급등 듀얼 트리거:                             │
│  ┌─────────────────────────────────────────────┐        │
│  │ 🔥 1분봉 극단:  +7% AND RVOL 3x  → +30점   │        │
│  │ 📈 5분봉 급등:  +4% AND RVOL 2x  → +20점   │        │
│  │ ※ v5.1: Surge 감지 시 Anti-Chase 우회      │        │
│  └─────────────────────────────────────────────┘        │
│                                                         │
│  Dip Scalper v2.0 (급락 스캘퍼):                        │
│  ┌─────────────────────────────────────────────┐        │
│  │ 📉 급락폭: -2% ~ -8%                        │        │
│  │ 🔒 BTC 안정: -3% 이내                       │        │
│  │ 📊 RVOL: 2.0x 이상                          │        │
│  │ 🛡️ 호가창: Bid 비율 40%+                    │        │
│  └─────────────────────────────────────────────┘        │
│                                                         │
│  급등 근접 조건 (6개):                                   │
│  ┌─────────────────────────────────────────────┐        │
│  │ 📈 1분 상승률   25%   >= 1.5%               │        │
│  │ 📊 거래량 배수  20%   >= 3.0x               │        │
│  │ ⚡ 거래량 급증  15%   >= 2.0x (직전 대비)    │        │
│  │ 🚀 거래량 가속  10%   >= 1.5x (3분 평균)    │        │
│  │ 🌡️ 과열 체크    10%   <= 8.0x              │        │
│  │ 🛡️ 추격 방지    20%   dist_atr <= 0.9      │        │
│  └─────────────────────────────────────────────┘        │
│                                                         │
│  Anti-Chase Type:                                       │
│  - Type A: 첫 점화 (100% 사이징)                        │
│  - Type B: 리테스트 (70% 사이징)                        │
│  - Type X: 차단 (진입 금지)                             │
│                                                         │
│  DD 방어:                                               │
│  - 0~2%: 정상 운영                                      │
│  - 2~5%: 단계적 축소                                    │
│  - 5%+: 신규진입 금지, 분할청산                          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```
