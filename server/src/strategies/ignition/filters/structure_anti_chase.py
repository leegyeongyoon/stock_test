"""
구조적 Anti-Chase 필터 (v4.2)

목적:
- "5분 +5% = 이미 급등" 판정 제거
- 거리/구조 기반으로 추격 여부 판단
- A(첫 점화) + B(리테스트) 진입 모드 지원

핵심 개념:
- 추격 여부 = 가격이 얼마나 올랐냐 (X)
- 추격 여부 = 돌파레벨/VWAP에서 얼마나 멀리 갔냐 (O)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()


class EntryType(str, Enum):
    """진입 타입"""

    FIRST_IGNITION = "A"  # 첫 점화 - 돌파 직후 진입
    RETEST_ENTRY = "B"  # 리테스트 - 되돌림 후 재돌파 진입
    BLOCKED = "X"  # 추격 금지


@dataclass
class StructureAntiChaseResult:
    """구조적 Anti-Chase 체크 결과"""

    passed: bool
    entry_type: EntryType  # A, B, or X
    dist_breakout: float  # 돌파레벨 대비 거리 (%)
    dist_vwap: float  # VWAP 대비 이격 (%)
    dist_atr: float  # ATR 정규화 거리
    position_mult: float  # 1.0 = 풀사이즈, 0.7 = 70%
    reason: str = ""


class StructureAntiChase:
    """
    구조 기반 Anti-Chase 필터

    - "5분 +5%" 판정 제거
    - 거리/구조로 추격 판단
    - A(첫점화) + B(리테스트) 진입 허용

    지표:
    - dist_breakout: 돌파레벨 대비 거리
    - dist_vwap: VWAP 대비 이격
    - dist_atr: ATR 정규화 거리
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._stats = {
            "total_checks": 0,
            "type_a_passed": 0,  # 첫 점화 통과
            "type_b_passed": 0,  # 리테스트 통과
            "blocked": 0,  # 추격 차단
        }

    def check(
        self,
        symbol: str,
        current_price: float,
        breakout_level: float,
        vwap_5m: float,
        atr_5m: float,
        is_retest: bool = False,
    ) -> StructureAntiChaseResult:
        """
        구조적 추격 체크

        Args:
            symbol: 심볼
            current_price: 현재 가격
            breakout_level: 돌파 레벨 (최근 5분 고점)
            vwap_5m: 5분 VWAP
            atr_5m: 5분 ATR
            is_retest: 리테스트 패턴 여부

        Returns:
            StructureAntiChaseResult:
            - entry_type=A: 첫 점화 진입 허용
            - entry_type=B: 리테스트 진입 허용
            - entry_type=X: 추격 금지
        """
        self._stats["total_checks"] += 1
        settings = self._settings

        # === 안전 검사 ===
        if current_price <= 0 or breakout_level <= 0:
            return StructureAntiChaseResult(
                passed=False,
                entry_type=EntryType.BLOCKED,
                dist_breakout=0,
                dist_vwap=0,
                dist_atr=0,
                position_mult=0,
                reason="invalid_price",
            )

        # VWAP이 없으면 현재가 사용
        if vwap_5m <= 0:
            vwap_5m = current_price

        # ATR이 없으면 가격의 1% 사용
        if atr_5m <= 0:
            atr_5m = current_price * 0.01

        # === 거리 계산 ===
        # 돌파레벨 대비 거리 (%)
        dist_breakout = (current_price - breakout_level) / breakout_level

        # VWAP 대비 이격 (%)
        dist_vwap = (current_price - vwap_5m) / vwap_5m

        # ATR 대비 비율 (정규화)
        atr_pct = atr_5m / current_price
        dist_atr = dist_vwap / atr_pct if atr_pct > 0 else 0

        # === 임계값 로드 (v4.0 업데이트) ===
        # dist_atr 최대값 (0.9 초과시 추격 금지)
        chase_vwap_mult = getattr(
            settings, "structure_chase_dist_atr_max", 0.9
        )
        # dist_breakout 배수 (0.6 * ATR% 초과시 추격 금지)
        chase_breakout_mult = getattr(
            settings, "structure_chase_dist_breakout_max", 0.6
        )
        retest_vwap_zone = getattr(settings, "structure_retest_vwap_zone", 0.3)
        retest_breakout_zone = getattr(
            settings, "structure_retest_breakout_zone", 0.25
        )
        retest_position_mult = getattr(
            settings, "structure_retest_position_mult", 0.7
        )

        # === 추격 판단 임계값 ===
        chase_breakout_threshold = chase_breakout_mult * atr_pct  # 돌파레벨 + 0.7*ATR
        chase_vwap_threshold = chase_vwap_mult  # VWAP 대비 0.9*ATR 이상

        # === Type A: 첫 점화 (돌파 직후) ===
        # 조건: 돌파레벨 근처 + VWAP 근처
        if (
            dist_breakout <= chase_breakout_threshold
            and dist_atr <= chase_vwap_threshold
        ):
            self._stats["type_a_passed"] += 1
            logger.debug(
                "Structure anti-chase: Type A passed",
                symbol=symbol,
                dist_breakout=f"{dist_breakout:.4f}",
                dist_atr=f"{dist_atr:.2f}",
            )
            return StructureAntiChaseResult(
                passed=True,
                entry_type=EntryType.FIRST_IGNITION,
                dist_breakout=dist_breakout,
                dist_vwap=dist_vwap,
                dist_atr=dist_atr,
                position_mult=1.0,  # 풀사이즈
            )

        # === Type B: 리테스트 진입 ===
        # 조건: 한번 올랐다가 되돌림 후 다시 상승
        if is_retest:
            # 리테스트 = VWAP 근처로 되돌림 후 재상승
            # VWAP ± 0.3*ATR 범위에서 반등
            vwap_zone_lower = vwap_5m * (1 - retest_vwap_zone * atr_pct)
            vwap_zone_upper = vwap_5m * (1 + retest_vwap_zone * atr_pct)
            in_vwap_zone = vwap_zone_lower <= current_price <= vwap_zone_upper

            # 또는 돌파레벨 근처로 되돌림
            breakout_zone_lower = breakout_level * (
                1 - retest_breakout_zone * atr_pct
            )
            breakout_zone_upper = breakout_level * (1 + 0.4 * atr_pct)
            in_breakout_zone = (
                breakout_zone_lower <= current_price <= breakout_zone_upper
            )

            if in_vwap_zone or in_breakout_zone:
                self._stats["type_b_passed"] += 1
                logger.debug(
                    "Structure anti-chase: Type B passed",
                    symbol=symbol,
                    in_vwap_zone=in_vwap_zone,
                    in_breakout_zone=in_breakout_zone,
                )
                return StructureAntiChaseResult(
                    passed=True,
                    entry_type=EntryType.RETEST_ENTRY,
                    dist_breakout=dist_breakout,
                    dist_vwap=dist_vwap,
                    dist_atr=dist_atr,
                    position_mult=retest_position_mult,  # 리테스트는 70% 사이즈
                )

        # === Type X: 추격 금지 ===
        # 조건: 너무 멀리 감 (돌파레벨/VWAP에서 멀리 이격)
        self._stats["blocked"] += 1

        if dist_atr > chase_vwap_threshold:
            reason = f"dist_atr {dist_atr:.2f} > {chase_vwap_threshold}"
        else:
            reason = "wait_for_retest"

        logger.debug(
            "Structure anti-chase: Blocked",
            symbol=symbol,
            dist_atr=f"{dist_atr:.2f}",
            reason=reason,
        )

        return StructureAntiChaseResult(
            passed=False,
            entry_type=EntryType.BLOCKED,
            dist_breakout=dist_breakout,
            dist_vwap=dist_vwap,
            dist_atr=dist_atr,
            position_mult=0,
            reason=reason,
        )

    def get_stats(self) -> dict:
        """통계 반환"""
        total = self._stats["total_checks"]
        return {
            "total_checks": total,
            "type_a_passed": self._stats["type_a_passed"],
            "type_b_passed": self._stats["type_b_passed"],
            "blocked": self._stats["blocked"],
            "pass_rate": (
                (self._stats["type_a_passed"] + self._stats["type_b_passed"])
                / total
                if total > 0
                else 0
            ),
        }


# 싱글톤
_structure_anti_chase: Optional[StructureAntiChase] = None


def get_structure_anti_chase() -> StructureAntiChase:
    """StructureAntiChase 싱글톤"""
    global _structure_anti_chase
    if _structure_anti_chase is None:
        _structure_anti_chase = StructureAntiChase()
    return _structure_anti_chase
