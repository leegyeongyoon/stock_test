"""Risk Overlay - 통합 리스크 관리 계층

우선순위 체인 (코드 레벨 강제):
1. SYSTEM SAFETY     → 지연/실패/리콘실/연결 상태
2. TAIL RISK         → DD / 일손실 / 상관 급증 / 급변장
3. MARKET REGIME     → BTC 기반 risk-on/off
4. POSITION STATE    → WEAK/NORMAL/STRONG/EXTREME
5. SIGNAL ENGINE     → 진입 신호 (가장 마지막)

원칙: 신호가 아무리 좋아도 1~3에서 위험이면 절대 진입 금지
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

import structlog

from src.risk.config import get_risk_config
from src.risk.correlation import CorrelationGuard, GuardAction
from src.risk.dd_tracker import DrawdownTracker, get_dd_tracker
from src.risk.exec_health import ExecHealthMonitor, get_exec_health_monitor

if TYPE_CHECKING:
    from src.data.candle_manager import CandleManager
    from src.features.feature_engine import FeatureEngine

logger = structlog.get_logger()


class RiskMode(str, Enum):
    """리스크 모드"""

    NORMAL = "NORMAL"  # 정상 운용
    SAFE = "SAFE"  # 방어 모드 (신규 진입 제한)
    HALT = "HALT"  # 완전 정지 (포지션 정리)


class MarketRegime(str, Enum):
    """시장 레짐"""

    RISK_ON = "RISK_ON"  # 공격적
    NEUTRAL = "NEUTRAL"  # 중립
    RISK_OFF = "RISK_OFF"  # 방어적


@dataclass
class RiskDecision:
    """리스크 의사결정 결과"""

    mode: RiskMode = RiskMode.NORMAL
    regime: MarketRegime = MarketRegime.NEUTRAL

    # 전략별 허용
    satellite_allowed: bool = True
    core_allowed: bool = True

    # 노출 제한
    max_exposure_pct: float = 1.0  # 0.0 ~ 1.0
    sizing_multiplier: float = 1.0  # 0.0 ~ 1.0

    # 헤지
    hedge_required: bool = False

    # 이유
    primary_reason: str = ""
    all_reasons: list = field(default_factory=list)

    # 우선순위 체인 상태
    system_safety_ok: bool = True
    tail_risk_ok: bool = True
    regime_ok: bool = True

    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "regime": self.regime.value,
            "satellite_allowed": self.satellite_allowed,
            "core_allowed": self.core_allowed,
            "max_exposure_pct": self.max_exposure_pct,
            "sizing_multiplier": self.sizing_multiplier,
            "hedge_required": self.hedge_required,
            "primary_reason": self.primary_reason,
            "all_reasons": self.all_reasons,
            "system_safety_ok": self.system_safety_ok,
            "tail_risk_ok": self.tail_risk_ok,
            "regime_ok": self.regime_ok,
            "timestamp": self.timestamp.isoformat(),
        }


class RiskOverlay:
    """
    통합 리스크 오버레이

    계좌/시장/실행 상태를 실시간 감시하고
    NORMAL/SAFE/HALT 자동 전환

    모든 진입/포지션 결정 전에 이 레이어를 통과해야 함
    """

    def __init__(
        self,
        candle_manager: "CandleManager" = None,
        feature_engine: "FeatureEngine" = None,
    ):
        self.config = get_risk_config()
        self.candle_manager = candle_manager
        self.feature_engine = feature_engine

        # 컴포넌트
        self.exec_health = get_exec_health_monitor()
        self.dd_tracker = get_dd_tracker()
        self.correlation_guard = (
            CorrelationGuard(candle_manager) if candle_manager else None
        )

        # 상태
        self._current_mode: RiskMode = RiskMode.NORMAL
        self._last_decision: Optional[RiskDecision] = None
        self._mode_change_time: Optional[datetime] = None

    def evaluate(self, current_equity: float = None) -> RiskDecision:
        """
        리스크 평가 수행

        우선순위 체인에 따라 순차적으로 평가:
        1. SYSTEM SAFETY (exec_health)
        2. TAIL RISK (dd + correlation + volatility)
        3. MARKET REGIME (btc regime)

        Args:
            current_equity: 현재 총 자산 (DD 계산용)

        Returns:
            RiskDecision with mode and constraints
        """
        decision = RiskDecision()
        reasons = []

        # === 1. SYSTEM SAFETY (최우선) ===
        system_ok, system_reason = self._check_system_safety()
        decision.system_safety_ok = system_ok

        if not system_ok:
            reasons.append(f"SYSTEM: {system_reason}")
            decision.mode = RiskMode.SAFE
            decision.satellite_allowed = False
            decision.core_allowed = False
            decision.max_exposure_pct = 0.0
            decision.sizing_multiplier = 0.0

        # === 2. TAIL RISK ===
        if decision.mode != RiskMode.HALT:
            tail_ok, tail_reason, tail_action = self._check_tail_risk(current_equity)
            decision.tail_risk_ok = tail_ok

            if not tail_ok:
                reasons.append(f"TAIL: {tail_reason}")

                if tail_action == "HALT":
                    decision.mode = RiskMode.HALT
                    decision.satellite_allowed = False
                    decision.core_allowed = False
                    decision.max_exposure_pct = 0.0
                    decision.sizing_multiplier = 0.0
                elif tail_action == "SAFE":
                    if decision.mode != RiskMode.HALT:
                        decision.mode = RiskMode.SAFE
                    decision.satellite_allowed = False
                    decision.sizing_multiplier = min(
                        decision.sizing_multiplier,
                        self.dd_tracker.get_sizing_multiplier(),
                    )

        # === 3. MARKET REGIME ===
        if decision.mode == RiskMode.NORMAL:
            regime, regime_ok, regime_reason = self._check_market_regime()
            decision.regime = regime
            decision.regime_ok = regime_ok

            if not regime_ok:
                reasons.append(f"REGIME: {regime_reason}")

            # 레짐에 따른 조정
            if regime == MarketRegime.RISK_OFF:
                decision.satellite_allowed = False
                decision.max_exposure_pct = 0.3
                decision.hedge_required = True
            elif regime == MarketRegime.NEUTRAL:
                decision.max_exposure_pct = 0.7
                decision.sizing_multiplier = min(decision.sizing_multiplier, 0.7)

        # === 상관 가드 체크 ===
        if self.correlation_guard:
            corr_state = self.correlation_guard.get_guard_action()

            if corr_state.action in [GuardAction.HEDGE, GuardAction.BLOCK]:
                decision.satellite_allowed = False
                decision.hedge_required = True
                reasons.append(f"CORR: {corr_state.action.value}")
            elif corr_state.action == GuardAction.REDUCE:
                decision.max_exposure_pct = min(decision.max_exposure_pct, 0.3)
                reasons.append("CORR: REDUCE exposure")

        # === 최종 결정 ===
        decision.all_reasons = reasons
        decision.primary_reason = reasons[0] if reasons else "OK"

        # 모드 변경 로깅
        if decision.mode != self._current_mode:
            logger.warning(
                "Risk mode changed",
                from_mode=self._current_mode.value,
                to_mode=decision.mode.value,
                reason=decision.primary_reason,
            )
            self._current_mode = decision.mode
            self._mode_change_time = datetime.utcnow()

        self._last_decision = decision
        return decision

    def _check_system_safety(self) -> tuple[bool, str]:
        """
        시스템 안전성 체크 (우선순위 1)

        - WS 지연
        - 주문 실패율
        - 리콘실 drift
        """
        should_safe, reason = self.exec_health.should_enter_safe()
        return not should_safe, reason

    def _check_tail_risk(
        self, current_equity: float = None
    ) -> tuple[bool, str, str]:
        """
        테일 리스크 체크 (우선순위 2)

        - Drawdown
        - 일손실
        - 상관 급증
        - 급변장

        Returns:
            (is_ok, reason, action: "HALT" | "SAFE" | "WARN")
        """
        reasons = []
        action = "OK"

        # DD 체크
        if current_equity is not None:
            dd_state = self.dd_tracker.update(current_equity)

            if dd_state.is_halt_trigger:
                reasons.append(f"DD {dd_state.drawdown_pct:.2%} >= HALT threshold")
                action = "HALT"
            elif dd_state.is_safe_trigger:
                reasons.append(f"DD {dd_state.drawdown_pct:.2%} >= SAFE threshold")
                if action != "HALT":
                    action = "SAFE"

        # 상관 급증 + BTC 충격
        if self.correlation_guard:
            corr_state = self.correlation_guard.get_guard_action()

            if corr_state.is_spike and corr_state.is_btc_shock:
                reasons.append(
                    f"Correlation spike ({corr_state.avg_correlation:.2f}) + BTC shock"
                )
                if action != "HALT":
                    action = "SAFE"

        is_ok = action == "OK"
        reason = "; ".join(reasons) if reasons else ""

        return is_ok, reason, action

    def _check_market_regime(self) -> tuple[MarketRegime, bool, str]:
        """
        시장 레짐 체크 (우선순위 3)

        BTC 기반 risk-on/off 판단
        """
        if not self.feature_engine:
            return MarketRegime.NEUTRAL, True, ""

        btc_regime = self.feature_engine.check_btc_regime()

        regime_str = btc_regime.get("regime", "NEUTRAL")
        is_volatile = btc_regime.get("is_volatile", False)

        if is_volatile or regime_str == "VOLATILE":
            return MarketRegime.RISK_OFF, False, "BTC volatile"

        if regime_str == "BEARISH":
            return MarketRegime.RISK_OFF, False, "BTC bearish"

        if regime_str == "BULLISH":
            return MarketRegime.RISK_ON, True, ""

        return MarketRegime.NEUTRAL, True, ""

    def can_open_satellite(self) -> tuple[bool, str]:
        """Satellite 신규 진입 가능 여부"""
        decision = self._last_decision or self.evaluate()

        if decision.mode == RiskMode.HALT:
            return False, "HALT mode"

        if not decision.satellite_allowed:
            return False, decision.primary_reason

        if decision.sizing_multiplier <= 0:
            return False, "Sizing multiplier is 0"

        return True, ""

    def can_open_core(self) -> tuple[bool, str]:
        """Core 신규 진입 가능 여부"""
        decision = self._last_decision or self.evaluate()

        if decision.mode == RiskMode.HALT:
            return False, "HALT mode"

        if not decision.core_allowed:
            return False, decision.primary_reason

        return True, ""

    def get_sizing_multiplier(self) -> float:
        """현재 사이징 배수"""
        decision = self._last_decision or self.evaluate()
        return decision.sizing_multiplier

    def get_max_exposure(self) -> float:
        """최대 노출 비율"""
        decision = self._last_decision or self.evaluate()
        return decision.max_exposure_pct

    def is_hedge_required(self) -> bool:
        """헤지 필요 여부"""
        decision = self._last_decision or self.evaluate()
        return decision.hedge_required

    def get_mode(self) -> RiskMode:
        """현재 모드"""
        return self._current_mode

    def get_decision(self) -> Optional[RiskDecision]:
        """마지막 결정"""
        return self._last_decision

    def get_summary(self) -> dict:
        """요약 정보"""
        decision = self._last_decision or self.evaluate()

        return {
            "mode": decision.mode.value,
            "regime": decision.regime.value,
            "satellite_allowed": decision.satellite_allowed,
            "core_allowed": decision.core_allowed,
            "sizing_multiplier": decision.sizing_multiplier,
            "max_exposure_pct": decision.max_exposure_pct,
            "hedge_required": decision.hedge_required,
            "primary_reason": decision.primary_reason,
            "exec_health": self.exec_health.get_summary(),
            "dd_state": (
                self.dd_tracker.get_state().to_dict()
                if self.dd_tracker.get_state()
                else None
            ),
            "correlation": (
                self.correlation_guard.get_last_state().to_dict()
                if self.correlation_guard and self.correlation_guard.get_last_state()
                else None
            ),
        }

    def force_mode(self, mode: RiskMode, reason: str) -> None:
        """수동 모드 강제 설정"""
        logger.warning(
            "Risk mode forced",
            from_mode=self._current_mode.value,
            to_mode=mode.value,
            reason=reason,
        )
        self._current_mode = mode
        self._mode_change_time = datetime.utcnow()


# Singleton
_overlay: RiskOverlay = None


def get_risk_overlay(
    candle_manager: "CandleManager" = None,
    feature_engine: "FeatureEngine" = None,
) -> RiskOverlay:
    """RiskOverlay 싱글톤"""
    global _overlay
    if _overlay is None:
        _overlay = RiskOverlay(
            candle_manager=candle_manager,
            feature_engine=feature_engine,
        )
    return _overlay
