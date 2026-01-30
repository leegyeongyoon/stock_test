"""Score Calculator for Position State Machine

Calculates position strength score (0-100) based on:
A) Volume (RVOL): 0-25
B) VWAP position: -25 to +20
C) Structure (higher/lower lows): -20 to +15
D) Candle strength (ClosePos): 0-10
E) Momentum: 0-10
F) Overheat penalty: 0 to -20
G) BTC regime penalty: 0 to -30
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import structlog

from src.position.config import PositionStateMachineConfig, get_psm_config
from src.position.schemas import ScoreBreakdown

if TYPE_CHECKING:
    from src.data.candle_manager import CandleManager
    from src.features.feature_engine import FeatureEngine

logger = structlog.get_logger()


@dataclass
class ScoreConfig:
    """Score calculation configuration (from PSM config)"""

    # Volume thresholds
    rvol_strong: float = 4.0
    rvol_good: float = 2.5
    rvol_normal: float = 1.5

    # VWAP thresholds
    vwap_above_strong: float = 0.005
    vwap_above_normal: float = 0.0
    vwap_below_weak: float = -0.003
    vwap_below_critical: float = -0.008

    # Candle thresholds
    close_pos_strong: float = 0.85
    close_pos_good: float = 0.70

    # Momentum thresholds
    momentum_strong: float = 0.02
    momentum_weak: float = 0.01

    # Overheat thresholds
    overheat_rvol: float = 6.0
    overheat_wick_ratio: float = 1.5

    @classmethod
    def from_psm_config(cls, cfg: PositionStateMachineConfig) -> "ScoreConfig":
        """Create from PSM config"""
        return cls(
            rvol_strong=cfg.psm_rvol_strong,
            rvol_good=cfg.psm_rvol_good,
            rvol_normal=cfg.psm_rvol_normal,
            vwap_above_strong=cfg.psm_vwap_above_strong,
            vwap_above_normal=cfg.psm_vwap_above_normal,
            vwap_below_weak=cfg.psm_vwap_below_weak,
            vwap_below_critical=cfg.psm_vwap_below_critical,
            close_pos_strong=cfg.psm_close_pos_strong,
            close_pos_good=cfg.psm_close_pos_good,
            momentum_strong=cfg.psm_momentum_strong,
            momentum_weak=cfg.psm_momentum_weak,
            overheat_rvol=cfg.psm_overheat_rvol,
            overheat_wick_ratio=cfg.psm_overheat_wick_ratio,
        )


class ScoreCalculator:
    """
    Calculate position strength score (0-100)

    Base score is 50, with adjustments from 7 components:
    A) Volume (RVOL): 0-25
    B) VWAP position: -25 to +20
    C) Structure: -20 to +15
    D) Candle strength: 0-10
    E) Momentum: 0-10
    F) Overheat penalty: 0 to -20
    G) BTC regime penalty: 0 to -30
    """

    def __init__(
        self,
        feature_engine: "FeatureEngine",
        candle_manager: "CandleManager",
        config: Optional[ScoreConfig] = None,
    ):
        self.feature_engine = feature_engine
        self.candle_manager = candle_manager

        if config is None:
            psm_cfg = get_psm_config()
            config = ScoreConfig.from_psm_config(psm_cfg)
        self.config = config

    def calculate_score(self, symbol: str, position_side: str) -> ScoreBreakdown:
        """
        Calculate comprehensive position score

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            position_side: "LONG" or "SHORT"

        Returns:
            ScoreBreakdown with all score components
        """
        breakdown = ScoreBreakdown()

        # A) Volume Score (0-25)
        breakdown.volume_score = self._calc_volume_score(symbol)

        # B) VWAP Position (-25 to +20)
        breakdown.vwap_score = self._calc_vwap_score(symbol, position_side)

        # C) Structure Score (-20 to +15)
        breakdown.structure_score = self._calc_structure_score(symbol, position_side)

        # D) Candle Strength (0-10)
        breakdown.candle_score = self._calc_candle_score(symbol, position_side)

        # E) Momentum (0-10)
        breakdown.momentum_score = self._calc_momentum_score(symbol, position_side)

        # F) Overheat Penalty (0 to -20)
        breakdown.overheat_penalty = self._calc_overheat_penalty(symbol)

        # G) BTC Regime Penalty (0 to -30)
        breakdown.btc_penalty = self._calc_btc_penalty(position_side)

        # Total = 50 (base) + all adjustments, clamped to 0-100
        raw_score = (
            50
            + breakdown.volume_score
            + breakdown.vwap_score
            + breakdown.structure_score
            + breakdown.candle_score
            + breakdown.momentum_score
            + breakdown.overheat_penalty
            + breakdown.btc_penalty
        )
        breakdown.total_score = max(0, min(100, raw_score))

        logger.debug(
            "Score calculated",
            symbol=symbol,
            side=position_side,
            total=breakdown.total_score,
            breakdown=breakdown.to_dict(),
        )

        return breakdown

    def _calc_volume_score(self, symbol: str) -> float:
        """A) RVOL-based score: 0-25"""
        features = self.feature_engine.get_features(symbol)
        if not features:
            return 0

        rvol = features.rvol_5m
        cfg = self.config

        if rvol >= cfg.rvol_strong:
            return 25
        elif rvol >= cfg.rvol_good:
            return 15
        elif rvol >= cfg.rvol_normal:
            return 8
        return 0

    def _calc_vwap_score(self, symbol: str, side: str) -> float:
        """B) VWAP position score: -25 to +20"""
        features = self.feature_engine.get_features(symbol)

        if not features or features.vwap_5m <= 0:
            return 0

        # Get current close
        candles = self.candle_manager.get_candles(symbol, "5m", 1)
        if not candles:
            return 0

        current_price = candles[-1].close
        vwap = features.vwap_5m

        # Distance from VWAP as percentage
        distance_pct = (current_price - vwap) / vwap

        # For SHORT: above VWAP is bad, below is good (invert)
        if side == "SHORT":
            distance_pct = -distance_pct

        cfg = self.config

        if distance_pct >= cfg.vwap_above_strong:
            return 20
        elif distance_pct >= cfg.vwap_above_normal:
            return 10
        elif distance_pct >= cfg.vwap_below_weak:
            return 0
        elif distance_pct >= cfg.vwap_below_critical:
            return -15
        return -25

    def _calc_structure_score(self, symbol: str, side: str) -> float:
        """C) Higher low / Lower low structure: -20 to +15"""
        candles = self.candle_manager.get_candles(symbol, "5m", 6)

        if len(candles) < 6:
            return 0

        # Get swing lows from recent and previous periods
        recent_lows = [c.low for c in candles[-3:]]
        prev_lows = [c.low for c in candles[-6:-3]]

        current_swing_low = min(recent_lows)
        prev_swing_low = min(prev_lows)

        # Check for higher low (bullish) or lower low (bearish)
        is_higher_low = current_swing_low > prev_swing_low * 1.001  # 0.1% buffer
        is_lower_low = current_swing_low < prev_swing_low * 0.999

        if side == "LONG":
            if is_higher_low:
                return 15
            elif is_lower_low:
                return -20
        else:  # SHORT
            # For short, lower lows are good
            if is_lower_low:
                return 15
            elif is_higher_low:
                return -20

        return 0

    def _calc_candle_score(self, symbol: str, side: str) -> float:
        """D) ClosePos strength: 0-10"""
        features = self.feature_engine.get_features(symbol)

        if not features:
            return 0

        close_pos = features.close_pos

        # For SHORT, invert close_pos (low close is strong)
        if side == "SHORT":
            close_pos = 1 - close_pos

        cfg = self.config

        if close_pos >= cfg.close_pos_strong:
            return 10
        elif close_pos >= cfg.close_pos_good:
            return 5
        return 0

    def _calc_momentum_score(self, symbol: str, side: str) -> float:
        """E) 3-bar momentum: 0-10"""
        candles = self.candle_manager.get_candles(symbol, "5m", 4)

        if len(candles) < 4:
            return 0

        # 3-bar price change (15 minutes)
        price_change = (candles[-1].close - candles[-4].close) / candles[-4].close

        # For SHORT, positive momentum is bad
        if side == "SHORT":
            price_change = -price_change

        cfg = self.config

        if price_change >= cfg.momentum_strong:
            return 10
        elif price_change >= cfg.momentum_weak:
            return 5
        return 0

    def _calc_overheat_penalty(self, symbol: str) -> float:
        """F) Overheat penalty: 0 to -20"""
        features = self.feature_engine.get_features(symbol)
        candles = self.candle_manager.get_candles(symbol, "5m", 1)

        if not features or not candles:
            return 0

        candle = candles[-1]
        rvol = features.rvol_5m

        # Calculate upper wick ratio
        body = abs(candle.close - candle.open)
        upper_wick = candle.high - max(candle.open, candle.close)

        if body <= 0:
            wick_ratio = 0
        else:
            wick_ratio = upper_wick / body

        cfg = self.config

        # Overheat: RVOL >= 6 + long upper wick
        if rvol >= cfg.overheat_rvol and wick_ratio >= cfg.overheat_wick_ratio:
            return -20
        elif rvol >= cfg.overheat_rvol * 0.67:  # ~4.0
            if wick_ratio >= cfg.overheat_wick_ratio * 0.8:  # ~1.2
                return -10

        return 0

    def _calc_btc_penalty(self, side: str) -> float:
        """G) BTC regime penalty: 0 to -30"""
        btc_regime = self.feature_engine.check_btc_regime()

        regime = btc_regime.get("regime", "NEUTRAL")
        is_volatile = btc_regime.get("is_volatile", False)

        # RISK-OFF: volatile or extreme ATR
        if is_volatile or regime == "VOLATILE":
            return -30

        # Regime mismatch
        if side == "LONG" and regime == "BEARISH":
            return -30
        if side == "SHORT" and regime == "BULLISH":
            return -30

        if regime == "NEUTRAL":
            return -10

        return 0

    def check_overheat_condition(self, symbol: str) -> bool:
        """
        Check if overheat (EXTREME) hard rule applies

        Overheat condition:
        - RVOL >= 6
        - Upper wick >= 1.5x body
        - Failed new high (high > prev_high but close < high * 0.995)
        """
        features = self.feature_engine.get_features(symbol)
        candles = self.candle_manager.get_candles(symbol, "5m", 2)

        if not features or len(candles) < 2:
            return False

        candle = candles[-1]
        prev_candle = candles[-2]
        rvol = features.rvol_5m

        # Calculate upper wick ratio
        body = abs(candle.close - candle.open)
        upper_wick = candle.high - max(candle.open, candle.close)
        wick_ratio = upper_wick / body if body > 0 else 0

        # Failed new high
        made_new_high = candle.high > prev_candle.high
        failed_to_hold = candle.close < candle.high * 0.995

        cfg = self.config

        return (
            rvol >= cfg.overheat_rvol
            and wick_ratio >= cfg.overheat_wick_ratio
            and made_new_high
            and failed_to_hold
        )

    def check_structure_break(self, symbol: str, side: str) -> bool:
        """
        Check for structure break (WEAK hard rule)

        Structure break for LONG:
        - Lower low detected
        - Close below VWAP for 2 consecutive candles
        """
        candles = self.candle_manager.get_candles(symbol, "5m", 6)
        features = self.feature_engine.get_features(symbol)

        if len(candles) < 6 or not features:
            return False

        # Check lower low
        recent_lows = [c.low for c in candles[-3:]]
        prev_lows = [c.low for c in candles[-6:-3]]

        current_swing_low = min(recent_lows)
        prev_swing_low = min(prev_lows)

        is_lower_low = current_swing_low < prev_swing_low * 0.998

        # Check VWAP position (2 consecutive below)
        vwap = features.vwap_5m
        if vwap <= 0:
            return False

        closes = [c.close for c in candles[-2:]]
        below_vwap_2x = all(c < vwap for c in closes)

        if side == "LONG":
            return is_lower_low and below_vwap_2x
        else:
            # For SHORT: higher high + above VWAP
            recent_highs = [c.high for c in candles[-3:]]
            prev_highs = [c.high for c in candles[-6:-3]]
            is_higher_high = max(recent_highs) > max(prev_highs) * 1.002
            above_vwap_2x = all(c > vwap for c in closes)
            return is_higher_high and above_vwap_2x
