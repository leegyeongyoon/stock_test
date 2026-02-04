"""실시간 알고리즘 모니터링 API"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

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


class AttackCandidate(BaseModel):
    """Attack 후보 종목"""

    symbol: str
    korean_name: str = ""
    score: float
    level: int
    distance_to_entry: float
    change_rate: float
    volume_24h: float
    components: list[dict]


class FilterStats(BaseModel):
    """필터링 통계"""

    anti_chase: int
    exposure_limit: int
    overheat: int
    total: int


class MonitoringCandidatesResponse(BaseModel):
    """모니터링 후보 응답"""

    attack_candidates: list[AttackCandidate]
    filter_stats: FilterStats
    cache_updated_at: Optional[datetime]
    entry_threshold: int = 80


@router.get("/monitoring/candidates", response_model=MonitoringCandidatesResponse)
async def get_monitoring_candidates(
    limit: int = Query(5, ge=1, le=20, description="반환할 후보 수"),
    min_score: int = Query(50, ge=0, le=100, description="최소 점수"),
):
    """
    실시간 알고리즘 모니터링 후보 목록

    Attack 전략의 상위 후보 종목과 점수를 반환합니다.

    - **attack_candidates**: 점수 높은 순으로 정렬된 Attack 후보
    - **filter_stats**: 오늘의 필터링 통계 (차단 횟수)
    - **entry_threshold**: 진입 기준 점수 (기본 80점)
    """
    engine = get_engine()

    # 상위 Attack 후보 조회
    attack_candidates = engine.get_top_attack_candidates(limit=limit, min_score=min_score)

    # 필터 통계 조회
    filter_stats = engine.get_filter_stats()

    return MonitoringCandidatesResponse(
        attack_candidates=[AttackCandidate(**c) for c in attack_candidates],
        filter_stats=FilterStats(**filter_stats),
        cache_updated_at=engine._attack_score_cache_time,
        entry_threshold=80,
    )


@router.get("/monitoring/attack-scores")
async def get_all_attack_scores():
    """
    모든 캐시된 Attack 점수 조회 (디버깅용)

    캐시된 모든 Attack 점수를 반환합니다.
    """
    engine = get_engine()

    return {
        "scores": list(engine._attack_score_cache.values()),
        "cache_time": engine._attack_score_cache_time,
        "total_symbols": len(engine._attack_score_cache),
    }


@router.get("/monitoring/filter-stats", response_model=FilterStats)
async def get_filter_stats():
    """
    오늘의 필터링 통계

    필터 유형별 차단 횟수를 반환합니다.
    """
    engine = get_engine()
    return FilterStats(**engine.get_filter_stats())


# ============================================================
# Rebound Scalper 전략 모니터링 (v4.2)
# ============================================================

class ReboundPositionInfo(BaseModel):
    """Rebound 포지션 정보"""
    symbol: str
    entry_price: float
    current_qty: float
    tp1_hit: bool
    tp2_hit: bool
    be_stop_active: bool
    trailing_active: bool
    highest_price: float
    hold_time_min: float


class ReboundSignalInfo(BaseModel):
    """Rebound 대기 시그널 정보"""
    symbol: str
    score: float
    level: int
    reason: str


class ReboundFilterInfo(BaseModel):
    """Rebound 필터 정보"""
    cooldowns: dict
    filter_settings: dict


class ReboundStats(BaseModel):
    """Rebound 통계"""
    total_trades: int
    win_rate: float
    tp1_hits: int
    tp2_hits: int
    stop_loss_hits: int


class ReboundMonitoringResponse(BaseModel):
    """Rebound 전략 모니터링 응답"""
    strategy: str
    enabled: bool
    mode: str
    positions: dict[str, ReboundPositionInfo]
    pending_signals: dict[str, ReboundSignalInfo]
    stats: ReboundStats
    filters: ReboundFilterInfo


class ReboundCandidate(BaseModel):
    """Rebound 후보 종목"""

    symbol: str
    korean_name: str = ""
    score: float
    level: int
    distance_to_entry: float
    change_rate: float
    volume_24h: float
    rsi: float
    support_distance_pct: float
    orderbook_ratio: float
    components: list[dict]


class ReboundCandidatesResponse(BaseModel):
    """Rebound 후보 응답"""

    candidates: list[ReboundCandidate]
    cache_updated_at: Optional[datetime]
    entry_threshold: int = 55


@router.get("/monitoring/rebound/candidates", response_model=ReboundCandidatesResponse)
async def get_rebound_candidates(
    limit: int = Query(5, ge=1, le=20, description="반환할 후보 수"),
    min_score: int = Query(0, ge=0, le=100, description="최소 점수"),
):
    """
    Rebound Scalper 후보 목록

    반등 매수 전략의 상위 후보 종목과 점수를 반환합니다.

    - **candidates**: 점수 높은 순으로 정렬된 Rebound 후보
    - **entry_threshold**: 진입 기준 점수 (L1 = 55점)
    """
    engine = get_engine()

    # 상위 Rebound 후보 조회
    rebound_candidates = engine.get_top_rebound_candidates(limit=limit, min_score=min_score)

    return ReboundCandidatesResponse(
        candidates=[ReboundCandidate(**c) for c in rebound_candidates],
        cache_updated_at=engine._rebound_score_cache_time,
        entry_threshold=55,
    )


@router.get("/monitoring/rebound")
async def get_rebound_status():
    """
    Rebound Scalper 전략 상태 조회

    현재 Rebound 전략의 전체 상태를 반환합니다.

    - **enabled**: 활성화 여부
    - **mode**: 현재 모드 (OFF/SAFE/NORMAL/AGGRESSIVE)
    - **positions**: 활성 포지션 목록
    - **pending_signals**: 대기 중인 시그널
    - **stats**: 거래 통계
    - **filters**: 필터 상태 (쿨다운, 변동성, 추세)
    """
    engine = get_engine()

    if not hasattr(engine, "rebound_strategy") or engine.rebound_strategy is None:
        raise HTTPException(status_code=404, detail="Rebound strategy not available")

    monitoring_data = engine.rebound_strategy.get_monitoring_data()

    return monitoring_data


@router.get("/monitoring/rebound/full-status")
async def get_rebound_full_status():
    """
    Rebound 전략 전체 상태 (상세)

    설정, 통계, 포지션, 필터 모든 정보를 포함합니다.
    """
    engine = get_engine()

    if not hasattr(engine, "rebound_strategy") or engine.rebound_strategy is None:
        raise HTTPException(status_code=404, detail="Rebound strategy not available")

    return engine.rebound_strategy.get_status()


@router.post("/monitoring/rebound/mode")
async def set_rebound_mode(mode: str = Query(..., description="모드 (OFF/SAFE/NORMAL/AGGRESSIVE)")):
    """
    Rebound 전략 모드 변경

    - **OFF**: 비활성화
    - **SAFE**: 안전 모드 (레벨 3만 진입)
    - **NORMAL**: 일반 모드 (레벨 2+ 진입)
    - **AGGRESSIVE**: 공격 모드 (레벨 1+ 진입)
    """
    engine = get_engine()

    if not hasattr(engine, "rebound_strategy") or engine.rebound_strategy is None:
        raise HTTPException(status_code=404, detail="Rebound strategy not available")

    valid_modes = ["OFF", "SAFE", "NORMAL", "AGGRESSIVE"]
    if mode.upper() not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {mode}. Valid modes: {valid_modes}"
        )

    engine.rebound_strategy.set_mode(mode.upper())

    return {
        "success": True,
        "message": f"Rebound mode set to {mode.upper()}",
        "enabled": engine.rebound_strategy.is_enabled(),
        "mode": mode.upper(),
    }


@router.get("/monitoring/rebound/positions")
async def get_rebound_positions():
    """
    Rebound 전략 활성 포지션 목록

    현재 보유 중인 Rebound 포지션과 상태를 반환합니다.
    """
    engine = get_engine()

    if not hasattr(engine, "rebound_strategy") or engine.rebound_strategy is None:
        raise HTTPException(status_code=404, detail="Rebound strategy not available")

    positions = engine.rebound_strategy.get_all_positions()

    return {
        "count": len(positions),
        "positions": {sym: pos.to_dict() for sym, pos in positions.items()},
    }


@router.get("/monitoring/rebound/filters")
async def get_rebound_filters():
    """
    Rebound 전략 필터 상태

    쿨다운, 변동성 필터, 추세 필터 상태를 반환합니다.
    """
    engine = get_engine()

    if not hasattr(engine, "rebound_strategy") or engine.rebound_strategy is None:
        raise HTTPException(status_code=404, detail="Rebound strategy not available")

    return engine.rebound_strategy.filter_manager.get_dashboard_data()
