# 컴포넌트 상세 문서

> 각 컴포넌트의 클래스, 메서드, 데이터 구조 상세

---

## 목차

1. [CandleManager](#candlemanager)
2. [FeatureEngine](#featureengine)
3. [RiskEngine](#riskengine)
4. [LiquidityFilter](#liquidityfilter)
5. [CoreCarryStrategy](#corecarrystrategy)
6. [SatelliteStrategy](#satellitestrategy)
7. [ExecutionEngine](#executionengine)
8. [Reconciler](#reconciler)
9. [SlackNotifier](#slacknotifier)

---

## CandleManager

**파일**: `src/data/candle_manager.py`

### 데이터 구조

```python
@dataclass
class Candle:
    symbol: str           # "BTCUSDT"
    interval: str         # "5m", "1h"
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float   # USDT 거래대금
    trades: int           # 체결 횟수
```

### 주요 메서드

| 메서드 | 설명 | 반환 |
|--------|------|------|
| `add_candle(candle)` | 캔들 추가 | None |
| `get_candles(symbol, interval, limit)` | 캔들 조회 | list[Candle] |
| `get_latest(symbol, interval)` | 최신 캔들 | Candle |

### 지표 계산 함수

| 함수 | 파라미터 | 반환 |
|------|----------|------|
| `calc_sma(candles, period)` | 캔들 리스트, 기간 | float |
| `calc_ema(candles, period)` | 캔들 리스트, 기간 | float |
| `calc_atr(candles, period)` | 캔들 리스트, 기간 | float |
| `calc_rvol(candles, period)` | 캔들 리스트, 기간 | float |
| `calc_vwap(candles)` | 캔들 리스트 | float |
| `calc_close_position(candle)` | 단일 캔들 | float (0~1) |
| `is_breakout(candles, period, direction)` | 캔들, 기간, 방향 | bool |

---

## FeatureEngine

**파일**: `src/features/feature_engine.py`

### 데이터 구조

```python
@dataclass
class Features:
    symbol: str
    timestamp: datetime

    # 1시간 지표
    sma_20_1h: float
    sma_50_1h: float
    ma_50_1h: float
    atr_14_1h: float
    atr_pct_1h: float

    # 5분 지표
    rvol_5m: float
    vwap_5m: float
    close_pos: float

    # 돌파 시그널
    breakout_1h_up: bool
    breakout_12_5m_up: bool

    # 상태
    is_volatile: bool
```

### 주요 메서드

| 메서드 | 설명 | 반환 |
|--------|------|------|
| `calculate_features(symbol)` | 전체 피처 계산 | Features |
| `check_momentum_signal(symbol)` | 모멘텀 시그널 | bool |
| `check_btc_regime()` | BTC 레짐 판단 | Regime |
| `is_volatile(symbol)` | 급변장 여부 | bool |

---

## RiskEngine

**파일**: `src/risk/risk_engine.py`

### Enum 정의

```python
class RiskMode(str, Enum):
    NORMAL = "NORMAL"  # 정상 운영
    SAFE = "SAFE"      # 신규 진입 금지
    HALT = "HALT"      # 전체 중지

class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
```

### 데이터 구조

```python
@dataclass
class OrderEvent:
    timestamp: datetime
    success: bool
    order_id: str

@dataclass
class WSLatencyEvent:
    timestamp: datetime
    latency_ms: float
    is_high: bool

@dataclass
class ExposureInfo:
    gross_exposure: float      # 총 노출
    net_exposure: float        # 순 노출
    symbol_exposures: dict     # 심볼별 노출
    largest_position: str      # 최대 포지션
```

### 주요 메서드

| 메서드 | 설명 | 반환 |
|--------|------|------|
| `get_mode()` | 현재 모드 | RiskMode |
| `set_mode(mode, reason)` | 모드 변경 | None |
| `can_open_position` | 신규 진입 가능 | bool |
| `on_order_success()` | 주문 성공 기록 | None |
| `on_order_failure()` | 주문 실패 기록 | None |
| `on_ws_latency(latency_ms)` | WS 지연 기록 | None |
| `on_hedge_failure(symbol, side)` | 헤지 실패 처리 | None |
| `check_daily_loss()` | 일 손실 체크 | None |
| `check_exposure_limit(symbol, size)` | 노출 한도 | bool |
| `get_exposure_info()` | 노출 정보 | ExposureInfo |

### SAFE/HALT 트리거 상수

```python
# SAFE 트리거
WS_LATENCY_THRESHOLD_MS = 2000
WS_LATENCY_DURATION_SEC = 10
ORDER_FAILURE_RATE_THRESHOLD = 0.20
DAILY_LOSS_SAFE = -0.015
ATR_SPIKE_MULTIPLIER = 1.8

# HALT 트리거
DAILY_LOSS_HALT = -0.030
LIQUIDATION_DISTANCE_CRITICAL = 0.025
MAX_AUTH_FAILURES = 3
```

---

## LiquidityFilter

**파일**: `src/risk/liquidity_filter.py`

### 데이터 구조

```python
@dataclass
class LiquidityCheck:
    symbol: str
    exchange: str           # "spot" or "perp"
    passed: bool
    volume_24h: float       # USDT
    spread_bps: float
    depth_ratio: float      # 깊이 / 주문량
    reason: str             # 실패 사유
```

### 주요 메서드

| 메서드 | 설명 | 반환 |
|--------|------|------|
| `check_spot(symbol, size)` | Spot 유동성 | LiquidityCheck |
| `check_perp(symbol, size)` | Perp 유동성 | LiquidityCheck |
| `check_both_exchanges(symbol, size)` | 양쪽 검증 | tuple[LiquidityCheck, LiquidityCheck] |

### 설정

```python
MIN_VOLUME_USDT = 50_000_000    # 50M
MAX_SPREAD_BPS = 8
DEPTH_MULTIPLIER = 5           # 주문량의 5배
```

---

## CoreCarryStrategy

**파일**: `src/strategies/core_carry.py`

### 데이터 구조

```python
@dataclass
class EdgeCalculation:
    basis_pct: float           # 원시 베이시스
    fee_total: float           # 총 수수료
    slippage_buffer: float     # 슬리피지 버퍼
    safety_buffer: float       # 안전 마진
    net_edge: float            # 순 엣지
    is_profitable: bool        # 수익성

class TrancheStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    EXECUTED = "EXECUTED"
    CANCELED = "CANCELED"

@dataclass
class Tranche:
    tranche_num: int           # 1, 2, 3
    size_pct: float            # 0.4, 0.3, 0.3
    status: TrancheStatus
    scheduled_time: datetime
    executed_time: Optional[datetime]
    executed_price: Optional[float]
```

### 주요 메서드

| 메서드 | 설명 | 반환 |
|--------|------|------|
| `calculate_edge(spot_price, perp_price)` | Edge 계산 | EdgeCalculation |
| `generate_signal(market_data)` | 시그널 생성 | Optional[Signal] |
| `should_exit(position, market_data)` | 청산 판단 | Optional[Signal] |
| `get_position_size(signal, capital)` | 사이즈 계산 | float |
| `_schedule_tranches(total_size)` | 트랜치 생성 | list[Tranche] |
| `_process_pending_tranches()` | 트랜치 처리 | list[Signal] |

### 설정

```python
MIN_EDGE_PCT = 0.0020          # 0.20%
FEE_BUFFER = 0.0008            # 0.08%
SLIPPAGE_BUFFER = 0.0006       # 6bps
SAFETY_BUFFER = 0.0002         # 2bps
EXIT_EDGE_THRESHOLD = 0.0005   # 0.05%

TRANCHE_SPLIT = [0.40, 0.30, 0.30]
TRANCHE_DELAY_SEC = 300        # 5분
```

---

## SatelliteStrategy

**파일**: `src/strategies/satellite.py`

### 데이터 구조

```python
class Regime(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    VOLATILE = "VOLATILE"

class ConfirmationStatus(str, Enum):
    NONE = "NONE"
    PHASE1 = "PHASE1"          # 시그널 감지
    PHASE2 = "PHASE2"          # 확인 대기
    CONFIRMED = "CONFIRMED"    # 진입 확정

@dataclass
class PendingSignal:
    symbol: str
    detected_at: datetime
    breakout_level: float
    initial_rvol: float
    status: ConfirmationStatus

@dataclass
class SatellitePosition:
    symbol: str
    side: str
    entry_price: float
    quantity: float
    entry_time: datetime
    highest_price: float
    trailing_active: bool
```

### 주요 메서드

| 메서드 | 설명 | 반환 |
|--------|------|------|
| `get_btc_regime()` | BTC 레짐 판단 | Regime |
| `generate_signal(market_data)` | 시그널 생성 | Optional[Signal] |
| `should_exit(position, market_data)` | 청산 판단 | Optional[Signal] |
| `_detect_signal(symbol, features)` | Phase 1 감지 | Optional[PendingSignal] |
| `_check_confirmation(pending)` | Phase 2 확인 | bool |
| `_check_time_stop(position)` | 타임스톱 | bool |
| `_check_trailing_stop(position, price)` | 트레일링 | bool |

### 설정

```python
HARD_STOP_PCT = -0.008         # -0.8%
TRAILING_TRIGGER_PCT = 0.01    # +1.0%
TRAILING_STOP_PCT = 0.006      # 0.6%
TIME_STOP_MINUTES = 30
RVOL_THRESHOLD = 2.5
CLOSE_POS_THRESHOLD = 0.75
ATR_VOLATILE_MULTIPLIER = 1.8
```

---

## ExecutionEngine

**파일**: `src/execution/executor.py`

### 데이터 구조

```python
class OrderUrgency(str, Enum):
    LOW = "LOW"                # 일반: maker 우선
    HIGH = "HIGH"              # 긴급: taker 허용

@dataclass
class OrderPolicy:
    urgency: OrderUrgency
    timeout_sec: float         # 타임아웃
    max_price_chase: int       # 가격 추종 횟수
    max_slippage_bps: float    # 최대 슬리피지
    use_post_only: bool        # PostOnly 사용

@dataclass
class HedgeRecoveryResult:
    success: bool
    attempts: int
    spot_rollback_executed: bool
    rollback_quantity: float
    total_slippage_bps: float
    final_perp_order_id: Optional[str]
```

### 주요 메서드

| 메서드 | 설명 | 반환 |
|--------|------|------|
| `create_order(...)` | 주문 생성 | OrderStateMachine |
| `submit_order(order, is_spot, urgency)` | 주문 제출 | bool |
| `submit_hedge_pair(spot, perp, urgency)` | 헤지 페어 | tuple[bool, bool] |
| `cancel_order(order_id, is_spot)` | 주문 취소 | bool |
| `_execute_with_policy(exchange, order, policy)` | 정책 실행 | bool |
| `_wait_for_fill(exchange, order, timeout)` | 체결 대기 | bool |
| `_chase_price(exchange, order, policy, price)` | 가격 추종 | bool |
| `_execute_ioc(exchange, order, policy, price)` | IOC 실행 | bool |
| `_attempt_hedge_recovery(spot, perp)` | 헤지 복구 | HedgeRecoveryResult |
| `get_avg_slippage_bps(symbol)` | 평균 슬리피지 | float |
| `get_slippage_top3()` | TOP3 슬리피지 | list[tuple] |
| `get_hedge_recovery_stats()` | 복구 통계 | dict |

---

## Reconciler

**파일**: `src/portfolio/reconcile.py`

### 데이터 구조

```python
class ReconcileCheckType(str, Enum):
    BALANCE = "BALANCE"
    POSITION = "POSITION"
    ORDER = "ORDER"

@dataclass
class PositionMismatch:
    symbol: str
    internal_qty: float
    exchange_qty: float
    internal_side: str
    exchange_side: str
    drift_pct: float

@dataclass
class OrderMismatch:
    order_id: str
    symbol: str
    issue: str                 # "MISSING_FILL", "UNKNOWN_ORDER"
    internal_status: str
    exchange_status: str

@dataclass
class ReconcileResult:
    success: bool
    timestamp: datetime
    spot_balance_expected: float
    spot_balance_actual: float
    perp_balance_expected: float
    perp_balance_actual: float
    spot_drift: float
    perp_drift: float
    total_drift_pct: float
    message: str
    position_mismatches: list[PositionMismatch]
    order_mismatches: list[OrderMismatch]
    check_type: ReconcileCheckType
```

### 주요 메서드

| 메서드 | 설명 | 반환 |
|--------|------|------|
| `start()` | 루프 시작 | None |
| `stop()` | 루프 중지 | None |
| `reconcile()` | 전체 검증 | ReconcileResult |
| `_reconcile_balance()` | 잔고 검증 | ReconcileResult |
| `_reconcile_positions()` | 포지션 검증 | ReconcileResult |
| `_reconcile_orders()` | 주문 검증 | ReconcileResult |
| `update_expected_balances(spot, perp)` | 예상 잔고 | None |
| `update_expected_position(symbol, ...)` | 예상 포지션 | None |
| `track_order(order_id, ...)` | 주문 추적 | None |
| `get_status()` | 상태 조회 | dict |

### 검증 주기

```python
BALANCE_CHECK_INTERVAL = 10    # 10초
POSITION_CHECK_INTERVAL = 30   # 30초 (3사이클)
ORDER_CHECK_INTERVAL = 60      # 60초 (6사이클)
DRIFT_THRESHOLD = 0.01         # 1%
MAX_CONSECUTIVE_FAILURES = 3
```

---

## SlackNotifier

**파일**: `src/monitoring/slack.py`

### 데이터 구조

```python
class AlertLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

@dataclass
class SlackMessage:
    text: str
    level: AlertLevel
    blocks: Optional[list]
```

### 주요 메서드

| 메서드 | 설명 | 반환 |
|--------|------|------|
| `send(message)` | 메시지 전송 | bool |
| `notify_mode_change(new, old, reason)` | 모드 변경 | bool |
| `notify_hedge_failure(...)` | 헤지 실패 | bool |
| `notify_daily_loss(loss, threshold, action)` | 손실 알림 | bool |
| `notify_ws_disconnection(exchange, duration)` | WS 끊김 | bool |
| `notify_liquidation_risk(...)` | 청산 위험 | bool |
| `notify_order_filled(...)` | 체결 알림 | bool |
| `send_daily_report(...)` | 일일 리포트 | bool |
| `update_daily_exposure(exposure)` | 노출 기록 | None |
| `update_daily_drawdown(drawdown)` | MDD 기록 | None |
| `update_strategy_pnl(strategy, pnl)` | PnL 기록 | None |

### 중복 방지

```python
DEDUP_WINDOW_SECONDS = 60      # 같은 메시지 60초 내 중복 방지
RATE_LIMIT_SECONDS = 1         # 초당 1개 메시지
```

---

## 의존성 관계도

```
                    CandleManager
                         │
                         ▼
                   FeatureEngine
                    │         │
         ┌──────────┘         └──────────┐
         ▼                               ▼
  CoreCarryStrategy              SatelliteStrategy
         │                               │
         └──────────┐    ┌───────────────┘
                    ▼    ▼
                  RiskEngine ◄── LiquidityFilter
                       │
                       ▼
                ExecutionEngine
                    │    │
         ┌──────────┘    └──────────┐
         ▼                          ▼
   BinanceSpot                BinancePerp
         │                          │
         └──────────┐    ┌──────────┘
                    ▼    ▼
                  Reconciler
                       │
                       ▼
                 SlackNotifier
```
