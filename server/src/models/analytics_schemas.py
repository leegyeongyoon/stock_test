"""분석 API 스키마"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PeriodType(str, Enum):
    """분석 기간 타입"""

    WEEK = "1w"
    MONTH = "1m"
    THREE_MONTHS = "3m"
    SIX_MONTHS = "6m"
    YEAR = "1y"


class DailyReturn(BaseModel):
    """일별 수익률"""

    date: str
    pnl: float
    pnl_pct: float
    equity: float
    core_pnl: float = 0.0
    satellite_pnl: float = 0.0


class PeriodReturnsResponse(BaseModel):
    """기간별 수익률 응답"""

    period: PeriodType
    start_date: str
    end_date: str
    total_pnl: float
    total_pnl_pct: float
    daily_returns: list[DailyReturn]
    max_drawdown: float
    sharpe_ratio: Optional[float] = None
    win_rate: float = 0.0


class SymbolPnl(BaseModel):
    """종목별 수익"""

    symbol: str
    strategy: str
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    trades_count: int
    win_rate: float
    avg_pnl_per_trade: float


class SymbolPnlResponse(BaseModel):
    """종목별 수익 응답"""

    period: PeriodType
    symbols: list[SymbolPnl]
    total_realized: float
    total_unrealized: float


class StrategyPnl(BaseModel):
    """전략별 수익"""

    strategy: str
    realized_pnl: float
    unrealized_pnl: float
    trades_count: int
    win_rate: float
    avg_holding_time_minutes: float = 0.0


class StrategyPnlResponse(BaseModel):
    """전략별 수익 응답"""

    period: PeriodType
    strategies: list[StrategyPnl]


class HourlyPnl(BaseModel):
    """시간대별 수익"""

    hour: int  # 0-23
    pnl: float
    trades_count: int
    avg_pnl: float


class HourlyPnlResponse(BaseModel):
    """시간대별 수익 응답"""

    period: PeriodType
    hourly_data: list[HourlyPnl]
    best_hour: int
    worst_hour: int


class EquityPoint(BaseModel):
    """자산 시계열 포인트"""

    timestamp: str
    equity: float
    pnl: float


class EquityCurveResponse(BaseModel):
    """자산 곡선 응답"""

    period: PeriodType
    data: list[EquityPoint]
    start_equity: float
    end_equity: float
    total_return_pct: float
