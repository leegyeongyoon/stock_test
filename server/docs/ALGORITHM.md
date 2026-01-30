# 트레이딩 알고리즘 구조 문서

> **버전**: 3.1 (MDD 5% 방어 최적화)
> **최종 수정**: 2026-01-30
> **목표**: 청산 0회, MDD 5% 제약, 꾸준한 플러스 수익

---

## 목차

1. [시스템 개요](#시스템-개요)
2. [MDD 5% 방어 시스템](#mdd-5-방어-시스템)
3. [아키텍처](#아키텍처)
4. [핵심 컴포넌트](#핵심-컴포넌트)
5. [전략 상세](#전략-상세)
6. [리스크 관리](#리스크-관리)
7. [실행 엔진](#실행-엔진)
8. [모니터링](#모니터링)
9. [설정 파라미터](#설정-파라미터)

---

## 시스템 개요

### 투자 철학
- **MDD 5%는 목표가 아닌 제약조건**: 위반 시 모든 엔진 HALT
- **수익보다 생존이 먼저**: 신호가 아무리 좋아도 리스크가 높으면 진입 금지
- **분산 전략**: Core(30~60%) + Satellite(20~40%) + Reserve(20~30%)

### 핵심 수치 (v3.1 최적화)

| 항목 | v3.0 | v3.1 |
|------|------|------|
| 일 손실 SAFE | -1.0% | **-1.0%** |
| 일 손실 HALT | -1.5% | **-1.5%** |
| SAFE 트리거 DD | 1% | **2%+ 또는 1%+조합** |
| Soft DD (Satellite OFF) | 3.5% | **3.5%** |
| Hard DD | 5% 전량청산 | **5% 분할청산** |
| Core 비중 | 고정 70% | **동적 30~60%** |
| Correlation Threshold | 단일 0.80 | **2단계 (0.70/0.80)** |
| Satellite Stop | 고정 -0.8% | **ATR 기반 + 상한** |

---

## MDD 5% 방어 시스템

### 우선순위 체인 (코드 레벨 강제)

```
1. SYSTEM SAFETY     → 지연/실패/리콘실/연결 상태
2. TAIL RISK         → DD / 일손실 / 상관 급증 / 급변장
3. MARKET REGIME     → BTC 기반 risk-on/off
4. POSITION STATE    → WEAK/NORMAL/STRONG/EXTREME
5. SIGNAL ENGINE     → 진입 신호 (가장 마지막)

원칙: 신호가 아무리 좋아도 1~3에서 위험이면 절대 진입 금지
```

### DD 6-Tier Multiplier System

| Tier | DD 범위 | 사이징 배수 | 상태 | 액션 |
|------|---------|-------------|------|------|
| 1 | 0~1% | **1.0x** | 정상 | 풀사이즈 |
| 2 | 1~2% | **0.8x** | 주의 | 20% 감소 |
| 3 | 2~3.5% | **0.6x** | 경계 | 40% 감소, SAFE 트리거 |
| 4 | 3.5~4.5% | **0.3x** | Soft DD | **Satellite 신규진입 OFF** (기존 관리만) |
| 5 | 4.5~5% | **0.1x** | 위험 | 90% 감소 |
| 6 | ≥5% | **0.0x** | Hard DD | **HALT + 분할 청산** |

### v3.1 SAFE 트리거 (조합 방식)

| # | 조건 | 설명 |
|---|------|------|
| 1 | DD 2%+ | 단독 SAFE 트리거 |
| 2 | DD 1% + 일손실 -0.5% | 조합 트리거 |
| 3 | DD 1% + Correlation 0.80+ | 조합 트리거 |

### v3.1 Hard DD 분할 청산

즉시 전량 청산 대신 단계적 접근:

| Phase | 액션 | 조건 |
|-------|------|------|
| 1 | 손실 포지션 50% 축소 | DD 5% 도달 시 |
| 2 | 헤지 추가 + 30% 추가 축소 | Phase 1 후 |
| 3 | 조건부 전량 청산 | DD 6%+ 또는 일손실 -2% |

### DD 단계별 운영 매트릭스

| DD 구간 | Regime | Satellite | Core 비중 | 헤지 |
|---------|--------|-----------|-----------|------|
| 0~1% | RISK_ON | 100% | **60%** | OFF |
| 0~1% | NEUTRAL | 70% | **50%** | OFF |
| 0~1% | RISK_OFF | 30% | **30%** | ON |
| 1~2% | ANY | 80% 사이징 | **50%** | OFF |
| 2~3.5% | ANY | 60% 사이징 | **50%** (SAFE) | ON |
| 3.5~5% | ANY | **신규 OFF** | **30%** | ON |
| ≥5% | ANY | **HALT** | **HALT** | 분할 청산 |

### Portfolio Risk Management

```python
# 새로운 포트폴리오 리스크 제한
max_concurrent_positions = 5      # 최대 동시 포지션 수
risk_per_trade = 0.30%            # 트레이드당 기본 리스크
max_risk_per_trade = 0.50%        # 트레이드당 최대 리스크
total_risk_limit_normal = 1.5%    # 정상 시장 총 리스크 한도
total_risk_limit_risky = 0.8%     # 위험 시장 총 리스크 한도
```

### Position Reducer (Reduce & Stay)

위험 상황 시 포지션 강제 축소 후 일정 기간 유지

| 트리거 | 축소율 | Stay 기간 |
|--------|--------|-----------|
| RISK_OFF | 40% | 60분 |
| Correlation Shock | 30~60% | 60분 |
| Soft DD (3.5%) | 50% | - |
| Hard DD (5%) | **100%** (전량 청산) | - |

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                         API Layer                                │
│                     (FastAPI REST API)                          │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Risk Overlay (NEW)                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ DD Tracker  │  │  Portfolio  │  │    Position Reducer     │  │
│  │ (6-Tier)    │  │    Risk     │  │   (Reduce & Stay)       │  │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘  │
│         └────────────────┼─────────────────────┘                │
│                          ▼                                       │
│              ┌───────────────────────┐                          │
│              │  NORMAL / SAFE / HALT │                          │
│              └───────────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Trading Engine                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   Core      │  │  Satellite  │  │     Risk Engine         │  │
│  │  Strategy   │  │  Strategy   │  │   (Mode Controller)     │  │
│  │   (70%)     │  │   (20%)     │  │                         │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────────┘  │
│         │                │                     │                 │
│         └────────────────┼─────────────────────┘                 │
│                          ▼                                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                  Execution Engine                          │  │
│  │     (IOC / 타임아웃 / 재시도 / 슬리피지 버퍼 관리)         │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  Binance Spot   │ │  Binance Perp   │ │   Reconciler    │
│    Exchange     │ │    Exchange     │ │ (정합성 검증)   │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

### 데이터 흐름

```
Market Data (WS) ──► CandleManager ──► FeatureEngine ──► Strategies
                          │                  │
                          │                  ▼
                          │           Features (지표)
                          │           - SMA, EMA, ATR
                          │           - RVOL, VWAP
                          │           - ClosePos, Breakout
                          ▼
                    Risk Overlay ◄── DD Tracker (6-Tier)
                          │      ◄── Portfolio Risk
                          │      ◄── Position Reducer
                          ▼
                   ExecutionEngine ──► Exchange APIs
```

---

## 핵심 컴포넌트

### 1. Risk Overlay (`src/risk/risk_overlay.py`)

통합 리스크 관리 계층 - 모든 의사결정의 게이트키퍼

```python
class RiskOverlay:
    """
    우선순위 체인에 따른 평가:
    1. SYSTEM SAFETY (exec_health)
    2. TAIL RISK (dd + correlation + volatility)
    3. MARKET REGIME (btc regime)

    Returns:
        RiskDecision with:
        - mode: NORMAL/SAFE/HALT
        - satellite_allowed: bool
        - core_allowed: bool
        - sizing_multiplier: float (0~1)
        - hedge_required: bool
    """
```

### 2. DD Tracker (`src/risk/dd_tracker.py`)

6-Tier 드로우다운 추적

```python
class DrawdownTracker:
    """
    Peak Equity 대비 현재 DD 계산
    6-Tier에 따른 사이징 배수 결정
    Soft DD (3.5%) / Hard DD (5%) 감지
    """

    def update(self, current_equity: float) -> DrawdownState:
        # Returns: dd_tier, sizing_multiplier, is_soft_dd, is_hard_dd
```

### 3. Portfolio Risk (`src/risk/portfolio_risk.py`)

포트폴리오 수준 리스크 관리

```python
class PortfolioRiskManager:
    """
    책임:
    1. 동시 포지션 수 제한 (max 5)
    2. 트레이드당 리스크 계산 및 제한
    3. 총 리스크 한도 관리
    4. 시장 상태에 따른 리스크 한도 조정
    """

    def can_open_position(self, symbol: str) -> tuple[bool, str]
    def calc_position_size(self, entry_price, stop_price) -> float
```

### 4. Position Reducer (`src/risk/position_reducer.py`)

위험 시 포지션 강제 축소

```python
class PositionReducer:
    """
    책임:
    1. 위험 시장 감지 시 축소 명령 생성
    2. Reduce & Stay 정책 적용
    3. 축소 실행 추적
    """

    def trigger_reduce(self, trigger, positions, severity) -> list[ReduceOrder]
    def can_expand_position(self) -> tuple[bool, str]  # Stay 기간 중 False
```

### 5. CandleManager (`src/data/candle_manager.py`)

실시간 OHLCV 데이터 수집 및 캐싱

```python
# 지원 타임프레임
INTERVALS = ["5m", "1h"]

# 주요 지표 계산 함수
calc_sma(candles, period)      # 단순이동평균
calc_ema(candles, period)      # 지수이동평균
calc_atr(candles, period)      # ATR
calc_rvol(candles, period)     # 상대거래량
calc_vwap(candles)             # VWAP
calc_close_position(candle)    # 종가 위치 (0~1)
is_breakout(candles, period)   # 돌파 여부
```

### 6. FeatureEngine (`src/features/feature_engine.py`)

시장 데이터 → 트레이딩 시그널용 피처 변환

| 피처 | 설명 | 용도 |
|------|------|------|
| `sma_50_1h` | 1시간 50봉 SMA | 레짐 판단 |
| `atr_14_1h` | 1시간 ATR(14) | 변동성 측정 |
| `atr_pct_1h` | ATR 백분율 | 급변장 감지 |
| `rvol_5m` | 5분 상대거래량 | 모멘텀 확인 |
| `vwap_5m` | 5분 VWAP | 가격 레벨 |
| `close_pos` | 종가 위치 (0~1) | 강도 측정 |
| `breakout_12_5m_up` | 12봉 돌파 | 진입 시그널 |
| `is_volatile` | 급변장 여부 | 진입 금지 |

### 7. RiskEngine (`src/risk/risk_engine.py`)

리스크 관리 및 모드 제어

```
모드 체계:
┌──────────┐     SAFE 트리거     ┌──────────┐     HALT 트리거     ┌──────────┐
│  NORMAL  │ ──────────────────► │   SAFE   │ ──────────────────► │   HALT   │
│ (정상)   │ ◄────────────────── │ (경계)   │                     │ (중지)   │
└──────────┘     조건 해제       └──────────┘                     └──────────┘
     │                                                                  │
     └───────────────────── 수동 리셋 필요 ◄────────────────────────────┘
```

### 8. LiquidityFilter (`src/risk/liquidity_filter.py`)

유동성 검증 (진입 전 필수 통과)

| 조건 | 기준 |
|------|------|
| 24시간 거래대금 | ≥ 50M USDT |
| 스프레드 | ≤ 8 bps |
| 오더북 깊이 | 0.5% 범위 내 누적호가 ≥ 주문수량 × 5 |

### 9. ExecutionEngine (`src/execution/executor.py`)

주문 실행 및 슬리피지 관리

```python
class OrderPolicy:
    timeout_ms: int = 3000          # 3초 타임아웃
    max_retries: int = 2            # 최대 재시도
    fallback_to_market: bool = True # 실패 시 시장가
    max_slippage_pct: float = 0.01  # 1% 초과 시 취소
```

---

## 전략 상세

### Core Strategy (Cash & Carry)

**목적**: 현물-선물 베이시스 차익 수취

**파일**: `src/strategies/core_carry.py`

#### 안전장치 (`src/strategies/core_safety.py`)

```python
class CoreSafetyGuard:
    # 허용 심볼 (초유동성만)
    ALLOWED_SYMBOLS = ["BTCUSDT", "ETHUSDT"]

    # 펀딩 역전 감지
    MIN_FUNDING_RATE = -0.0001      # 역전 시 경고
    NEGATIVE_FUNDING_EXIT = -0.0003 # 3연속 시 청산

    # Edge 축소 감지
    MIN_EDGE_BPS = 5                # 5bp 미만 시 신규 진입 금지
```

#### Edge 계산 (v3.1: 수수료 분리)

```python
# 수익 계산
basis_pct = (spot_price - perp_price) / spot_price

# v3.1: 수수료 분리 (실제 티어 적용)
fee_maker = 0.0002  # 메이커 2bps
fee_taker = 0.0004  # 테이커 4bps

# Core는 메이커 주문 사용 (슬로우 체결 허용)
# Satellite는 테이커 주문 사용 (빠른 체결 필요)
fee_core = fee_maker × 4      # 양방향 × 진입/청산 = 8bps
fee_satellite = fee_taker × 2 # 진입 + 청산 = 8bps

slippage_buffer = 0.0006      # 6bps
safety_buffer = 0.0002        # 2bps

# 순 Edge
net_edge = basis_pct - fee_core - slippage_buffer - safety_buffer

# 진입 조건
MIN_EDGE_REQUIRED = 0.30%  (30bps)
```

#### 분할 진입 (3 트랜치)

```
조건 충족 ──► 1차: 40% (즉시)
              │
              │ (5분 대기, 조건 재확인)
              ▼
             2차: 30%
              │
              │ (5분 대기, 조건 재확인)
              ▼
             3차: 30%
```

---

### Satellite Strategy (모멘텀 스캐너)

**목적**: 단기 모멘텀 포착 (고위험/고수익)

**파일**: `src/strategies/satellite.py`

#### v3.1: ATR 기반 스탑 + 안전장치

```python
# 기존: 고정 퍼센트 스탑
hard_stop_pct = -0.008  # -0.8% 고정

# v3.1: ATR 기반 동적 스탑
atr_multiplier = 1.5
atr_stop = atr_14_1h * atr_multiplier

# 안전장치: 최소/최대 상한
stop_min = 0.005  # 최소 0.5%
stop_max = 0.015  # 최대 1.5% (안전장치)

effective_stop = clamp(atr_stop, stop_min, stop_max)
```

#### DD-Tier 인지 포지션 관리

```python
# DD Tier에 따른 State Machine 조정
if dd_tier >= 4:          # 3.5%+ DD
    force_weak()          # 강제 WEAK 상태
    new_entry = False     # 신규진입 OFF (기존 관리만)

if dd_tier >= 3:          # 2%+ DD
    if r_pnl <= -0.5:     # 손실 -0.5R 이상
        force_weak()

if is_soft_dd and r_pnl < 0:  # Soft DD + 손실
    force_weak()
```

#### 레짐 필터 (1시간 기준)

```python
def get_btc_regime():
    btc_close = 현재 BTC 종가
    btc_ma50 = BTC 50봉 SMA
    btc_atr = BTC ATR(14)
    btc_atr_avg = 최근 14봉 ATR 평균

    # 급변장 체크
    if btc_atr > btc_atr_avg × 1.8:
        return VOLATILE  # 신규 진입 금지

    # 추세 체크
    if btc_close < btc_ma50:
        return BEARISH  # 롱 진입 금지

    return BULLISH
```

#### 2단계 확인 진입

```
Phase 1 - 시그널 감지:
├── RVOL_5m ≥ 2.5
├── Close > Highest(High, 12봉)
├── Close ≥ VWAP_5m
└── ClosePos ≥ 0.75

            │ (다음 5분 봉 대기)
            ▼

Phase 2 - 확인:
├── Close > 돌파 레벨 유지
└── 거래량 유지

            │
            ▼

       실제 진입
```

---

## 리스크 관리

### SAFE 모드 트리거 (v3.1 최적화)

| # | 조건 | v3.1 변경 |
|---|------|-----------|
| 1 | WS 지연 > 2초 (10초 지속) | - |
| 2 | 주문 실패율 > 20% (60초 윈도우) | - |
| 3 | 한쪽 체결 + 헤지 실패 | - |
| 4 | **일중 손실 -1.0%** | - |
| 5 | BTC ATR 급증(1.8배) + 급락 | - |
| 6 | **DD 2%+ 단독** | ~~1%~~ → **2%** |
| 7 | **DD 1% + Correlation 0.80+** | 조합 트리거 |
| 8 | **DD 1% + 일손실 -0.5%** | 조합 트리거 |

### HALT 모드 트리거 (v3.1)

| # | 조건 | v3.1 변경 |
|---|------|-----------|
| 1 | Reconcile 실패 (불일치) | - |
| 2 | API 인증 오류 3회 | - |
| 3 | **일중 손실 -1.5%** | 즉시 전량 청산 |
| 4 | 청산 거리 < 2.5% | - |
| 5 | **Hard DD (5%)** | **분할 청산** |

### Soft DD 트리거 (3.5%)

Soft DD (3.5%) 도달 시:
- **Satellite 신규진입 OFF** (기존 포지션은 정상 관리)
- Core 사이징 0.3x
- Core 비중 30%로 축소

### Correlation 2단계 시스템 (v3.1 신규)

| Stage | 임계값 | 액션 |
|-------|--------|------|
| WARN | 0.70+ | 필터 강화, 신규 진입 주의 |
| ACTION | 0.80+ | REDUCE (노출 축소) |
| ACTION + BTC shock | 0.80+ & BTC 급락 | HEDGE 필요 |

### Kill Switch

```python
# 일일 손실 기반
if daily_loss <= -1.0%:
    mode = SAFE
    satellite_allowed = False

if daily_loss <= -1.5%:
    mode = HALT
    force_close_all()  # 즉시 전량 청산

# v3.1 DD 기반 (분할 청산)
if dd >= 3.5%:  # Soft DD
    satellite_new_entry = False  # 신규만 OFF
    core_allocation = 30%

if dd >= 5.0%:  # Hard DD
    mode = HALT
    gradual_close(phase=1)  # 손실 포지션 50% 축소
    # Phase 2-3는 조건에 따라

# v3.1 조합 트리거
if dd >= 1.0% and (corr >= 0.80 or daily_loss <= -0.5%):
    mode = SAFE

# 주간 손실 기반
if weekly_loss <= -5.0%:
    satellite_strategy.disable()
```

---

## 실행 엔진

### 주문 정책 (슬리피지 버퍼 포함)

| 긴급도 | 주문 유형 | 타임아웃 | 슬리피지 버퍼 |
|--------|----------|----------|---------------|
| LOW (일반) | Limit + PostOnly | 3초 | 0.5% |
| HIGH (헤지) | Market (IOC) | 2초 | 1.0% |

### 포지션 사이징 (슬리피지 포함)

```python
def calc_position_size(capital, risk_pct, entry_price, stop_price):
    """슬리피지 포함한 리스크 기반 사이징"""

    stop_distance_pct = abs(entry_price - stop_price) / entry_price
    slippage_buffer = 0.005  # 0.5%

    # 실효 스탑 거리 = 스탑 거리 + 슬리피지 버퍼
    effective_stop = stop_distance_pct + slippage_buffer

    # 리스크 금액
    risk_amount = capital * risk_pct

    # 포지션 가치 = 리스크 금액 / 실효 스탑 거리
    position_value = risk_amount / effective_stop

    return position_value / entry_price
```

### 주문 흐름

```
1. Limit + PostOnly 주문
       │
       ▼
   [3초 대기]
       │
   ┌───┴───┐
   │       │
체결됨   미체결
   │       │
   ▼       ▼
  완료   취소 + 가격 추종
              │
              ▼
         [슬리피지 체크]
              │
         ┌────┴────┐
         │         │
      허용 내    초과
         │         │
         ▼         ▼
    IOC 재주문   거부
```

---

## 모니터링

### API 엔드포인트

#### Risk Overlay 상태
```bash
curl http://localhost:8086/risk/overlay
```

응답 예시:
```json
{
  "mode": "NORMAL",
  "regime": "NEUTRAL",
  "satellite_allowed": true,
  "core_allowed": true,
  "sizing_multiplier": 0.8,
  "dd_tier": 2,
  "is_soft_dd": false,
  "is_hard_dd": false,
  "open_positions": 2,
  "max_positions": 5,
  "total_risk_pct": 0.45,
  "available_risk_pct": 1.05
}
```

#### DD 상태
```bash
curl http://localhost:8086/risk/drawdown
```

#### Portfolio Risk 상태
```bash
curl http://localhost:8086/risk/can-trade
```

---

## 설정 파라미터

### 환경 변수 (.env)

```bash
# === 거래소 연결 ===
BINANCE_API_KEY=your_api_key
BINANCE_SECRET=your_secret
USE_TESTNET=true

# === DD 6-Tier 설정 (신규) ===
DD_TIER1_THRESHOLD=0.01         # 1%
DD_TIER1_MULTIPLIER=1.0
DD_TIER2_THRESHOLD=0.02         # 2%
DD_TIER2_MULTIPLIER=0.8
DD_TIER3_THRESHOLD=0.035        # 3.5% (Soft DD)
DD_TIER3_MULTIPLIER=0.6
DD_TIER4_THRESHOLD=0.045        # 4.5%
DD_TIER4_MULTIPLIER=0.3
DD_TIER5_THRESHOLD=0.05         # 5% (Hard DD)
DD_TIER5_MULTIPLIER=0.1
DD_TIER6_MULTIPLIER=0.0

# === 일손실 제한 (강화) ===
DAILY_LOSS_LIMIT_SAFE=-0.01     # -1.0% (기존 -1.5%)
DAILY_LOSS_LIMIT_HALT=-0.015    # -1.5% (기존 -3.0%)
WEEKLY_LOSS_LIMIT=-0.05         # -5.0%

# === Portfolio Risk (신규) ===
MAX_CONCURRENT_POSITIONS=5
RISK_PER_TRADE=0.003            # 0.30%
MAX_RISK_PER_TRADE=0.005        # 0.50%
TOTAL_RISK_LIMIT_NORMAL=0.015   # 1.5%
TOTAL_RISK_LIMIT_RISKY=0.008    # 0.8%

# === Position Reducer (신규) ===
REDUCE_RATE_RISK_OFF=0.40       # 40%
REDUCE_RATE_CORR_SHOCK_MIN=0.30 # 30%
REDUCE_RATE_CORR_SHOCK_MAX=0.60 # 60%
REDUCE_STAY_DURATION_MINUTES=60

# === 노출 제한 ===
MAX_GROSS_EXPOSURE=1.2          # 120%
MAX_SYMBOL_EXPOSURE_CORE=0.10   # 10%
MAX_SYMBOL_EXPOSURE_SAT=0.03    # 3%

# === 슬리피지 버퍼 (강화) ===
SLIPPAGE_BUFFER_PCT=0.005       # 0.5%
MAX_SLIPPAGE_PCT=0.01           # 1%
MAX_SLIPPAGE_CORE_BPS=6
MAX_SLIPPAGE_SAT_BPS=15

# === Core 전략 ===
CORE_MIN_EDGE_PCT=0.003         # 0.30% (강화)
CORE_ALLOWED_SYMBOLS=BTCUSDT,ETHUSDT
CORE_MIN_FUNDING_RATE=-0.0001
CORE_MIN_EDGE_BPS=5
CORE_SPLIT_ENTRY=true
CORE_TRANCHE_1_PCT=0.40
CORE_TRANCHE_2_PCT=0.30
CORE_TRANCHE_3_PCT=0.30
CORE_TRANCHE_DELAY_SEC=300

# === Satellite 전략 ===
SAT_HARD_STOP_PCT=-0.008        # -0.8%
SAT_TRAILING_TRIGGER_PCT=0.01   # +1.0%
SAT_TRAILING_STOP_PCT=0.006     # 0.6%
SAT_TIME_STOP_MINUTES=30
SAT_RVOL_THRESHOLD=2.5
SAT_CLOSE_POS_THRESHOLD=0.75
SAT_CONFIRMATION_ENTRY=true

# === 유동성 필터 ===
MIN_LIQUIDITY_USDT=50000000     # 50M
MAX_SPREAD_BPS=8

# === 상관관계 가드 (v3.1: 2단계) ===
CORR_WARN_THRESHOLD=0.70        # Stage 1 (경고)
CORR_ACTION_THRESHOLD=0.80      # Stage 2 (액션)
CORR_SPIKE_THRESHOLD=0.80       # 하위호환
CORR_LOOKBACK_BARS=20
CORR_BTC_DROP_PCT=-0.03         # -3%

# === 수수료 파라미터 (v3.1: 분리) ===
FEE_MAKER_PCT=0.0002            # 메이커 2bps
FEE_TAKER_PCT=0.0004            # 테이커 4bps
FEE_TIER_VIP0=0.0004            # VIP0 테이커
FEE_TIER_VIP1=0.00036           # VIP1 테이커
FEE_USE_MAKER_FOR_CORE=true
FEE_USE_TAKER_FOR_SATELLITE=true

# === Satellite ATR 스탑 (v3.1) ===
SAT_STOP_ATR_MULTIPLIER=1.5
SAT_STOP_MIN_PCT=0.005          # 최소 0.5%
SAT_STOP_MAX_PCT=0.015          # 최대 1.5% (안전장치)
SAT_USE_ATR_STOP=true

# === Hard DD 분할 청산 (v3.1) ===
HARD_DD_PHASE1_REDUCE_PCT=0.50  # 1단계: 50% 축소
HARD_DD_PHASE2_HEDGE=true       # 2단계: 헤지 추가
HARD_DD_GRADUAL_CLOSE_ENABLED=true

# === Slack 알림 ===
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
SLACK_CHANNEL=#trading-alerts
```

---

## 파일 구조

```
src/
├── config.py                 # 전역 설정
├── api/
│   └── app.py                # FastAPI 앱
│
├── data/
│   ├── candle_manager.py     # OHLCV 수집/캐싱
│   └── market_data.py        # WebSocket 데이터
│
├── features/
│   └── feature_engine.py     # 지표 계산
│
├── strategies/
│   ├── base.py               # 전략 베이스 클래스
│   ├── core_carry.py         # Core 전략
│   ├── core_safety.py        # Core 안전장치 (신규)
│   └── satellite.py          # Satellite 전략
│
├── risk/
│   ├── config.py             # 리스크 설정 (강화)
│   ├── risk_overlay.py       # 통합 리스크 오버레이 (신규)
│   ├── dd_tracker.py         # DD 추적 (6-Tier)
│   ├── portfolio_risk.py     # 포트폴리오 리스크 (신규)
│   ├── position_reducer.py   # 포지션 축소 (신규)
│   ├── correlation.py        # 상관관계 가드
│   ├── exec_health.py        # 실행 건강도
│   ├── risk_engine.py        # 리스크 엔진
│   └── liquidity_filter.py   # 유동성 필터
│
├── position/
│   ├── state_machine.py      # 포지션 상태 머신 (DD-tier 인지)
│   ├── score_calculator.py   # 점수 계산
│   ├── policies.py           # 상태별 정책
│   └── schemas.py            # 스키마
│
├── execution/
│   ├── executor.py           # 실행 엔진
│   ├── order_policy.py       # IOC/슬리피지 정책
│   └── order_state.py        # 주문 상태 관리
│
├── portfolio/
│   ├── ledger.py             # 장부 관리
│   └── reconcile.py          # 정합성 검증
│
├── exchange/
│   ├── base.py               # 거래소 인터페이스
│   ├── binance_spot.py       # Binance Spot
│   └── binance_perp.py       # Binance Perp
│
└── monitoring/
    ├── slack.py              # Slack 알림
    ├── telegram.py           # Telegram 알림
    └── metrics.py            # 메트릭 수집
```

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 3.1 | 2026-01-30 | MDD 5% 방어 최적화 |
| | | - Core 비중 동적 조절 (30~60%) |
| | | - SAFE 트리거 완화 (1%→2%+ 또는 조합) |
| | | - Correlation 2단계화 (warn 0.70 / action 0.80) |
| | | - Hard DD 분할 청산 (즉시 전량 → 단계적) |
| | | - 수수료 파라미터 분리 (maker/taker/tier) |
| | | - Satellite 스탑 ATR 기반 + 상한 안전장치 |
| | | - Tier4 명확화: Satellite 신규진입만 OFF |
| 3.0 | 2026-01-30 | MDD 5% 방어 시스템 강화 |
| | | - DD 6-Tier Multiplier 도입 |
| | | - Portfolio Risk Management 추가 |
| | | - Position Reducer (Reduce & Stay) 추가 |
| | | - 일손실 제한 강화 (-1.0%/-1.5%) |
| | | - State Machine DD-tier 인지 강화 |
| 2.0 | 2026-01-29 | 생존 중심 업그레이드 전체 구현 |
| 1.0 | - | 초기 버전 |

---

## 참고 사항

### 서버 실행
```bash
source .venv/bin/activate
uvicorn src.api.app:app --port 8086 --reload
```

### 헬스 체크
```bash
curl http://localhost:8086/api/health
curl http://localhost:8086/risk/overlay
curl http://localhost:8086/risk/drawdown
curl http://localhost:8086/risk/can-trade
```

### 테스트 실행
```bash
pytest tests/ -v
```
