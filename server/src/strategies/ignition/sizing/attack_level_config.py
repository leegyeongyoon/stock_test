"""
ATTACK_LEVEL 설정 (v4.0)

Level별 리스크 설정:
- Level 1 (10% 비중급): risk_per_trade = 0.20%
- Level 2 (30% 비중급): risk_per_trade = 0.35%
- Level 3 (50% 비중급): risk_per_trade = 0.50%

비중이 아닌 리스크로 정의하여 손절폭에 따라 실제 비중이 결정됨
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()


class AttackLevel(IntEnum):
    """공격 레벨"""

    LEVEL_1 = 1  # 보수적
    LEVEL_2 = 2  # 보통
    LEVEL_3 = 3  # 공격적


@dataclass
class LevelConfig:
    """레벨별 설정"""

    risk_per_trade: float  # 트레이드당 리스크 (%)
    max_positions: int  # 최대 동시 포지션
    max_daily_trades: int  # 일일 최대 트레이드
    min_dd_tier: int  # 허용 최소 DD Tier (1 = 정상)
    max_dd_tier: int  # 허용 최대 DD Tier
    require_retest_only: bool  # 리테스트만 허용 여부


class AttackLevelConfig:
    """
    ATTACK_LEVEL 설정 관리

    핵심: "비중 버튼"이 아니라 "리스크 버튼"
    - 50% 투자하고 싶으면 손절이 짧고 레벨이 명확해야 함
    - 한 번 삐끗해도 계좌가 한 방에 깨지는 걸 방지
    """

    # 레벨별 기본 설정
    _LEVEL_CONFIGS = {
        AttackLevel.LEVEL_1: LevelConfig(
            risk_per_trade=0.0020,  # 0.20%
            max_positions=3,
            max_daily_trades=10,
            min_dd_tier=1,
            max_dd_tier=5,  # Tier 5까지 허용
            require_retest_only=False,
        ),
        AttackLevel.LEVEL_2: LevelConfig(
            risk_per_trade=0.0035,  # 0.35%
            max_positions=2,
            max_daily_trades=6,
            min_dd_tier=1,
            max_dd_tier=3,  # Tier 3까지 허용
            require_retest_only=False,
        ),
        AttackLevel.LEVEL_3: LevelConfig(
            risk_per_trade=0.0050,  # 0.50%
            max_positions=1,
            max_daily_trades=3,
            min_dd_tier=1,
            max_dd_tier=2,  # Tier 2까지만 허용
            require_retest_only=False,
        ),
    }

    def __init__(self) -> None:
        self._settings = get_settings()

    def get_config(self, level: AttackLevel) -> LevelConfig:
        """레벨별 설정 조회"""
        base_config = self._LEVEL_CONFIGS[level]

        # Config에서 오버라이드 가능한 값 적용
        risk_override = self._get_risk_override(level)
        if risk_override is not None:
            return LevelConfig(
                risk_per_trade=risk_override,
                max_positions=base_config.max_positions,
                max_daily_trades=base_config.max_daily_trades,
                min_dd_tier=base_config.min_dd_tier,
                max_dd_tier=base_config.max_dd_tier,
                require_retest_only=base_config.require_retest_only,
            )

        return base_config

    def _get_risk_override(self, level: AttackLevel) -> Optional[float]:
        """Config에서 리스크 오버라이드 값 조회"""
        settings = self._settings

        if level == AttackLevel.LEVEL_1:
            return getattr(settings, "ignition_risk_per_trade_l1", None)
        elif level == AttackLevel.LEVEL_2:
            return getattr(settings, "ignition_risk_per_trade_l2", None)
        elif level == AttackLevel.LEVEL_3:
            return getattr(settings, "ignition_risk_per_trade_l3", None)

        return None

    def get_risk_per_trade(
        self,
        level: AttackLevel,
        dd_tier: int,
        daily_loss_pct: float,
    ) -> float:
        """
        DD 상태에 따른 실제 risk_per_trade 반환

        Args:
            level: 공격 레벨
            dd_tier: 현재 DD Tier (1-6)
            daily_loss_pct: 일손실률 (음수)

        Returns:
            조정된 risk_per_trade
        """
        config = self.get_config(level)

        # DD Tier 제한 체크
        if dd_tier > config.max_dd_tier:
            # 허용 범위 초과 시 한 단계 낮은 레벨의 리스크 사용
            if level > AttackLevel.LEVEL_1:
                lower_level = AttackLevel(level - 1)
                lower_config = self.get_config(lower_level)
                logger.debug(
                    "Risk reduced due to DD tier",
                    original_level=level,
                    dd_tier=dd_tier,
                    new_risk=lower_config.risk_per_trade,
                )
                return lower_config.risk_per_trade

        # 일손실 기반 추가 감소
        if daily_loss_pct <= -0.005:  # -0.5% 손실 시
            adjusted = config.risk_per_trade * 0.7  # 30% 감소
            logger.debug(
                "Risk reduced due to daily loss",
                daily_loss=f"{daily_loss_pct:.2%}",
                adjusted_risk=adjusted,
            )
            return adjusted

        return config.risk_per_trade

    def can_use_level(
        self,
        level: AttackLevel,
        dd_tier: int,
        daily_loss_pct: float,
        is_aggressive_mode: bool,
    ) -> tuple[bool, str]:
        """
        특정 레벨 사용 가능 여부 체크

        Returns:
            (허용 여부, 사유)
        """
        config = self.get_config(level)

        # Level 3는 AGGRESSIVE 모드에서만 허용
        if level == AttackLevel.LEVEL_3 and not is_aggressive_mode:
            return False, "Level 3 requires AGGRESSIVE mode"

        # DD Tier 체크
        if dd_tier > config.max_dd_tier:
            return False, f"DD Tier {dd_tier} exceeds max {config.max_dd_tier}"

        # 일손실 체크
        if daily_loss_pct <= -0.01:  # -1.0%
            return False, "Daily loss exceeds -1.0%, SAFE mode"

        if daily_loss_pct <= -0.015:  # -1.5%
            return False, "Daily loss exceeds -1.5%, HALT mode"

        return True, "OK"

    def get_max_position_value_pct(
        self,
        level: AttackLevel,
        stop_distance_pct: float,
    ) -> float:
        """
        리스크 기반 최대 포지션 비중 계산

        공식: position_pct = risk_per_trade / stop_distance_pct

        예: risk=0.5%, stop=1% → position=50%
        예: risk=0.5%, stop=2% → position=25%
        """
        config = self.get_config(level)

        if stop_distance_pct <= 0:
            return 0

        position_pct = config.risk_per_trade / stop_distance_pct

        # 절대 상한 (안전장치)
        max_caps = {
            AttackLevel.LEVEL_1: 0.15,  # 15%
            AttackLevel.LEVEL_2: 0.35,  # 35%
            AttackLevel.LEVEL_3: 0.55,  # 55%
        }

        return min(position_pct, max_caps.get(level, 0.15))


# 싱글톤
_attack_level_config: Optional[AttackLevelConfig] = None


def get_attack_level_config() -> AttackLevelConfig:
    """AttackLevelConfig 싱글톤"""
    global _attack_level_config
    if _attack_level_config is None:
        _attack_level_config = AttackLevelConfig()
    return _attack_level_config
