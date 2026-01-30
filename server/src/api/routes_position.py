"""Position State Machine API 라우트"""

from typing import Optional

from fastapi import APIRouter, HTTPException

from src.position.config import get_psm_config

router = APIRouter(prefix="/position", tags=["Position Management"])

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


@router.get("/states")
async def get_all_position_states():
    """
    모든 관리 중인 포지션 상태 조회

    Returns:
        List of position states with details
    """
    engine = get_engine()
    psm = engine.position_state_machine

    positions = psm.get_all_positions()

    result = []
    for symbol, pos in positions.items():
        result.append(pos.to_dict())

    return {
        "success": True,
        "count": len(result),
        "positions": result,
    }


@router.get("/states/{symbol}")
async def get_position_state(symbol: str):
    """
    특정 포지션 상세 상태 조회

    Args:
        symbol: Trading symbol (e.g., "BTCUSDT")

    Returns:
        Position state details
    """
    engine = get_engine()
    psm = engine.position_state_machine

    summary = psm.get_state_summary(symbol)

    if not summary:
        raise HTTPException(
            status_code=404,
            detail=f"Position not found: {symbol}",
        )

    return {
        "success": True,
        "position": summary,
    }


@router.get("/scores/{symbol}")
async def get_position_score(symbol: str):
    """
    포지션 점수 breakdown 조회

    Args:
        symbol: Trading symbol

    Returns:
        Score breakdown with all components
    """
    engine = get_engine()
    psm = engine.position_state_machine

    position = psm.get_position(symbol)

    if not position:
        raise HTTPException(
            status_code=404,
            detail=f"Position not found: {symbol}",
        )

    # 현재 점수 계산
    score_breakdown = psm.score_calculator.calculate_score(symbol, position.side)

    return {
        "success": True,
        "symbol": symbol,
        "side": position.side,
        "current_state": position.current_state.value,
        "score": score_breakdown.to_dict(),
        "score_history": [s.to_dict() for s in position.score_history[-10:]],
    }


@router.get("/scores/{symbol}/live")
async def get_live_score(symbol: str, side: str = "LONG"):
    """
    라이브 점수 계산 (포지션 없어도 조회 가능)

    Args:
        symbol: Trading symbol
        side: Position side ("LONG" or "SHORT")

    Returns:
        Score breakdown
    """
    engine = get_engine()
    psm = engine.position_state_machine

    if side not in ["LONG", "SHORT"]:
        raise HTTPException(
            status_code=400,
            detail="side must be 'LONG' or 'SHORT'",
        )

    score_breakdown = psm.score_calculator.calculate_score(symbol, side)

    # 상태 판단
    config = get_psm_config()
    total = score_breakdown.total_score

    if total < config.psm_weak_threshold:
        suggested_state = "WEAK"
    elif total >= config.psm_extreme_threshold:
        suggested_state = "EXTREME"
    elif total >= config.psm_strong_threshold:
        suggested_state = "STRONG"
    else:
        suggested_state = "NORMAL"

    return {
        "success": True,
        "symbol": symbol,
        "side": side,
        "score": score_breakdown.to_dict(),
        "suggested_state": suggested_state,
        "thresholds": {
            "weak": config.psm_weak_threshold,
            "strong": config.psm_strong_threshold,
            "extreme": config.psm_extreme_threshold,
        },
    }


@router.get("/config")
async def get_state_machine_config():
    """
    Position State Machine 설정값 조회

    Returns:
        All PSM configuration values
    """
    config = get_psm_config()

    return {
        "success": True,
        "config": {
            # 점수 임계값
            "score_thresholds": {
                "weak": config.psm_weak_threshold,
                "strong": config.psm_strong_threshold,
                "extreme": config.psm_extreme_threshold,
            },
            # 볼륨 점수
            "volume_thresholds": {
                "rvol_strong": config.psm_rvol_strong,
                "rvol_good": config.psm_rvol_good,
                "rvol_normal": config.psm_rvol_normal,
            },
            # 과열 감지
            "overheat": {
                "rvol": config.psm_overheat_rvol,
                "wick_ratio": config.psm_overheat_wick_ratio,
            },
            # 타임스톱
            "time_stops": {
                "weak_minutes": config.psm_weak_time_stop_min,
                "normal_minutes": config.psm_normal_time_stop_min,
                "weak_min_r": config.psm_weak_min_r,
                "normal_min_r": config.psm_normal_min_r,
            },
            # ATR 배수
            "atr_multipliers": {
                "initial_stop": config.psm_initial_stop_atr,
                "weak_stop": config.psm_weak_stop_atr,
                "normal_trailing": config.psm_normal_trailing_atr,
                "strong_trailing": config.psm_strong_trailing_atr,
                "extreme_stop": config.psm_extreme_stop_atr,
            },
            # 익절 레벨
            "take_profit": {
                "tp1_r": config.psm_tp1_r,
                "tp2_r": config.psm_tp2_r,
                "tp2_strong_r": config.psm_tp2_strong_r,
                "tp1_pct_normal": config.psm_tp1_pct_normal,
                "tp1_pct_strong": config.psm_tp1_pct_strong,
                "tp2_pct": config.psm_tp2_pct,
                "extreme_tp_pct": config.psm_extreme_tp_pct,
            },
            # 쿨다운
            "cooldown": {
                "extreme_minutes": config.psm_extreme_cooldown_min,
            },
        },
    }


@router.get("/health")
async def get_psm_health():
    """
    Position State Machine 상태 체크

    Returns:
        Health status and statistics
    """
    engine = get_engine()
    psm = engine.position_state_machine

    positions = psm.get_all_positions()

    # 상태별 카운트
    state_counts = {"WEAK": 0, "NORMAL": 0, "STRONG": 0, "EXTREME": 0}
    for pos in positions.values():
        state_counts[pos.current_state.value] += 1

    return {
        "success": True,
        "healthy": True,
        "statistics": {
            "total_positions": len(positions),
            "by_state": state_counts,
        },
    }


@router.post("/simulate/{symbol}")
async def simulate_position(
    symbol: str,
    side: str,
    entry_price: float,
    quantity: float,
    atr: Optional[float] = None,
):
    """
    포지션 시뮬레이션 (테스트용)

    실제 주문 없이 상태 머신에 포지션을 등록합니다.

    Args:
        symbol: Trading symbol
        side: "LONG" or "SHORT"
        entry_price: Entry price
        quantity: Position size
        atr: ATR value (optional, will calculate if not provided)

    Returns:
        Created position details
    """
    engine = get_engine()
    psm = engine.position_state_machine

    if side not in ["LONG", "SHORT"]:
        raise HTTPException(
            status_code=400,
            detail="side must be 'LONG' or 'SHORT'",
        )

    # ATR 계산
    if atr is None:
        atr = engine.candle_manager.calc_atr(symbol, "5m", 14) or 100.0

    # 포지션 생성
    position = psm.create_position(
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        quantity=quantity,
        atr_5m=atr,
    )

    return {
        "success": True,
        "message": f"Simulated position created for {symbol}",
        "position": position.to_dict(),
    }


@router.delete("/simulate/{symbol}")
async def remove_simulated_position(symbol: str):
    """
    시뮬레이션 포지션 제거

    Args:
        symbol: Trading symbol

    Returns:
        Success status
    """
    engine = get_engine()
    psm = engine.position_state_machine

    position = psm.get_position(symbol)
    if not position:
        raise HTTPException(
            status_code=404,
            detail=f"Position not found: {symbol}",
        )

    psm.close_position(symbol)

    return {
        "success": True,
        "message": f"Position removed: {symbol}",
    }
