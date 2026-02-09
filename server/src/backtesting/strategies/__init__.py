"""백테스트용 전략 어댑터 (v27)"""

from src.backtesting.strategies.strategy_adapters import (
    get_signal_generator,
    get_exit_checker,
)

__all__ = [
    "get_signal_generator",
    "get_exit_checker",
]
