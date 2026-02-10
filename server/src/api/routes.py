"""FastAPI REST 엔드포인트"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from pydantic import BaseModel

from src.config import Settings, get_settings
from src.engine.command_queue import Command, CommandType
from src.models.user_mode import UserMode
from src.services.mode_manager import get_mode_manager
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
    settings = get_settings()

    ws_connected = engine.exchange.is_connected

    return HealthSchema(
        status="healthy" if engine.is_running else "degraded",
        db_connected=True,  # TODO: 실제 DB 체크
        engine_running=engine.is_running,
        ws_connected=ws_connected,
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


@router.get("/config")
async def get_config():
    """설정 조회 (readonly)"""
    settings = get_settings()

    config = {
        "exchange_type": settings.exchange_type,
        "is_paper_mode": settings.is_paper_mode,
        "daily_loss_limit_safe": settings.daily_loss_limit_safe,
        "daily_loss_limit_halt": settings.daily_loss_limit_halt,
        "reconcile_interval_sec": settings.reconcile_interval_sec,
        "v3_enabled": settings.v3_enabled,
        "v3_max_positions": settings.v3_max_positions,
        "min_liquidity_krw": settings.min_liquidity_krw,
    }

    return config


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


@router.get("/capital-profile/status")
async def get_capital_profile_status():
    """Capital Profile 상태 조회

    Returns:
        enabled: 활성화 여부
        current_mode: 현재 모드 (GROWTH/PRESERVE)
        config: 현재 적용되는 설정
            - risk_per_trade_min: 최소 리스크
            - risk_per_trade_max: 최대 리스크
            - total_risk_limit: 총 리스크 한도
            - allowed_attack_levels: 허용 Attack Level
            - allowed_attack_modes: 허용 Attack Mode
            - slippage_guard_multiplier: 슬리피지 가드 배수
        hysteresis: 히스테리시스 상태
            - consecutive_preserve_days: Preserve 조건 연속 일수
            - consecutive_growth_days: Growth 조건 연속 일수
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Capital Profile is only available for Upbit",
        )

    if not hasattr(engine, "capital_profile"):
        raise HTTPException(
            status_code=503,
            detail="Capital Profile not initialized",
        )

    return engine.capital_profile.get_status_dict()


@router.get("/market-data")
async def get_market_data():
    """현재 시장 데이터 조회 (디버그용)"""
    engine = get_engine()
    settings = get_settings()

    return {
        "exchange": "upbit",
        "btc_regime": engine._btc_regime,
        "v3_enabled": engine.v3_enabled,
        "symbols": list(engine._market_data.keys()),
        "data": {
            symbol: {
                "price": data.get("price"),
                "volume_24h": data.get("volume_24h"),
                "rvol": data.get("rvol", 0),
                "price_change_pct": data.get("price_change_pct", 0),
            }
            for symbol, data in engine._market_data.items()
        },
    }


