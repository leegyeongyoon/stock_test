"""Risk Overlay - 통합 리스크 관리 계층

우선순위 체인 (코드 레벨 강제):
1. SYSTEM SAFETY     → 지연/실패/리콘실/연결 상태
2. TAIL RISK         → DD / 일손실 / 상관 급증 / 급변장
3. MARKET REGIME     → BTC 기반 risk-on/off
4. POSITION STATE    → WEAK/NORMAL/STRONG/EXTREME
5. SIGNAL ENGINE     → 진입 신호 (가장 마지막)

원칙: 신호가 아무리 좋아도 1~3에서 위험이면 절대 진입 금지

MDD 5% 방어:
- 6-Tier DD Multiplier
- Portfolio Risk Management
- Position Reducer (Reduce & Stay)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

import structlog

from src.config import get_settings
from src.risk.config import get_risk_config
from src.risk.correlation import CorrelationGuard, GuardAction
from src.risk.dd_tracker import DrawdownTracker, get_dd_tracker
from src.risk.exec_health import ExecHealthMonitor, get_exec_health_monitor
from src.risk.portfolio_risk import (
    MarketCondition,
    PortfolioRiskManager,
    get_portfolio_risk_manager,
)
from src.risk.position_reducer import (
    PositionReducer,
    ReduceTrigger,
    get_position_reducer,
)

# v4.2 신규 컴포넌트
from src.risk.kmvi import KMVICalculator, KMVIState, KMVITier, get_kmvi_calculator
from src.risk.pump_setup_score import (
    PumpSetupScore,
    PumpSetupScoreCalculator,
    get_pump_setup_calculator,
)

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

    # 신규 진입 허용
    satellite_allowed: bool = True  # v3 전략 신규 진입 허용 플래그
    core_allowed: bool = True

    # 노출 제한
    max_exposure_pct: float = 1.0  # 0.0 ~ 1.0
    sizing_multiplier: float = 1.0  # 0.0 ~ 1.0

    # v3.1: Core 동적 비중 (30~60%)
    core_allocation_pct: float = 0.50  # 기본 50%

    # 헤지
    hedge_required: bool = False

    # 이유
    primary_reason: str = ""
    all_reasons: list = field(default_factory=list)

    # 우선순위 체인 상태
    system_safety_ok: bool = True
    tail_risk_ok: bool = True
    regime_ok: bool = True

    # DD 6-Tier 상태
    dd_tier: int = 1
    is_soft_dd: bool = False  # 3.5% DD
    is_hard_dd: bool = False  # 5% DD
    is_combined_safe: bool = False  # v3.1: 조합 트리거

    # Portfolio Risk 상태
    open_positions: int = 0
    max_positions: int = 5
    total_risk_pct: float = 0.0
    available_risk_pct: float = 0.015

    # Position Reducer 상태
    is_reducing: bool = False
    reduce_pct: float = 0.0
    hard_dd_phase: int = 0  # v3.1: Hard DD 분할 청산 단계 (0=none, 1-3)

    # v4.2: KMVI 상태
    kmvi_tier: str = "NORMAL"
    kmvi_value_pct: float = 0.0
    kmvi_micro_scalper_allowed: bool = True
    kmvi_new_entry_allowed: bool = True

    # v4.2: Ignition 전략 허용
    ignition_allowed: bool = True
    ignition_sizing: float = 1.0

    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "regime": self.regime.value,
            "satellite_allowed": self.satellite_allowed,
            "core_allowed": self.core_allowed,
            "max_exposure_pct": self.max_exposure_pct,
            "sizing_multiplier": self.sizing_multiplier,
            "core_allocation_pct": round(self.core_allocation_pct * 100, 1),
            "hedge_required": self.hedge_required,
            "primary_reason": self.primary_reason,
            "all_reasons": self.all_reasons,
            "system_safety_ok": self.system_safety_ok,
            "tail_risk_ok": self.tail_risk_ok,
            "regime_ok": self.regime_ok,
            "dd_tier": self.dd_tier,
            "is_soft_dd": self.is_soft_dd,
            "is_hard_dd": self.is_hard_dd,
            "is_combined_safe": self.is_combined_safe,
            "open_positions": self.open_positions,
            "max_positions": self.max_positions,
            "total_risk_pct": round(self.total_risk_pct * 100, 3),
            "available_risk_pct": round(self.available_risk_pct * 100, 3),
            "is_reducing": self.is_reducing,
            "reduce_pct": round(self.reduce_pct * 100, 1),
            "hard_dd_phase": self.hard_dd_phase,
            # v4.2: KMVI
            "kmvi_tier": self.kmvi_tier,
            "kmvi_value_pct": round(self.kmvi_value_pct, 2),
            "kmvi_micro_scalper_allowed": self.kmvi_micro_scalper_allowed,
            "kmvi_new_entry_allowed": self.kmvi_new_entry_allowed,
            # v4.2: Ignition
            "ignition_allowed": self.ignition_allowed,
            "ignition_sizing": round(self.ignition_sizing, 2),
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

        # 신규 컴포넌트 (MDD 5% 강화)
        self.portfolio_risk = get_portfolio_risk_manager()
        self.position_reducer = get_position_reducer()

        # v4.2 신규 컴포넌트
        self.kmvi_calculator = get_kmvi_calculator()
        self.pump_setup_calculator = get_pump_setup_calculator()
        self._last_kmvi: Optional[KMVIState] = None

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
        settings = get_settings()

        # === 0. V3_ENABLED 설정 체크 (최우선) ===
        if not settings.v3_enabled:
            decision.satellite_allowed = False
            reasons.append("CONFIG: v3_enabled=false")

        # Portfolio Risk에 equity 업데이트
        if current_equity is not None:
            self.portfolio_risk.update_equity(current_equity)

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

        # === 2. TAIL RISK (6-Tier DD 포함) ===
        if decision.mode != RiskMode.HALT:
            tail_ok, tail_reason, tail_action = self._check_tail_risk(current_equity)
            decision.tail_risk_ok = tail_ok

            # DD 상태 업데이트
            dd_state = self.dd_tracker.get_state()
            if dd_state:
                decision.dd_tier = dd_state.dd_tier
                decision.is_soft_dd = dd_state.is_soft_dd
                decision.is_hard_dd = dd_state.is_hard_dd
                decision.is_combined_safe = dd_state.is_combined_safe
                decision.sizing_multiplier = dd_state.sizing_multiplier

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

            # Soft DD (3.5%+): Satellite 신규진입 OFF (기존 관리만)
            if decision.is_soft_dd and not decision.is_hard_dd:
                decision.satellite_allowed = False
                reasons.append(f"Soft DD Tier{decision.dd_tier}: Satellite new entry OFF")

        # === 2.5. KMVI (v4.2) ===
        kmvi_state = self._last_kmvi
        if kmvi_state:
            decision.kmvi_tier = kmvi_state.tier.value
            decision.kmvi_value_pct = kmvi_state.value_pct
            decision.kmvi_micro_scalper_allowed = kmvi_state.micro_scalper_allowed
            decision.kmvi_new_entry_allowed = kmvi_state.new_entry_allowed

            # KMVI 기반 제한
            if not kmvi_state.new_entry_allowed:
                # CRISIS: 모든 신규 진입 금지
                decision.satellite_allowed = False
                decision.ignition_allowed = False
                reasons.append(f"KMVI CRISIS: {kmvi_state.value_pct:.2f}%")

            elif not kmvi_state.micro_scalper_allowed:
                # HIGH: Micro Scalper (Satellite) OFF
                decision.satellite_allowed = False
                reasons.append(f"KMVI HIGH: {kmvi_state.value_pct:.2f}%")

            # KMVI sizing multiplier 적용
            decision.sizing_multiplier *= kmvi_state.sizing_multiplier
            decision.ignition_sizing = kmvi_state.sizing_multiplier

        # === 3. MARKET REGIME ===
        regime, regime_ok, regime_reason = self._check_market_regime()
        decision.regime = regime
        decision.regime_ok = regime_ok

        if not regime_ok:
            reasons.append(f"REGIME: {regime_reason}")

        # 레짐에 따른 조정 및 Market Condition 설정
        # v3.1: Core 비중 동적 조절 (30~60%)
        cfg = self.config
        if regime == MarketRegime.RISK_OFF:
            decision.satellite_allowed = False
            decision.max_exposure_pct = 0.3
            decision.hedge_required = True
            decision.core_allocation_pct = cfg.core_allocation_min  # 30%
            self.portfolio_risk.set_market_condition(MarketCondition.RISKY)
        elif regime == MarketRegime.NEUTRAL:
            decision.max_exposure_pct = 0.7
            decision.sizing_multiplier = min(decision.sizing_multiplier, 0.7)
            decision.core_allocation_pct = cfg.core_allocation_default  # 50%
            self.portfolio_risk.set_market_condition(MarketCondition.NORMAL)
        else:  # RISK_ON
            decision.core_allocation_pct = cfg.core_allocation_max  # 60%
            self.portfolio_risk.set_market_condition(MarketCondition.NORMAL)

        # DD 티어에 따른 Core 비중 추가 조절
        if decision.dd_tier >= 4:  # 3.5%+ DD
            decision.core_allocation_pct = min(
                decision.core_allocation_pct, cfg.core_allocation_min
            )
        elif decision.dd_tier >= 3:  # 2%+ DD
            decision.core_allocation_pct = min(
                decision.core_allocation_pct, cfg.core_allocation_default
            )

        # === 상관 가드 체크 ===
        corr_shock = False
        corr_severity = 0.0

        if self.correlation_guard:
            corr_state = self.correlation_guard.get_guard_action()

            if corr_state.action in [GuardAction.HEDGE, GuardAction.BLOCK]:
                decision.satellite_allowed = False
                decision.hedge_required = True
                reasons.append(f"CORR: {corr_state.action.value}")
                corr_shock = True
                corr_severity = min(1.0, corr_state.avg_correlation / 0.9)
                self.portfolio_risk.set_market_condition(MarketCondition.RISKY)
            elif corr_state.action == GuardAction.REDUCE:
                decision.max_exposure_pct = min(decision.max_exposure_pct, 0.3)
                reasons.append("CORR: REDUCE exposure")
                corr_shock = True
                corr_severity = 0.5

        # === Position Reducer 체크 ===
        reduce_state = self.position_reducer.get_state()
        decision.is_reducing = reduce_state.is_reducing
        decision.reduce_pct = reduce_state.reduce_pct

        # Reduce & Stay 기간 중 신규 진입 제한
        if reduce_state.is_reducing:
            can_expand, expand_reason = self.position_reducer.can_expand_position()
            if not can_expand:
                decision.satellite_allowed = False
                reasons.append(f"REDUCER: {expand_reason}")

        # === Portfolio Risk 상태 업데이트 ===
        portfolio_state = self.portfolio_risk.get_state()
        decision.open_positions = portfolio_state.open_positions
        decision.max_positions = portfolio_state.max_positions
        decision.total_risk_pct = portfolio_state.total_risk_pct
        decision.available_risk_pct = portfolio_state.available_risk_pct

        # 포지션 수 제한 체크
        if not portfolio_state.can_open_new:
            if "Max positions" in portfolio_state.block_reason:
                decision.satellite_allowed = False
                reasons.append(f"PORTFOLIO: {portfolio_state.block_reason}")
            elif "Insufficient risk" in portfolio_state.block_reason:
                decision.satellite_allowed = False
                reasons.append(f"PORTFOLIO: {portfolio_state.block_reason}")

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

        v3.1 6-Tier DD 시스템:
        - Tier 1-2: NORMAL (1% DD → 0.8x, 신규 제한 없음)
        - Tier 3-4: Soft DD (2%+ → SAFE, 3.5%+ → Satellite OFF)
        - Tier 5-6: Hard DD (5%+ → HALT, 분할 청산)

        v3.1 SAFE 트리거:
        - 2%+ DD 단독
        - 1% DD + 상관충격 조합
        - 1% DD + 일손실 -0.5% 조합

        Returns:
            (is_ok, reason, action: "HALT" | "SAFE" | "WARN")
        """
        reasons = []
        action = "OK"
        cfg = self.config

        # DD 체크 (6-Tier)
        dd_state = None
        if current_equity is not None:
            dd_state = self.dd_tracker.update(current_equity)

            # Hard DD (5%): HALT + 분할 청산
            if dd_state.is_hard_dd:
                reasons.append(
                    f"Hard DD Tier{dd_state.dd_tier}: {dd_state.drawdown_pct:.2%} >= 5%"
                )
                action = "HALT"

            # Soft DD (3.5%): SAFE + Satellite OFF
            elif dd_state.is_soft_dd:
                reasons.append(
                    f"Soft DD Tier{dd_state.dd_tier}: {dd_state.drawdown_pct:.2%} >= 3.5%"
                )
                if action != "HALT":
                    action = "SAFE"

            # v3.1: Pure SAFE (2%+ DD)
            elif dd_state.is_safe_trigger and not dd_state.is_combined_safe:
                reasons.append(
                    f"DD Tier{dd_state.dd_tier}: {dd_state.drawdown_pct:.2%} >= 2%"
                )
                if action != "HALT":
                    action = "SAFE"

            # 일손실 강화 체크 (-1.0% SAFE, -1.5% HALT)
            if dd_state.daily_pnl_pct <= cfg.daily_loss_halt:
                reasons.append(f"Daily loss HALT: {dd_state.daily_pnl_pct:.2%}")
                action = "HALT"
            elif dd_state.daily_pnl_pct <= cfg.daily_loss_safe:
                if action != "HALT":
                    reasons.append(f"Daily loss SAFE: {dd_state.daily_pnl_pct:.2%}")
                    action = "SAFE"

        # 상관 가드 체크
        corr_shock = False
        if self.correlation_guard:
            corr_state = self.correlation_guard.get_guard_action()

            # 상관 급증 + BTC 충격
            if corr_state.is_spike and corr_state.is_btc_shock:
                reasons.append(
                    f"Corr spike ({corr_state.avg_correlation:.2f}) + BTC shock"
                )
                corr_shock = True
                if action != "HALT":
                    action = "SAFE"

            # v3.1: 1% DD + 상관 충격 → SAFE (조합 트리거)
            elif corr_state.is_spike and dd_state:
                if dd_state.drawdown_pct >= cfg.risk_dd_safe_combined_threshold:
                    reasons.append(
                        f"Combined: DD {dd_state.drawdown_pct:.2%} + Corr {corr_state.avg_correlation:.2f}"
                    )
                    corr_shock = True
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
        """v3 전략 신규 진입 가능 여부"""
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

    def get_core_allocation(self) -> float:
        """v3.1: Core 동적 비중 (30~60%)"""
        decision = self._last_decision or self.evaluate()
        return decision.core_allocation_pct

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

    # === v4.2 KMVI 메서드 ===

    def update_kmvi(self, symbols: list[str]) -> Optional[KMVIState]:
        """
        KMVI 업데이트 (v4.2)

        Args:
            symbols: 거래량 순 정렬된 심볼 리스트

        Returns:
            KMVIState or None
        """
        if not self.candle_manager:
            return None

        try:
            state = self.kmvi_calculator.update(symbols, self.candle_manager)
            self._last_kmvi = state
            return state
        except Exception as e:
            logger.error("KMVI update failed", error=str(e))
            return None

    def get_kmvi_state(self) -> Optional[KMVIState]:
        """현재 KMVI 상태"""
        return self._last_kmvi

    # === v4.2 Pump Setup 메서드 ===

    def check_pump_setup(
        self,
        symbol: str,
        candles_5m: list,
        orderbook: dict,
        ticker: dict = None,
    ) -> PumpSetupScore:
        """
        Pump Setup Score 계산 (v4.2)

        Args:
            symbol: 심볼
            candles_5m: 5분봉 리스트
            orderbook: 호가창 데이터
            ticker: 티커 데이터 (옵션)

        Returns:
            PumpSetupScore
        """
        return self.pump_setup_calculator.calculate(
            symbol=symbol,
            candles_5m=candles_5m,
            orderbook=orderbook,
            ticker=ticker,
        )

    def can_open_ignition(
        self,
        symbol: str = None,
        candles_5m: list = None,
        orderbook: dict = None,
        ticker: dict = None,
    ) -> tuple[bool, str, float]:
        """
        Ignition 전략 진입 가능 여부 (v4.2)

        Args:
            symbol: 심볼 (Pump Setup 평가용)
            candles_5m: 5분봉
            orderbook: 호가창
            ticker: 티커

        Returns:
            (가능 여부, 사유, 사이징 배수)
        """
        decision = self._last_decision or self.evaluate()

        # 1. 기본 모드 체크
        if decision.mode == RiskMode.HALT:
            return False, "HALT mode", 0.0

        if not decision.ignition_allowed:
            return False, "Ignition blocked by KMVI/DD", 0.0

        # 2. KMVI 체크
        if self._last_kmvi and not self._last_kmvi.new_entry_allowed:
            return False, "KMVI CRISIS", 0.0

        # 3. DD Tier 체크 (Tier 4+ 에서 Ignition 제한)
        if decision.dd_tier >= 4:
            return False, f"DD Tier {decision.dd_tier} too high", 0.0

        # 4. Pump Setup 체크 (옵션)
        sizing = decision.ignition_sizing * decision.sizing_multiplier
        pump_bonus = 0.0

        if symbol and candles_5m and orderbook:
            pump_result = self.check_pump_setup(symbol, candles_5m, orderbook, ticker)
            if pump_result.passed:
                # Pump Setup 통과 시 보너스
                pump_bonus = 0.2
                sizing = min(1.0, sizing + pump_bonus)
                logger.info(
                    "Pump Setup passed, bonus applied",
                    symbol=symbol,
                    score=pump_result.total_score,
                    sizing=sizing,
                )

        return True, "", sizing

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
            # v3.1: Core 동적 비중
            "core_allocation_pct": round(decision.core_allocation_pct * 100, 1),
            "hedge_required": decision.hedge_required,
            "primary_reason": decision.primary_reason,
            # 6-Tier DD 상태
            "dd_tier": decision.dd_tier,
            "is_soft_dd": decision.is_soft_dd,
            "is_hard_dd": decision.is_hard_dd,
            "is_combined_safe": decision.is_combined_safe,
            # Portfolio Risk 상태
            "open_positions": decision.open_positions,
            "max_positions": decision.max_positions,
            "total_risk_pct": round(decision.total_risk_pct * 100, 3),
            "available_risk_pct": round(decision.available_risk_pct * 100, 3),
            # Position Reducer 상태
            "is_reducing": decision.is_reducing,
            "reduce_pct": round(decision.reduce_pct * 100, 1),
            "hard_dd_phase": decision.hard_dd_phase,
            # 컴포넌트 상태
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
            "portfolio_risk": self.portfolio_risk.get_state().to_dict(),
            "reducer_state": self.position_reducer.get_state().to_dict(),
            # v4.2: KMVI 상태
            "kmvi": (
                self._last_kmvi.to_dict() if self._last_kmvi else None
            ),
            "kmvi_status": self.kmvi_calculator.get_status(),
            # v4.2: Ignition
            "ignition_allowed": decision.ignition_allowed,
            "ignition_sizing": round(decision.ignition_sizing, 2),
        }

    def get_portfolio_risk(self) -> PortfolioRiskManager:
        """Portfolio Risk Manager 반환"""
        return self.portfolio_risk

    def get_position_reducer(self) -> PositionReducer:
        """Position Reducer 반환"""
        return self.position_reducer

    def trigger_reduce(
        self,
        trigger: ReduceTrigger,
        positions: dict[str, float],
        severity: float = 1.0,
    ) -> list:
        """
        포지션 축소 트리거

        Args:
            trigger: 축소 트리거
            positions: 현재 포지션 (symbol -> quantity)
            severity: 심각도 (corr_shock 시 사용)

        Returns:
            축소 주문 목록
        """
        return self.position_reducer.trigger_reduce(trigger, positions, severity)

    def should_force_close(self) -> tuple[bool, str, int]:
        """
        강제 청산 필요 여부 (v3.1: 분할 청산 지원)

        Returns:
            tuple[bool, str, int]: (필요 여부, 사유, 분할 청산 단계)
            - phase 0: 즉시 전량 청산
            - phase 1-3: 분할 청산 단계
        """
        cfg = self.config
        dd_state = self.dd_tracker.get_state()

        # 일손실 HALT 도달 → 즉시 전량 청산 (분할 없음)
        if dd_state and dd_state.daily_pnl_pct <= cfg.daily_loss_halt:
            return True, f"Daily loss HALT: {dd_state.daily_pnl_pct:.2%}", 0

        # Hard DD (5%) 도달
        is_hard, reason = self.dd_tracker.is_hard_dd()
        if is_hard:
            if cfg.hard_dd_gradual_close_enabled:
                # v3.1: 분할 청산 1단계부터 시작
                return True, f"Hard DD: {reason}", 1
            return True, f"Hard DD: {reason}", 0

        return False, "", 0

    def get_hard_dd_phase(self) -> int:
        """현재 Hard DD 분할 청산 단계"""
        decision = self._last_decision
        if decision:
            return decision.hard_dd_phase
        return 0

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
