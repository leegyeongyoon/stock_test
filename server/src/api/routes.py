"""FastAPI REST 엔드포인트"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.config import Settings, get_settings
from src.engine.command_queue import Command, CommandType
from src.models.schemas import (
    BotCommandRequest,
    ConfigSchema,
    EventSchema,
    FlattenRequest,
    HealthSchema,
    ModeSchema,
    OrderSchema,
    OrderSide,
    PositionSchema,
    SummarySchema,
    TradingMode,
)

router = APIRouter()

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


@router.get("/health", response_model=HealthSchema)
async def health_check():
    """헬스체크"""
    engine = get_engine()

    return HealthSchema(
        status="healthy" if engine.is_running else "degraded",
        db_connected=True,  # TODO: 실제 DB 체크
        engine_running=engine.is_running,
        ws_connected=engine.spot_exchange.is_connected and engine.perp_exchange.is_connected,
        last_heartbeat=engine.last_heartbeat,
    )


@router.get("/mode", response_model=ModeSchema)
async def get_mode():
    """현재 모드 조회"""
    engine = get_engine()
    risk = engine.risk_engine

    return ModeSchema(
        mode=risk.mode,
        reason=risk.mode_manager.reason,
        changed_at=risk.mode_manager.changed_at,
    )


@router.get("/summary", response_model=SummarySchema)
async def get_summary():
    """요약 정보 조회"""
    engine = get_engine()
    summary = engine.get_summary()
    settings = get_settings()

    return SummarySchema(
        equity=summary.get("equity", 0),
        pnl_today=summary.get("pnl_today", 0),
        pnl_today_pct=summary.get("pnl_today_pct", 0),
        drawdown=summary.get("drawdown", 0),
        exposure=summary.get("exposure", 0),
        cash=summary.get("cash", 0),
        margin_used=summary.get("margin_used", 0),
        mode=TradingMode(summary.get("mode", "NORMAL")),
        is_paper=settings.is_paper_mode,
        updated_at=datetime.fromisoformat(summary.get("updated_at", datetime.utcnow().isoformat())),
    )


@router.get("/positions", response_model=list[PositionSchema])
async def get_positions():
    """포지션 목록 조회"""
    engine = get_engine()
    positions = engine.get_positions()

    result = []
    for p in positions:
        # Futures의 LONG/SHORT을 BUY/SELL로 변환
        side_str = p["side"]
        if side_str == "LONG":
            side = OrderSide.BUY
        elif side_str == "SHORT":
            side = OrderSide.SELL
        else:
            side = OrderSide(side_str)

        result.append(
            PositionSchema(
                symbol=p["symbol"],
                strategy=p["strategy"],
                side=side,
                quantity=p["quantity"],
                avg_price=p["avg_price"],
                current_price=p["current_price"],
                unrealized_pnl=p["unrealized_pnl"],
                realized_pnl=p["realized_pnl"],
                notional=p["notional"],
                leverage=p.get("leverage", 1.0),
                opened_at=datetime.utcnow(),  # TODO: 실제 시간
            )
        )
    return result


@router.get("/orders", response_model=list[OrderSchema])
async def get_orders(
    status: Optional[str] = Query(None, description="주문 상태 필터"),
    limit: int = Query(100, le=500),
):
    """주문 목록 조회"""
    # TODO: 실제 주문 조회 구현
    return []


@router.get("/events", response_model=list[EventSchema])
async def get_events(
    level: Optional[str] = Query(None, description="이벤트 레벨 필터"),
    limit: int = Query(100, le=500),
):
    """이벤트 타임라인 조회"""
    engine = get_engine()
    events = engine.get_events(limit=limit)

    result = []
    for e in events:
        result.append(
            EventSchema(
                id=e["id"],
                timestamp=datetime.fromisoformat(e["timestamp"]),
                level=e["level"],
                event_type=e["event_type"],
                message=e["message"],
                details=e.get("details"),
            )
        )

    if level:
        result = [e for e in result if e.level.value == level.upper()]

    return result


@router.get("/config", response_model=ConfigSchema)
async def get_config():
    """설정 조회 (readonly)"""
    settings = get_settings()

    return ConfigSchema(
        is_paper_mode=settings.is_paper_mode,
        daily_loss_limit_safe=settings.daily_loss_limit_safe,
        daily_loss_limit_halt=settings.daily_loss_limit_halt,
        reconcile_interval_sec=settings.reconcile_interval_sec,
        core_min_edge_pct=settings.core_min_edge_pct,
        core_max_position_usd=settings.core_max_position_usd,
        satellite_enabled=settings.satellite_enabled,
        satellite_max_position_usd=settings.satellite_max_position_usd,
        satellite_hard_stop_pct=settings.satellite_hard_stop_pct,
        satellite_trailing_stop_pct=settings.satellite_trailing_stop_pct,
        satellite_time_stop_minutes=settings.satellite_time_stop_minutes,
    )


@router.post("/bot/pause")
async def pause_bot(request: BotCommandRequest):
    """SAFE 모드로 전환"""
    engine = get_engine()

    command = Command(
        command_type=CommandType.PAUSE,
        params={"reason": request.reason or "Manual pause via API"},
    )

    command_id = await engine.command_queue.put(command)

    try:
        result = await engine.command_queue.get_result(command_id, timeout=10.0)
        return {
            "success": result.result.get("success", False) if result.result else False,
            "mode": engine.mode.value,
            "message": "Bot paused" if result.result.get("success") else "Pause failed",
        }
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Command timeout")


@router.post("/bot/resume")
async def resume_bot(request: BotCommandRequest):
    """NORMAL 모드로 복귀"""
    engine = get_engine()

    # HALT에서는 바로 NORMAL로 갈 수 없음
    if engine.mode == TradingMode.HALT:
        raise HTTPException(
            status_code=400,
            detail="Cannot resume directly from HALT. Use /bot/safe-resume first.",
        )

    command = Command(
        command_type=CommandType.RESUME,
        params={"reason": request.reason or "Manual resume via API"},
    )

    command_id = await engine.command_queue.put(command)

    try:
        result = await engine.command_queue.get_result(command_id, timeout=10.0)
        return {
            "success": result.result.get("success", False) if result.result else False,
            "mode": engine.mode.value,
            "message": "Bot resumed" if result.result.get("success") else "Resume failed",
        }
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Command timeout")


@router.post("/bot/flatten")
async def flatten_positions(request: FlattenRequest):
    """긴급 포지션 정리"""
    settings = get_settings()

    # Live 모드에서만 confirm 필수
    if not settings.is_paper_mode and not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Live mode requires confirm=true",
        )

    engine = get_engine()

    command = Command(
        command_type=CommandType.FLATTEN,
        params={
            "strategy": request.strategy.value if request.strategy else None,
            "symbol": request.symbol,
        },
    )

    command_id = await engine.command_queue.put(command)

    try:
        result = await engine.command_queue.get_result(command_id, timeout=30.0)
        return {
            "success": result.result.get("success", False) if result.result else False,
            "message": result.result.get("message", "") if result.result else "",
        }
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Command timeout")


@router.post("/bot/safe-resume")
async def safe_resume_from_halt(request: BotCommandRequest):
    """HALT에서 SAFE로 전환"""
    engine = get_engine()

    if engine.mode != TradingMode.HALT:
        raise HTTPException(
            status_code=400,
            detail="Not in HALT mode",
        )

    success = await engine.risk_engine.safe_resume_from_halt(
        reason=request.reason or "Manual safe resume via API"
    )

    return {
        "success": success,
        "mode": engine.mode.value,
        "message": "Transitioned to SAFE" if success else "Transition failed",
    }


@router.get("/risk/status")
async def get_risk_status():
    """Risk Engine 상태 조회"""
    engine = get_engine()
    return engine.risk_engine.get_status()


@router.get("/market-data")
async def get_market_data():
    """현재 시장 데이터 조회 (디버그용)"""
    engine = get_engine()
    return {
        "btc_regime": engine._btc_regime,
        "symbols": list(engine._market_data.keys()),
        "data": {
            symbol: {
                "spot_price": data.get("spot_price"),
                "perp_price": data.get("perp_price"),
                "funding_rate": data.get("funding_rate"),
                "edge_pct": (data.get("spot_price", 0) - data.get("perp_price", 0)) / data.get("spot_price", 1) if data.get("spot_price") else 0,
            }
            for symbol, data in engine._market_data.items()
        },
        "core_strategy_enabled": engine.core_strategy._enabled,
        "satellite_strategy_enabled": engine.satellite_strategy._enabled,
    }
