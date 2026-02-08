"""
Trading History API 엔드포인트

v6.0: 전략별 매수/매도 내역 및 수익 분석 API
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.database import (
    TradeDetailModel,
    StrategyDailyPnlModel,
    get_db,
)

router = APIRouter(prefix="/trading-history", tags=["Trading History"])


# === Pydantic 스키마 ===

class TradeDetailResponse(BaseModel):
    """거래 상세 응답"""
    trade_id: str
    symbol: str
    strategy: str
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    pnl: float
    pnl_pct: float
    exit_reason: Optional[str]
    hold_duration_sec: Optional[int]
    max_profit_pct: float
    max_drawdown_pct: float
    strategy_score: Optional[float]
    strategy_level: Optional[int]
    indicators_at_entry: Optional[dict]

    class Config:
        from_attributes = True


class StrategyStatsResponse(BaseModel):
    """전략 통계 응답"""
    strategy: str
    total_pnl: float
    total_pnl_pct: float
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    trades: list[TradeDetailResponse]


class DailyTradingHistoryResponse(BaseModel):
    """일별 거래 내역 응답"""
    date: str
    strategies: dict[str, StrategyStatsResponse]
    total_pnl: float
    total_trades: int


class StrategyPnlHistoryResponse(BaseModel):
    """전략별 일별 PnL 히스토리"""
    strategy: str
    history: list[dict]  # [{"date": "2026-02-07", "pnl": 5000, "trades": 3}, ...]


# === API 엔드포인트 ===

@router.get("/daily", response_model=DailyTradingHistoryResponse)
async def get_daily_trading_history(
    date: Optional[str] = Query(None, description="조회 날짜 (YYYY-MM-DD, 기본: 오늘)"),
    db: AsyncSession = Depends(get_db),
):
    """
    일별 전략별 거래 내역 조회

    Response:
    - date: 조회 날짜
    - strategies: 전략별 통계 및 거래 내역
    - total_pnl: 전체 PnL
    - total_trades: 전체 거래 수
    """
    if date is None:
        date = datetime.utcnow().strftime("%Y-%m-%d")

    # 전략별 거래 조회
    query = select(TradeDetailModel).where(
        TradeDetailModel.trade_date == date
    ).order_by(TradeDetailModel.entry_time.desc())

    result = await db.execute(query)
    trades = result.scalars().all()

    # 전략별 그룹화
    strategies_data: dict[str, dict] = {}
    total_pnl = 0.0
    total_trades = 0

    for trade in trades:
        strategy = trade.strategy
        if strategy not in strategies_data:
            strategies_data[strategy] = {
                "strategy": strategy,
                "total_pnl": 0.0,
                "total_pnl_pct": 0.0,
                "trade_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
                "trades": [],
                "_win_pcts": [],
                "_loss_pcts": [],
            }

        stat = strategies_data[strategy]
        stat["trades"].append(TradeDetailResponse(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            strategy=trade.strategy,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            pnl=trade.pnl,
            pnl_pct=trade.pnl_pct,
            exit_reason=trade.exit_reason,
            hold_duration_sec=trade.hold_duration_sec,
            max_profit_pct=trade.max_profit_pct,
            max_drawdown_pct=trade.max_drawdown_pct,
            strategy_score=trade.strategy_score,
            strategy_level=trade.strategy_level,
            indicators_at_entry=trade.indicators_json,
        ))

        stat["total_pnl"] += trade.pnl
        stat["trade_count"] += 1
        total_pnl += trade.pnl
        total_trades += 1

        if trade.pnl >= 0:
            stat["win_count"] += 1
            stat["_win_pcts"].append(trade.pnl_pct)
        else:
            stat["loss_count"] += 1
            stat["_loss_pcts"].append(trade.pnl_pct)

    # 통계 계산
    for strategy, stat in strategies_data.items():
        if stat["trade_count"] > 0:
            stat["win_rate"] = stat["win_count"] / stat["trade_count"] * 100
        if stat["_win_pcts"]:
            stat["avg_win_pct"] = sum(stat["_win_pcts"]) / len(stat["_win_pcts"]) * 100
        if stat["_loss_pcts"]:
            stat["avg_loss_pct"] = sum(stat["_loss_pcts"]) / len(stat["_loss_pcts"]) * 100

        # 임시 필드 제거
        del stat["_win_pcts"]
        del stat["_loss_pcts"]

    # StrategyStatsResponse로 변환
    strategies_response = {
        k: StrategyStatsResponse(**v) for k, v in strategies_data.items()
    }

    return DailyTradingHistoryResponse(
        date=date,
        strategies=strategies_response,
        total_pnl=total_pnl,
        total_trades=total_trades,
    )


@router.get("/trades", response_model=list[TradeDetailResponse])
async def get_trades_by_strategy(
    strategy: Optional[str] = Query(None, description="전략 (PULLBACK, REBOUND, DIP_SCALPER, ATTACK)"),
    date: Optional[str] = Query(None, description="조회 날짜 (YYYY-MM-DD)"),
    days: int = Query(7, ge=1, le=90, description="조회 기간 (일)"),
    limit: int = Query(100, ge=1, le=500, description="최대 조회 수"),
    db: AsyncSession = Depends(get_db),
):
    """
    전략별 상세 거래 내역 조회

    Query Params:
    - strategy: 전략 필터 (optional)
    - date: 특정 날짜 (optional)
    - days: 조회 기간 (기본 7일)
    - limit: 최대 조회 수 (기본 100)
    """
    # 조건 빌드
    conditions = []

    if strategy:
        conditions.append(TradeDetailModel.strategy == strategy.upper())

    if date:
        conditions.append(TradeDetailModel.trade_date == date)
    else:
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        conditions.append(TradeDetailModel.trade_date >= start_date)

    # 쿼리 실행
    query = select(TradeDetailModel)
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(TradeDetailModel.entry_time.desc()).limit(limit)

    result = await db.execute(query)
    trades = result.scalars().all()

    return [
        TradeDetailResponse(
            trade_id=t.trade_id,
            symbol=t.symbol,
            strategy=t.strategy,
            entry_time=t.entry_time,
            exit_time=t.exit_time,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            quantity=t.quantity,
            pnl=t.pnl,
            pnl_pct=t.pnl_pct,
            exit_reason=t.exit_reason,
            hold_duration_sec=t.hold_duration_sec,
            max_profit_pct=t.max_profit_pct,
            max_drawdown_pct=t.max_drawdown_pct,
            strategy_score=t.strategy_score,
            strategy_level=t.strategy_level,
            indicators_at_entry=t.indicators_json,
        )
        for t in trades
    ]


@router.get("/strategy-pnl", response_model=dict[str, list[dict]])
async def get_strategy_pnl_history(
    days: int = Query(30, ge=1, le=365, description="조회 기간 (일)"),
    db: AsyncSession = Depends(get_db),
):
    """
    전략별 일별 수익 요약 조회

    Response:
    {
        "PULLBACK": [{"date": "2026-02-07", "pnl": 5000, "trades": 3}, ...],
        "REBOUND": [...],
        "DIP_SCALPER": [...]
    }
    """
    start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 일별 전략별 PnL 집계
    query = select(StrategyDailyPnlModel).where(
        StrategyDailyPnlModel.date >= start_date
    ).order_by(StrategyDailyPnlModel.date.asc())

    result = await db.execute(query)
    records = result.scalars().all()

    # 전략별 그룹화
    strategy_history: dict[str, list[dict]] = {}

    for record in records:
        if record.strategy not in strategy_history:
            strategy_history[record.strategy] = []

        strategy_history[record.strategy].append({
            "date": record.date,
            "pnl": record.total_pnl,
            "pnl_pct": record.total_pnl_pct,
            "trades": record.trade_count,
            "win_rate": record.win_rate,
            "avg_win_pct": record.avg_win_pct,
            "avg_loss_pct": record.avg_loss_pct,
        })

    return strategy_history


@router.get("/loss-analysis")
async def get_loss_analysis(
    strategy: Optional[str] = Query(None, description="전략 필터"),
    days: int = Query(30, ge=1, le=90, description="분석 기간 (일)"),
    db: AsyncSession = Depends(get_db),
):
    """
    손실 거래 패턴 분석

    손실 거래만 조회하여 공통 지표 패턴 분석
    """
    start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 손실 거래만 조회
    conditions = [
        TradeDetailModel.trade_date >= start_date,
        TradeDetailModel.pnl < 0,
    ]
    if strategy:
        conditions.append(TradeDetailModel.strategy == strategy.upper())

    query = select(TradeDetailModel).where(
        and_(*conditions)
    ).order_by(TradeDetailModel.pnl.asc())

    result = await db.execute(query)
    loss_trades = result.scalars().all()

    # 손실 패턴 분석
    analysis = {
        "total_loss_trades": len(loss_trades),
        "total_loss": sum(t.pnl for t in loss_trades),
        "avg_loss_pct": 0.0,
        "by_strategy": {},
        "by_exit_reason": {},
        "indicator_patterns": {
            "low_rvol": {"count": 0, "avg_rvol": 0.0, "total_loss": 0.0},
            "high_spread": {"count": 0, "avg_spread": 0.0, "total_loss": 0.0},
            "weak_orderbook": {"count": 0, "avg_bid_ratio": 0.0, "total_loss": 0.0},
        },
        "time_patterns": {},
    }

    if not loss_trades:
        return analysis

    analysis["avg_loss_pct"] = sum(t.pnl_pct for t in loss_trades) / len(loss_trades) * 100

    # 전략별 집계
    for trade in loss_trades:
        strat = trade.strategy
        if strat not in analysis["by_strategy"]:
            analysis["by_strategy"][strat] = {"count": 0, "total_loss": 0.0}
        analysis["by_strategy"][strat]["count"] += 1
        analysis["by_strategy"][strat]["total_loss"] += trade.pnl

        # 청산 사유별 집계
        reason = trade.exit_reason or "unknown"
        if reason not in analysis["by_exit_reason"]:
            analysis["by_exit_reason"][reason] = {"count": 0, "total_loss": 0.0}
        analysis["by_exit_reason"][reason]["count"] += 1
        analysis["by_exit_reason"][reason]["total_loss"] += trade.pnl

        # 지표 패턴 분석
        indicators = trade.indicators_json or {}

        # RVOL 분석
        rvol = indicators.get("rvol_1m", 1.0)
        if rvol < 2.0:
            analysis["indicator_patterns"]["low_rvol"]["count"] += 1
            analysis["indicator_patterns"]["low_rvol"]["total_loss"] += trade.pnl

        # 스프레드 분석
        spread = indicators.get("spread_bps", 0)
        if spread > 15:
            analysis["indicator_patterns"]["high_spread"]["count"] += 1
            analysis["indicator_patterns"]["high_spread"]["total_loss"] += trade.pnl

        # 호가창 분석
        bid_ratio = indicators.get("bid_ratio", 0.5)
        if bid_ratio < 0.4:
            analysis["indicator_patterns"]["weak_orderbook"]["count"] += 1
            analysis["indicator_patterns"]["weak_orderbook"]["total_loss"] += trade.pnl

        # 시간대 분석
        if trade.entry_time:
            hour = trade.entry_time.hour
            hour_key = f"{hour:02d}:00"
            if hour_key not in analysis["time_patterns"]:
                analysis["time_patterns"][hour_key] = {"count": 0, "total_loss": 0.0}
            analysis["time_patterns"][hour_key]["count"] += 1
            analysis["time_patterns"][hour_key]["total_loss"] += trade.pnl

    return analysis


@router.get("/summary")
async def get_trading_summary(
    days: int = Query(7, ge=1, le=90, description="요약 기간 (일)"),
    db: AsyncSession = Depends(get_db),
):
    """
    거래 요약 통계

    전체 기간 동안의 전략별 성과 요약
    """
    start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 전체 거래 집계
    query = select(TradeDetailModel).where(
        TradeDetailModel.trade_date >= start_date
    )

    result = await db.execute(query)
    trades = result.scalars().all()

    summary = {
        "period_days": days,
        "total_trades": len(trades),
        "total_pnl": sum(t.pnl for t in trades),
        "total_pnl_pct": 0.0,
        "win_count": sum(1 for t in trades if t.pnl >= 0),
        "loss_count": sum(1 for t in trades if t.pnl < 0),
        "win_rate": 0.0,
        "by_strategy": {},
    }

    if trades:
        summary["total_pnl_pct"] = sum(t.pnl_pct for t in trades) / len(trades) * 100
        summary["win_rate"] = summary["win_count"] / len(trades) * 100

        # 전략별 집계
        for trade in trades:
            strat = trade.strategy
            if strat not in summary["by_strategy"]:
                summary["by_strategy"][strat] = {
                    "trades": 0,
                    "pnl": 0.0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0.0,
                }
            s = summary["by_strategy"][strat]
            s["trades"] += 1
            s["pnl"] += trade.pnl
            if trade.pnl >= 0:
                s["wins"] += 1
            else:
                s["losses"] += 1

        # 승률 계산
        for strat, data in summary["by_strategy"].items():
            if data["trades"] > 0:
                data["win_rate"] = data["wins"] / data["trades"] * 100

    return summary
