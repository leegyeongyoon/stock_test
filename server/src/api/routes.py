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

    # Upbit: 단일 거래소, Binance: spot + perp
    if settings.is_upbit:
        ws_connected = engine.exchange.is_connected
    else:
        ws_connected = engine.spot_exchange.is_connected and engine.perp_exchange.is_connected

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
        "satellite_enabled": settings.satellite_enabled,
        "satellite_max_position_usd": settings.satellite_max_position_usd,
        "satellite_hard_stop_pct": settings.satellite_hard_stop_pct,
        "satellite_trailing_stop_pct": settings.satellite_trailing_stop_pct,
        "satellite_time_stop_minutes": settings.satellite_time_stop_minutes,
    }

    if settings.is_upbit:
        # Upbit 전용 설정
        config.update({
            "min_liquidity_krw": settings.min_liquidity_krw,
            "core_cash_ratio_risk_on": settings.core_cash_ratio_risk_on,
            "core_cash_ratio_neutral": settings.core_cash_ratio_neutral,
            "core_cash_ratio_risk_off": settings.core_cash_ratio_risk_off,
            "core_cash_ratio_volatile": settings.core_cash_ratio_volatile,
            "core_rebalance_interval_min": settings.core_rebalance_interval_min,
            "core_rebalance_threshold": settings.core_rebalance_threshold,
        })
    else:
        # Binance 전용 설정
        config.update({
            "futures_only_mode": settings.futures_only_mode,
            "core_min_edge_pct": settings.core_min_edge_pct,
            "core_max_position_usd": settings.core_max_position_usd,
        })

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

    if settings.is_upbit:
        # Upbit 형식
        return {
            "exchange": "upbit",
            "btc_regime": engine._btc_regime,
            "symbols": list(engine._market_data.keys()),
            "data": {
                symbol: {
                    "price": data.get("price"),
                    "change_rate": data.get("change_rate"),
                    "volume_24h": data.get("volume_24h"),
                    # Satellite 디버그용 필드
                    "rvol": data.get("rvol", 0),
                    "close_pos": data.get("close_pos", 0),
                    "highest_12_5m": data.get("highest_12_5m", 0),
                    "lowest_12_5m": data.get("lowest_12_5m", 0),
                    "vwap": data.get("vwap", 0),
                }
                for symbol, data in engine._market_data.items()
            },
            "core_strategy_enabled": engine.core_strategy._enabled if hasattr(engine, "core_strategy") and engine.core_strategy else False,
            "satellite_strategy_enabled": engine.satellite_strategy._enabled if engine.satellite_strategy else False,
        }
    else:
        # Binance 형식
        return {
            "exchange": "binance",
            "btc_regime": engine._btc_regime,
            "symbols": list(engine._market_data.keys()),
            "data": {
                symbol: {
                    "spot_price": data.get("spot_price"),
                    "perp_price": data.get("perp_price"),
                    "funding_rate": data.get("funding_rate"),
                    "edge_pct": (data.get("spot_price", 0) - data.get("perp_price", 0)) / data.get("spot_price", 1) if data.get("spot_price") else 0,
                    # Satellite 디버그용 필드
                    "rvol": data.get("rvol", 0),
                    "close_pos": data.get("close_pos", 0),
                    "highest_12_5m": data.get("highest_12_5m", 0),
                    "lowest_12_5m": data.get("lowest_12_5m", 0),
                    "vwap": data.get("vwap", 0),
                    "price_change_pct": data.get("price_change_pct", 0),
                }
                for symbol, data in engine._market_data.items()
            },
            "core_strategy_enabled": engine.core_strategy._enabled if engine.core_strategy else False,
            "satellite_strategy_enabled": engine.satellite_strategy._enabled if engine.satellite_strategy else False,
        }


