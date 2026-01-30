"""Position State Machine Core

4-state position management system:
- WEAK: Defensive mode - tight stops, quick exits
- NORMAL: Standard rules - partial TPs + trailing
- STRONG: Trend continuation - hold longer
- EXTREME: Climax - aggressive profit taking

Uses Hard Rules + Score-based hybrid decision system with hysteresis.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Dict, Optional

import structlog

from src.position.config import get_psm_config
from src.position.policies import get_policy
from src.position.schemas import (
    ManagedPosition,
    OrderIntent,
    PositionState,
    ScoreBreakdown,
    StateTransition,
)
from src.position.score_calculator import ScoreCalculator

if TYPE_CHECKING:
    from src.data.candle_manager import CandleManager
    from src.features.feature_engine import FeatureEngine
    from src.risk.risk_engine import RiskEngine

logger = structlog.get_logger()


class PositionStateMachine:
    """
    Position State Machine for Satellite strategy

    Evaluates positions every 5-minute candle and outputs OrderIntents
    for execution by the trading engine.

    Key features:
    - Hard Rules for forced state transitions
    - Score-based state determination with hysteresis
    - State-specific policies for exits/TPs/trailing
    """

    def __init__(
        self,
        feature_engine: "FeatureEngine",
        candle_manager: "CandleManager",
        risk_engine: Optional["RiskEngine"] = None,
    ):
        self.feature_engine = feature_engine
        self.candle_manager = candle_manager
        self.risk_engine = risk_engine
        self.config = get_psm_config()

        # Score calculator
        self.score_calculator = ScoreCalculator(
            feature_engine=feature_engine,
            candle_manager=candle_manager,
        )

        # Active managed positions
        self._positions: Dict[str, ManagedPosition] = {}

    def create_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        atr_5m: float,
    ) -> ManagedPosition:
        """
        Create a new managed position

        Args:
            symbol: Trading symbol
            side: "LONG" or "SHORT"
            entry_price: Entry price
            quantity: Position size
            atr_5m: Current 5-minute ATR

        Returns:
            ManagedPosition instance
        """
        # Calculate initial stop using ATR
        if side == "LONG":
            initial_stop = entry_price - (atr_5m * self.config.psm_initial_stop_atr)
        else:
            initial_stop = entry_price + (atr_5m * self.config.psm_initial_stop_atr)

        r_value = abs(entry_price - initial_stop)

        position = ManagedPosition(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.utcnow(),
            initial_stop=initial_stop,
            r_value=r_value,
            highest_price=entry_price,
            lowest_price=entry_price,
            current_stop=initial_stop,
            current_state=PositionState.NORMAL,
            state_history=[],
            consecutive_weak_count=0,
            consecutive_strong_count=0,
            consecutive_extreme_count=0,
            tp_30_triggered=False,
            tp_60_triggered=False,
            last_score=50.0,
            score_history=[],
        )

        self._positions[symbol] = position

        logger.info(
            "Position created",
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            initial_stop=initial_stop,
            r_value=r_value,
        )

        return position

    def evaluate(self, symbol: str, current_price: float) -> Optional[OrderIntent]:
        """
        Evaluate position and get order intent

        Called every 5-minute candle close.

        Args:
            symbol: Trading symbol
            current_price: Current market price

        Returns:
            OrderIntent with desired actions, or None if no position
        """
        position = self._positions.get(symbol)
        if not position:
            return None

        # Update MFE/MAE
        position.update_extremes(current_price)

        # Step 1: Check Hard Rules for forced state transitions
        hard_rule_state = self._check_hard_rules(symbol, position, current_price)
        if hard_rule_state:
            self._transition_state(position, hard_rule_state, "Hard Rule")

        # Step 2: Calculate score
        score_breakdown = self.score_calculator.calculate_score(symbol, position.side)
        position.last_score = score_breakdown.total_score
        position.score_history.append(score_breakdown)

        # Keep only last 20 scores
        if len(position.score_history) > 20:
            position.score_history = position.score_history[-20:]

        # Step 3: Determine state from score (with hysteresis)
        if not hard_rule_state:
            new_state = self._score_to_state(symbol, score_breakdown.total_score, position)
            if new_state != position.current_state:
                self._transition_state(position, new_state, f"Score: {score_breakdown.total_score:.1f}")

        # Step 4: Get policy for current state and execute
        policy = get_policy(position.current_state)

        # Get market data for policy
        vwap = self._get_vwap(symbol)
        atr = self._get_atr(symbol)
        swing_low = self._get_swing_low(symbol, position.side)

        order_intent = policy.get_action(
            position=position,
            current_price=current_price,
            vwap=vwap,
            atr=atr,
            swing_low=swing_low,
        )

        logger.info(
            "Position evaluated",
            symbol=symbol,
            state=position.current_state.value,
            score=score_breakdown.total_score,
            r_pnl=position.calc_r_pnl(current_price),
            intent=order_intent.reason if order_intent.reason else "hold",
        )

        return order_intent

    def close_position(self, symbol: str) -> None:
        """Remove position from tracking"""
        if symbol in self._positions:
            position = self._positions.pop(symbol)
            logger.info(
                "Position closed",
                symbol=symbol,
                final_state=position.current_state.value,
                time_in_trade_min=position.time_in_trade_minutes,
            )

    def get_position(self, symbol: str) -> Optional[ManagedPosition]:
        """Get managed position by symbol"""
        return self._positions.get(symbol)

    def get_all_positions(self) -> Dict[str, ManagedPosition]:
        """Get all managed positions"""
        return self._positions.copy()

    def get_state_summary(self, symbol: str) -> Optional[dict]:
        """
        Get position state summary for dashboard

        Returns:
            Dictionary with position details or None if not found
        """
        position = self._positions.get(symbol)
        if not position:
            return None

        return position.to_dict()

    def update_tp_status(
        self,
        symbol: str,
        tp_30_triggered: bool = False,
        tp_60_triggered: bool = False,
    ) -> None:
        """Update take profit trigger status after partial fill"""
        position = self._positions.get(symbol)
        if not position:
            return

        if tp_30_triggered:
            position.tp_30_triggered = True
        if tp_60_triggered:
            position.tp_60_triggered = True

    def update_quantity(self, symbol: str, new_quantity: float) -> None:
        """Update position quantity after partial exit"""
        position = self._positions.get(symbol)
        if position:
            position.quantity = new_quantity

    def _check_hard_rules(
        self,
        symbol: str,
        position: ManagedPosition,
        current_price: float,
    ) -> Optional[PositionState]:
        """
        Check Hard Rules for forced state transitions

        Hard Rules override score-based transitions.

        Returns:
            Forced state or None if no hard rule applies
        """
        # WEAK Hard Rules
        weak_reason = self._check_weak_hard_rules(symbol, position, current_price)
        if weak_reason:
            logger.warning(
                "WEAK hard rule triggered",
                symbol=symbol,
                reason=weak_reason,
            )
            return PositionState.WEAK

        # EXTREME Hard Rules
        extreme_reason = self._check_extreme_hard_rules(symbol, position)
        if extreme_reason:
            logger.warning(
                "EXTREME hard rule triggered",
                symbol=symbol,
                reason=extreme_reason,
            )
            return PositionState.EXTREME

        return None

    def _check_weak_hard_rules(
        self,
        symbol: str,
        position: ManagedPosition,
        current_price: float,
    ) -> Optional[str]:
        """
        Check WEAK hard rules

        WEAK if any of:
        1. BTC RISK-OFF (volatile or bearish for LONG)
        2. Risk engine in SAFE mode
        3. Structure break (lower low + 2x below VWAP for LONG)
        4. Near initial stop (-0.8R)
        """
        # 1. BTC RISK-OFF
        btc_regime = self.feature_engine.check_btc_regime()
        if btc_regime.get("is_volatile", False) or btc_regime.get("regime") == "VOLATILE":
            return "BTC RISK-OFF (volatile)"

        if position.side == "LONG" and btc_regime.get("regime") == "BEARISH":
            return "BTC RISK-OFF (bearish for LONG)"

        if position.side == "SHORT" and btc_regime.get("regime") == "BULLISH":
            return "BTC RISK-OFF (bullish for SHORT)"

        # 2. Risk engine SAFE mode
        if self.risk_engine:
            if hasattr(self.risk_engine, "mode") and self.risk_engine.mode != "NORMAL":
                return f"Risk engine in {self.risk_engine.mode} mode"

        # 3. Structure break
        if self.score_calculator.check_structure_break(symbol, position.side):
            return "Structure break detected"

        # 4. Near initial stop (-0.8R)
        r_pnl = position.calc_r_pnl(current_price)
        if r_pnl <= -0.8:
            return f"Near initial stop (R={r_pnl:.2f})"

        return None

    def _check_extreme_hard_rules(
        self,
        symbol: str,
        position: ManagedPosition,
    ) -> Optional[str]:
        """
        Check EXTREME hard rules

        EXTREME if:
        - Overheat condition: RVOL >= 6 + upper wick >= 1.5x body + failed new high
        """
        if self.score_calculator.check_overheat_condition(symbol):
            return "Overheat condition (climax)"

        return None

    def _score_to_state(
        self,
        symbol: str,
        score: float,
        position: ManagedPosition,
    ) -> PositionState:
        """
        Determine state from score with hysteresis

        Hysteresis rules:
        - WEAK: score < 40 (immediate)
        - STRONG: score >= 70 for 2 consecutive
        - EXTREME: score >= 85 + overheat (immediate if overheat)
        - NORMAL: default, or recovery from WEAK with score >= 40 for 2 consecutive
        """
        cfg = self.config
        current_state = position.current_state

        # WEAK: immediate if score < threshold
        if score < cfg.psm_weak_threshold:
            position.consecutive_strong_count = 0
            position.consecutive_extreme_count = 0
            position.consecutive_weak_count += 1
            return PositionState.WEAK

        # EXTREME: score >= 85 with overheat
        if score >= cfg.psm_extreme_threshold:
            if self.score_calculator.check_overheat_condition(symbol):
                position.consecutive_weak_count = 0
                position.consecutive_strong_count = 0
                position.consecutive_extreme_count += 1
                return PositionState.EXTREME

        # STRONG: score >= 70 for 2 consecutive
        if score >= cfg.psm_strong_threshold:
            position.consecutive_weak_count = 0
            position.consecutive_extreme_count = 0
            position.consecutive_strong_count += 1

            if position.consecutive_strong_count >= 2:
                return PositionState.STRONG
            elif current_state == PositionState.STRONG:
                # Already STRONG, maintain
                return PositionState.STRONG

        # Recovery from WEAK to NORMAL
        if current_state == PositionState.WEAK:
            if score >= cfg.psm_weak_threshold:
                position.consecutive_weak_count = 0
                # Need VWAP recovery check
                features = self.feature_engine.get_features(symbol)
                candles = self.candle_manager.get_candles(symbol, "5m", 1)

                if features and candles:
                    current_close = candles[-1].close
                    vwap = features.vwap_5m

                    if current_close >= vwap:
                        return PositionState.NORMAL

            # Stay WEAK if not recovered
            return PositionState.WEAK

        # Reset counters if not meeting thresholds
        if score < cfg.psm_strong_threshold:
            position.consecutive_strong_count = 0

        if score < cfg.psm_extreme_threshold:
            position.consecutive_extreme_count = 0

        # Default to NORMAL
        return PositionState.NORMAL

    def _transition_state(
        self,
        position: ManagedPosition,
        new_state: PositionState,
        reason: str,
    ) -> None:
        """Record state transition"""
        if new_state == position.current_state:
            return

        transition = StateTransition(
            from_state=position.current_state.value,
            to_state=new_state.value,
            timestamp=datetime.utcnow().isoformat(),
            score=position.last_score,
            reason=reason,
        )

        position.state_history.append(
            {
                "from": transition.from_state,
                "to": transition.to_state,
                "time": transition.timestamp,
                "score": transition.score,
                "reason": transition.reason,
            }
        )

        # Keep only last 20 transitions
        if len(position.state_history) > 20:
            position.state_history = position.state_history[-20:]

        old_state = position.current_state
        position.current_state = new_state

        logger.info(
            "State transition",
            symbol=position.symbol,
            from_state=old_state.value,
            to_state=new_state.value,
            reason=reason,
            score=position.last_score,
        )

    def _get_vwap(self, symbol: str) -> Optional[float]:
        """Get current VWAP"""
        features = self.feature_engine.get_features(symbol)
        if features:
            return features.vwap_5m
        return None

    def _get_atr(self, symbol: str) -> Optional[float]:
        """Get current ATR"""
        features = self.feature_engine.get_features(symbol)
        if features:
            return features.atr_14_5m
        return None

    def _get_swing_low(self, symbol: str, side: str) -> Optional[float]:
        """
        Get recent swing low/high for trailing stop

        For LONG: returns swing low (min of last 3 lows)
        For SHORT: returns swing high (max of last 3 highs)
        """
        candles = self.candle_manager.get_candles(symbol, "5m", 3)

        if len(candles) < 3:
            return None

        if side == "LONG":
            return min(c.low for c in candles)
        else:
            return max(c.high for c in candles)