@router.get("/v3/status")
async def get_v3_status():
    """v3 전략 상태 조회"""
    engine = get_engine()

    strategies_info = []
    for strat in engine.v3_strategies:
        positions = strat.get_all_positions()
        pos_details = {}
        for sym, pos in positions.items():
            market_data = engine._market_data.get(sym, {})
            current_price = market_data.get("price", pos.entry_price)
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

            pos_details[sym] = {
                "entry_price": pos.entry_price,
                "current_price": current_price,
                "quantity": pos.quantity,
                "pnl_pct": f"{pnl_pct:.2%}",
                "highest_price": pos.highest_price,
                "trailing_active": pos.trailing_active,
            }

        strategies_info.append({
            "name": strat.name,
            "positions_count": len(positions),
            "positions": pos_details,
        })

    total_positions = sum(s["positions_count"] for s in strategies_info)

    return {
        "v3_enabled": engine.v3_enabled,
        "v3_max_positions": engine.v3_max_positions,
        "total_positions": total_positions,
        "strategies": strategies_info,
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
    """잔고 조회 (Upbit: KRW, Binance: USDT)"""
    engine = get_engine()
    settings = get_settings()

    krw_balance = await engine.exchange.get_balance("KRW")
    all_balances = await engine.exchange.get_all_balances()

    return {
        "exchange": "upbit",
        "currency": "KRW",
        "cash": {
            "total": krw_balance.total if krw_balance else 0,
            "free": krw_balance.free if krw_balance else 0,
            "locked": krw_balance.locked if krw_balance else 0,
        },
        "assets": [
            {
                "currency": b.asset,
                "balance": b.total,
                "locked": b.locked,
                "avg_buy_price": b.avg_buy_price,
            }
            for b in all_balances
            if b.asset != "KRW" and b.total > 0
        ],
    }


@router.post("/test/buy")
async def test_buy(symbol: str = "KRW-BTC", amount: float = 10000):
    """테스트 매수 (Upbit KRW)

    Args:
        symbol: 심볼 (KRW-BTC)
        amount: KRW 금액
    """
    engine = get_engine()

    results = {"buy": None, "errors": []}

    buy_result = await engine.exchange.place_order(
        symbol=symbol if symbol.startswith("KRW-") else f"KRW-{symbol}",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=None,
        price=amount,
    )

    if buy_result.success:
        results["buy"] = {
            "filled_qty": buy_result.filled_qty,
            "avg_price": buy_result.avg_price,
            "amount_krw": amount,
        }
        engine.add_event(
            level="INFO",
            event_type="ORDER",
            message=f"Test buy executed: {symbol} {amount:,.0f} KRW",
            details=results,
        )
    else:
        results["errors"].append(f"Buy failed: {buy_result.error}")

    return {
        "success": len(results["errors"]) == 0,
        "exchange": "upbit",
        "symbol": symbol,
        "amount_krw": amount,
        "results": results,
    }


@router.post("/close-all")
async def close_all_positions(confirm: bool = False):
    """모든 포지션 청산 (Upbit 전용)

    Args:
        confirm: True로 설정해야 실제 청산 실행
    """
    settings = get_settings()
    engine = get_engine()

    if not settings.is_upbit:
        raise HTTPException(status_code=400, detail="This endpoint is for Upbit only")

    if not confirm:
        # 청산할 포지션 목록만 반환
        positions = engine.get_positions()
        return {
            "success": False,
            "message": "Set confirm=true to execute. Preview:",
            "positions_to_close": [
                {"symbol": p["symbol"], "quantity": p["quantity"], "notional": p["notional"]}
                for p in positions
            ],
            "total_count": len(positions),
        }

    # 실제 청산 실행
    all_balances = await engine.exchange.get_all_balances()
    closed = []
    errors = []

    for balance in all_balances:
        if balance.asset == "KRW" or balance.total <= 0:
            continue

        symbol = f"KRW-{balance.asset}"

        try:
            result = await engine.exchange.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=balance.total,
            )

            if result.success:
                closed.append({
                    "symbol": symbol,
                    "quantity": balance.total,
                    "price": result.avg_price,
                    "amount": balance.total * (result.avg_price or 0),
                })
                engine.add_event(
                    level="INFO",
                    event_type="ORDER",
                    message=f"Closed position: {symbol}",
                    details={"quantity": balance.total, "price": result.avg_price},
                )
            else:
                errors.append({"symbol": symbol, "error": result.error})
        except Exception as e:
            errors.append({"symbol": symbol, "error": str(e)})

    return {
        "success": len(errors) == 0,
        "message": f"Closed {len(closed)} positions",
        "closed": closed,
        "errors": errors if errors else None,
    }


# ============================================================
# User Mode API
# ============================================================


class UserModeRequest(BaseModel):
    """유저 모드 변경 요청"""

    mode: str


@router.get("/user/mode")
async def get_user_mode():
    """현재 유저 모드 상태 조회

    Returns:
        requested_mode: 사용자가 요청한 모드
        effective_mode: 실제 적용 모드 (자동 다운그레이드 반영)
        downgrade_reason: 다운그레이드 사유 (있을 경우)
        config: 현재 적용되는 설정
    """
    mode_manager = get_mode_manager()
    return mode_manager.get_status_dict()


@router.post("/user/mode")
async def set_user_mode(request: UserModeRequest):
    """유저 모드 변경

    Args:
        mode: SAFE, BALANCED, AGGRESSIVE, PAPER 중 하나

    Returns:
        변경 후 모드 상태
    """
    try:
        mode = UserMode(request.mode.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {request.mode}. Must be one of: SAFE, BALANCED, AGGRESSIVE, PAPER",
        )

    mode_manager = get_mode_manager()
    engine = get_engine()

    # 모드 변경
    mode_manager.set_mode(mode)

    # 이벤트 기록
    engine.add_event(
        level="INFO",
        event_type="MODE_CHANGE",
        message=f"User mode changed to {mode.value}",
        details=mode_manager.get_status_dict(),
    )

    return mode_manager.get_status_dict()

