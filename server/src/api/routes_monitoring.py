"""실시간 알고리즘 모니터링 API"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.config import get_settings

router = APIRouter(tags=["monitoring"])

# 엔진 인스턴스 (app.py에서 주입)
_engine = None


def get_engine():
    """엔진 의존성"""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    return _engine


def set_engine(engine):
    """엔진 설정"""
    global _engine
    _engine = engine


class FilterStats(BaseModel):
    """필터링 통계"""

    anti_chase: int
    exposure_limit: int
    overheat: int
    total: int


@router.get("/monitoring/filter-stats", response_model=FilterStats)
async def get_filter_stats():
    """오늘의 필터링 통계"""
    engine = get_engine()
    return FilterStats(**engine.get_filter_stats())


@router.get("/monitoring/v3/strategies")
async def get_v3_strategies_monitoring():
    """v3 전략 모니터링 데이터"""
    engine = get_engine()

    strategies_data = []
    for strat in engine.v3_strategies:
        positions = strat.get_all_positions()
        pos_list = []
        for sym, pos in positions.items():
            market_data = engine._market_data.get(sym, {})
            current_price = market_data.get("price", pos.entry_price)
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
            hold_minutes = (datetime.utcnow() - pos.entry_time).total_seconds() / 60

            pos_list.append({
                "symbol": sym,
                "entry_price": pos.entry_price,
                "current_price": current_price,
                "quantity": pos.quantity,
                "pnl_pct": round(pnl_pct * 100, 2),
                "highest_price": pos.highest_price,
                "trailing_active": pos.trailing_active,
                "hold_minutes": round(hold_minutes, 1),
                "stop_loss_pct": pos.stop_loss_pct,
                "take_profit_pct": pos.take_profit_pct,
                "time_stop_minutes": pos.time_stop_minutes,
            })

        strategies_data.append({
            "name": strat.name,
            "cooldown_minutes": strat.cooldown_minutes,
            "positions_count": len(positions),
            "positions": pos_list,
        })

    total_positions = sum(s["positions_count"] for s in strategies_data)

    return {
        "v3_enabled": engine.v3_enabled,
        "v3_max_positions": engine.v3_max_positions,
        "total_positions": total_positions,
        "strategies": strategies_data,
    }
