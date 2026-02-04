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
