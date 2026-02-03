"""
HotYesterdayPolicy - 전일 급등주 조건 강화

핵심 로직:
- 전일 +10% 이상 급등 -> 금지가 아닌 "강화 모드"
- 파라미터 조정:
  - volume_mult: 1.6 -> 2.0 (더 높은 거래량 요구)
  - position_size: 1.0 -> 0.6 (사이징 감소)
  - time_stop: 5min -> 3min (더 빠른 청산)
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()


@dataclass(frozen=True)
class HotYesterdayAdjustment:
    """전일 급등주 조정값"""

    is_hot: bool  # 전일 급등주 여부
    signed_change_rate: float  # 전일 변화율
    volume_mult_adjusted: float  # 조정된 거래량 배수
    position_size_mult: float  # 포지션 사이즈 배수 (0.6 = 60%)
    time_stop_min: int  # 조정된 타임스톱 (분)
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "is_hot": self.is_hot,
            "signed_change_rate": round(self.signed_change_rate * 100, 2),
            "volume_mult_adjusted": round(self.volume_mult_adjusted, 2),
            "position_size_mult": round(self.position_size_mult, 2),
            "time_stop_min": self.time_stop_min,
            "reason": self.reason,
        }


class HotYesterdayPolicy:
    """
    전일 급등주 조건 강화 정책

    - 금지하지 않음 (기회 유지)
    - 더 까다로운 진입 조건
    - 더 빠른 청산
    - 더 작은 포지션
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._hot_count = 0
        self._normal_count = 0

    def check(
        self,
        symbol: str,
        signed_change_rate: float,
        base_volume_mult: float = 3.0,
        base_time_stop_min: int = 5,
    ) -> HotYesterdayAdjustment:
        """
        전일 급등주 조건 체크 및 파라미터 조정

        Args:
            symbol: 심볼
            signed_change_rate: 전일 변화율 (0.15 = +15%)
            base_volume_mult: 기본 거래량 배수
            base_time_stop_min: 기본 타임스톱 (분)

        Returns:
            HotYesterdayAdjustment (조정된 파라미터)
        """
        # 설정값 가져오기 (기본값 사용 가능)
        hot_threshold = getattr(self._settings, "hot_yesterday_threshold", 0.10)
        position_mult = getattr(self._settings, "hot_yesterday_position_mult", 0.6)
        time_stop_reduce = getattr(self._settings, "hot_yesterday_time_stop_reduce", 2)

        # 전일 급등 체크
        is_hot = signed_change_rate > hot_threshold

        if is_hot:
            self._hot_count += 1
            # 파라미터 강화
            volume_mult_adjusted = base_volume_mult * 1.25  # 25% 더 높은 거래량 요구
            time_stop_adjusted = max(base_time_stop_min - time_stop_reduce, 2)  # 최소 2분

            logger.info(
                "HotYesterday mode",
                symbol=symbol,
                change_rate=f"{signed_change_rate * 100:.1f}%",
                volume_mult=f"{base_volume_mult:.1f} -> {volume_mult_adjusted:.1f}",
                position_mult=f"{position_mult:.1f}x",
                time_stop=f"{base_time_stop_min}min -> {time_stop_adjusted}min",
            )

            return HotYesterdayAdjustment(
                is_hot=True,
                signed_change_rate=signed_change_rate,
                volume_mult_adjusted=volume_mult_adjusted,
                position_size_mult=position_mult,
                time_stop_min=time_stop_adjusted,
                reason=f"Yesterday +{signed_change_rate*100:.1f}% > {hot_threshold*100:.0f}%",
            )

        self._normal_count += 1
        return HotYesterdayAdjustment(
            is_hot=False,
            signed_change_rate=signed_change_rate,
            volume_mult_adjusted=base_volume_mult,
            position_size_mult=1.0,
            time_stop_min=base_time_stop_min,
        )

    def get_stats(self) -> dict:
        """통계 반환"""
        total = self._hot_count + self._normal_count
        return {
            "total_checks": total,
            "hot_stocks": self._hot_count,
            "normal_stocks": self._normal_count,
            "hot_rate": round(self._hot_count / total * 100, 1) if total > 0 else 0,
        }


# 싱글톤
_hot_yesterday_policy: Optional[HotYesterdayPolicy] = None


def get_hot_yesterday_policy() -> HotYesterdayPolicy:
    """HotYesterdayPolicy 싱글톤"""
    global _hot_yesterday_policy
    if _hot_yesterday_policy is None:
        _hot_yesterday_policy = HotYesterdayPolicy()
    return _hot_yesterday_policy
