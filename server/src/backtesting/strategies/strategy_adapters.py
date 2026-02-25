"""백테스트용 전략 어댑터 (v3 + v4 전략)

v3.3 최적화: 30일 +15.39%, 90일 +13.04% 달성 3개 전략
v4.1: VolumeSurgeReversal 1개 (데이터 분석 기반 고도화)
"""

from typing import Callable

from src.backtesting.data.data_loader import HistoryProvider


def get_signal_generator(strategy: str, params: dict = None) -> Callable:
    """전략별 시그널 생성기 반환"""
    from src.backtesting.strategies.v3_strategies import (
        VolatileOversoldBounceSignalGenerator,
        CrashRecoverySignalGenerator,
        TripleBearishReversalSignalGenerator,
    )
    from src.backtesting.strategies.v4_strategies import (
        VolumeSurgeReversalSignalGenerator,
    )

    generators = {
        "VOLATILE_OVERSOLD_BOUNCE": VolatileOversoldBounceSignalGenerator,
        "CRASH_RECOVERY": CrashRecoverySignalGenerator,
        "TRIPLE_BEARISH_REVERSAL": TripleBearishReversalSignalGenerator,
        "VOLUME_SURGE_REVERSAL": VolumeSurgeReversalSignalGenerator,
    }

    if strategy not in generators:
        raise ValueError(f"Unknown strategy: {strategy}")

    return generators[strategy](params)


def get_exit_checker(strategy: str, params: dict = None) -> Callable:
    """전략별 청산 체커 반환"""
    from src.backtesting.engine.backtest_engine import create_pullback_exit_checker

    params = params or {}

    # v3.3 + v4.1 청산 파라미터
    default_params = {
        "VOLATILE_OVERSOLD_BOUNCE": {
            "stop_loss_pct": -0.020,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.008,
            "time_stop_minutes": 180,
        },
        "CRASH_RECOVERY": {
            "stop_loss_pct": -0.015,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.005,
            "time_stop_minutes": 120,
        },
        "TRIPLE_BEARISH_REVERSAL": {
            "stop_loss_pct": -0.020,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.020,
            "trailing_stop_pct": 0.010,
            "time_stop_minutes": 180,
        },
        "VOLUME_SURGE_REVERSAL": {
            "stop_loss_pct": -0.015,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.008,
            "time_stop_minutes": 90,
        },
    }

    strategy_defaults = default_params.get(strategy, default_params["VOLATILE_OVERSOLD_BOUNCE"])

    return create_pullback_exit_checker(
        stop_loss_pct=params.get("stop_loss_pct", strategy_defaults["stop_loss_pct"]),
        take_profit_pct=params.get("take_profit_pct", strategy_defaults["take_profit_pct"]),
        trailing_trigger_pct=params.get("trailing_trigger_pct", strategy_defaults["trailing_trigger_pct"]),
        trailing_stop_pct=params.get("trailing_stop_pct", strategy_defaults["trailing_stop_pct"]),
        time_stop_minutes=params.get("time_stop_minutes", strategy_defaults.get("time_stop_minutes", 0)),
    )
