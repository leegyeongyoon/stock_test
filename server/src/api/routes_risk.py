"""Risk Overlay API 라우트"""

from fastapi import APIRouter, HTTPException

from src.risk.config import get_risk_config
from src.risk.risk_overlay import RiskMode
from src.strategies.core_safety import get_core_safety_guard

router = APIRouter(prefix="/risk", tags=["Risk Management"])

# TradingEngine 참조 (app.py에서 설정)
_engine = None


def set_engine(engine):
    """TradingEngine 참조 설정"""
    global _engine
    _engine = engine


def get_engine():
    """TradingEngine 참조 조회"""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    return _engine


@router.get("/overlay")
async def get_risk_overlay_status():
    """
    Risk Overlay 상태 조회

    우선순위 체인:
    1. SYSTEM SAFETY
    2. TAIL RISK
    3. MARKET REGIME
    """
    engine = get_engine()
    overlay = engine.risk_overlay

    return {
        "success": True,
        "status": overlay.get_summary(),
    }


@router.get("/decision")
async def get_risk_decision():
    """
    현재 리스크 의사결정 조회
    """
    engine = get_engine()
    overlay = engine.risk_overlay

    decision = overlay.get_decision()
    if not decision:
        return {
            "success": True,
            "decision": None,
            "message": "No evaluation performed yet",
        }

    return {
        "success": True,
        "decision": decision.to_dict(),
    }


@router.get("/exec-health")
async def get_exec_health():
    """
    실행 건강도 조회
    """
    engine = get_engine()
    health = engine.exec_health.get_health()

    return {
        "success": True,
        "health": health.to_dict(),
    }


@router.get("/drawdown")
async def get_drawdown_state():
    """
    드로우다운 상태 조회
    """
    engine = get_engine()
    dd_state = engine.risk_overlay.dd_tracker.get_state()

    if not dd_state:
        return {
            "success": True,
            "state": None,
            "message": "No equity data yet",
        }

    return {
        "success": True,
        "state": dd_state.to_dict(),
    }


@router.get("/correlation")
async def get_correlation_state():
    """
    상관관계 상태 조회
    """
    engine = get_engine()
    corr_guard = engine.risk_overlay.correlation_guard

    if not corr_guard:
        return {
            "success": True,
            "state": None,
            "message": "Correlation guard not initialized",
        }

    state = corr_guard.get_guard_action()
    return {
        "success": True,
        "state": state.to_dict(),
    }


@router.get("/config")
async def get_risk_config_values():
    """
    리스크 설정값 조회
    """
    config = get_risk_config()

    return {
        "success": True,
        "config": {
            "drawdown": {
                "safe_threshold": config.risk_dd_safe_threshold,
                "reduce_threshold": config.risk_dd_reduce_threshold,
                "halt_threshold": config.risk_dd_halt_threshold,
            },
            "daily_loss": {
                "safe": config.daily_loss_safe,
                "halt": config.daily_loss_halt,
            },
            "exec_health": {
                "ws_latency_warn_ms": config.exec_ws_latency_warn_ms,
                "ws_latency_safe_ms": config.exec_ws_latency_safe_ms,
                "order_failure_rate_max": config.exec_order_failure_rate_max,
                "reconcile_drift_max": config.exec_reconcile_drift_max,
            },
            "correlation": {
                "spike_threshold": config.corr_spike_threshold,
                "lookback_bars": config.corr_lookback_bars,
                "btc_drop_pct": config.corr_btc_drop_pct,
            },
            "slippage": {
                "buffer_pct": config.slippage_buffer_pct,
                "max_pct": config.max_slippage_pct,
            },
            "exposure": {
                "max_total_pct": config.max_total_exposure_pct,
                "max_single_position_pct": config.max_single_position_pct,
            },
        },
    }


@router.post("/mode/{mode}")
async def force_risk_mode(mode: str, reason: str = "Manual override"):
    """
    리스크 모드 강제 설정 (주의: 수동 오버라이드)

    Args:
        mode: NORMAL, SAFE, or HALT
        reason: 변경 사유
    """
    engine = get_engine()
    overlay = engine.risk_overlay

    try:
        risk_mode = RiskMode(mode.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {mode}. Must be NORMAL, SAFE, or HALT",
        )

    overlay.force_mode(risk_mode, reason)

    return {
        "success": True,
        "mode": risk_mode.value,
        "reason": reason,
    }


@router.get("/can-trade")
async def check_can_trade():
    """
    거래 가능 여부 체크
    """
    engine = get_engine()
    overlay = engine.risk_overlay

    sat_allowed, sat_reason = overlay.can_open_satellite()
    core_allowed, core_reason = overlay.can_open_core()

    return {
        "success": True,
        "satellite": {
            "allowed": sat_allowed,
            "reason": sat_reason if not sat_allowed else "OK",
        },
        "core": {
            "allowed": core_allowed,
            "reason": core_reason if not core_allowed else "OK",
        },
        "sizing_multiplier": overlay.get_sizing_multiplier(),
        "max_exposure": overlay.get_max_exposure(),
        "hedge_required": overlay.is_hedge_required(),
    }


@router.get("/core-safety")
async def get_core_safety_status():
    """
    Core 전략 안전장치 상태 조회

    펀딩 역전, Edge 충분성 등 모니터링
    """
    core_safety = get_core_safety_guard()
    all_states = core_safety.get_all_states()

    return {
        "success": True,
        "allowed_symbols": core_safety.get_allowed_symbols(),
        "states": {
            symbol: state.to_dict()
            for symbol, state in all_states.items()
        },
    }


@router.get("/core-safety/{symbol}")
async def get_core_safety_for_symbol(symbol: str):
    """
    특정 심볼의 Core 안전장치 상태 조회
    """
    core_safety = get_core_safety_guard()

    if not core_safety.is_allowed_symbol(symbol):
        return {
            "success": True,
            "symbol": symbol,
            "allowed": False,
            "reason": f"Symbol {symbol} not in allowed list",
            "state": None,
        }

    state = core_safety.get_safety_state(symbol)
    can_open, reason = core_safety.can_open_core(symbol)

    return {
        "success": True,
        "symbol": symbol,
        "allowed": can_open,
        "reason": reason,
        "state": state.to_dict(),
    }
