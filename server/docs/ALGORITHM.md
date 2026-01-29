# 트레이딩 알고리즘 구조 문서

> **버전**: 2.0 (생존 중심 업그레이드)
> **최종 수정**: 2026-01-29
> **목표**: 청산 0회, MDD 최소화, 꾸준한 플러스 수익

---

## 목차

1. [시스템 개요](#시스템-개요)
2. [아키텍처](#아키텍처)
3. [핵심 컴포넌트](#핵심-컴포넌트)
4. [전략 상세](#전략-상세)
5. [리스크 관리](#리스크-관리)
6. [실행 엔진](#실행-엔진)
7. [모니터링](#모니터링)
8. [설정 파라미터](#설정-파라미터)

---

## 시스템 개요

### 투자 철학
- **생존 우선**: 수익보다 자본 보존을 최우선
- **보수적 진입**: 엄격한 조건 충족 시에만 진입
- **분산 전략**: Core(70%) + Satellite(20%) + Reserve(10%)

### 핵심 수치
| 항목 | 목표 |
|------|------|
| 일 손실 한도 | -1.5% (SAFE) / -3.0% (HALT) |
| 주 손실 한도 | -5.0% (Satellite 비활성화) |
| 최대 총 노출 | 120% |
| 심볼당 노출 (Core) | 10% |
| 심볼당 노출 (Satellite) | 3% |

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                        API Layer                            │
│                    (FastAPI REST API)                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Trading Engine                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │   Core      │  │  Satellite  │  │    Risk Engine      │ │
│  │  Strategy   │  │  Strategy   │  │  (Mode Controller)  │ │
│  │   (70%)     │  │   (20%)     │  │                     │ │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘ │
│         │                │                     │            │
│         └────────────────┼─────────────────────┘            │
│                          ▼                                  │
│  ┌───────────────────────────────────────────────────────┐ │
│  │                 Execution Engine                       │ │
│  │    (주문 정책 / 한쪽 체결 대응 / 슬리피지 관리)        │ │
│  └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
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
                    RiskEngine ◄── LiquidityFilter
                          │
                          ▼
                   ExecutionEngine ──► Exchange APIs
```

---

## 핵심 컴포넌트

### 1. CandleManager (`src/data/candle_manager.py`)

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

### 2. FeatureEngine (`src/features/feature_engine.py`)

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

### 3. RiskEngine (`src/risk/risk_engine.py`)

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

### 4. LiquidityFilter (`src/risk/liquidity_filter.py`)

유동성 검증 (진입 전 필수 통과)

| 조건 | 기준 |
|------|------|
| 24시간 거래대금 | ≥ 50M USDT |
| 스프레드 | ≤ 8 bps |
| 오더북 깊이 | 0.5% 범위 내 누적호가 ≥ 주문수량 × 5 |

### 5. ExecutionEngine (`src/execution/executor.py`)

주문 실행 및 헤지 관리

```python
# 주문 긴급도
class OrderUrgency:
    LOW   # 일반 진입: maker 우선, 5초 타임아웃
    HIGH  # 헤지/리스크: IOC taker, 2초 타임아웃

# 슬리피지 한도
CORE_MAX_SLIPPAGE = 6 bps
SATELLITE_MAX_SLIPPAGE = 15 bps
```

### 6. Reconciler (`src/portfolio/reconcile.py`)

정합성 검증 (10초마다)

| 체크 주기 | 검증 항목 |
|-----------|-----------|
| 10초 | 잔고 (Spot + Perp USDT) |
| 30초 | 포지션 (내부 vs 거래소) |
| 60초 | 주문 (미체결, 누락 체결) |

---

## 전략 상세

### Core Strategy (Cash & Carry)

**목적**: 현물-선물 베이시스 차익 수취

**파일**: `src/strategies/core_carry.py`

#### Edge 계산

```python
# 수익 계산
basis_pct = (spot_price - perp_price) / spot_price

# 비용 차감
fee_total = maker_fee × 2 + taker_fee × 2  # 0.08%
slippage_buffer = 0.0006                    # 6bps
safety_buffer = 0.0002                      # 2bps

# 순 Edge
net_edge = basis_pct - fee_total - slippage_buffer - safety_buffer

# 진입 조건
MIN_EDGE_REQUIRED = 0.20%  (20bps)
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

#### 청산 조건

| 조건 | 기준 |
|------|------|
| Edge 수렴 | ≤ 0.05% |
| 펀딩 손실 누적 | > 1% |
| 헤지 오류 | > 0.5% (리밸런싱 후에도) |
| 마진 비율 악화 | 동적 판단 |

---

### Satellite Strategy (모멘텀 스캐너)

**목적**: 단기 모멘텀 포착 (고위험/고수익)

**파일**: `src/strategies/satellite.py`

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

#### 청산 조건

| 조건 | 기준 |
|------|------|
| 하드 손절 | -0.8% |
| 트레일링 | +1.0% 도달 후 0.6% trailing |
| 타임스톱 | 30분 내 진전 없음 |
| 레짐 악화 | VOLATILE 감지 시 즉시 |

---

## 리스크 관리

### SAFE 모드 트리거

| # | 조건 | 설명 |
|---|------|------|
| 1 | WS 지연 > 2초 (10초 지속) | 데이터 신뢰성 저하 |
| 2 | 주문 실패율 > 20% (60초 윈도우) | 거래소 이상 |
| 3 | 한쪽 체결 + 헤지 실패 | 노출 위험 |
| 4 | 일중 손실 -1.5% | 일일 한도 접근 |
| 5 | BTC ATR 급증(1.8배) + 급락 | 급변장 |
| 6 | 노출 한도 90% 도달 | 레버리지 위험 |

### HALT 모드 트리거

| # | 조건 | 설명 |
|---|------|------|
| 1 | Reconcile 실패 (불일치) | 정합성 오류 |
| 2 | API 인증 오류 3회 | 연결 문제 |
| 3 | 일중 손실 -3.0% | 일일 한도 도달 |
| 4 | 청산 거리 < 2.5% | 청산 임박 |

### Kill Switch

```python
# 일일 손실 기반
if daily_loss <= -1.5%:
    mode = SAFE

if daily_loss <= -3.0%:
    mode = HALT

# 주간 손실 기반
if weekly_loss <= -5.0%:
    satellite_strategy.disable()
```

---

## 실행 엔진

### 주문 정책

| 긴급도 | 주문 유형 | 타임아웃 | 슬리피지 |
|--------|----------|----------|----------|
| LOW (일반) | Limit + PostOnly | 5초 | 전략별 |
| HIGH (헤지) | Market (IOC) | 2초 | 15bps |

### 주문 흐름

```
1. Limit + PostOnly 주문
       │
       ▼
   [5초 대기]
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

### 헤지 실패 복구

```
Spot 체결 완료
       │
       ▼
Perp 주문 실패
       │
       ▼
┌─────────────────┐
│ 복구 시도 (3회) │
│ - IOC 시장가    │
│ - 1초 간격      │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
  성공       실패
    │         │
    ▼         ▼
 페어 등록  Spot 롤백
            (되돌림)
```

---

## 모니터링

### Slack 알림 (`src/monitoring/slack.py`)

#### 즉시 알림

| 이벤트 | 레벨 |
|--------|------|
| SAFE/HALT 모드 전환 | WARNING/CRITICAL |
| 한쪽 체결 발생 | WARNING/CRITICAL |
| 일 손실 -1.5% / -3.0% | WARNING/CRITICAL |
| WS 끊김 10초 이상 | WARNING |
| 청산 거리 < 5% | ERROR/CRITICAL |

#### 일일 리포트 (매일 자정)

```
📅 일일 리포트 - 2026-01-29

📈 수익 현황
> 총 자산: $100,000.00
> 일일 PnL: $150.00 (+0.15%)
> Core PnL: $120.00
> Satellite PnL: $30.00

📊 노출 & 리스크
> 최대 노출: 85.0%
> 최대 드로우다운: 0.80%

🧾 슬리피지 TOP3
  1. BTCUSDT: 4.50bps
  2. ETHUSDT: 3.20bps
  3. SOLUSDT: 2.80bps

⚠️ 위험 이벤트 요약
  - MODE_CHANGE: 1건
  - WS_DISCONNECT: 2건

> 총 거래 횟수: 12건
```

---

## 설정 파라미터

### 환경 변수 (.env)

```bash
# === 거래소 연결 ===
BINANCE_API_KEY=your_api_key
BINANCE_SECRET=your_secret
USE_TESTNET=true

# === 리스크 파라미터 ===
DAILY_LOSS_LIMIT_SAFE=-0.015      # -1.5%
DAILY_LOSS_LIMIT_HALT=-0.030      # -3.0%
WEEKLY_LOSS_LIMIT=-0.05           # -5.0%
MAX_GROSS_EXPOSURE=1.2            # 120%
MAX_SYMBOL_EXPOSURE_CORE=0.10     # 10%
MAX_SYMBOL_EXPOSURE_SAT=0.03      # 3%

# === 유동성 필터 ===
MIN_LIQUIDITY_USDT=50000000       # 50M
MAX_SPREAD_BPS=8
MAX_SLIPPAGE_CORE_BPS=6
MAX_SLIPPAGE_SAT_BPS=15

# === Core 전략 ===
CORE_MIN_EDGE_PCT=0.0020          # 0.20%
CORE_FEE_BUFFER=0.0008            # 0.08%
CORE_SLIPPAGE_BUFFER=0.0006       # 6bps
CORE_SPLIT_ENTRY=true
CORE_TRANCHE_1_PCT=0.40
CORE_TRANCHE_2_PCT=0.30
CORE_TRANCHE_3_PCT=0.30
CORE_TRANCHE_DELAY_SEC=300        # 5분

# === Satellite 전략 ===
SAT_HARD_STOP_PCT=-0.008          # -0.8%
SAT_TRAILING_TRIGGER_PCT=0.01     # +1.0%
SAT_TRAILING_STOP_PCT=0.006       # 0.6%
SAT_TIME_STOP_MINUTES=30
SAT_RVOL_THRESHOLD=2.5
SAT_CLOSE_POS_THRESHOLD=0.75
SAT_CONFIRMATION_ENTRY=true

# === Slack 알림 ===
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
SLACK_CHANNEL=#trading-alerts
```

---

## 파일 구조

```
src/
├── config.py                 # 전역 설정
├── app.py                    # FastAPI 앱
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
│   └── satellite.py          # Satellite 전략
│
├── risk/
│   ├── risk_engine.py        # 리스크 엔진
│   └── liquidity_filter.py   # 유동성 필터
│
├── execution/
│   ├── executor.py           # 실행 엔진
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
| 2.0 | 2026-01-29 | 생존 중심 업그레이드 전체 구현 |
| 1.0 | - | 초기 버전 |

---

## 참고 사항

### 테스트 실행
```bash
pytest tests/test_algorithm_upgrade.py -v
```

### 서버 실행
```bash
uvicorn src.app:app --port 8086
```

### 헬스 체크
```bash
curl http://localhost:8086/api/health
curl http://localhost:8086/api/mode
curl http://localhost:8086/api/positions
```
