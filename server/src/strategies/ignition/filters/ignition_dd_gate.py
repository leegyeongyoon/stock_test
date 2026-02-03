"""
Ignition DD Gate - DD Tier 기반 엔진 제어 (v4.0)

규칙:
- Tier 1-3: Ignition + Retest 모두 허용
- Tier 4 (3.5% DD): Ignition OFF, Retest만 허용
- Tier 5-6 (4%+ DD): Retest만 허용, 사이징 축소
- daily_loss <= -1.0%: SAFE 모드 (Ignition OFF)
- daily_loss <= -1.5%: HALT 모드 (전략 중지)
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()


@dataclass
class DDGateResult:
    """DD Gate 체크 결과"""

    ignition_allowed: bool  # Ignition 진입 허용
    retest_allowed: bool  # Retest 진입 허용
    sizing_multiplier: float  # 사이징 배수 (1.0 = 풀사이즈)
    mode: str  # NORMAL / SAFE / HALT
    reason: str  # 판단 사유


class IgnitionDDGate:
    """
    DD Tier 기반 Ignition/Retest 진입 제어

    - Ignition: 공격적, DD Tier 4+ 에서 OFF
    - Retest: 보수적, DD Tier 5까지 허용

    일손실 기반:
    - -1.0%: SAFE 모드 (Ignition OFF)
    - -1.5%: HALT 모드 (전략 중지)
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def check(
        self,
        dd_tier: int,
        daily_loss_pct: float,
        is_aggressive_mode: bool = False,
    ) -> DDGateResult:
        """
        DD 상태 기반 진입 허용 여부 체크

        Args:
            dd_tier: 현재 DD Tier (1-6)
            daily_loss_pct: 일손실률 (음수)
            is_aggressive_mode: AGGRESSIVE 모드 여부

        Returns:
            DDGateResult
        """
        settings = self._settings

        # 설정 로드
        ignition_disable_tier = getattr(settings, "ignition_disable_dd_tier", 4)
        retest_max_tier = getattr(settings, "retest_max_dd_tier", 5)

        # === HALT 체크 (최우선) ===
        if daily_loss_pct <= -0.015:  # -1.5%
            return DDGateResult(
                ignition_allowed=False,
                retest_allowed=False,
                sizing_multiplier=0,
                mode="HALT",
                reason="Daily loss -1.5%, all trading halted",
            )

        # === SAFE 체크 ===
        if daily_loss_pct <= -0.01:  # -1.0%
            # SAFE 모드에서 Retest만 허용 (사이징 축소)
            sizing_mult = 0.5  # 50% 사이즈

            return DDGateResult(
                ignition_allowed=False,
                retest_allowed=dd_tier <= retest_max_tier,
                sizing_multiplier=sizing_mult,
                mode="SAFE",
                reason=f"Daily loss -1.0%, SAFE mode (Retest only, {sizing_mult:.0%} size)",
            )

        # === DD Tier 기반 체크 ===

        # Ignition 허용 여부
        ignition_allowed = dd_tier < ignition_disable_tier

        # Retest 허용 여부
        retest_allowed = dd_tier <= retest_max_tier

        # 사이징 배수 계산
        sizing_mult = self._calc_sizing_multiplier(
            dd_tier, daily_loss_pct, is_aggressive_mode
        )

        # 모드 결정
        if dd_tier >= ignition_disable_tier:
            mode = "DEFENSIVE"
        elif daily_loss_pct <= -0.005:  # -0.5%
            mode = "CAUTIOUS"
        else:
            mode = "NORMAL"

        # 사유 생성
        if not ignition_allowed and not retest_allowed:
            reason = f"DD Tier {dd_tier} too high, all engines disabled"
        elif not ignition_allowed:
            reason = f"DD Tier {dd_tier} >= {ignition_disable_tier}, Ignition disabled"
        elif sizing_mult < 1.0:
            reason = f"DD Tier {dd_tier}, sizing reduced to {sizing_mult:.0%}"
        else:
            reason = "All engines allowed"

        return DDGateResult(
            ignition_allowed=ignition_allowed,
            retest_allowed=retest_allowed,
            sizing_multiplier=sizing_mult,
            mode=mode,
            reason=reason,
        )

    def _calc_sizing_multiplier(
        self,
        dd_tier: int,
        daily_loss_pct: float,
        is_aggressive_mode: bool,
    ) -> float:
        """
        DD 상태 기반 사이징 배수 계산

        Tier가 높을수록, 일손실이 클수록 사이징 축소
        """
        base_mult = 1.0

        # DD Tier 기반 감소
        tier_reduction = {
            1: 0,
            2: 0,
            3: 0.1,  # 90%
            4: 0.3,  # 70%
            5: 0.5,  # 50%
            6: 0.7,  # 30%
        }
        base_mult -= tier_reduction.get(dd_tier, 0.7)

        # 일손실 기반 추가 감소
        if daily_loss_pct <= -0.005:  # -0.5%
            base_mult *= 0.8
        elif daily_loss_pct <= -0.003:  # -0.3%
            base_mult *= 0.9

        # AGGRESSIVE 모드 보너스 (제한적)
        if is_aggressive_mode and dd_tier <= 2:
            base_mult = min(base_mult * 1.2, 1.0)

        return max(base_mult, 0.2)  # 최소 20%

    def get_attack_level_cap(
        self,
        dd_tier: int,
        daily_loss_pct: float,
    ) -> int:
        """
        DD 상태 기반 최대 Attack Level 반환

        Returns:
            1, 2, 또는 3
        """
        if daily_loss_pct <= -0.01:  # -1.0% SAFE
            return 1

        if dd_tier >= 4:
            return 1
        elif dd_tier >= 3:
            return 2
        else:
            return 3


# 싱글톤
_ignition_dd_gate: Optional[IgnitionDDGate] = None


def get_ignition_dd_gate() -> IgnitionDDGate:
    """IgnitionDDGate 싱글톤"""
    global _ignition_dd_gate
    if _ignition_dd_gate is None:
        _ignition_dd_gate = IgnitionDDGate()
    return _ignition_dd_gate
