"""SQLAlchemy 2.0 async 데이터베이스 설정"""

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, Float, Integer, String, Text, Boolean, JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import get_settings
from src.models.schemas import (
    EventLevel,
    EventType,
    OrderSide,
    OrderStatus,
    OrderType,
    StrategyType,
    TradingMode,
)

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.log_level == "DEBUG",
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# PostgreSQL Enum types (use existing database enums)
# create_type=False tells SQLAlchemy not to create new enum types
PgStrategyType = Enum(StrategyType, name="strategytype", create_type=False)
PgOrderSide = Enum(OrderSide, name="orderside", create_type=False)
PgOrderType = Enum(OrderType, name="ordertype", create_type=False)
PgOrderStatus = Enum(OrderStatus, name="orderstatus", create_type=False)
PgEventLevel = Enum(EventLevel, name="eventlevel", create_type=False)
PgEventType = Enum(EventType, name="eventtype", create_type=False)
PgTradingMode = Enum(TradingMode, name="tradingmode", create_type=False)


class Base(DeclarativeBase):
    """SQLAlchemy Base 클래스"""

    pass


class OrderModel(Base):
    """주문 테이블"""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    exchange_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy: Mapped[StrategyType] = mapped_column(PgStrategyType)
    side: Mapped[OrderSide] = mapped_column(PgOrderSide)
    order_type: Mapped[OrderType] = mapped_column(PgOrderType)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[OrderStatus] = mapped_column(PgOrderStatus, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class PositionModel(Base):
    """포지션 테이블"""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy: Mapped[StrategyType] = mapped_column(PgStrategyType)
    side: Mapped[OrderSide] = mapped_column(PgOrderSide)
    quantity: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TradeModel(Base):
    """체결 기록 테이블"""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy: Mapped[StrategyType] = mapped_column(PgStrategyType)
    side: Mapped[OrderSide] = mapped_column(PgOrderSide)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    fee_asset: Mapped[str] = mapped_column(String(10), default="USDT")
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventModel(Base):
    """이벤트 로그 테이블"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    level: Mapped[EventLevel] = mapped_column(PgEventLevel, index=True)
    event_type: Mapped[EventType] = mapped_column(PgEventType, index=True)
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ModeHistoryModel(Base):
    """모드 변경 이력 테이블"""

    __tablename__ = "mode_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[TradingMode] = mapped_column(PgTradingMode)
    reason: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class DailyStatsModel(Base):
    """일별 통계 테이블"""

    __tablename__ = "daily_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # YYYY-MM-DD
    starting_equity: Mapped[float] = mapped_column(Float)
    ending_equity: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    core_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    satellite_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)


class HourlyStatsModel(Base):
    """시간별 통계 테이블"""

    __tablename__ = "hourly_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    hour: Mapped[int] = mapped_column(Integer, index=True)  # 0-23
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    core_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    satellite_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)


class SymbolStatsModel(Base):
    """종목별 통계 테이블"""

    __tablename__ = "symbol_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy: Mapped[StrategyType] = mapped_column(PgStrategyType)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, default=0)


class EquitySnapshotModel(Base):
    """자산 스냅샷 테이블 (차트용)"""

    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True, default=datetime.utcnow)
    equity: Mapped[float] = mapped_column(Float)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)


class PositionLedgerModel(Base):
    """P0: 포지션 원장 테이블 (단일 진실 원장)"""

    __tablename__ = "position_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(20), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[float] = mapped_column(Float)
    avg_entry_price: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    total_fees: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), index=True)  # OPEN, PARTIAL_CLOSED, CLOSED
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    last_fill_time: Mapped[datetime] = mapped_column(DateTime)
    fill_count: Mapped[int] = mapped_column(Integer, default=1)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    initial_stop_distance: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ExecutionCostModel(Base):
    """P1: 체결비용 기록 테이블"""

    __tablename__ = "execution_costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    order_type: Mapped[str] = mapped_column(String(20))  # ENTRY, EXIT, PARTIAL_EXIT, ADD
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy_id: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10))
    requested_price: Mapped[float] = mapped_column(Float)
    filled_price: Mapped[float] = mapped_column(Float)
    filled_qty: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    fee_krw: Mapped[float] = mapped_column(Float)
    slippage_bps: Mapped[float] = mapped_column(Float)
    spread_bps_at_fill: Mapped[float] = mapped_column(Float, default=0.0)
    notional_krw: Mapped[float] = mapped_column(Float)
    total_cost_krw: Mapped[float] = mapped_column(Float)
    cost_pct: Mapped[float] = mapped_column(Float)


class TradeDetailModel(Base):
    """
    거래 상세 기록 테이블 (지표 포함)

    v6.0: 손실 패턴 분석을 위한 진입 시점 지표 기록
    """

    __tablename__ = "trade_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy: Mapped[str] = mapped_column(String(20), index=True)  # PULLBACK, REBOUND, DIP_SCALPER, ATTACK

    # 진입/청산 정보
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 진입 시점 지표 (JSON으로 저장)
    indicators_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 분석용 메타데이터
    hold_duration_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_profit_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # 전략 점수
    strategy_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    strategy_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 날짜 인덱스 (일별 조회용)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class StrategyDailyPnlModel(Base):
    """
    전략별 일별 수익 요약 테이블

    v6.0: 대시보드 전략 비교용
    """

    __tablename__ = "strategy_daily_pnl"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    strategy: Mapped[str] = mapped_column(String(20), index=True)

    # 수익 통계
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    total_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)

    # 거래 금액
    total_volume: Mapped[float] = mapped_column(Float, default=0.0)
    avg_trade_size: Mapped[float] = mapped_column(Float, default=0.0)

    # 손익 분석
    avg_win_pct: Mapped[float] = mapped_column(Float, default=0.0)
    avg_loss_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_win_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_loss_pct: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


async def init_db() -> None:
    """데이터베이스 초기화"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """DB 세션 의존성"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