@router.get("/satellite-status")
async def get_satellite_status():
    """Satellite 전략 상세 상태 조회 (디버그용)"""
    engine = get_engine()
    sat = engine.satellite_strategy

    # 현재가로 수익률 계산
    active_positions_detail = {}
    for sym, pos in sat._active_positions.items():
        market_data = engine._market_data.get(sym, {})
        current_price = market_data.get("price", pos.entry_price)
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

        active_positions_detail[sym] = {
            "entry_price": pos.entry_price,
            "current_price": current_price,
            "quantity": pos.quantity,
            "pnl_pct": f"{pnl_pct:.2%}",
            "highest_price": pos.highest_price,
            "trailing_active": pos.trailing_active,
        }

    return {
        "enabled": sat._enabled,
        "long_only_mode": sat._long_only_mode,
        "overheat_threshold": sat.overheat_threshold,
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
        "active_positions_count": len(sat._active_positions),
        "active_positions": active_positions_detail,
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

    if settings.is_upbit:
        # Upbit: KRW 잔고 + 보유 자산
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
    else:
        # Binance: Spot + Perp USDT
        spot_balance = await engine.spot_exchange.get_balance("USDT")
        perp_balance = await engine.perp_exchange.get_balance("USDT")

        return {
            "exchange": "binance",
            "currency": "USDT",
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
    """헤지되지 않은 Perp 포지션 청산 (Binance 전용)

    이 엔드포인트는 Spot 잔고 부족 등으로 인해 헤지되지 않은
    Perp 포지션만 있는 경우에 사용합니다.
    """
    settings = get_settings()
    engine = get_engine()

    # Upbit은 Futures 없음
    if settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Upbit does not support futures - this endpoint is not available",
        )

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
async def test_buy(symbol: str = "BTCUSDT", amount: float = 10000):
    """테스트 매수

    Upbit: KRW 금액으로 매수
    Binance: FUTURES_ONLY 모드에서 Long + Short 헤지

    Args:
        symbol: 심볼 (Upbit: KRW-BTC, Binance: BTCUSDT)
        amount: 금액 (Upbit: KRW, Binance: quantity)
    """
    settings = get_settings()
    engine = get_engine()

    if settings.is_upbit:
        # Upbit 테스트 매수 (KRW 금액으로)
        results = {"buy": None, "errors": []}

        # KRW 금액으로 시장가 매수
        buy_result = await engine.exchange.place_order(
            symbol=symbol if symbol.startswith("KRW-") else f"KRW-{symbol.replace('USDT', '')}",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=None,  # 수량 대신
            price=amount,  # KRW 금액 사용
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

    else:
        # Binance 테스트 (기존 로직)
        if not settings.is_paper_mode:
            raise HTTPException(status_code=400, detail="Only available in paper mode")

        quantity = amount  # Binance는 수량 사용
        results = {"long": None, "short": None, "errors": []}

        if settings.futures_only_mode:
            # Futures Long
            long_result = await engine.perp_exchange.place_order(
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=quantity,
            )

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

            # Futures Short (헤지)
            short_result = await engine.perp_exchange.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=quantity,
            )

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
            results["errors"].append("Spot+Perp mode requires Spot balance")

        engine.add_event(
            level="INFO",
            event_type="ORDER",
            message=f"Test buy executed: {symbol}",
            details=results,
        )

        return {
            "success": len(results["errors"]) == 0,
            "exchange": "binance",
            "symbol": symbol,
            "quantity": quantity,
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


# ============================================================
# Attack Module API
# ============================================================


class AttackModeRequest(BaseModel):
    """Attack 모드 변경 요청"""

    mode: str  # OFF, NORMAL, PLUS, MAX


@router.get("/attack/status")
async def get_attack_status():
    """Attack 전략 상태 조회

    Returns:
        mode: 현재 Attack 모드
        enabled: 활성화 여부
        active_positions: 활성 Attack 포지션 수
        pending_signals: 대기 중인 시그널 수
        gate_status: 리스크 게이트 상태
        stats: 통계
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Attack module is only available for Upbit",
        )

    if not hasattr(engine, "attack_strategy"):
        raise HTTPException(
            status_code=503,
            detail="Attack strategy not initialized",
        )

    return engine.attack_strategy.get_status()


@router.post("/attack/set-mode")
async def set_attack_mode(request: AttackModeRequest):
    """Attack 모드 변경

    Args:
        mode: OFF, NORMAL, PLUS, MAX 중 하나

    OFF: Attack 비활성화
    NORMAL: 기본 모드 (리스크 0.5%/트레이드)
    PLUS: 공격적 모드 (리스크 0.6%/트레이드)
    MAX: 최대 공격 모드 (리스크 0.7%/트레이드)

    Returns:
        변경 후 Attack 상태
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Attack module is only available for Upbit",
        )

    mode_upper = request.mode.upper()
    if mode_upper not in ["OFF", "NORMAL", "PLUS", "MAX"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {request.mode}. Must be one of: OFF, NORMAL, PLUS, MAX",
        )

    if not hasattr(engine, "attack_strategy"):
        raise HTTPException(
            status_code=503,
            detail="Attack strategy not initialized",
        )

    # 모드 변경
    engine.attack_strategy.set_attack_mode(mode_upper)

    # 이벤트 기록
    engine.add_event(
        level="INFO",
        event_type="ATTACK_MODE_CHANGE",
        message=f"Attack mode changed to {mode_upper}",
        details={"mode": mode_upper},
    )

    return {
        "success": True,
        "mode": mode_upper,
        "status": engine.attack_strategy.get_status(),
    }


@router.get("/attack/score/{symbol}")
async def get_attack_score(symbol: str):
    """특정 심볼의 Attack Score 조회

    Args:
        symbol: 심볼 (예: KRW-BTC)

    Returns:
        Attack Score 상세 정보
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Attack module is only available for Upbit",
        )

    if not hasattr(engine, "attack_strategy"):
        raise HTTPException(
            status_code=503,
            detail="Attack strategy not initialized",
        )

    # 심볼 형식 변환
    if not symbol.startswith("KRW-"):
        symbol = f"KRW-{symbol.replace('USDT', '').upper()}"

    # 시장 데이터 가져오기
    market_data = engine._market_data.get(symbol, {})
    if not market_data:
        raise HTTPException(
            status_code=404,
            detail=f"Market data not found for {symbol}",
        )

    # Attack Score 계산
    score_result = engine.attack_strategy.get_attack_score(market_data)

    return {
        "symbol": symbol,
        "total_score": score_result.total_score,
        "level": score_result.level,
        "target_allocation": score_result.target_allocation,
        "components": [c.to_dict() for c in score_result.components],
        "timestamp": score_result.timestamp.isoformat(),
    }


@router.get("/attack/positions")
async def get_attack_positions():
    """활성 Attack 포지션 조회

    Returns:
        active_positions: 활성 포지션 목록
        pending_signals: 대기 중인 시그널 목록
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Attack module is only available for Upbit",
        )

    if not hasattr(engine, "attack_strategy"):
        raise HTTPException(
            status_code=503,
            detail="Attack strategy not initialized",
        )

    return {
        "active_positions": engine.attack_strategy.get_active_positions(),
        "pending_signals": engine.attack_strategy.get_pending_signals(),
    }


