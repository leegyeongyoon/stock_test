"""Position State Machine Configuration"""

from pydantic import Field
from pydantic_settings import BaseSettings


class PositionStateMachineConfig(BaseSettings):
    """Configuration for position state machine - can be overridden via .env"""

    # Score thresholds
    psm_weak_threshold: float = Field(
        default=40.0,
        description="Score below this -> WEAK state",
    )
    psm_strong_threshold: float = Field(
        default=70.0,
        description="Score above this (2 consecutive) -> STRONG state",
    )
    psm_extreme_threshold: float = Field(
        default=85.0,
        description="Score above this + overheat -> EXTREME state",
    )

    # Volume score thresholds (A: 0-25)
    psm_rvol_strong: float = Field(
        default=4.0,
        description="RVOL for +25 score",
    )
    psm_rvol_good: float = Field(
        default=2.5,
        description="RVOL for +15 score",
    )
    psm_rvol_normal: float = Field(
        default=1.5,
        description="RVOL for +8 score",
    )

    # VWAP score thresholds (B: -25 to +20)
    psm_vwap_above_strong: float = Field(
        default=0.005,
        description="Distance above VWAP for +20 score (0.5%)",
    )
    psm_vwap_above_normal: float = Field(
        default=0.0,
        description="Distance above VWAP for +10 score",
    )
    psm_vwap_below_weak: float = Field(
        default=-0.003,
        description="Distance below VWAP for -15 score (-0.3%)",
    )
    psm_vwap_below_critical: float = Field(
        default=-0.008,
        description="Distance below VWAP for -25 score (-0.8%)",
    )

    # Candle strength thresholds (D: 0-10)
    psm_close_pos_strong: float = Field(
        default=0.85,
        description="ClosePos for +10 score",
    )
    psm_close_pos_good: float = Field(
        default=0.70,
        description="ClosePos for +5 score",
    )

    # Momentum thresholds (E: 0-10)
    psm_momentum_strong: float = Field(
        default=0.02,
        description="3-bar momentum for +10 score (2%)",
    )
    psm_momentum_weak: float = Field(
        default=0.01,
        description="3-bar momentum for +5 score (1%)",
    )

    # Overheat detection (F: 0 to -20)
    psm_overheat_rvol: float = Field(
        default=6.0,
        description="RVOL threshold for overheat detection",
    )
    psm_overheat_wick_ratio: float = Field(
        default=1.5,
        description="Upper wick/body ratio for overheat",
    )

    # Time stops
    psm_weak_time_stop_min: int = Field(
        default=15,
        description="WEAK time stop in minutes",
    )
    psm_normal_time_stop_min: int = Field(
        default=30,
        description="NORMAL time stop in minutes",
    )

    # R thresholds for time stops
    psm_weak_min_r: float = Field(
        default=0.3,
        description="Min R for WEAK time stop exit",
    )
    psm_normal_min_r: float = Field(
        default=0.5,
        description="Min R for NORMAL time stop exit",
    )

    # ATR multipliers for stops
    psm_initial_stop_atr: float = Field(
        default=1.2,
        description="ATR multiplier for initial stop",
    )
    psm_weak_stop_atr: float = Field(
        default=0.3,
        description="ATR multiplier for WEAK stop (from VWAP)",
    )
    psm_normal_trailing_atr: float = Field(
        default=0.8,
        description="ATR multiplier for NORMAL trailing stop",
    )
    psm_strong_trailing_atr: float = Field(
        default=1.2,
        description="ATR multiplier for STRONG trailing stop",
    )
    psm_extreme_stop_atr: float = Field(
        default=0.1,
        description="ATR multiplier for EXTREME stop (from VWAP)",
    )

    # Take profit levels (R multiples)
    psm_tp1_r: float = Field(
        default=0.8,
        description="R multiple for first take profit",
    )
    psm_tp2_r: float = Field(
        default=1.5,
        description="R multiple for second take profit (NORMAL)",
    )
    psm_tp2_strong_r: float = Field(
        default=2.0,
        description="R multiple for second take profit (STRONG)",
    )

    # Take profit percentages
    psm_tp1_pct_normal: float = Field(
        default=0.30,
        description="Position % for TP1 in NORMAL state",
    )
    psm_tp1_pct_strong: float = Field(
        default=0.20,
        description="Position % for TP1 in STRONG state (smaller)",
    )
    psm_tp2_pct: float = Field(
        default=0.30,
        description="Position % for TP2",
    )
    psm_extreme_tp_pct: float = Field(
        default=0.50,
        description="Position % for immediate TP in EXTREME",
    )

    # Cooldown
    psm_extreme_cooldown_min: int = Field(
        default=60,
        description="Cooldown minutes after EXTREME exit",
    )

    class Config:
        env_prefix = ""  # No prefix - use PSM_ in .env
        extra = "ignore"


# Singleton instance
_config: PositionStateMachineConfig = None


def get_psm_config() -> PositionStateMachineConfig:
    """Get PSM configuration singleton"""
    global _config
    if _config is None:
        _config = PositionStateMachineConfig()
    return _config
