"""User Mode Manager

사용자 트레이딩 모드 관리
- 모드 선택 (SAFE/BALANCED/AGGRESSIVE/PAPER)
- 자동 다운그레이드 (리스크 상황 시)
- EffectiveMode 계산 (requested vs effective)
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import structlog

from src.models.user_mode import (
    UserMode,
    UserModeConfig,
    USER_MODE_CONFIGS,
    get_mode_display_name,
)
from src.risk.dd_tracker import DrawdownTracker, get_dd_tracker

logger = structlog.get_logger()


@dataclass
class ModeStatus:
    """모드 상태"""

    requested_mode: str
    effective_mode: str
    downgrade_reason: Optional[str]
    config: dict
    timestamp: str


class ModeManager:
    """
    사용자 모드 관리자

    기능:
    - 사용자 요청 모드 설정
    - 리스크 상태 기반 자동 다운그레이드
    - 실효 모드 계산
    """

    def __init__(self, dd_tracker: Optional[DrawdownTracker] = None):
        self._requested_mode: UserMode = UserMode.BALANCED
        self._effective_mode: UserMode = UserMode.BALANCED
        self._downgrade_reason: Optional[str] = None
        self._dd_tracker = dd_tracker or get_dd_tracker()
        self._last_evaluation: Optional[datetime] = None

    @property
    def requested_mode(self) -> UserMode:
        """사용자 요청 모드"""
        return self._requested_mode

    @property
    def effective_mode(self) -> UserMode:
        """실제 적용 모드"""
        return self._effective_mode

    @property
    def downgrade_reason(self) -> Optional[str]:
        """다운그레이드 사유"""
        return self._downgrade_reason

    def set_mode(self, mode: UserMode) -> ModeStatus:
        """
        사용자 모드 설정

        Args:
            mode: 설정할 모드

        Returns:
            ModeStatus: 현재 모드 상태
        """
        old_mode = self._requested_mode
        self._requested_mode = mode

        logger.info(
            "User mode change requested",
            old_mode=old_mode.value,
            new_mode=mode.value,
        )

        # 실효 모드 재계산
        self._evaluate_effective_mode()

        return self.get_status()

    def _evaluate_effective_mode(self) -> None:
        """
        리스크 상태 기반 실효 모드 결정

        자동 다운그레이드 조건:
        - AGGRESSIVE → BALANCED: DD > 3%, correlation > 0.75, daily_loss > 2%
        - BALANCED → SAFE: DD > 4%, correlation > 0.80, daily_loss > 1.5%
        """
        dd_state = self._dd_tracker.get_state()

        # DD 상태가 없으면 요청 모드 그대로 사용
        if dd_state is None:
            self._effective_mode = self._requested_mode
            self._downgrade_reason = None
            return

        dd_pct = dd_state.drawdown_pct
        daily_loss_pct = abs(min(dd_state.daily_pnl_pct, 0))  # 손실만 양수로

        # PAPER 모드는 다운그레이드 없음
        if self._requested_mode == UserMode.PAPER:
            self._effective_mode = UserMode.PAPER
            self._downgrade_reason = None
            return

        # 다운그레이드 체크
        new_mode = self._requested_mode
        downgrade_reason = None

        # AGGRESSIVE → BALANCED 다운그레이드 조건
        if self._requested_mode == UserMode.AGGRESSIVE:
            if dd_pct > 0.03:  # DD > 3%
                new_mode = UserMode.BALANCED
                downgrade_reason = f"DD {dd_pct:.1%} > 3%"
            elif daily_loss_pct > 0.02:  # daily loss > 2%
                new_mode = UserMode.BALANCED
                downgrade_reason = f"Daily loss {daily_loss_pct:.1%} > 2%"

        # BALANCED → SAFE 다운그레이드 조건 (또는 AGGRESSIVE가 이미 BALANCED로 다운그레이드된 경우)
        if new_mode == UserMode.BALANCED or self._requested_mode == UserMode.BALANCED:
            if dd_pct > 0.04:  # DD > 4%
                new_mode = UserMode.SAFE
                downgrade_reason = f"DD {dd_pct:.1%} > 4%"
            elif daily_loss_pct > 0.015:  # daily loss > 1.5%
                new_mode = UserMode.SAFE
                downgrade_reason = f"Daily loss {daily_loss_pct:.1%} > 1.5%"

        # 다운그레이드 발생 시 로깅
        if new_mode != self._requested_mode:
            logger.warning(
                "Mode auto-downgraded",
                requested=self._requested_mode.value,
                effective=new_mode.value,
                reason=downgrade_reason,
            )

        self._effective_mode = new_mode
        self._downgrade_reason = downgrade_reason
        self._last_evaluation = datetime.utcnow()

    def update_risk_state(self) -> None:
        """
        리스크 상태 업데이트 후 모드 재평가

        주기적으로 호출하여 자동 다운그레이드 체크
        """
        self._evaluate_effective_mode()

    def get_config(self) -> UserModeConfig:
        """현재 적용되는 설정 반환"""
        return USER_MODE_CONFIGS[self._effective_mode]

    def get_status(self) -> ModeStatus:
        """현재 모드 상태 조회"""
        config = self.get_config()
        return ModeStatus(
            requested_mode=self._requested_mode.value,
            effective_mode=self._effective_mode.value,
            downgrade_reason=self._downgrade_reason,
            config={
                "max_position_pct": config.max_position_pct,
                "max_concurrent_positions": config.max_concurrent_positions,
                "leverage_max": config.leverage_max,
                "dd_halt_threshold": config.dd_halt_threshold,
                "daily_loss_limit": config.daily_loss_limit,
                "correlation_warn": config.correlation_warn,
                "correlation_action": config.correlation_action,
                "execute_trades": config.execute_trades,
            },
            timestamp=datetime.utcnow().isoformat(),
        )

    def get_status_dict(self) -> dict:
        """상태를 딕셔너리로 반환"""
        status = self.get_status()
        return {
            "requested_mode": status.requested_mode,
            "requested_mode_display": get_mode_display_name(self._requested_mode),
            "effective_mode": status.effective_mode,
            "effective_mode_display": get_mode_display_name(self._effective_mode),
            "downgrade_reason": status.downgrade_reason,
            "config": status.config,
            "timestamp": status.timestamp,
        }

    def should_execute_trades(self) -> bool:
        """실제 거래 실행 여부"""
        return self.get_config().execute_trades

    def get_max_position_pct(self) -> float:
        """최대 포지션 비율"""
        return self.get_config().max_position_pct

    def get_max_concurrent_positions(self) -> int:
        """최대 동시 포지션 수"""
        return self.get_config().max_concurrent_positions

    def get_max_leverage(self) -> int:
        """최대 레버리지"""
        return self.get_config().leverage_max


# Singleton
_mode_manager: Optional[ModeManager] = None


def get_mode_manager() -> ModeManager:
    """ModeManager 싱글톤"""
    global _mode_manager
    if _mode_manager is None:
        _mode_manager = ModeManager()
    return _mode_manager


def reset_mode_manager() -> None:
    """ModeManager 리셋 (테스트용)"""
    global _mode_manager
    _mode_manager = None
