"""User Mode 정의 및 설정"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class UserMode(str, Enum):
    """사용자 트레이딩 모드"""

    SAFE = "SAFE"           # 보수 - 낮은 리스크
    BALANCED = "BALANCED"   # 기본 - 균형
    AGGRESSIVE = "AGGRESSIVE"  # 공격 - 높은 리스크
    PAPER = "PAPER"         # 모의 - 실거래 없음


@dataclass(frozen=True)
class UserModeConfig:
    """모드별 설정 파라미터"""

    # 포지션 관련
    max_position_pct: float       # 최대 포지션 비율 (자본 대비)
    max_concurrent_positions: int  # 최대 동시 포지션 수
    leverage_max: int              # 최대 레버리지

    # 리스크 관련
    dd_halt_threshold: float       # DD 정지 임계값
    daily_loss_limit: float        # 일일 손실 한도

    # 상관관계 관련
    correlation_warn: float        # 상관관계 경고 임계값
    correlation_action: float      # 상관관계 조치 임계값

    # 거래 실행
    execute_trades: bool           # 실제 거래 실행 여부


# 모드별 기본 설정
USER_MODE_CONFIGS: Dict[UserMode, UserModeConfig] = {
    UserMode.SAFE: UserModeConfig(
        max_position_pct=0.03,         # 3%
        max_concurrent_positions=2,
        leverage_max=3,
        dd_halt_threshold=0.03,        # 3%
        daily_loss_limit=0.01,         # 1%
        correlation_warn=0.60,
        correlation_action=0.70,
        execute_trades=True,
    ),
    UserMode.BALANCED: UserModeConfig(
        max_position_pct=0.05,         # 5%
        max_concurrent_positions=3,
        leverage_max=5,
        dd_halt_threshold=0.05,        # 5%
        daily_loss_limit=0.02,         # 2%
        correlation_warn=0.70,
        correlation_action=0.80,
        execute_trades=True,
    ),
    UserMode.AGGRESSIVE: UserModeConfig(
        max_position_pct=0.50,         # 50% (잔고 전체 사용 가능)
        max_concurrent_positions=10,   # 동시 10개
        leverage_max=10,
        dd_halt_threshold=0.07,        # 7%
        daily_loss_limit=0.03,         # 3%
        correlation_warn=0.75,
        correlation_action=0.85,
        execute_trades=True,
    ),
    UserMode.PAPER: UserModeConfig(
        max_position_pct=0.05,         # 5%
        max_concurrent_positions=3,
        leverage_max=5,
        dd_halt_threshold=0.05,        # 5%
        daily_loss_limit=0.02,         # 2%
        correlation_warn=0.70,
        correlation_action=0.80,
        execute_trades=False,          # 실거래 안함
    ),
}


def get_mode_config(mode: UserMode) -> UserModeConfig:
    """모드별 설정 조회"""
    return USER_MODE_CONFIGS[mode]


def get_mode_display_name(mode: UserMode) -> str:
    """모드 한글 표시명"""
    display_names = {
        UserMode.SAFE: "보수",
        UserMode.BALANCED: "기본",
        UserMode.AGGRESSIVE: "공격",
        UserMode.PAPER: "모의",
    }
    return display_names.get(mode, mode.value)