@router.get("/attack/gate-check/{symbol}")
async def check_attack_gate(symbol: str):
    """Attack Gate 체크 (진입 가능 여부 확인)

    Args:
        symbol: 심볼 (예: KRW-BTC)

    Returns:
        Gate 체크 결과 및 허용 여부
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Attack module is only available for Upbit",
        )

    if not hasattr(engine, "attack_strategy"):
        raise HTTPException(
            status_code=503,
            detail="Attack strategy not initialized",
        )

    # 심볼 형식 변환
    if not symbol.startswith("KRW-"):
        symbol = f"KRW-{symbol.replace('USDT', '').upper()}"

    # 시장 데이터 가져오기
    market_data = engine._market_data.get(symbol, {})
    if not market_data:
        raise HTTPException(
            status_code=404,
            detail=f"Market data not found for {symbol}",
        )

    # Attack Score 계산
    score_result = engine.attack_strategy.get_attack_score(market_data)

    # Attack Gate 체크
    from src.risk.attack_gate import get_attack_gate

    attack_gate = get_attack_gate()
    gate_result = attack_gate.check(
        attack_level=score_result.level,
        dd_tier=engine.attack_strategy._dd_tier,
        regime=engine.attack_strategy._btc_regime,
        daily_loss=engine.attack_strategy._daily_loss,
        is_volatile=engine.attack_strategy._btc_is_volatile,
        change_rate=market_data.get("change_rate", 0),
        attack_mode=engine.attack_strategy.attack_mode,
    )

    return {
        "symbol": symbol,
        "score": {
            "total": score_result.total_score,
            "level": score_result.level,
        },
        "gate_result": gate_result.to_dict(),
    }


# ===== Pullback 전략 API (눌림목 매수) =====


class PullbackModeRequest(BaseModel):
    """Pullback 모드 변경 요청"""

    mode: str  # OFF, SAFE, NORMAL, AGGRESSIVE


@router.get("/pullback/status")
async def get_pullback_status():
    """Pullback 전략 상태 조회

    Returns:
        mode: 현재 Pullback 모드
        enabled: 활성화 여부
        active_positions: 활성 포지션 수
        pending_signals: 대기 중인 시그널 수
        positions: 포지션 상세
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Pullback module is only available for Upbit",
        )

    if not hasattr(engine, "pullback_strategy"):
        raise HTTPException(
            status_code=503,
            detail="Pullback strategy not initialized",
        )

    return engine.pullback_strategy.get_status()


