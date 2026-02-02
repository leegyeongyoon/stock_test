"""Attack Gate - Risk Overlay 하위 게이트

Attack 전략의 리스크 게이트

역할:
- RiskOverlay 아래에서 Attack 허용 여부 판단
- DD Tier, 레짐, 일중 손실에 따른 Attack 제한
- MDD 5% 방어를 위한 최종 관문
- Capital Profile (Growth/Preserve) 연동

하드 게이트 (Attack 금지):
- DD Tier ≥ 3 (DD 2%+)
- 일중 손실 ≤ -1.0% (SAFE)
- BTC VOLATILE 또는 RISK_OFF + 상관 쇼크
- 펌프&덤프 의심

소프트 게이트 (레벨 강등):
- DD Tier 2: L3 금지
- 과열 +15%↑: L3 금지
- Preserve Mode: L3 금지
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.risk.capital_profile import CapitalProfileManager, get_capital_profile_manager
from src.risk.pump_dump_filter import PumpDumpFilter, get_pump_dump_filter

logger = structlog.get_logger()
settings = get_settings()


class AttackGateAction(str, Enum):
    """Attack Gate 액션"""

    ALLOW = "ALLOW"  # 허용
    DOWNGRADE = "DOWNGRADE"  # 레벨 강등
    BLOCK = "BLOCK"  # 차단


@dataclass
class AttackGateResult:
    """Attack Gate 결과"""

    allowed: bool
    action: AttackGateAction
    max_level: int  # 허용 최대 레벨 (0~3)
    original_level: int  # 원래 레벨
    final_level: int  # 최종 레벨
    reasons: list[str] = field(default_factory=list)
    dd_tier: int = 0
    regime: str = "NEUTRAL"
    daily_loss: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "action": self.action.value,
            "max_level": self.max_level,
            "original_level": self.original_level,
            "final_level": self.final_level,
            "reasons": self.reasons,
            "dd_tier": self.dd_tier,
            "regime": self.regime,
            "daily_loss": self.daily_loss,
            "timestamp": self.timestamp.isoformat(),
        }


class AttackGate:
    """
    Attack 리스크 게이트

    RiskOverlay 하위에서 Attack 허용 여부 판단
    MDD 5% 방어를 위한 최종 관문
    Capital Profile (Growth/Preserve) 연동
    """

    def __init__(
        self,
        capital_profile: CapitalProfileManager = None,
        pump_dump_filter: PumpDumpFilter = None,
    ):
        # 설정 로드
        self.max_dd_tier = settings.attack_max_dd_tier  # 1
        self.disable_if_safe = settings.attack_disable_if_safe
        self.disable_if_risk_off = settings.attack_disable_if_risk_off
        self.disable_if_volatile = settings.attack_disable_if_volatile
        self.overheat_threshold = settings.attack_overheat_threshold  # 0.15

        # Capital Profile 연동
        self.capital_profile = capital_profile or get_capital_profile_manager()
        self.pump_dump_filter = pump_dump_filter or get_pump_dump_filter()

        # 사용자 모드에 따른 상한
        self.mode_max_level = {
            "OFF": 0,
            "NORMAL": 1,  # 10%까지
            "PLUS": 2,  # 30%까지
            "MAX": 3,  # 50%까지
        }

        logger.info(
            "AttackGate initialized with Capital Profile",
            capital_mode=self.capital_profile.get_current_mode().value,
        )

    def check(
        self,
        attack_level: int,
        dd_tier: int,
        regime: str,
        daily_loss: float,
        is_volatile: bool = False,
        change_rate: float = 0.0,
        attack_mode: str = "NORMAL",
        rvol: float = 1.0,
        change_rate_1h: float = 0.0,
    ) -> AttackGateResult:
        """
        Attack Gate 체크

        Args:
            attack_level: Attack Score 기반 레벨 (1~3)
            dd_tier: 현재 DD Tier (1~6)
            regime: BTC 레짐 (BULLISH/NEUTRAL/BEARISH/VOLATILE)
            daily_loss: 일중 손실률 (음수)
            is_volatile: VOLATILE 상태 여부
            change_rate: 전일 대비 변화율 (24h)
            attack_mode: 사용자 Attack 모드 (OFF/NORMAL/PLUS/MAX)
            rvol: 상대거래량 (펌프덤프 필터용)
            change_rate_1h: 1시간 변화율 (펌프덤프 필터용)

        Returns:
            AttackGateResult: 게이트 결과
        """
        reasons = []
        max_level = 3  # 초기값은 최대

        # 0. Capital Profile 모드 체크
        can_mode, mode_reason = self.capital_profile.can_use_attack_mode(attack_mode)
        if not can_mode:
            return AttackGateResult(
                allowed=False,
                action=AttackGateAction.BLOCK,
                max_level=0,
                original_level=attack_level,
                final_level=0,
                reasons=[f"Capital Profile: {mode_reason}"],
                dd_tier=dd_tier,
                regime=regime,
                daily_loss=daily_loss,
            )

        # 0.1 펌프&덤프 빠른 체크
        should_avoid, pd_reason = self.pump_dump_filter.quick_check(
            rvol=rvol,
            change_rate_1h=change_rate_1h,
            change_rate_24h=change_rate,
        )
        if should_avoid:
            return AttackGateResult(
                allowed=False,
                action=AttackGateAction.BLOCK,
                max_level=0,
                original_level=attack_level,
                final_level=0,
                reasons=[f"PumpDump Filter: {pd_reason}"],
                dd_tier=dd_tier,
                regime=regime,
                daily_loss=daily_loss,
            )

        # 1. 사용자 모드 체크
        mode_limit = self.mode_max_level.get(attack_mode.upper(), 1)
        if mode_limit == 0:
            return AttackGateResult(
                allowed=False,
                action=AttackGateAction.BLOCK,
                max_level=0,
                original_level=attack_level,
                final_level=0,
                reasons=["Attack mode is OFF"],
                dd_tier=dd_tier,
                regime=regime,
                daily_loss=daily_loss,
            )

        max_level = min(max_level, mode_limit)

        # 2. 하드 게이트 체크 (Attack 금지)

        # 2.1 DD Tier ≥ 3 (DD 2%+)
        if dd_tier >= 3:
            return AttackGateResult(
                allowed=False,
                action=AttackGateAction.BLOCK,
                max_level=0,
                original_level=attack_level,
                final_level=0,
                reasons=[f"DD Tier {dd_tier} >= 3: Attack blocked"],
                dd_tier=dd_tier,
                regime=regime,
                daily_loss=daily_loss,
            )

        # 2.2 일중 손실 ≤ -1.0% (SAFE 모드)
        if self.disable_if_safe and daily_loss <= -0.01:
            return AttackGateResult(
                allowed=False,
                action=AttackGateAction.BLOCK,
                max_level=0,
                original_level=attack_level,
                final_level=0,
                reasons=[f"Daily loss {daily_loss:.2%} <= -1.0%: Attack blocked (SAFE mode)"],
                dd_tier=dd_tier,
                regime=regime,
                daily_loss=daily_loss,
            )

        # 2.3 VOLATILE 레짐
        if self.disable_if_volatile and (is_volatile or regime.upper() == "VOLATILE"):
            return AttackGateResult(
                allowed=False,
                action=AttackGateAction.BLOCK,
                max_level=0,
                original_level=attack_level,
                final_level=0,
                reasons=["VOLATILE regime: Attack blocked"],
                dd_tier=dd_tier,
                regime=regime,
                daily_loss=daily_loss,
            )

        # 2.4 RISK_OFF 레짐
        if self.disable_if_risk_off and regime.upper() in ["BEARISH", "RISK_OFF"]:
            return AttackGateResult(
                allowed=False,
                action=AttackGateAction.BLOCK,
                max_level=0,
                original_level=attack_level,
                final_level=0,
                reasons=[f"RISK_OFF regime ({regime}): Attack blocked"],
                dd_tier=dd_tier,
                regime=regime,
                daily_loss=daily_loss,
            )

        # 3. 소프트 게이트 체크 (레벨 강등)

        # 3.0 Capital Profile 레벨 제한
        can_level, level_reason = self.capital_profile.can_use_attack_level(
            level=attack_level,
            dd_tier=dd_tier,
            regime=regime,
        )
        if not can_level:
            # 프로필에서 허용하지 않는 레벨이면 다운그레이드
            profile_config = self.capital_profile.get_config()
            allowed_levels = profile_config.allowed_attack_levels
            if allowed_levels:
                max_level = min(max_level, max(allowed_levels))
                reasons.append(f"Capital Profile: {level_reason}")
            else:
                max_level = 0
                reasons.append(f"Capital Profile: No levels allowed")

        # 3.0.1 과열 상태 Capital Profile 체크
        can_overheat, overheat_reason = self.capital_profile.check_overheat_restriction(
            change_rate_24h=change_rate,
            level=attack_level,
        )
        if not can_overheat:
            if attack_level == 3 and max_level >= 3:
                max_level = 2
                reasons.append(f"Capital Profile Overheat: {overheat_reason}")
            elif attack_level == 2 and max_level >= 2:
                max_level = 1
                reasons.append(f"Capital Profile Overheat: {overheat_reason}")

        # 3.1 DD Tier 2 (1~2%): L3 금지
        if dd_tier >= 2:
            if max_level > 2:
                max_level = 2
                reasons.append(f"DD Tier {dd_tier}: L3 downgrade to L2")

        # 3.2 DD Tier가 max_dd_tier 초과: L2/L3 금지
        if dd_tier > self.max_dd_tier:
            if max_level > 1:
                max_level = 1
                reasons.append(f"DD Tier {dd_tier} > {self.max_dd_tier}: max L1")

        # 3.3 과열 +15%↑: L3 금지 (기존 로직 유지)
        if change_rate >= self.overheat_threshold:
            if max_level > 2:
                max_level = 2
                reasons.append(f"Overheat {change_rate:.1%}: L3 downgrade")

        # 최종 레벨 결정
        final_level = min(attack_level, max_level)

        if final_level == 0:
            return AttackGateResult(
                allowed=False,
                action=AttackGateAction.BLOCK,
                max_level=max_level,
                original_level=attack_level,
                final_level=0,
                reasons=reasons if reasons else ["Level 0: No attack signal"],
                dd_tier=dd_tier,
                regime=regime,
                daily_loss=daily_loss,
            )

        if final_level < attack_level:
            action = AttackGateAction.DOWNGRADE
        else:
            action = AttackGateAction.ALLOW

        return AttackGateResult(
            allowed=True,
            action=action,
            max_level=max_level,
            original_level=attack_level,
            final_level=final_level,
            reasons=reasons if reasons else ["Attack allowed"],
            dd_tier=dd_tier,
            regime=regime,
            daily_loss=daily_loss,
        )

    def get_risk_per_trade(
        self,
        attack_mode: str,
        dd_tier: int,
        attack_level: int = 1,
        regime: str = "NEUTRAL",
    ) -> float:
        """
        현재 상황에 맞는 트레이드당 리스크 반환

        Capital Profile Manager에 위임하여 프로필별 리스크 계산

        Args:
            attack_mode: 사용자 Attack 모드
            dd_tier: 현재 DD Tier
            attack_level: Attack Level (1/2/3)
            regime: BTC Regime

        Returns:
            float: 트레이드당 최대 손실률
        """
        # Capital Profile Manager에서 리스크 계산
        result = self.capital_profile.get_risk_per_trade(
            dd_tier=dd_tier,
            attack_mode=attack_mode,
            attack_level=attack_level,
            regime=regime,
        )

        logger.debug(
            "Risk per trade calculated",
            attack_mode=attack_mode,
            dd_tier=dd_tier,
            attack_level=attack_level,
            regime=regime,
            profile_mode=result.profile_mode.value,
            base_risk=result.base_risk,
            adjusted_risk=result.adjusted_risk,
            risk_cap=result.risk_cap,
            reason=result.reason,
        )

        return result.risk_cap

    def get_slippage_guard(self) -> float:
        """슬리피지 가드 반환 (프로필별 배수 적용)"""
        return self.capital_profile.get_slippage_guard()

    def get_status(self) -> dict:
        """현재 게이트 설정 상태"""
        return {
            "max_dd_tier": self.max_dd_tier,
            "disable_if_safe": self.disable_if_safe,
            "disable_if_risk_off": self.disable_if_risk_off,
            "disable_if_volatile": self.disable_if_volatile,
            "overheat_threshold": self.overheat_threshold,
            "mode_max_level": self.mode_max_level,
        }


# Singleton
_attack_gate: Optional[AttackGate] = None


def get_attack_gate() -> AttackGate:
    """AttackGate 싱글톤"""
    global _attack_gate
    if _attack_gate is None:
        _attack_gate = AttackGate()
    return _attack_gate
