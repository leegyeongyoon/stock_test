"""백테스팅 데이터베이스 모델"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, JSON, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.database import Base


class BacktestCandleModel(Base):
    """과거 캔들 데이터 저장 테이블"""

    __tablename__ = "backtest_candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    interval: Mapped[str] = mapped_column(String(10), index=True)  # 1m, 5m, 15m, 1h, 4h, 1d
    open_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)  # 거래량 (코인)
    quote_volume: Mapped[float] = mapped_column(Float, default=0.0)  # 거래대금 (KRW)

    __table_args__ = (
        UniqueConstraint("symbol", "interval", "open_time", name="uq_candle"),
        Index("ix_candle_lookup", "symbol", "interval", "open_time"),
    )


class BacktestResultModel(Base):
    """백테스트 결과 저장 테이블"""

    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy: Mapped[str] = mapped_column(String(50), index=True)
    symbols: Mapped[str] = mapped_column(Text)  # JSON array of symbols
    interval: Mapped[str] = mapped_column(String(10))
    start_date: Mapped[datetime] = mapped_column(DateTime)
    end_date: Mapped[datetime] = mapped_column(DateTime)

    # 자본
    initial_capital: Mapped[float] = mapped_column(Float)
    final_equity: Mapped[float] = mapped_column(Float)

    # 핵심 지표
    total_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    profit_factor: Mapped[float] = mapped_column(Float, default=0.0)

    # 상세 메트릭스 (JSON)
    metrics_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 파라미터 (JSON)
    parameters_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 에쿼티 커브 (JSON array)
    equity_curve_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BacktestTradeModel(Base):
    """백테스트 거래 내역 테이블"""

    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    trade_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy: Mapped[str] = mapped_column(String(50))

    # 진입
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_quantity: Mapped[float] = mapped_column(Float)
    entry_value: Mapped[float] = mapped_column(Float)  # entry_price * entry_quantity

    # 청산
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 손익
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)

    # 보유 시간 (분)
    holding_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 추가 정보
    max_profit_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # 진입 시점 지표 (JSON)
    entry_indicators_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