@router.post("/pullback/set-mode")
async def set_pullback_mode(request: PullbackModeRequest):
    """Pullback 모드 변경

    Args:
        mode: OFF, SAFE, NORMAL, AGGRESSIVE 중 하나

    OFF: Pullback 비활성화
    SAFE: 안전 모드 (Level 3만 진입)
    NORMAL: 일반 모드 (Level 2+ 진입)
    AGGRESSIVE: 공격 모드 (Level 1+ 진입)

    Returns:
        변경 후 Pullback 상태
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Pullback module is only available for Upbit",
        )

    mode_upper = request.mode.upper()
    if mode_upper not in ["OFF", "SAFE", "NORMAL", "AGGRESSIVE"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {request.mode}. Must be one of: OFF, SAFE, NORMAL, AGGRESSIVE",
        )

    if not hasattr(engine, "pullback_strategy"):
        raise HTTPException(
            status_code=503,
            detail="Pullback strategy not initialized",
        )

    # 모드 변경
    engine.pullback_strategy.set_mode(mode_upper)

    # 이벤트 기록
    engine.add_event(
        level="INFO",
        event_type="PULLBACK_MODE_CHANGE",
        message=f"Pullback mode changed to {mode_upper}",
        details={"mode": mode_upper},
    )

    return {
        "success": True,
        "mode": mode_upper,
        "status": engine.pullback_strategy.get_status(),
    }


@router.get("/pullback/positions")
async def get_pullback_positions():
    """활성 Pullback 포지션 조회

    Returns:
        active_positions: 활성 포지션 목록
        pending_signals: 대기 중인 시그널 목록
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Pullback module is only available for Upbit",
        )

    if not hasattr(engine, "pullback_strategy"):
        raise HTTPException(
            status_code=503,
            detail="Pullback strategy not initialized",
        )

    all_positions = engine.pullback_strategy.get_all_positions()
    status = engine.pullback_strategy.get_status()

    return {
        "active_positions": {sym: pos.to_dict() for sym, pos in all_positions.items()},
        "pending_signals": status.get("signals", {}),
    }


@router.get("/pullback/score/{symbol}")
async def get_pullback_score(symbol: str):
    """특정 심볼의 Pullback Score 조회

    Args:
        symbol: 심볼 (예: KRW-BTC)

    Returns:
        Pullback Score 상세 정보
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Pullback module is only available for Upbit",
        )

    if not hasattr(engine, "pullback_strategy"):
        raise HTTPException(
            status_code=503,
            detail="Pullback strategy not initialized",
        )

    # 심볼 형식 변환
    if not symbol.startswith("KRW-"):
        symbol = f"KRW-{symbol.replace('USDT', '').upper()}"

    # 시장 데이터 가져오기
    market_data = engine._market_data.get(symbol, {})
    if not market_data:
        raise HTTPException(
            status_code=404,
            detail=f"Market data not found for {symbol}",
        )

    # 호가창 정보 추가 (실시간은 아님)
    enhanced_market_data = {
        **market_data,
        "highest_24h": market_data.get("high_20", market_data.get("price", 0) * 1.01),
        "lowest_24h": market_data.get("low_20", market_data.get("price", 0) * 0.99),
        "change_rate": market_data.get("price_change_pct", 0) / 100,
        "change_1h": market_data.get("price_change_pct", 0) / 100 / 24,
        "sma_20": market_data.get("vwap", 0),
        "bid_volume": 0,
        "ask_volume": 0,
    }

    # Pullback Score 계산
    from src.strategies.pullback_score import get_pullback_score_calculator

    calculator = get_pullback_score_calculator()
    score_result = calculator.calculate(enhanced_market_data)

    return score_result.to_dict()


# ===== SurgeDetector 급등 시작 감지 API =====


@router.get("/surge/status")
async def get_surge_status():
    """SurgeDetector 상태 조회

    Returns:
        enabled: 활성화 여부
        active_signals: 활성 신호 수
        active_positions: 활성 포지션 수
        cooldown_count: 쿨다운 중인 심볼 수
        signals: 활성 신호 목록
        positions: 활성 포지션 목록
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="SurgeDetector is only available for Upbit",
        )

    # SurgeDetector는 ignition_strategy 내부에 있거나 별도 싱글톤
    from src.strategies.ignition import get_surge_detector

    surge_detector = get_surge_detector()
    return {
        "enabled": True,
        **surge_detector.get_status(),
    }


