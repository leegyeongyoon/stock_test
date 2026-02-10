"""Pydantic 스키마 - API 응답 및 내부 데이터 구조"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TradingMode(str, Enum):
    """트레이딩 모드"""

    NORMAL = "NORMAL"  # 신규 진입 허용
    SAFE = "SAFE"  # 신규 진입 금지, 기존 포지션 축소/헤지 우선
    HALT = "HALT"  # 신규 주문 중지, 긴급 청산만


class OrderStatus(str, Enum):
    """주문 상태 머신"""

    NEW = "NEW"  # 생성됨
    PENDING = "PENDING"  # 미체결 (지정가 주문 대기 중)
    SENT = "SENT"  # 거래소 전송됨
    ACK = "ACK"  # 거래소 응답 받음
    PARTIAL = "PARTIAL"  # 부분 체결
    FILLED = "FILLED"  # 완전 체결
    CANCELED = "CANCELED"  # 취소됨
    REJECTED = "REJECTED"  # 거부됨
    SYNCED = "SYNCED"  # 동기화됨 (서버 재시작 시 복구)


class OrderSide(str, Enum):
    """주문 방향"""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """주문 타입"""

    MARKET = "MARKET"
    LIMIT = "LIMIT"


class StrategyType(str, Enum):
    """전략 타입 (v3 데이터 기반)"""

    VOLATILE_OVERSOLD_BOUNCE = "VOLATILE_OVERSOLD_BOUNCE"
    CRASH_RECOVERY = "CRASH_RECOVERY"
    TRIPLE_BEARISH_REVERSAL = "TRIPLE_BEARISH_REVERSAL"


class EventLevel(str, Enum):
    """이벤트 레벨"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class EventType(str, Enum):
    """이벤트 타입"""

    SYSTEM = "SYSTEM"
    ORDER = "ORDER"
    RISK = "RISK"
    STRATEGY = "STRATEGY"
    CONNECTION = "CONNECTION"
    MODE_CHANGE = "MODE_CHANGE"
    FILTER = "FILTER"
    V3_SIGNAL = "V3_SIGNAL"
    V3_ENTRY = "V3_ENTRY"
    V3_EXIT = "V3_EXIT"


# === API 응답 스키마 ===


class SummarySchema(BaseModel):
    """요약 정보 (Upbit KRW)"""

    equity: float = Field(description="총 자산 (KRW)")
    pnl_today: float = Field(description="오늘 PnL (KRW)")
    pnl_today_pct: float = Field(description="오늘 PnL (%)")
    drawdown: float = Field(description="최대 손실폭 (%)")
    exposure: float = Field(description="총 익스포저 (KRW)")
    cash: float = Field(description="가용 현금 (KRW)")
    mode: TradingMode = Field(description="현재 운영 모드")
    is_paper: bool = Field(description="Paper 모드 여부")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PositionSchema(BaseModel):
    """포지션 정보 (Upbit 현물 전용)"""

    symbol: str = Field(description="심볼 (예: KRW-BTC)")
    strategy: StrategyType = Field(description="전략 타입")
    side: OrderSide = Field(description="방향 (현물은 BUY만)")
    quantity: float = Field(description="수량")
    avg_price: float = Field(description="평균 진입가")
    current_price: float = Field(description="현재가")
    unrealized_pnl: float = Field(description="미실현 PnL (KRW)")
    realized_pnl: float = Field(description="실현 PnL (KRW)")
    notional: float = Field(description="명목 가치 (KRW)")
    opened_at: datetime = Field(description="포지션 오픈 시간")


class OrderSchema(BaseModel):
    """주문 정보"""

    order_id: str = Field(description="내부 주문 ID")
    exchange_order_id: Optional[str] = Field(default=None, description="거래소 주문 ID")
    symbol: str = Field(description="심볼")
    strategy: StrategyType = Field(description="전략 타입")
    side: OrderSide = Field(description="방향")
    order_type: OrderType = Field(description="주문 타입")
    quantity: float = Field(description="주문 수량")
    price: Optional[float] = Field(default=None, description="지정가 (LIMIT만)")
    filled_quantity: float = Field(default=0.0, description="체결 수량")
    avg_fill_price: Optional[float] = Field(default=None, description="평균 체결가")
    status: OrderStatus = Field(description="주문 상태")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EventSchema(BaseModel):
    """이벤트 정보"""

    id: str = Field(description="이벤트 ID")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    level: EventLevel = Field(description="이벤트 레벨")
    event_type: EventType = Field(description="이벤트 타입")
    message: str = Field(description="이벤트 메시지")
    details: Optional[dict] = Field(default=None, description="추가 상세 정보")


class ModeSchema(BaseModel):
    """현재 모드 정보"""

    mode: TradingMode = Field(description="현재 운영 모드")
    reason: str = Field(description="모드 전환 사유")
    changed_at: datetime = Field(description="모드 변경 시간")


class HealthSchema(BaseModel):
    """헬스체크 응답"""

    status: str = Field(description="overall status")
    db_connected: bool = Field(description="DB 연결 상태")
    engine_running: bool = Field(description="엔진 실행 상태")
    ws_connected: bool = Field(description="거래소 WebSocket 연결 상태")
    last_heartbeat: Optional[datetime] = Field(default=None)


class ConfigSchema(BaseModel):
    """설정 정보 (readonly)"""

    is_paper_mode: bool
    daily_loss_limit_safe: float
    daily_loss_limit_halt: float
    reconcile_interval_sec: int
    v3_enabled: bool = Field(default=True, description="v3 전략 활성화")
    v3_max_positions: int = Field(default=6, description="v3 최대 포지션 수")


# === API 요청 스키마 ===


class BotCommandRequest(BaseModel):
    """봇 명령 요청"""

    reason: Optional[str] = Field(default=None, description="명령 사유")


class FlattenRequest(BaseModel):
    """포지션 정리 요청"""

    strategy: Optional[StrategyType] = Field(default=None, description="특정 전략만 정리")
    symbol: Optional[str] = Field(default=None, description="특정 심볼만 정리")
    confirm: bool = Field(default=False, description="확인 플래그 (Live 모드 필수)")
