# 트레이딩 알고리즘 구조 문서

> **버전**: 4.2 (Pullback 눌림목 매수 전략)
> **최종 수정**: 2026-02-02
> **목표**: 소자본(1,000만원 이하) 안정적 성장, MDD 5% 제약
> **거래소**: Upbit (KRW 현물 전용)

---

## 목차

1. [시스템 개요](#시스템-개요)
2. [Pullback 눌림목 매수 전략](#pullback-눌림목-매수-전략) ← **v4.2 핵심**
3. [Pullback Score 시스템](#pullback-score-시스템)
4. [MDD 5% 방어 시스템](#mdd-5-방어-시스템)
5. [리스크 관리](#리스크-관리)
6. [실행 엔진](#실행-엔진)
7. [설정 파라미터](#설정-파라미터)
8. [모니터링](#모니터링)
9. [Deprecated: Attack 전략](#deprecated-attack-전략)

---

## 시스템 개요

### v4.2 전략 전환 배경

```
문제점 (v4.1 Attack 전략):
- 급등 "후" 추격 매수 → 이미 상승 끝난 시점에 진입
- 고점 물림 반복 → 손절 연속 → 자본 소진
- RVOL, Breakout = "후행 지표" (이미 일어난 일 감지)

해결책 (v4.2 Pullback 전략):
- 급등 "전" 선행 지표 활용 → 저점에서 매수
- 눌림목 매수 → 반등 시 수익
- 호가창 불균형, 축적 신호 = "선행 지표" (앞으로 일어날 일 예측)
```

### 투자 철학 (v4.2)

```
"급등을 쫓지 말고, 눌림을 사라"

기존 사고방식: 급등 감지 → 추격 매수 → 고점 물림
새로운 사고방식: 급등 후 조정 대기 → 눌림목 매수 → 반등 수익
```

- **눌림목 매수**: 급등 후 조정 구간에서 저점 매수
- **선행 지표 활용**: 호가창 불균형, 축적 신호로 반등 예측
- **MDD 5% 절대 수호**: 손절 -3%로 리스크 관리
- **현금이 헤지다**: 시그널 없으면 현금 보유

### 핵심 수치 (v4.2 vs v4.1)

| 항목 | v4.1 Attack | v4.2 Pullback |
|------|-------------|---------------|
| 전략 핵심 | 급등 추격 | **눌림목 매수** |
| 지표 유형 | 후행 (RVOL, Breakout) | **선행 (호가창, 축적)** |
| 진입 시점 | 급등 중 | **조정 후 반등 초기** |
| 최대 포지션 | 1개 | **10개 (분산)** |
| 포지션당 배분 | 50% | **5~10%** |
| 손절 | -3% | **-3%** |
| 익절 | 트레일링 | **+5% 또는 트레일링** |
| 타임스탑 | 45분 | **4시간** |

### 선행 vs 후행 지표

```
후행 지표 (Attack - deprecated):
┌─────────────────────────────────────────────┐
│ RVOL (거래량 폭발)   → 이미 급등 진행 중     │
│ Breakout (고점 돌파) → 이미 상승 시작됨      │
│ Close Position       → 현재 봉 위치 (과거)   │
└─────────────────────────────────────────────┘
         ↓
    급등 "후" 감지 = 고점 물림

선행 지표 (Pullback - v4.2):
┌─────────────────────────────────────────────┐
│ 호가창 불균형 (Bid > Ask) → 매수세 우위      │
│ 축적 신호 (Accumulation)  → 세력 매집 중     │
│ 지지선 근접               → 반등 가능 구간   │
└─────────────────────────────────────────────┘
         ↓
    반등 "전" 감지 = 저점 매수
```

---

## Pullback 눌림목 매수 전략

### 전략 핵심 구조

```
┌─────────────────────────────────────────────────────────────┐
│                 Pullback 눌림목 매수 전략                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │
│   │  Pre-filter  │───►│  Pullback    │───►│  Gate Check  │ │
│   │  (거래대금)  │    │  Score 계산  │    │  + Execution │ │
│   └──────────────┘    └──────────────┘    └──────────────┘ │
│          │                   │                    │        │
│          ▼                   ▼                    ▼        │
│   거래대금 1억+       55점+ (L1)          포지션 10개 미만  │
│   변화율 0.5~15%      70점+ (L2)          DD 5% 미만       │
│                       85점+ (L3)          NORMAL 모드      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Position Management                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │
│   │  Stop Loss   │    │  Take Profit │    │  Trailing    │ │
│   │    -3%       │    │    +5%       │    │  +3% → -2%   │ │
│   └──────────────┘    └──────────────┘    └──────────────┘ │
│                                                             │
│   ┌──────────────┐                                         │
│   │  Time Stop   │  4시간 경과 시 강제 청산                 │
│   │   4시간      │                                         │
│   └──────────────┘                                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 진입 조건 (Pullback Signal)

| 조건 | 기준 | 설명 |
|------|------|------|
| Pullback Score | ≥ 55점 (L1) | 눌림목 점수 시스템 |
| 최근 급등 이력 | 24h 변동 8%+ | 급등 후 조정 필수 |
| 눌림 깊이 | 고점 대비 -3~8% | 적절한 조정 |
| 지지선 근접 | VWAP/SMA/24h저점 근처 | 반등 예상 구간 |
| 축적 신호 | Bid > Ask | 매수세 우위 (선행!) |
| 포지션 수 | < 10개 | 분산 투자 |
| 운영 모드 | NORMAL | SAFE/HALT 시 진입 금지 |

### 포지션 배분 (레벨별 차등)

```python
# Pullback 레벨별 배분
PULLBACK_ALLOC_L1 = 0.05  # 5% (기본 시그널)
PULLBACK_ALLOC_L2 = 0.08  # 8% (강한 시그널)
PULLBACK_ALLOC_L3 = 0.10  # 10% (매우 강한 시그널)

# 계산 예시 (계좌 50만원 기준)
# L1 시그널: 50만원 × 5% = 25,000원 매수
# L2 시그널: 50만원 × 8% = 40,000원 매수
# L3 시그널: 50만원 × 10% = 50,000원 매수

# 최대 10개 포지션 = 최대 노출 50~100%
```

### 청산 조건

| 조건 | 기준 | 설명 |
|------|------|------|
| 손절 (Stop Loss) | -3% | 진입가 대비 -3% 하락 시 |
| 익절 (Take Profit) | +5% | 진입가 대비 +5% 상승 시 |
| 트레일링 (Trailing) | +3% 후 -2% | 고점 대비 -2% 하락 시 |
| 타임스탑 (Time Stop) | 4시간 | 진입 후 4시간 경과 시 |

### 청산 우선순위

```
1. 손절 (-3%)     → 즉시 청산 (리스크 관리)
2. 트레일링       → 수익 보호 (고점 대비 -2%)
3. 익절 (+5%)     → 목표 달성
4. 타임스탑 (4h)  → 횡보 방지
```

---

## Pullback Score 시스템

### Score 구성요소 (선행 지표 중심)

| 구성요소 | 최대 점수 | 유형 | 설명 |
|----------|----------|------|------|
| Recent Surge (급등 이력) | 20점 | 필터 | 최근 24h 변동폭 8%+ |
| Pullback Depth (눌림 깊이) | 25점 | 핵심 | 고점 대비 -3~8% 조정 |
| Support Level (지지선) | 20점 | 핵심 | VWAP/SMA/24h저점 근접 |
| **Accumulation (축적 신호)** | 20점 | **선행** | **Bid > Ask (호가창)** |
| Reversal Sign (반등 조짐) | 15점 | 확인 | 양봉, 거래량 증가 |

**총점: 100점**

### 핵심 선행 지표: 호가창 불균형

```python
def calc_accumulation_signal(bid_volume, ask_volume):
    """
    핵심 선행 지표: 호가창 매수/매도 비율

    bid_volume > ask_volume → 매수세 우위 → 상승 예상
    """
    if ask_volume == 0:
        return 0

    bid_ask_ratio = bid_volume / ask_volume

    # Ratio별 점수
    if bid_ask_ratio >= 2.0:
        return 20  # 매수세 압도적 우위
    elif bid_ask_ratio >= 1.5:
        return 15  # 매수세 강함
    elif bid_ask_ratio >= 1.2:
        return 10  # 매수세 소폭 우위
    else:
        return 0   # 매수세 부족
```

### Score Level 임계값

```python
PULLBACK_SCORE_L1 = 55   # 기본 진입 (5% 배분)
PULLBACK_SCORE_L2 = 70   # 강한 신호 (8% 배분)
PULLBACK_SCORE_L3 = 85   # 매우 강한 신호 (10% 배분)
```

### Score 계산 예시

```
KRW-ETH 눌림목 분석:

Recent Surge:     15점 (24h 변동폭 7.5%)
Pullback Depth:   20점 (고점 대비 -4.2% 조정)
Support Level:    15점 (VWAP 근접)
Accumulation:     15점 (Bid/Ask = 1.6)
Reversal Sign:    10점 (양봉 형성)
─────────────────────────────────────
Total Score:      75점 → L2 진입 (8% 배분)
```

### Pre-filtering (Rate Limit 방지)

```python
def prefilter_symbols_for_pullback(market_data):
    """
    호가창 조회 전 Pre-filtering (API 호출 최소화)

    조건:
    - 거래대금 > 1억 KRW
    - 변화율 0.5% ~ 15% (너무 횡보/급등 제외)
    - 최대 20개 심볼로 제한
    """
    candidates = []
    for symbol, data in market_data.items():
        volume = data.get("volume_24h", 0)
        change = abs(data.get("price_change_pct", 0))

        if volume > 100_000_000 and 0.5 < change < 15.0:
            candidates.append(symbol)

    return candidates[:20]  # Rate Limit 방지
```

---

## MDD 5% 방어 시스템

### 우선순위 체인

```
1. SYSTEM SAFETY     → 지연/실패/연결 상태
2. TAIL RISK         → DD / 일손실 / 급변장
3. MARKET REGIME     → BTC 기반 risk-on/off
4. POSITION STATE    → 현재 포지션 상태
5. SIGNAL ENGINE     → Pullback 진입 신호

원칙: 신호가 아무리 좋아도 1~3에서 위험이면 절대 진입 금지
```

### DD 단계별 대응

| DD 범위 | 상태 | 사이징 | Pullback |
|---------|------|--------|----------|
| 0~2% | 정상 | **100%** | 활성화 |
| 2~3.5% | 주의 | **70%** | 활성화 (신중) |
| 3.5~5% | 경계 | **30%** | **신규진입 금지** |
| ≥5% | HALT | **0%** | **전량 청산** |

### 일손실 제한

```python
DAILY_LOSS_LIMIT_SAFE = -0.015  # -1.5% → SAFE 모드
DAILY_LOSS_LIMIT_HALT = -0.030  # -3.0% → HALT 모드
```

---

## 리스크 관리

### Pullback 리스크 계산

```python
# 단일 포지션 최대 손실 (L1 기준)
def max_pullback_loss(equity, allocation, stop_pct):
    """
    equity = 500,000 (50만원)
    allocation = 0.05 (5%)
    stop_pct = 0.03 (-3%)

    position_size = 500,000 × 0.05 = 25,000원
    max_loss = 25,000 × 0.03 = 750원
    loss_pct = 750 / 500,000 = 0.15%
    """
    return equity * allocation * stop_pct / equity

# 최악의 시나리오: 10개 포지션 전부 손절
# 평균 배분 7% × 10개 × -3% = -2.1% < MDD 5% ✓
```

### Attack vs Pullback 리스크 비교

| 항목 | Attack (v4.1) | Pullback (v4.2) |
|------|---------------|-----------------|
| 단일 손실 | 50% × -3% = **-1.5%** | 5% × -3% = **-0.15%** |
| 최대 손실 (연속) | 2연패 = **-3%** | 10연패 = **-2.1%** |
| 분산 효과 | 없음 (단일 포지션) | 있음 (최대 10개) |
| 리스크 특성 | 집중 (고위험) | 분산 (저위험) |

---

## 실행 엔진

### 주문 흐름

```
Pullback Signal (Score 55+)
        │
        ▼
┌─────────────────────┐
│   Gate Check        │
│   (10포지션 미만)   │
└─────────────────────┘
        │ Pass
        ▼
┌─────────────────────┐
│   Position Sizing   │
│   Equity × 5~10%    │
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│   Market Order      │
│   MIN: 10,000 KRW   │
│   MAX: 100,000 KRW  │
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│   Position Tracking │
│   Entry Price 기록  │
└─────────────────────┘
        │
        ▼
    Position Active
```

### 호가창 캐싱 (Rate Limit 방지)

```python
# 호가창 캐시 (30초 TTL)
_orderbook_cache: dict[str, tuple[dict, float]] = {}
_orderbook_cache_ttl: float = 30.0

async def get_orderbook_cached(symbol: str):
    """캐시된 호가창 조회"""
    now = time.time()

    if symbol in _orderbook_cache:
        data, cached_time = _orderbook_cache[symbol]
        if now - cached_time < _orderbook_cache_ttl:
            return data  # 캐시 히트

    # 캐시 미스 → API 호출
    orderbook = await exchange.get_orderbook(symbol)
    _orderbook_cache[symbol] = (orderbook, now)
    return orderbook
```

---

## 설정 파라미터

### 환경 변수 (.env) - v4.2

```bash
# ===== 거래소 선택 =====
EXCHANGE_TYPE=upbit

# ===== Upbit API =====
UPBIT_ACCESS_KEY=your_access_key
UPBIT_SECRET_KEY=your_secret_key

# ===== 운영 모드 =====
ENABLE_LIVE_TRADING=false

# ===== Risk 파라미터 =====
DAILY_LOSS_LIMIT_SAFE=-0.015
DAILY_LOSS_LIMIT_HALT=-0.030

# ===== Attack Module (deprecated - OFF) =====
ATTACK_MODE=OFF

# ===== Pullback 전략 설정 (눌림목 매수) =====
PULLBACK_MODE=NORMAL

# Pullback Score 임계값
PULLBACK_SCORE_L1=55
PULLBACK_SCORE_L2=70
PULLBACK_SCORE_L3=85

# Pullback 배분 (레벨별)
PULLBACK_ALLOC_L1=0.05
PULLBACK_ALLOC_L2=0.08
PULLBACK_ALLOC_L3=0.10

# Pullback 포지션 관리
PULLBACK_MAX_POSITIONS=10
PULLBACK_STOP_LOSS_PCT=-0.03
PULLBACK_TAKE_PROFIT_PCT=0.05
PULLBACK_TRAILING_TRIGGER_PCT=0.03
PULLBACK_TRAILING_STOP_PCT=0.02
PULLBACK_TIME_STOP_HOURS=4
PULLBACK_COOLDOWN_MIN=60
```

---

## 모니터링

### API 엔드포인트

```bash
# 헬스 체크
curl http://localhost:8000/api/health

# 요약 정보 (잔고, PnL, 모드)
curl http://localhost:8000/api/summary

# 포지션 조회
curl http://localhost:8000/api/positions

# Pullback 상태
curl http://localhost:8000/api/pullback/status

# Pullback Score 조회
curl http://localhost:8000/api/pullback/score/KRW-BTC

# Pullback 포지션
curl http://localhost:8000/api/pullback/positions
```

### Pullback 상태 응답 예시

```json
{
  "enabled": true,
  "mode": "NORMAL",
  "min_level": 2,
  "max_positions": 10,
  "active_positions": 2,
  "pending_signals": 3,
  "positions": {
    "KRW-ETH": {
      "entry_price": 4500000,
      "quantity": 0.01,
      "highest_price": 4650000
    }
  },
  "signals": {}
}
```

### Pullback Score 응답 예시

```json
{
  "symbol": "KRW-ETH",
  "total_score": 75,
  "level": 2,
  "target_allocation": 0.08,
  "entry_price_target": 4480000,
  "stop_loss_price": 4345600,
  "components": [
    {"name": "Recent Surge", "score": 15, "max_score": 20},
    {"name": "Pullback Depth", "score": 20, "max_score": 25},
    {"name": "Support Level", "score": 15, "max_score": 20},
    {"name": "Accumulation Signal", "score": 15, "max_score": 20},
    {"name": "Reversal Sign", "score": 10, "max_score": 15}
  ],
  "timestamp": "2026-02-02T04:30:00"
}
```

---

## Deprecated: Attack 전략

> **경고**: Attack 전략은 v4.2부터 deprecated 되었습니다.
> 이유: 후행 지표(RVOL, Breakout) 기반으로 급등 "후" 추격 매수 → 고점 물림 반복

### Attack 비활성화 이유

```
Attack 전략의 근본적 문제:

1. 후행 지표 의존
   - RVOL: 거래량 폭발 = 이미 급등 진행 중
   - Breakout: 고점 돌파 = 이미 상승 시작됨

2. 진입 타이밍 문제
   - "급등 감지" 시점 = 이미 늦음
   - 세력은 미리 매집 완료, 개미만 추격 매수

3. 결과
   - 고점 물림 → 손절 → 자본 소진
   - "왜 사면 떨어지지?" 현상
```

### Attack 설정 (OFF 권장)

```bash
# Attack 비활성화
ATTACK_MODE=OFF
```

---

## 파일 구조

```
src/
├── config.py                 # 전역 설정
├── api/
│   └── routes.py             # API 라우트 (Pullback 포함)
│
├── strategies/
│   ├── pullback_score.py     # Pullback Score 계산 (v4.2 핵심)
│   ├── pullback_strategy.py  # Pullback 전략 (v4.2 핵심)
│   ├── attack_breakout.py    # Attack 전략 (deprecated)
│   └── satellite.py          # Satellite (비활성화)
│
├── risk/
│   ├── risk_overlay.py       # 리스크 오버레이
│   └── risk_engine.py        # 리스크 엔진
│
├── engine/
│   └── core.py               # 트레이딩 엔진 (Pullback 통합)
│
├── exchange/
│   └── upbit.py              # Upbit 거래소 (Rate Limit 관리)
│
└── models/
    └── schemas.py            # Pydantic 스키마
```

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| **4.2** | **2026-02-02** | **Pullback 눌림목 매수 전략 도입** |
| | | - Attack 전략 deprecated (고점 추격 문제) |
| | | - 선행 지표 도입 (호가창 불균형, 축적 신호) |
| | | - Pullback Score 시스템 (5개 컴포넌트) |
| | | - 분산 투자 (최대 10개 포지션) |
| | | - Pre-filtering + 호가창 캐싱 (Rate Limit 방지) |
| 4.1 | 2026-02-02 | Attack 50% 단일 포지션 전략 |
| 4.0 | 2026-02-02 | Upbit 거래소 전환 |
| 3.1 | 2026-01-30 | MDD 5% 방어 최적화 |
| 3.0 | 2026-01-30 | MDD 5% 방어 시스템 강화 |

---

## Quick Reference

```
┌─────────────────────────────────────────────────────┐
│          v4.2 Pullback 눌림목 매수 전략 요약         │
├─────────────────────────────────────────────────────┤
│                                                     │
│  핵심 원리: "급등을 쫓지 말고, 눌림을 사라"          │
│                                                     │
│  선행 지표:                                         │
│  - 호가창 불균형 (Bid > Ask)                        │
│  - 축적 신호 (Accumulation)                         │
│  - 지지선 근접                                      │
│                                                     │
│  Pullback Score 진입 조건:                          │
│  - L1: 55점+ (5% 배분)                              │
│  - L2: 70점+ (8% 배분)                              │
│  - L3: 85점+ (10% 배분)                             │
│                                                     │
│  청산 조건:                                         │
│  - 손절: -3%                                        │
│  - 익절: +5%                                        │
│  - 트레일링: +3% 후 -2%                             │
│  - 타임스탑: 4시간                                  │
│                                                     │
│  리스크 관리:                                       │
│  - 최대 10개 포지션 (분산)                          │
│  - 단일 포지션 손실: ~0.15~0.3%                     │
│  - 최대 예상 손실: ~2.1%                            │
│  - MDD 5% 미만 유지 ✓                               │
│                                                     │
└─────────────────────────────────────────────────────┘
```
