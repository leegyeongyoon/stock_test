"""분석 API 엔드포인트"""

from fastapi import APIRouter, Depends, Query

from src.models.analytics_schemas import (
    EquityCurveResponse,
    HourlyPnlResponse,
    PeriodReturnsResponse,
    PeriodType,
    StrategyPnlResponse,
    SymbolPnlResponse,
)
from src.models.database import AsyncSession, get_db
from src.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/returns", response_model=PeriodReturnsResponse)
async def get_period_returns(
    period: PeriodType = Query(PeriodType.MONTH, description="조회 기간"),
    db: AsyncSession = Depends(get_db),
):
    """기간별 수익률 조회

    - **period**: 조회 기간 (1w, 1m, 3m, 6m, 1y)
    """
    service = AnalyticsService(db)
    return await service.get_period_returns(period)


@router.get("/symbols", response_model=SymbolPnlResponse)
async def get_symbol_pnl(
    period: PeriodType = Query(PeriodType.MONTH, description="조회 기간"),
    db: AsyncSession = Depends(get_db),
):
    """종목별 수익 분석

    어떤 종목에서 수익을 많이 냈는지 분석합니다.

    - **period**: 조회 기간 (1w, 1m, 3m, 6m, 1y)
    """
    service = AnalyticsService(db)
    return await service.get_symbol_pnl(period)


@router.get("/strategies", response_model=StrategyPnlResponse)
async def get_strategy_pnl(
    period: PeriodType = Query(PeriodType.MONTH, description="조회 기간"),
    db: AsyncSession = Depends(get_db),
):
    """전략별 수익 분석 (Core vs Satellite)

    - **period**: 조회 기간 (1w, 1m, 3m, 6m, 1y)
    """
    service = AnalyticsService(db)
    return await service.get_strategy_pnl(period)


@router.get("/hourly", response_model=HourlyPnlResponse)
async def get_hourly_pnl(
    period: PeriodType = Query(PeriodType.MONTH, description="조회 기간"),
    db: AsyncSession = Depends(get_db),
):
    """시간대별 수익 분석

    어느 시간대에 수익을 많이 냈는지 분석합니다 (0-23시 UTC).

    - **period**: 조회 기간 (1w, 1m, 3m, 6m, 1y)
    """
    service = AnalyticsService(db)
    return await service.get_hourly_pnl(period)


@router.get("/equity-curve", response_model=EquityCurveResponse)
async def get_equity_curve(
    period: PeriodType = Query(PeriodType.MONTH, description="조회 기간"),
    db: AsyncSession = Depends(get_db),
):
    """자산 곡선 조회

    기간 동안의 자산 변화를 시계열로 조회합니다.

    - **period**: 조회 기간 (1w, 1m, 3m, 6m, 1y)
    """
    service = AnalyticsService(db)
    return await service.get_equity_curve(period)
