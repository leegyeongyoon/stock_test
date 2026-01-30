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
    OrderType,
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
    engine = get_engine()
    orders = engine.get_orders(limit=limit)

    result = []
    for o in orders:
        # status 필터
        if status and o.get("status") != status.upper():
            continue

        result.append(
            OrderSchema(
                order_id=o["order_id"],
                symbol=o["symbol"],
                strategy=o["strategy"],
                side=OrderSide(o["side"]),
                order_type=OrderType(o["order_type"]),
                quantity=o["quantity"],
                price=o.get("price"),
                status=o["status"],
                filled_quantity=o.get("filled_quantity", 0),
                avg_fill_price=o.get("avg_fill_price"),
                created_at=datetime.fromisoformat(o["created_at"]),
            )
        )

    return result


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
        futures_only_mode=settings.futures_only_mode,
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
                # Satellite 디버그용 추가 필드
                "rvol": data.get("rvol", 0),
                "close_pos": data.get("close_pos", 0),
                "highest_12_5m": data.get("highest_12_5m", 0),
                "lowest_12_5m": data.get("lowest_12_5m", 0),
                "vwap": data.get("vwap", 0),
                "price_change_pct": data.get("price_change_pct", 0),
            }
            for symbol, data in engine._market_data.items()
        },
        "core_strategy_enabled": engine.core_strategy._enabled,
        "satellite_strategy_enabled": engine.satellite_strategy._enabled,
    }


@router.get("/satellite-status")
async def get_satellite_status():
    """Satellite 전략 상세 상태 조회 (디버그용)"""
    engine = get_engine()
    sat = engine.satellite_strategy
    return {
        "enabled": sat._enabled,
        "btc_regime": sat._btc_regime.value,
        "btc_is_volatile": sat._btc_is_volatile,
        "confirmation_enabled": sat.confirmation_enabled,
        "rvol_threshold": sat.rvol_threshold,
        "close_pos_threshold": sat.close_pos_threshold,
        "pending_signals": {
            sym: {
                "side": sig.side.value,
                "status": sig.status.value,
                "detected_at": sig.detected_at.isoformat(),
            }
            for sym, sig in sat._pending_signals.items()
        },
        "active_positions": len(sat._active_positions),
    }


@router.get("/symbol-info/{symbol}")
async def get_symbol_info(symbol: str):
    """심볼 정보 조회 (디버그용)"""
    engine = get_engine()
    info = engine.symbol_manager.get_symbol_info(symbol)
    if info:
        return {
            "symbol": info.symbol,
            "price_precision": info.price_precision,
            "quantity_precision": info.quantity_precision,
            "min_notional": info.min_notional,
            "step_size": info.step_size,
            "tick_size": info.tick_size,
            "volume_24h": info.volume_24h,
            "in_qualified_list": engine.symbol_manager.is_qualified(symbol),
        }
    return {"error": f"Symbol {symbol} not found in SymbolManager cache"}


@router.get("/balance")
async def get_balance():
    """Spot/Perp 잔고 조회"""
    engine = get_engine()

    spot_balance = await engine.spot_exchange.get_balance("USDT")
    perp_balance = await engine.perp_exchange.get_balance("USDT")

    return {
        "spot": {
            "total": spot_balance.total if spot_balance else 0,
            "free": spot_balance.free if spot_balance else 0,
            "locked": spot_balance.locked if spot_balance else 0,
        },
        "perp": {
            "total": perp_balance.total if perp_balance else 0,
            "free": perp_balance.free if perp_balance else 0,
            "locked": perp_balance.locked if perp_balance else 0,
        },
    }


@router.post("/bot/close-unhedged")
async def close_unhedged_positions(confirm: bool = False):
    """헤지되지 않은 Perp 포지션 청산

    이 엔드포인트는 Spot 잔고 부족 등으로 인해 헤지되지 않은
    Perp 포지션만 있는 경우에 사용합니다.
    """
    settings = get_settings()
    engine = get_engine()

    if not settings.is_paper_mode and not confirm:
        raise HTTPException(
            status_code=400,
            detail="Live mode requires confirm=true to close positions",
        )

    # Perp 포지션 조회
    perp_positions = await engine.perp_exchange.get_positions()

    if not perp_positions:
        return {
            "success": True,
            "message": "No positions to close",
            "closed": [],
        }

    closed_positions = []
    errors = []

    for position in perp_positions:
        symbol = position.symbol
        side = position.side  # LONG or SHORT
        quantity = position.quantity

        # 포지션 청산을 위한 반대 주문
        close_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY

        result = await engine.perp_exchange.place_order(
            symbol=symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            quantity=abs(quantity),
            reduce_only=True,
        )

        if result.success:
            closed_positions.append({
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "close_price": result.avg_price,
            })
            engine.add_event(
                level="INFO",
                event_type="ORDER",
                message=f"Closed unhedged position: {symbol} {side} {quantity}",
                details={
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "price": result.avg_price,
                },
            )
        else:
            errors.append({
                "symbol": symbol,
                "error": result.error,
            })

    return {
        "success": len(errors) == 0,
        "message": f"Closed {len(closed_positions)} positions" if closed_positions else "No positions closed",
        "closed": closed_positions,
        "errors": errors if errors else None,
    }


@router.post("/test/buy")
async def test_buy(symbol: str = "BTCUSDT", quantity: float = 0.001):
    """테스트 매수 (FUTURES_ONLY 모드: Long + Short 헤지)

    테스트넷에서 수동으로 포지션을 열어 대시보드 확인용
    """
    settings = get_settings()
    engine = get_engine()

    if not settings.is_paper_mode:
        raise HTTPException(status_code=400, detail="Only available in paper mode")

    results = {"long": None, "short": None, "errors": []}

    # FUTURES_ONLY 모드: Long + Short
    if settings.futures_only_mode:
        # 1. Futures Long
        long_result = await engine.perp_exchange.place_order(
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=quantity,
        )

        # 주문 기록 추가
        engine.add_order(
            symbol=symbol,
            strategy="CORE",
            side="BUY",
            order_type="MARKET",
            quantity=quantity,
            status="FILLED" if long_result.success else "REJECTED",
            filled_qty=long_result.filled_qty if long_result.success else 0,
            avg_fill_price=long_result.avg_price if long_result.success else None,
        )

        if long_result.success:
            results["long"] = {
                "filled_qty": long_result.filled_qty,
                "avg_price": long_result.avg_price,
            }
        else:
            results["errors"].append(f"Long failed: {long_result.error}")
            return results

        # 2. Futures Short (헤지)
        short_result = await engine.perp_exchange.place_order(
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=quantity,
        )

        # 주문 기록 추가
        engine.add_order(
            symbol=symbol,
            strategy="CORE",
            side="SELL",
            order_type="MARKET",
            quantity=quantity,
            status="FILLED" if short_result.success else "REJECTED",
            filled_qty=short_result.filled_qty if short_result.success else 0,
            avg_fill_price=short_result.avg_price if short_result.success else None,
        )

        if short_result.success:
            results["short"] = {
                "filled_qty": short_result.filled_qty,
                "avg_price": short_result.avg_price,
            }
        else:
            results["errors"].append(f"Short failed: {short_result.error}")

    else:
        # Spot + Perp 모드
        results["errors"].append("Spot+Perp mode requires Spot balance")

    engine.add_event(
        level="INFO",
        event_type="ORDER",
        message=f"Test buy executed: {symbol}",
        details=results,
    )

    return {
        "success": len(results["errors"]) == 0,
        "symbol": symbol,
        "quantity": quantity,
        "results": results,
    }
