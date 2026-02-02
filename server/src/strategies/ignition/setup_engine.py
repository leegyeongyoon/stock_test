"""
Setup Engine - Watchlist 관리

역할:
- 15분 주기로 전체 심볼 스캔
- Setup Score >= 70인 종목을 Watchlist에 추가
- Watchlist TTL 관리 (2시간 후 만료)
- 최대 20종목 제한
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

from src.config import get_settings
from src.strategies.ignition.setup_score import (
    SetupScoreCalculator,
    SetupScoreResult,
    get_setup_score_calculator,
)

logger = structlog.get_logger()


@dataclass
class SetupCandidate:
    """Watchlist 후보 종목"""

    symbol: str
    setup_score: float
    range_high: float
    range_low: float
    atr_1h: float
    current_price: float
    added_at: datetime
    last_updated: datetime
    score_components: dict
    expires_at: datetime

    def is_expired(self) -> bool:
        """TTL 만료 여부"""
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "setup_score": round(self.setup_score, 2),
            "range_high": round(self.range_high, 2),
            "range_low": round(self.range_low, 2),
            "atr_1h": round(self.atr_1h, 4),
            "current_price": round(self.current_price, 2),
            "added_at": self.added_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "time_remaining_sec": max(
                0, (self.expires_at - datetime.utcnow()).total_seconds()
            ),
        }


class SetupEngine:
    """
    Setup Engine

    역할:
    - 전체 심볼 스캔하여 Setup Score 계산
    - Score >= 70인 종목 Watchlist 등록
    - Watchlist 관리 (TTL, 최대 개수)
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._score_calculator = get_setup_score_calculator()
        self._watchlist: dict[str, SetupCandidate] = {}
        self._last_scan: Optional[datetime] = None
        self._btc_change_24h: float = 0.0

    def set_btc_change(self, change_24h: float) -> None:
        """BTC 24시간 변화율 설정 (상대강도 계산용)"""
        self._btc_change_24h = change_24h

    async def scan_setups(
        self,
        symbols: list[str],
        market_data_map: dict[str, dict],
    ) -> list[SetupCandidate]:
        """
        전체 심볼 스캔하여 Setup 후보 추출

        Args:
            symbols: 스캔할 심볼 리스트
            market_data_map: 심볼별 시장 데이터

        Returns:
            새로 추가된 Setup 후보 리스트
        """
        settings = self._settings
        new_candidates: list[SetupCandidate] = []

        for symbol in symbols:
            market_data = market_data_map.get(symbol, {})
            if not market_data:
                continue

            # Setup Score 계산
            result = self._score_calculator.calculate(
                symbol=symbol,
                market_data=market_data,
                btc_change_24h=self._btc_change_24h,
            )

            # 기존 Watchlist에 있는지 확인
            existing = self._watchlist.get(symbol)

            if result.passes_threshold:
                # Watchlist에 추가/업데이트
                candidate = self._add_or_update(symbol, result)
                if existing is None:
                    new_candidates.append(candidate)
                    logger.info(
                        "New setup candidate",
                        symbol=symbol,
                        score=result.total_score,
                        range_high=result.range_high,
                        range_low=result.range_low,
                    )
            else:
                # Score가 임계값 이하로 떨어지면 제거
                if existing is not None:
                    self.remove_from_watchlist(symbol)
                    logger.info(
                        "Setup candidate removed (score dropped)",
                        symbol=symbol,
                        score=result.total_score,
                    )

        self._last_scan = datetime.utcnow()

        # TTL 만료 정리
        self.cleanup_stale()

        # 최대 개수 제한
        self._enforce_max_size()

        return new_candidates

    def _add_or_update(
        self,
        symbol: str,
        result: SetupScoreResult,
    ) -> SetupCandidate:
        """Watchlist에 추가 또는 업데이트"""
        settings = self._settings
        now = datetime.utcnow()
        ttl_hours = settings.ignition_watchlist_ttl_hours

        existing = self._watchlist.get(symbol)

        if existing:
            # 업데이트 (TTL은 갱신하지 않음 - 처음 추가 시점 기준)
            candidate = SetupCandidate(
                symbol=symbol,
                setup_score=result.total_score,
                range_high=result.range_high,
                range_low=result.range_low,
                atr_1h=result.atr_1h,
                current_price=result.current_price,
                added_at=existing.added_at,
                last_updated=now,
                score_components={c.name: c.weighted_score for c in result.components},
                expires_at=existing.expires_at,
            )
        else:
            # 새로 추가
            candidate = SetupCandidate(
                symbol=symbol,
                setup_score=result.total_score,
                range_high=result.range_high,
                range_low=result.range_low,
                atr_1h=result.atr_1h,
                current_price=result.current_price,
                added_at=now,
                last_updated=now,
                score_components={c.name: c.weighted_score for c in result.components},
                expires_at=now + timedelta(hours=ttl_hours),
            )

        self._watchlist[symbol] = candidate
        return candidate

    def remove_from_watchlist(self, symbol: str) -> bool:
        """Watchlist에서 제거"""
        if symbol in self._watchlist:
            del self._watchlist[symbol]
            return True
        return False

    def get_watchlist(self) -> list[SetupCandidate]:
        """현재 Watchlist 반환 (Score 높은 순)"""
        candidates = list(self._watchlist.values())
        candidates.sort(key=lambda c: c.setup_score, reverse=True)
        return candidates

    def get_candidate(self, symbol: str) -> Optional[SetupCandidate]:
        """특정 심볼의 후보 정보 반환"""
        return self._watchlist.get(symbol)

    def is_in_watchlist(self, symbol: str) -> bool:
        """Watchlist에 있는지 확인"""
        return symbol in self._watchlist

    def cleanup_stale(self) -> int:
        """TTL 만료 항목 정리"""
        expired = [s for s, c in self._watchlist.items() if c.is_expired()]

        for symbol in expired:
            del self._watchlist[symbol]
            logger.info("Setup candidate expired", symbol=symbol)

        return len(expired)

    def _enforce_max_size(self) -> None:
        """최대 개수 제한 적용"""
        settings = self._settings
        max_size = settings.ignition_watchlist_max

        if len(self._watchlist) <= max_size:
            return

        # Score 낮은 순으로 제거
        candidates = sorted(
            self._watchlist.items(),
            key=lambda x: x[1].setup_score,
        )

        remove_count = len(self._watchlist) - max_size
        for i in range(remove_count):
            symbol = candidates[i][0]
            del self._watchlist[symbol]
            logger.info(
                "Setup candidate removed (max size)",
                symbol=symbol,
                score=candidates[i][1].setup_score,
            )

    def get_status(self) -> dict:
        """현재 상태 반환"""
        return {
            "watchlist_count": len(self._watchlist),
            "last_scan": self._last_scan.isoformat() if self._last_scan else None,
            "btc_change_24h": self._btc_change_24h,
            "watchlist": [c.to_dict() for c in self.get_watchlist()],
        }


# 싱글톤 인스턴스
_setup_engine: Optional[SetupEngine] = None


def get_setup_engine() -> SetupEngine:
    """Setup Engine 싱글톤"""
    global _setup_engine
    if _setup_engine is None:
        _setup_engine = SetupEngine()
    return _setup_engine