@router.get("/surge/diagnostic")
async def get_surge_diagnostic():
    """SurgeDetector 진단 정보 조회 (1분봉 데이터 상태 등)"""
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="SurgeDetector is only available for Upbit",
        )

    from src.data.candle_manager import get_candle_manager
    cm = get_candle_manager()

    # 주요 심볼들의 1분봉 상태 확인
    test_symbols = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-ZIL", "KRW-SOL"]
    candle_status = {}

    for symbol in test_symbols:
        candles_1m = cm.get_candles(symbol, "1m", 10)
        candles_5m = cm.get_candles(symbol, "5m", 5)
        candle_status[symbol] = {
            "candles_1m": len(candles_1m) if candles_1m else 0,
            "candles_5m": len(candles_5m) if candles_5m else 0,
        }
        if candles_1m:
            last = candles_1m[-1]
            candle_status[symbol]["last_1m_close"] = last.close
            candle_status[symbol]["last_1m_time"] = str(last.open_time) if hasattr(last, "open_time") else "N/A"

    # 엔진 상태
    return {
        "candle_manager_status": candle_status,
        "engine_running": engine._running if hasattr(engine, "_running") else False,
        "is_upbit": settings.is_upbit,
        "candle_update_counter": getattr(engine, "_candle_update_counter", -1),
    }


@router.get("/surge/signals")
async def get_surge_signals():
    """활성 급등 신호 조회

    Returns:
        signals: 급등 신호 목록 (만료되지 않은 것만)
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="SurgeDetector is only available for Upbit",
        )

    from src.strategies.ignition import get_surge_detector

    surge_detector = get_surge_detector()
    signals = surge_detector.get_active_signals()

    return {"signals": [s.to_dict() for s in signals]}


@router.get("/surge/positions")
async def get_surge_positions():
    """급등 포지션 조회

    Returns:
        positions: 급등 전략으로 진입한 포지션 목록
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="SurgeDetector is only available for Upbit",
        )

    from src.strategies.ignition import get_surge_detector

    surge_detector = get_surge_detector()
    positions = surge_detector.get_positions()

    return {
        "positions": [
            {
                "symbol": p.symbol,
                "entry_price": p.entry_price,
                "quantity": p.quantity,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "highest_price": p.highest_price,
                "trailing_stop": p.trailing_stop,
            }
            for p in positions
        ]
    }


