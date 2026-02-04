"""SQLAlchemy 2.0 async 데이터베이스 설정"""

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, Float, Integer, String, Text, Boolean
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
    strategy: Mapped[StrategyType] = mapped_column(Enum(StrategyType))
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide))
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType))
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class PositionModel(Base):
    """포지션 테이블"""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy: Mapped[StrategyType] = mapped_column(Enum(StrategyType))
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide))
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
    strategy: Mapped[StrategyType] = mapped_column(Enum(StrategyType))
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide))
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
    level: Mapped[EventLevel] = mapped_column(Enum(EventLevel), index=True)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), index=True)
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ModeHistoryModel(Base):
    """모드 변경 이력 테이블"""

    __tablename__ = "mode_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[TradingMode] = mapped_column(Enum(TradingMode))
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
    strategy: Mapped[StrategyType] = mapped_column(Enum(StrategyType))
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
