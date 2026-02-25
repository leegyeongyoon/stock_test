"""복합 전략 인프라

여러 전략을 동시에 실행하여 시그널을 통합하고,
포지션별 전략에 맞는 청산 파라미터를 라우팅
"""

from typing import Callable, Optional

from src.backtesting.data.data_loader import HistoryProvider
from src.backtesting.engine.backtest_engine import (
    SimulatedPosition,
    create_pullback_exit_checker,
)


class CombinedSignalGenerator:
    """
    여러 시그널 생성기를 래핑하여 모든 시그널을 수집

    - 각 전략의 시그널을 모두 수집
    - score 내림차순 정렬 (높은 확신 시그널 우선 체결)
    - 엔진의 심볼당 1포지션 제한이 자연스럽게 중복 방지
    """

    def __init__(self, generators: list[Callable]) -> None:
        self._generators = generators

    def __call__(self, timestamp, history, symbols) -> list[dict]:
        all_signals = []
        for gen in self._generators:
            signals = gen(timestamp, history, symbols)
            all_signals.extend(signals)

        # score 내림차순 정렬 → 높은 확신 시그널 우선
        all_signals.sort(key=lambda s: s.get("score", 0), reverse=True)
        return all_signals


class CombinedExitChecker:
    """
    position.strategy 기반으로 전략별 청산 파라미터 라우팅

    각 전략마다 다른 SL/TP/trailing/time_stop 적용
    """

    def __init__(self, exit_params: dict[str, dict]) -> None:
        """
        Args:
            exit_params: {전략명: {stop_loss_pct, take_profit_pct, ...}}
        """
        self._checkers: dict[str, Callable] = {}
        for strategy_name, params in exit_params.items():
            self._checkers[strategy_name] = create_pullback_exit_checker(
                stop_loss_pct=params.get("stop_loss_pct", -0.02),
                take_profit_pct=params.get("take_profit_pct", 0.02),
                trailing_trigger_pct=params.get("trailing_trigger_pct", 0.015),
                trailing_stop_pct=params.get("trailing_stop_pct", 0.008),
                time_stop_minutes=params.get("time_stop_minutes", 180),
            )

    def __call__(
        self,
        position: SimulatedPosition,
        current_price: float,
        history_provider: HistoryProvider,
    ) -> Optional[str]:
        strategy = position.strategy
        checker = self._checkers.get(strategy)
        if checker is None:
            # 폴백: 기본 파라미터
            checker = create_pullback_exit_checker()
        return checker(position, current_price, history_provider)
