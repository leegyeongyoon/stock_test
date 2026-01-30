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
    dd_tier: int = 1  # 1~6 tier
    is_safe_trigger: bool = False
    is_combined_safe: bool = False  # v3.1: 1% DD + 다른 트리거 조합
    is_soft_dd: bool = False  # 3.5% DD
    is_hard_dd: bool = False  # 5% DD (HALT)
    is_halt_trigger: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "peak_equity": self.peak_equity,
            "current_equity": self.current_equity,
            "drawdown_pct": round(self.drawdown_pct * 100, 3),
            "drawdown_abs": self.drawdown_abs,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_pct": round(self.daily_pnl_pct * 100, 3),
            "sizing_multiplier": self.sizing_multiplier,
            "dd_tier": self.dd_tier,
            "is_safe_trigger": self.is_safe_trigger,
            "is_combined_safe": self.is_combined_safe,
            "is_soft_dd": self.is_soft_dd,
            "is_hard_dd": self.is_hard_dd,
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

        # 사이징 배수 및 티어 결정
        sizing_multiplier, dd_tier = self._calc_sizing_multiplier(drawdown_pct)

        # 트리거 체크 (v3.1: SAFE 기준 완화)
        cfg = self.config

        # Soft DD: 3.5% (Satellite OFF + 50% reduce)
        is_soft_dd = drawdown_pct >= cfg.risk_dd_reduce_threshold

        # Hard DD: 5% (HALT + force close)
        is_hard_dd = drawdown_pct >= cfg.risk_dd_halt_threshold

        # v3.1 SAFE 트리거 로직:
        # 1) 2%+ DD → SAFE (단독 트리거)
        # 2) 1% DD + 일손실 -0.5% → SAFE (조합 트리거)
        is_pure_safe = drawdown_pct >= cfg.risk_dd_safe_threshold  # 2%+
        is_combined_safe = (
            drawdown_pct >= cfg.risk_dd_safe_combined_threshold  # 1%+
            and daily_pnl_pct <= cfg.daily_loss_safe * 0.5  # -0.5% 일손실
        )
        is_safe_trigger = is_pure_safe or is_combined_safe

        # Halt trigger (5% DD 또는 -1.5% 일손실)
        is_halt_trigger = is_hard_dd or daily_pnl_pct <= cfg.daily_loss_halt

        state = DrawdownState(
            peak_equity=self._peak_equity,
            current_equity=current_equity,
            drawdown_pct=drawdown_pct,
            drawdown_abs=drawdown_abs,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            sizing_multiplier=sizing_multiplier,
            dd_tier=dd_tier,
            is_safe_trigger=is_safe_trigger,
            is_combined_safe=is_combined_safe,
            is_soft_dd=is_soft_dd,
            is_hard_dd=is_hard_dd,
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

    def _calc_sizing_multiplier(self, drawdown_pct: float) -> tuple[float, int]:
        """
        6-Tier DD 시스템에 따른 사이징 배수

        Tier 1: DD 0~1%    → 1.0x (풀사이즈)
        Tier 2: DD 1~2%    → 0.8x
        Tier 3: DD 2~3.5%  → 0.6x (Soft DD 시작)
        Tier 4: DD 3.5~4.5% → 0.3x
        Tier 5: DD 4.5~5%  → 0.1x
        Tier 6: DD ≥5%     → 0.0x (HALT)

        Returns:
            tuple[float, int]: (사이징 배수, DD 티어)
        """
        cfg = self.config

        # Tier 6: ≥5% (Hard DD - HALT)
        if drawdown_pct >= cfg.dd_tier5_threshold:
            return (cfg.dd_tier6_multiplier, 6)

        # Tier 5: 4.5~5%
        if drawdown_pct >= cfg.dd_tier4_threshold:
            return (cfg.dd_tier5_multiplier, 5)

        # Tier 4: 3.5~4.5%
        if drawdown_pct >= cfg.dd_tier3_threshold:
            return (cfg.dd_tier4_multiplier, 4)

        # Tier 3: 2~3.5%
        if drawdown_pct >= cfg.dd_tier2_threshold:
            return (cfg.dd_tier3_multiplier, 3)

        # Tier 2: 1~2%
        if drawdown_pct >= cfg.dd_tier1_threshold:
            return (cfg.dd_tier2_multiplier, 2)

        # Tier 1: 0~1% (풀사이즈)
        return (cfg.dd_tier1_multiplier, 1)

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

    def is_soft_dd(self) -> tuple[bool, str]:
        """Soft DD (3.5%) 도달 여부"""
        if self._last_state and self._last_state.is_soft_dd:
            dd_pct = self._last_state.drawdown_pct
            return True, f"Soft DD triggered: {dd_pct:.2%} >= 3.5%"
        return False, ""

    def is_hard_dd(self) -> tuple[bool, str]:
        """Hard DD (5%) 도달 여부"""
        if self._last_state and self._last_state.is_hard_dd:
            dd_pct = self._last_state.drawdown_pct
            return True, f"Hard DD triggered: {dd_pct:.2%} >= 5%"
        return False, ""

    def get_dd_tier(self) -> int:
        """현재 DD 티어 (1~6)"""
        if self._last_state:
            return self._last_state.dd_tier
        return 1

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