@router.get("/surge/candidates")
async def get_surge_candidates(
    min_score: float = Query(70.0, description="최소 점수 (0-100)"),
    limit: int = Query(20, description="최대 반환 개수"),
):
    """급등 조건 근접 종목 조회

    각 조건별 달성률(%)을 계산하여 급등 임박 종목 반환

    조건 및 가중치:
    - 1분 변화율 (30%): 목표 1.5%
    - 거래량 배수 (30%): 목표 3배 (전일급등주는 3.75배)
    - VolOverheat (15%): 8배 초과 차단
    - Anti-Chase (25%): 구조적 거리 기반

    Returns:
        candidates: 점수순으로 정렬된 근접 종목 목록
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="SurgeDetector is only available for Upbit",
        )

    from src.strategies.ignition import get_surge_detector
    from src.data.candle_manager import get_candle_manager

    surge_detector = get_surge_detector()
    cm = get_candle_manager()

    # 심볼 및 시장 데이터 수집
    symbols = engine.symbol_manager.get_all_symbols()
    market_data_map = {}

    for symbol in symbols:
        # 1분봉에서 현재가 가져오기
        candles = cm.get_candles(symbol, "1m", 1)
        if candles and len(candles) > 0:
            price = candles[-1].close
            if price and price > 0:
                # 한글명/영문명 조회
                names = engine.symbol_manager.get_symbol_names(symbol)
                market_data_map[symbol] = {
                    "price": price,
                    "signed_change_rate": 0,
                    "korean_name": names.get("korean_name", ""),
                    "english_name": names.get("english_name", ""),
                }

    candidates = surge_detector.get_surge_candidates(
        symbols=symbols,
        market_data_map=market_data_map,
        min_score=min_score,
        limit=limit,
    )

    return {
        "candidates": [c.to_dict() for c in candidates],
        "total_scanned": len(symbols),
        "filtered_count": len(candidates),
    }


# ===== Ignition Watchlist API =====


@router.get("/ignition/watchlist")
async def get_ignition_watchlist():
    """Ignition Watchlist 조회 - 현재 감시 중인 종목

    Returns:
        watchlist: 감시 중인 종목 목록 (Setup 통과)
        count: 종목 수
        mode: 현재 Ignition 모드
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Ignition module is only available for Upbit",
        )

    if not hasattr(engine, "ignition_strategy") or engine.ignition_strategy is None:
        return {
            "watchlist": [],
            "count": 0,
            "mode": settings.ignition_mode,
            "message": "Ignition strategy not initialized",
        }

    watchlist = engine.ignition_strategy.get_watchlist()

    return {
        "watchlist": [
            {
                "symbol": c.symbol,
                "score": c.total_score,
                "added_at": c.added_at.isoformat() if hasattr(c, "added_at") else None,
                "setup_scores": c.scores if hasattr(c, "scores") else {},
            }
            for c in watchlist
        ],
        "count": len(watchlist),
        "mode": settings.ignition_mode,
    }


@router.post("/ignition/scan")
async def trigger_ignition_scan():
    """Ignition Setup 스캔 수동 트리거 (테스트용)"""
    engine = get_engine()
    settings = get_settings()

    if not hasattr(engine, "ignition_strategy") or engine.ignition_strategy is None:
        return {"error": "Ignition strategy not initialized"}

    # 심볼 가져오기
    qualified_symbols = engine.symbol_manager.get_qualified_symbols()

    if not qualified_symbols:
        return {
            "error": "No qualified symbols",
            "all_symbols_count": len(engine.symbol_manager.get_all_symbols()),
        }

    # 스캔 실행
    import asyncio
    candidates = await engine.ignition_strategy.scan_setups(
        symbols=qualified_symbols,
        market_data_map=engine._market_data,
    )

    return {
        "scanned": len(qualified_symbols),
        "candidates": len(candidates) if candidates else 0,
        "watchlist": len(engine.ignition_strategy.get_watchlist()),
        "symbols_sample": qualified_symbols[:5],
    }


@router.get("/ignition/status")
async def get_ignition_status():
    """Ignition 전략 전체 상태 조회

    Returns:
        mode: Ignition 모드
        watchlist_count: Watchlist 종목 수
        active_positions: 활성 포지션 수
        settings: 주요 설정값
    """
    engine = get_engine()
    settings = get_settings()

    if not settings.is_upbit:
        raise HTTPException(
            status_code=400,
            detail="Ignition module is only available for Upbit",
        )

    watchlist_count = 0
    active_positions = 0

    if hasattr(engine, "ignition_strategy") and engine.ignition_strategy is not None:
        watchlist_count = len(engine.ignition_strategy.get_watchlist())
        active_positions = len(engine.ignition_strategy.get_all_positions())

    return {
        "mode": settings.ignition_mode,
        "watchlist_count": watchlist_count,
        "active_positions": active_positions,
        "settings": {
            "setup_scan_interval_min": settings.ignition_setup_scan_interval_min,
            "watchlist_max": settings.ignition_watchlist_max,
            "setup_min_score": settings.ignition_setup_min_score,
            "min_position_pct_l3": getattr(settings, "ignition_min_position_pct_l3", 0.50),
            "max_position_pct_l3": getattr(settings, "ignition_max_position_pct_l3", 0.60),
        },
    }
