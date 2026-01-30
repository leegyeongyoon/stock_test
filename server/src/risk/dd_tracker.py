"""Drawdown Tracker

MDD 5% 제약을 위한 드로우다운 추적
- Peak equity 추적
- 현재 DD 계산
- DD 단계별 사이징 배수 결정
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from src.risk.config import get_risk_config

logger = structlog.get_logger()


@dataclass
class DrawdownState:
    """드로우다운 상태"""

    peak_equity: float = 0.0
    current_equity: float = 0.0
    drawdown_pct: float = 0.0
    drawdown_abs: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    sizing_multiplier: float = 1.0
    is_safe_trigger: bool = False
    is_halt_trigger: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "peak_equity": self.peak_equity,
            "current_equity": self.current_equity,
            "drawdown_pct": self.drawdown_pct,
            "drawdown_abs": self.drawdown_abs,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_pct": self.daily_pnl_pct,
            "sizing_multiplier": self.sizing_multiplier,
            "is_safe_trigger": self.is_safe_trigger,
            "is_halt_trigger": self.is_halt_trigger,
            "timestamp": self.timestamp.isoformat(),
        }


class DrawdownTracker:
    """
    드로우다운 추적기

    MDD 5% 제약 조건을 모니터링하고
    DD 단계에 따른 사이징 배수를 결정
    """

    def __init__(self):
        self.config = get_risk_config()

        # Peak equity 추적
        self._peak_equity: float = 0.0
        self._current_equity: float = 0.0

        # 일일 기준
        self._day_start_equity: float = 0.0
        self._last_reset_date: Optional[datetime] = None

        # 상태 히스토리
        self._last_state: Optional[DrawdownState] = None

    def update(self, current_equity: float) -> DrawdownState:
        """
        자산 업데이트 및 DD 계산

        Args:
            current_equity: 현재 총 자산

        Returns:
            DrawdownState
        """
        self._current_equity = current_equity

        # Peak 업데이트
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        # 일일 기준 리셋 (UTC 자정)
        now = datetime.utcnow()
        if self._last_reset_date is None or now.date() > self._last_reset_date.date():
            self._day_start_equity = current_equity
            self._last_reset_date = now
            logger.info(
                "Day reset",
                day_start_equity=current_equity,
            )

        # DD 계산
        if self._peak_equity > 0:
            drawdown_abs = self._peak_equity - current_equity
            drawdown_pct = drawdown_abs / self._peak_equity
        else:
            drawdown_abs = 0.0
            drawdown_pct = 0.0

        # 일일 PnL 계산
        if self._day_start_equity > 0:
            daily_pnl = current_equity - self._day_start_equity
            daily_pnl_pct = daily_pnl / self._day_start_equity
        else:
            daily_pnl = 0.0
            daily_pnl_pct = 0.0

        # 사이징 배수 결정
        sizing_multiplier = self._calc_sizing_multiplier(drawdown_pct)

        # 트리거 체크
        cfg = self.config
        is_safe_trigger = (
            drawdown_pct >= cfg.risk_dd_safe_threshold
            or daily_pnl_pct <= cfg.daily_loss_safe
        )
        is_halt_trigger = (
            drawdown_pct >= cfg.risk_dd_halt_threshold
            or daily_pnl_pct <= cfg.daily_loss_halt
        )

        state = DrawdownState(
            peak_equity=self._peak_equity,
            current_equity=current_equity,
            drawdown_pct=drawdown_pct,
            drawdown_abs=drawdown_abs,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            sizing_multiplier=sizing_multiplier,
            is_safe_trigger=is_safe_trigger,
            is_halt_trigger=is_halt_trigger,
        )

        # 로깅
        if is_halt_trigger:
            logger.critical(
                "DD HALT trigger",
                drawdown_pct=drawdown_pct,
                daily_pnl_pct=daily_pnl_pct,
            )
        elif is_safe_trigger:
            logger.warning(
                "DD SAFE trigger",
                drawdown_pct=drawdown_pct,
                daily_pnl_pct=daily_pnl_pct,
            )

        self._last_state = state
        return state

    def _calc_sizing_multiplier(self, drawdown_pct: float) -> float:
        """
        DD 단계에 따른 사이징 배수

        DD 0~1%: 1.0 (풀사이즈)
        DD 1~3%: 0.5 (절반)
        DD 3~5%: 0.25 (최소)
        DD >5%: 0.0 (진입 금지)
        """
        cfg = self.config

        if drawdown_pct >= cfg.risk_dd_halt_threshold:
            return 0.0
        elif drawdown_pct >= cfg.risk_dd_reduce_threshold:
            return 0.25
        elif drawdown_pct >= cfg.risk_dd_safe_threshold:
            return 0.5
        else:
            return 1.0

    def get_sizing_multiplier(self) -> float:
        """현재 사이징 배수 조회"""
        if self._last_state:
            return self._last_state.sizing_multiplier
        return 1.0

    def should_enter_safe(self) -> tuple[bool, str]:
        """SAFE 모드 진입 필요 여부"""
        if self._last_state and self._last_state.is_safe_trigger:
            dd_pct = self._last_state.drawdown_pct
            daily_pnl_pct = self._last_state.daily_pnl_pct
            return True, f"DD {dd_pct:.2%} or Daily PnL {daily_pnl_pct:.2%}"
        return False, ""

    def should_halt(self) -> tuple[bool, str]:
        """HALT 모드 진입 필요 여부"""
        if self._last_state and self._last_state.is_halt_trigger:
            dd_pct = self._last_state.drawdown_pct
            daily_pnl_pct = self._last_state.daily_pnl_pct
            return True, f"DD {dd_pct:.2%} or Daily PnL {daily_pnl_pct:.2%}"
        return False, ""

    def get_state(self) -> Optional[DrawdownState]:
        """현재 상태 조회"""
        return self._last_state

    def reset_peak(self, new_peak: float = None) -> None:
        """
        Peak 리셋 (수동)

        주의: 신중하게 사용해야 함
        """
        if new_peak is not None:
            self._peak_equity = new_peak
        else:
            self._peak_equity = self._current_equity

        logger.warning(
            "Peak equity reset",
            new_peak=self._peak_equity,
        )


# Singleton
_tracker: DrawdownTracker = None


def get_dd_tracker() -> DrawdownTracker:
    """DrawdownTracker 싱글톤"""
    global _tracker
    if _tracker is None:
        _tracker = DrawdownTracker()
    return _tracker
