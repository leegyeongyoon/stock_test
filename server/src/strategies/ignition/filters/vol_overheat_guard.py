"""
VolOverheatGuard - 거래량 과열 차단

핵심 로직:
- 상한 컷: vol_ratio > 8.0 (24h 평균 대비 8배 초과) -> 차단
- 군중이 이미 진입한 상태, 너무 늦음
- 2-5x = 최적 구간 (초기 점화)
- < 2x = 초기 단계 (허용)
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()


@dataclass(frozen=True)
class VolOverheatResult:
    """거래량 과열 체크 결과"""

    passed: bool
    vol_ratio: float
    zone: str  # "EARLY", "OPTIMAL", "OVERHEAT"
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "vol_ratio": round(self.vol_ratio, 2),
            "zone": self.zone,
            "reason": self.reason,
        }


class VolOverheatGuard:
    """
    거래량 과열 차단 필터

    - 상한 컷: 군중 이미 진입시 차단
    - 최적 구간 판별
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._blocked_count = 0
        self._passed_count = 0

    def check(
        self,
        symbol: str,
        current_volume: float,
        avg_volume_24h: float,
    ) -> VolOverheatResult:
        """
        거래량 과열 체크

        Args:
            symbol: 심볼
            current_volume: 현재 1분 거래량
            avg_volume_24h: 24시간 평균 거래량 (1분 기준)

        Returns:
            VolOverheatResult
        """
        if avg_volume_24h <= 0:
            return VolOverheatResult(
                passed=True,
                vol_ratio=0,
                zone="EARLY",
                reason="avg_volume_24h is zero",
            )

        vol_ratio = current_volume / avg_volume_24h

        # 설정값 가져오기 (기본값 사용 가능)
        max_ratio = getattr(self._settings, "vol_overheat_max_ratio", 8.0)
        optimal_lower = getattr(self._settings, "vol_overheat_optimal_lower", 2.0)
        optimal_upper = getattr(self._settings, "vol_overheat_optimal_upper", 5.0)

        # 상한 컷: 8배 초과시 차단
        if vol_ratio > max_ratio:
            self._blocked_count += 1
            logger.info(
                "VolOverheat blocked",
                symbol=symbol,
                vol_ratio=f"{vol_ratio:.1f}x",
                max_ratio=max_ratio,
            )
            return VolOverheatResult(
                passed=False,
                vol_ratio=vol_ratio,
                zone="OVERHEAT",
                reason=f"vol_ratio {vol_ratio:.1f}x > {max_ratio}x (too late)",
            )

        # Zone 판별
        if vol_ratio >= optimal_lower and vol_ratio <= optimal_upper:
            zone = "OPTIMAL"
        elif vol_ratio < optimal_lower:
            zone = "EARLY"
        else:
            zone = "HIGH"  # optimal_upper < vol_ratio <= max_ratio

        self._passed_count += 1
        return VolOverheatResult(
            passed=True,
            vol_ratio=vol_ratio,
            zone=zone,
        )

    def get_stats(self) -> dict:
        """통계 반환"""
        total = self._blocked_count + self._passed_count
        return {
            "total_checks": total,
            "blocked": self._blocked_count,
            "passed": self._passed_count,
            "block_rate": (
                round(self._blocked_count / total * 100, 1) if total > 0 else 0
            ),
        }


# 싱글톤
_vol_overheat_guard: Optional[VolOverheatGuard] = None


def get_vol_overheat_guard() -> VolOverheatGuard:
    """VolOverheatGuard 싱글톤"""
    global _vol_overheat_guard
    if _vol_overheat_guard is None:
        _vol_overheat_guard = VolOverheatGuard()
    return _vol_overheat_guard
