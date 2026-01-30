"""Position State Machine Schemas"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class PositionState(str, Enum):
    """Position state machine states"""

    WEAK = "WEAK"  # Defensive mode - reduce/exit quickly
    NORMAL = "NORMAL"  # Standard rules - partial TP + trailing
    STRONG = "STRONG"  # Trend continuation - hold longer
    EXTREME = "EXTREME"  # Climax/overheated - aggressive TP


@dataclass
class ScoreBreakdown:
    """Detailed score components for position evaluation"""

    volume_score: float = 0.0  # A: 0-25
    vwap_score: float = 0.0  # B: -25 to +20
    structure_score: float = 0.0  # C: -20 to +15
    candle_score: float = 0.0  # D: 0-10
    momentum_score: float = 0.0  # E: 0-10
    overheat_penalty: float = 0.0  # F: 0 to -20
    btc_penalty: float = 0.0  # G: 0 to -30
    total_score: float = 50.0  # Base 50 + adjustments
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """Convert to dictionary for API response"""
        return {
            "volume": self.volume_score,
            "vwap": self.vwap_score,
            "structure": self.structure_score,
            "candle": self.candle_score,
            "momentum": self.momentum_score,
            "overheat_penalty": self.overheat_penalty,
            "btc_penalty": self.btc_penalty,
            "total": self.total_score,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ManagedPosition:
    """Extended position tracking for state machine"""

    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    quantity: float
    entry_time: datetime

    # R calculation
    initial_stop: float  # entry - 1.2 * ATR_5m
    r_value: float  # |entry_price - initial_stop|

    # Price tracking (MFE/MAE)
    highest_price: float
    lowest_price: float
    current_stop: float

    # State machine
    current_state: PositionState = PositionState.NORMAL
    state_history: list = field(default_factory=list)
    consecutive_weak_count: int = 0
    consecutive_strong_count: int = 0
    consecutive_extreme_count: int = 0

    # Partial TPs
    tp_30_triggered: bool = False  # 30% at +0.8R
    tp_60_triggered: bool = False  # Additional 30% at +1.5R

    # Score tracking
    last_score: float = 50.0
    score_history: list = field(default_factory=list)

    @property
    def time_in_trade_seconds(self) -> float:
        """Time since entry in seconds"""
        return (datetime.utcnow() - self.entry_time).total_seconds()

    @property
    def time_in_trade_minutes(self) -> float:
        """Time since entry in minutes"""
        return self.time_in_trade_seconds / 60

    def calc_r_pnl(self, current_price: float) -> float:
        """Calculate PnL in R multiples"""
        if self.r_value <= 0:
            return 0.0

        if self.side == "LONG":
            pnl = current_price - self.entry_price
        else:
            pnl = self.entry_price - current_price

        return pnl / self.r_value

    def calc_pnl_pct(self, current_price: float) -> float:
        """Calculate PnL percentage"""
        if self.entry_price <= 0:
            return 0.0

        if self.side == "LONG":
            return (current_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - current_price) / self.entry_price

    def update_extremes(self, current_price: float) -> None:
        """Update MFE/MAE tracking"""
        if current_price > self.highest_price:
            self.highest_price = current_price
        if current_price < self.lowest_price:
            self.lowest_price = current_price

    def to_dict(self) -> dict:
        """Convert to dictionary for API response"""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "entry_time": self.entry_time.isoformat(),
            "initial_stop": self.initial_stop,
            "r_value": self.r_value,
            "highest_price": self.highest_price,
            "lowest_price": self.lowest_price,
            "current_stop": self.current_stop,
            "current_state": self.current_state.value,
            "consecutive_weak": self.consecutive_weak_count,
            "consecutive_strong": self.consecutive_strong_count,
            "consecutive_extreme": self.consecutive_extreme_count,
            "tp_30_triggered": self.tp_30_triggered,
            "tp_60_triggered": self.tp_60_triggered,
            "last_score": self.last_score,
            "time_in_trade_minutes": self.time_in_trade_minutes,
            "state_history": self.state_history[-10:],  # Last 10 transitions
        }


@dataclass
class TakeProfitOrder:
    """Take profit order specification"""

    pct: float  # Percentage of position to close
    price: float  # Target price (or current price for market)


@dataclass
class OrderIntent:
    """Output from state machine - order intent for execution"""

    symbol: str
    desired_stop_price: float
    take_profit_orders: list = field(default_factory=list)  # List of TakeProfitOrder dicts
    exit_now: bool = False  # Full exit immediately
    reduce_only: bool = False  # No additional entries allowed
    cooldown_minutes: int = 0  # Minutes before re-entry
    reason: str = ""  # Explanation for the action

    def to_dict(self) -> dict:
        """Convert to dictionary for logging"""
        return {
            "symbol": self.symbol,
            "desired_stop_price": self.desired_stop_price,
            "take_profit_orders": self.take_profit_orders,
            "exit_now": self.exit_now,
            "reduce_only": self.reduce_only,
            "cooldown_minutes": self.cooldown_minutes,
            "reason": self.reason,
        }


@dataclass
class StateTransition:
    """Record of state transition"""

    from_state: str
    to_state: str
    timestamp: str
    score: Optional[float] = None
    reason: Optional[str] = None
