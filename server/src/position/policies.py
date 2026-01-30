"""State-specific policies for Position State Machine

Each policy defines the actions for a specific state:
- WEAK: Defensive mode - tight stops, quick exits
- NORMAL: Standard rules - partial TPs + trailing
- STRONG: Trend continuation - hold longer
- EXTREME: Climax - aggressive profit taking
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import structlog

from src.position.config import get_psm_config
from src.position.schemas import ManagedPosition, OrderIntent, PositionState

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()


class BasePolicy(ABC):
    """Base class for state policies"""

    def __init__(self):
        self.config = get_psm_config()

    @abstractmethod
    def get_action(
        self,
        position: ManagedPosition,
        current_price: float,
        vwap: Optional[float] = None,
        atr: Optional[float] = None,
        swing_low: Optional[float] = None,
    ) -> OrderIntent:
        """
        Get order intent for current state

        Args:
            position: Managed position data
            current_price: Current market price
            vwap: Current VWAP (optional)
            atr: Current ATR (optional)
            swing_low: Recent swing low (optional)

        Returns:
            OrderIntent with desired actions
        """
        pass

    def _calc_r_pnl(self, position: ManagedPosition, current_price: float) -> float:
        """Calculate PnL in R multiples"""
        return position.calc_r_pnl(current_price)

    def _calc_stop_from_swing(
        self,
        position: ManagedPosition,
        swing_low: float,
        atr: float,
        multiplier: float,
    ) -> float:
        """Calculate stop from swing low with ATR buffer"""
        if position.side == "LONG":
            new_stop = swing_low - (atr * multiplier)
            return max(position.current_stop, new_stop)
        else:
            # For SHORT, swing_low is actually swing_high
            new_stop = swing_low + (atr * multiplier)
            return min(position.current_stop, new_stop)


class WeakPolicy(BasePolicy):
    """
    WEAK Policy - Defensive mode

    - Aggressive stop tightening: stop = max(stop, vwap - 0.3*ATR)
    - Short time stop: 15min, exit if < +0.3R
    - 50% reduction after 2x consecutive WEAK
    - No additional entries (reduce_only)
    """

    def get_action(
        self,
        position: ManagedPosition,
        current_price: float,
        vwap: Optional[float] = None,
        atr: Optional[float] = None,
        swing_low: Optional[float] = None,
    ) -> OrderIntent:
        cfg = self.config
        r_pnl = self._calc_r_pnl(position, current_price)

        intent = OrderIntent(
            symbol=position.symbol,
            desired_stop_price=position.current_stop,
            take_profit_orders=[],
            reduce_only=True,  # Always reduce-only in WEAK
        )

        # Time stop: 15min and < +0.3R
        time_in_trade = position.time_in_trade_minutes
        if time_in_trade >= cfg.psm_weak_time_stop_min and r_pnl < cfg.psm_weak_min_r:
            intent.exit_now = True
            intent.reason = f"WEAK time stop: {time_in_trade:.0f}min, R={r_pnl:.2f}"
            logger.info(
                "WEAK time stop triggered",
                symbol=position.symbol,
                time_min=time_in_trade,
                r_pnl=r_pnl,
            )
            return intent

        # 50% reduction after 2x consecutive WEAK
        if position.consecutive_weak_count >= 2:
            intent.take_profit_orders = [{"pct": 0.50, "price": current_price}]
            intent.reason = "WEAK: 2x consecutive - 50% reduction"
            logger.info(
                "WEAK reduction triggered",
                symbol=position.symbol,
                consecutive=position.consecutive_weak_count,
            )

        # Aggressive stop tightening (using VWAP - 0.3*ATR)
        if vwap and atr and position.side == "LONG":
            weak_stop = vwap - (atr * cfg.psm_weak_stop_atr)
            if weak_stop > position.current_stop:
                intent.desired_stop_price = weak_stop
        elif vwap and atr and position.side == "SHORT":
            weak_stop = vwap + (atr * cfg.psm_weak_stop_atr)
            if weak_stop < position.current_stop:
                intent.desired_stop_price = weak_stop

        intent.cooldown_minutes = 30  # Cooldown before re-entry

        return intent


class NormalPolicy(BasePolicy):
    """
    NORMAL Policy - Standard rules

    - Partial TP: 30% at +0.8R, 30% at +1.5R
    - Trailing: stop = max(stop, swing_low - 0.8*ATR)
    - Time stop: 30min if < +0.5R
    """

    def get_action(
        self,
        position: ManagedPosition,
        current_price: float,
        vwap: Optional[float] = None,
        atr: Optional[float] = None,
        swing_low: Optional[float] = None,
    ) -> OrderIntent:
        cfg = self.config
        r_pnl = self._calc_r_pnl(position, current_price)

        intent = OrderIntent(
            symbol=position.symbol,
            desired_stop_price=position.current_stop,
            take_profit_orders=[],
        )

        # Time stop: 30min and < +0.5R
        time_in_trade = position.time_in_trade_minutes
        if time_in_trade >= cfg.psm_normal_time_stop_min and r_pnl < cfg.psm_normal_min_r:
            intent.exit_now = True
            intent.reason = f"NORMAL time stop: {time_in_trade:.0f}min, R={r_pnl:.2f}"
            logger.info(
                "NORMAL time stop triggered",
                symbol=position.symbol,
                time_min=time_in_trade,
                r_pnl=r_pnl,
            )
            return intent

        # Partial TPs
        if r_pnl >= cfg.psm_tp1_r and not position.tp_30_triggered:
            intent.take_profit_orders.append({
                "pct": cfg.psm_tp1_pct_normal,
                "price": current_price,
            })
            intent.reason = f"NORMAL: {cfg.psm_tp1_pct_normal*100:.0f}% TP at +{cfg.psm_tp1_r}R"
            logger.info(
                "NORMAL TP1 triggered",
                symbol=position.symbol,
                r_pnl=r_pnl,
                pct=cfg.psm_tp1_pct_normal,
            )

        if r_pnl >= cfg.psm_tp2_r and not position.tp_60_triggered:
            intent.take_profit_orders.append({
                "pct": cfg.psm_tp2_pct,
                "price": current_price,
            })
            intent.reason = f"NORMAL: {cfg.psm_tp2_pct*100:.0f}% TP at +{cfg.psm_tp2_r}R"
            logger.info(
                "NORMAL TP2 triggered",
                symbol=position.symbol,
                r_pnl=r_pnl,
                pct=cfg.psm_tp2_pct,
            )

        # Trailing stop using swing low - 0.8*ATR
        if swing_low and atr:
            new_stop = self._calc_stop_from_swing(
                position, swing_low, atr, cfg.psm_normal_trailing_atr
            )
            if position.side == "LONG" and new_stop > position.current_stop:
                intent.desired_stop_price = new_stop
            elif position.side == "SHORT" and new_stop < position.current_stop:
                intent.desired_stop_price = new_stop

        return intent


class StrongPolicy(BasePolicy):
    """
    STRONG Policy - Trend continuation

    - Slower TP: 20% at +0.8R, delay 1.5R TP to +2.0R
    - Looser trailing: stop = swing_low - 1.2*ATR
    - Stay while: close > VWAP + higher_low maintained
    - No time stop
    """

    def get_action(
        self,
        position: ManagedPosition,
        current_price: float,
        vwap: Optional[float] = None,
        atr: Optional[float] = None,
        swing_low: Optional[float] = None,
    ) -> OrderIntent:
        cfg = self.config
        r_pnl = self._calc_r_pnl(position, current_price)

        intent = OrderIntent(
            symbol=position.symbol,
            desired_stop_price=position.current_stop,
            take_profit_orders=[],
        )

        # Slower TPs - only 20% at +0.8R
        if r_pnl >= cfg.psm_tp1_r and not position.tp_30_triggered:
            intent.take_profit_orders.append({
                "pct": cfg.psm_tp1_pct_strong,
                "price": current_price,
            })
            intent.reason = f"STRONG: {cfg.psm_tp1_pct_strong*100:.0f}% TP at +{cfg.psm_tp1_r}R"
            logger.info(
                "STRONG TP1 triggered",
                symbol=position.symbol,
                r_pnl=r_pnl,
                pct=cfg.psm_tp1_pct_strong,
            )

        # Delay 1.5R TP - wait for +2R instead
        if r_pnl >= cfg.psm_tp2_strong_r and not position.tp_60_triggered:
            intent.take_profit_orders.append({
                "pct": cfg.psm_tp2_pct,
                "price": current_price,
            })
            intent.reason = f"STRONG: {cfg.psm_tp2_pct*100:.0f}% TP at +{cfg.psm_tp2_strong_r}R"
            logger.info(
                "STRONG TP2 triggered",
                symbol=position.symbol,
                r_pnl=r_pnl,
                pct=cfg.psm_tp2_pct,
            )

        # Looser trailing stop - 1.2*ATR
        if swing_low and atr:
            new_stop = self._calc_stop_from_swing(
                position, swing_low, atr, cfg.psm_strong_trailing_atr
            )
            if position.side == "LONG" and new_stop > position.current_stop:
                intent.desired_stop_price = new_stop
            elif position.side == "SHORT" and new_stop < position.current_stop:
                intent.desired_stop_price = new_stop

        # No time stop in STRONG - let it run

        return intent


class ExtremePolicy(BasePolicy):
    """
    EXTREME Policy - Climax/overheated

    - Immediate 50-70% TP
    - Tight trailing: stop = max(stop, vwap + 0.1*ATR)
    - 2x consecutive EXTREME -> full exit
    - No additional entries (reduce_only)
    """

    def get_action(
        self,
        position: ManagedPosition,
        current_price: float,
        vwap: Optional[float] = None,
        atr: Optional[float] = None,
        swing_low: Optional[float] = None,
    ) -> OrderIntent:
        cfg = self.config

        intent = OrderIntent(
            symbol=position.symbol,
            desired_stop_price=position.current_stop,
            take_profit_orders=[],
            reduce_only=True,  # Always reduce-only in EXTREME
        )

        # 2x consecutive EXTREME -> full exit
        if position.consecutive_extreme_count >= 2:
            intent.exit_now = True
            intent.reason = "EXTREME: 2x consecutive - full exit"
            logger.warning(
                "EXTREME full exit triggered",
                symbol=position.symbol,
                consecutive=position.consecutive_extreme_count,
            )
            return intent

        # Immediate TP based on consecutive count
        tp_pct = cfg.psm_extreme_tp_pct  # 50% default
        if position.consecutive_extreme_count == 1:
            tp_pct = 0.70  # Increase to 70% on second

        intent.take_profit_orders = [{"pct": tp_pct, "price": current_price}]
        intent.reason = f"EXTREME: {tp_pct*100:.0f}% immediate TP"
        intent.cooldown_minutes = cfg.psm_extreme_cooldown_min

        logger.info(
            "EXTREME TP triggered",
            symbol=position.symbol,
            tp_pct=tp_pct,
        )

        # Tight trailing using VWAP + 0.1*ATR
        if vwap and atr and position.side == "LONG":
            extreme_stop = vwap + (atr * cfg.psm_extreme_stop_atr)
            if extreme_stop > position.current_stop:
                intent.desired_stop_price = extreme_stop
        elif vwap and atr and position.side == "SHORT":
            extreme_stop = vwap - (atr * cfg.psm_extreme_stop_atr)
            if extreme_stop < position.current_stop:
                intent.desired_stop_price = extreme_stop

        return intent


def get_policy(state: PositionState) -> BasePolicy:
    """Get policy instance for state"""
    policies = {
        PositionState.WEAK: WeakPolicy,
        PositionState.NORMAL: NormalPolicy,
        PositionState.STRONG: StrongPolicy,
        PositionState.EXTREME: ExtremePolicy,
    }
    return policies[state]()
