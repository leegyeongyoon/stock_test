"""
시간대별 유동성 필터 (v4.0)

"00:00~01:00이면 컷"이 아니라
"00:00~01:00이면 진입 방식 제한 + 유동성 기준 상향"

시간 컷은 반쪽짜리.
대신 자정 전후엔 '기회는 많지만, 슬리피지가 커진다'로 접근
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()


@dataclass
class LiquidityTimeAdjustment:
    """시간대별 유동성 조정값"""

    liquidity_mult: float  # 유동성 기준 배수 (1.0 = 기본)
    spread_mult: float  # 스프레드 기준 배수 (1.0 = 기본)
    allow_market_order: bool  # 시장가 주문 허용 여부
    force_retest_only: bool  # 리테스트만 허용
    time_zone: str  # 시간대 정보
    reason: str  # 조정 사유


class LiquidityTimeFilter:
    """
    시간대별 유동성 요구 조정

    규칙:
    - 자정 전후(00:00~01:00 KST): 유동성 기준 1.5배, 시장가 금지
    - 주말 밤: 유동성 기준 2.0배 (선택적)
    - 평상시: 기본 기준
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def get_adjustment(
        self,
        current_time: Optional[datetime] = None,
    ) -> LiquidityTimeAdjustment:
        """
        시간대별 조정값 반환

        Args:
            current_time: 체크 시간 (UTC). None이면 현재 시간 사용

        Returns:
            LiquidityTimeAdjustment
        """
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        # KST 변환 (UTC + 9)
        kst_hour = (current_time.hour + 9) % 24
        weekday = current_time.weekday()  # 0=월, 6=일

        # 설정 로드
        midnight_mult = getattr(self._settings, "midnight_liquidity_mult", 1.5)
        midnight_start = getattr(self._settings, "midnight_start_hour", 0)
        midnight_end = getattr(self._settings, "midnight_end_hour", 1)

        # === 자정 전후 (00:00~01:00 KST) ===
        if midnight_start <= kst_hour < midnight_end:
            return LiquidityTimeAdjustment(
                liquidity_mult=midnight_mult,
                spread_mult=1 / midnight_mult,  # 스프레드 기준 더 엄격
                allow_market_order=False,  # 시장가 금지
                force_retest_only=False,
                time_zone=f"KST {kst_hour:02d}:00",
                reason="midnight_high_volatility",
            )

        # === 업비트 일일 초기화 시간 (09:00 KST) ===
        # 업비트는 KST 09:00에 일일 거래량/변화율이 초기화됨
        # 이 시간대 전후로 신호 신뢰도가 낮아짐
        if 8 <= kst_hour < 10:
            return LiquidityTimeAdjustment(
                liquidity_mult=1.3,  # 유동성 기준 30% 상향
                spread_mult=0.8,  # 스프레드 기준 더 엄격
                allow_market_order=True,
                force_retest_only=False,
                time_zone=f"KST {kst_hour:02d}:00",
                reason="upbit_daily_reset_window",
            )

        # === 새벽 (02:00~06:00 KST) - 유동성 낮음 ===
        if 2 <= kst_hour < 6:
            return LiquidityTimeAdjustment(
                liquidity_mult=1.3,
                spread_mult=0.8,
                allow_market_order=True,
                force_retest_only=False,
                time_zone=f"KST {kst_hour:02d}:00",
                reason="low_liquidity_hours",
            )

        # === 주말 밤 (금요일 23시 이후 ~ 일요일) ===
        is_weekend_night = (
            (weekday == 4 and kst_hour >= 23)  # 금요일 밤
            or weekday == 5  # 토요일
            or weekday == 6  # 일요일
        )
        if is_weekend_night:
            return LiquidityTimeAdjustment(
                liquidity_mult=1.5,
                spread_mult=0.7,
                allow_market_order=True,
                force_retest_only=False,
                time_zone=f"KST {kst_hour:02d}:00 (weekend)",
                reason="weekend_reduced_liquidity",
            )

        # === 평상시 ===
        return LiquidityTimeAdjustment(
            liquidity_mult=1.0,
            spread_mult=1.0,
            allow_market_order=True,
            force_retest_only=False,
            time_zone=f"KST {kst_hour:02d}:00",
            reason="normal_hours",
        )

    def adjust_microstructure_thresholds(
        self,
        base_max_spread_bps: float,
        base_depth_mult: float,
        current_time: Optional[datetime] = None,
    ) -> tuple[float, float]:
        """
        MicrostructureFilter 임계값 조정

        Args:
            base_max_spread_bps: 기본 최대 스프레드 (bps)
            base_depth_mult: 기본 호가 깊이 배수
            current_time: 체크 시간

        Returns:
            (조정된 max_spread_bps, 조정된 depth_mult)
        """
        adjustment = self.get_adjustment(current_time)

        # 스프레드 기준 조정 (더 엄격하게)
        adjusted_spread = base_max_spread_bps * adjustment.spread_mult

        # 호가 깊이 기준 조정 (더 높은 요구)
        adjusted_depth = base_depth_mult * adjustment.liquidity_mult

        return adjusted_spread, adjusted_depth

    def should_block_market_order(
        self,
        current_time: Optional[datetime] = None,
    ) -> tuple[bool, str]:
        """
        시장가 주문 차단 여부

        Returns:
            (차단 여부, 사유)
        """
        adjustment = self.get_adjustment(current_time)

        if not adjustment.allow_market_order:
            return True, f"Market order blocked: {adjustment.reason}"

        return False, "Market order allowed"

    def should_force_retest_only(
        self,
        current_time: Optional[datetime] = None,
    ) -> tuple[bool, str]:
        """
        리테스트 전용 모드 강제 여부

        Returns:
            (강제 여부, 사유)
        """
        adjustment = self.get_adjustment(current_time)

        if adjustment.force_retest_only:
            return True, f"Retest only: {adjustment.reason}"

        return False, "All engines allowed"


# 싱글톤
_liquidity_time_filter: Optional[LiquidityTimeFilter] = None


def get_liquidity_time_filter() -> LiquidityTimeFilter:
    """LiquidityTimeFilter 싱글톤"""
    global _liquidity_time_filter
    if _liquidity_time_filter is None:
        _liquidity_time_filter = LiquidityTimeFilter()
    return _liquidity_time_filter
