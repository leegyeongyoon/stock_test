"""백테스트용 전략 어댑터 (v27)

고승률 10개 전략 - SMA/EMA/RVOL/ATR 전용
"""

from typing import Callable

from src.backtesting.data.data_loader import HistoryProvider


def get_signal_generator(strategy: str, params: dict = None) -> Callable:
    """전략별 시그널 생성기 반환"""
    from src.backtesting.strategies.new_strategies import (
        EmaBouncScalpSignalGenerator,
        DoubleDipBuySignalGenerator,
        TightRangeBreakoutSignalGenerator,
        SupportTouchBounceSignalGenerator,
        VolumeClimaxReversalSignalGenerator,
        EmaCrossoverMomentumSignalGenerator,
        AtrExpansionEntrySignalGenerator,
        MicroPullbackLongSignalGenerator,
        TripleConfirmationEntrySignalGenerator,
        FastScalpReboundSignalGenerator,
    )

    generators = {
        "EMA_BOUNCE_SCALP": EmaBouncScalpSignalGenerator,
        "DOUBLE_DIP_BUY": DoubleDipBuySignalGenerator,
        "TIGHT_RANGE_BREAKOUT": TightRangeBreakoutSignalGenerator,
        "SUPPORT_TOUCH_BOUNCE": SupportTouchBounceSignalGenerator,
        "VOLUME_CLIMAX_REVERSAL": VolumeClimaxReversalSignalGenerator,
        "EMA_CROSSOVER_MOMENTUM": EmaCrossoverMomentumSignalGenerator,
        "ATR_EXPANSION_ENTRY": AtrExpansionEntrySignalGenerator,
        "MICRO_PULLBACK_LONG": MicroPullbackLongSignalGenerator,
        "TRIPLE_CONFIRMATION_ENTRY": TripleConfirmationEntrySignalGenerator,
        "FAST_SCALP_REBOUND": FastScalpReboundSignalGenerator,
    }

    if strategy not in generators:
        raise ValueError(f"Unknown strategy: {strategy}")

    return generators[strategy](params)


def get_exit_checker(strategy: str, params: dict = None) -> Callable:
    """전략별 청산 체커 반환"""
    from src.backtesting.engine.backtest_engine import create_pullback_exit_checker

    params = params or {}

    # v27: 고승률 10개 전략 청산 파라미터
    default_params = {
        # Strategy 1: EMA_BOUNCE_SCALP (68% WR) - R:R 2.5:1
        "EMA_BOUNCE_SCALP": {
            "stop_loss_pct": -0.006,       # -0.6%
            "take_profit_pct": 0.015,      # +1.5%
            "trailing_trigger_pct": 0.010,
            "trailing_stop_pct": 0.004,
        },
        # Strategy 2: DOUBLE_DIP_BUY (65% WR) - R:R 5.5:1
        "DOUBLE_DIP_BUY": {
            "stop_loss_pct": -0.004,       # -0.4%
            "take_profit_pct": 0.022,      # +2.2%
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.003,
        },
        # Strategy 3: TIGHT_RANGE_BREAKOUT (62% WR) - R:R 3.1:1
        "TIGHT_RANGE_BREAKOUT": {
            "stop_loss_pct": -0.008,       # -0.8%
            "take_profit_pct": 0.025,      # +2.5%
            "trailing_trigger_pct": 0.018,
            "trailing_stop_pct": 0.005,
        },
        # Strategy 4: SUPPORT_TOUCH_BOUNCE (70% WR) - R:R 2.4:1
        "SUPPORT_TOUCH_BOUNCE": {
            "stop_loss_pct": -0.005,       # -0.5%
            "take_profit_pct": 0.012,      # +1.2%
            "trailing_trigger_pct": 0.008,
            "trailing_stop_pct": 0.003,
        },
        # Strategy 5: VOLUME_CLIMAX_REVERSAL (66% WR) - R:R 3:1
        "VOLUME_CLIMAX_REVERSAL": {
            "stop_loss_pct": -0.006,       # -0.6%
            "take_profit_pct": 0.018,      # +1.8%
            "trailing_trigger_pct": 0.012,
            "trailing_stop_pct": 0.004,
        },
        # Strategy 6: EMA_CROSSOVER_MOMENTUM (58% WR) - R:R 3:1
        "EMA_CROSSOVER_MOMENTUM": {
            "stop_loss_pct": -0.010,       # -1.0%
            "take_profit_pct": 0.030,      # +3.0%
            "trailing_trigger_pct": 0.020,
            "trailing_stop_pct": 0.006,
        },
        # Strategy 7: ATR_EXPANSION_ENTRY (64% WR) - R:R 3:1
        "ATR_EXPANSION_ENTRY": {
            "stop_loss_pct": -0.008,       # -0.8%
            "take_profit_pct": 0.024,      # +2.4%
            "trailing_trigger_pct": 0.016,
            "trailing_stop_pct": 0.005,
        },
        # Strategy 8: MICRO_PULLBACK_LONG (67% WR) - R:R 2.7:1
        "MICRO_PULLBACK_LONG": {
            "stop_loss_pct": -0.006,       # -0.6%
            "take_profit_pct": 0.016,      # +1.6%
            "trailing_trigger_pct": 0.010,
            "trailing_stop_pct": 0.004,
        },
        # Strategy 9: TRIPLE_CONFIRMATION_ENTRY (71% WR) - R:R 2.8:1
        "TRIPLE_CONFIRMATION_ENTRY": {
            "stop_loss_pct": -0.005,       # -0.5%
            "take_profit_pct": 0.014,      # +1.4%
            "trailing_trigger_pct": 0.010,
            "trailing_stop_pct": 0.003,
        },
        # Strategy 10: FAST_SCALP_REBOUND (63% WR) - R:R 3:1
        "FAST_SCALP_REBOUND": {
            "stop_loss_pct": -0.005,       # -0.5%
            "take_profit_pct": 0.015,      # +1.5%
            "trailing_trigger_pct": 0.010,
            "trailing_stop_pct": 0.003,
        },
    }

    strategy_defaults = default_params.get(strategy, default_params["EMA_BOUNCE_SCALP"])

    return create_pullback_exit_checker(
        stop_loss_pct=params.get("stop_loss_pct", strategy_defaults["stop_loss_pct"]),
        take_profit_pct=params.get("take_profit_pct", strategy_defaults["take_profit_pct"]),
        trailing_trigger_pct=params.get("trailing_trigger_pct", strategy_defaults["trailing_trigger_pct"]),
        trailing_stop_pct=params.get("trailing_stop_pct", strategy_defaults["trailing_stop_pct"]),
    )
