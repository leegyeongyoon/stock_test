"""파라미터 최적화기

그리드 서치 또는 랜덤 서치로 최적 파라미터 탐색
"""

import itertools
import random
from datetime import datetime
from typing import Optional

import structlog

from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.models.schemas import (
    BacktestConfig,
    OptimizationConfig,
    OptimizationResult,
    OptimizationResultItem,
    BacktestMetricsSchema,
)

logger = structlog.get_logger()


class ParameterOptimizer:
    """
    파라미터 최적화기

    Methods:
    - Grid Search: 모든 조합 테스트
    - Random Search: 랜덤 샘플링
    """

    def __init__(
        self,
        config: OptimizationConfig,
        data_loader: BacktestDataLoader,
        signal_generator_factory: callable,
        exit_checker_factory: Optional[callable] = None,
    ) -> None:
        """
        Args:
            config: 최적화 설정
            data_loader: 데이터 로더
            signal_generator_factory: 시그널 생성기 팩토리
                (params: dict) -> callable
            exit_checker_factory: 청산 체커 팩토리 (선택)
                (params: dict) -> callable
        """
        self._config = config
        self._data_loader = data_loader
        self._signal_generator_factory = signal_generator_factory
        self._exit_checker_factory = exit_checker_factory

    async def optimize(
        self,
        progress_callback: Optional[callable] = None,
    ) -> OptimizationResult:
        """
        최적화 실행

        Args:
            progress_callback: 진행 상황 콜백
                (current, total) -> None

        Returns:
            최적화 결과
        """
        # 파라미터 조합 생성
        param_combinations = self._generate_combinations()
        total = len(param_combinations)

        logger.info(
            "Starting parameter optimization",
            strategy=self._config.strategy,
            method=self._config.method,
            total_combinations=total,
            target=self._config.optimize_target,
        )

        results: list[OptimizationResultItem] = []

        for idx, params in enumerate(param_combinations):
            # 백테스트 실행
            backtest_config = BacktestConfig(
                strategy=self._config.strategy,
                symbols=self._config.symbols,
                start_date=self._config.start_date,
                end_date=self._config.end_date,
                interval=self._config.interval,
                initial_capital=self._config.initial_capital,
                parameters=params,
            )

            engine = BacktestEngine(backtest_config, self._data_loader)

            signal_gen = self._signal_generator_factory(params)
            exit_checker = (
                self._exit_checker_factory(params)
                if self._exit_checker_factory
                else None
            )

            result = await engine.run(signal_gen, exit_checker)

            # 목표 지표 추출
            score = self._get_target_score(result.metrics)

            results.append(
                OptimizationResultItem(
                    parameters=params,
                    metrics=result.metrics,
                    score=score,
                )
            )

            if progress_callback:
                progress_callback(idx + 1, total)

            logger.debug(
                "Optimization iteration",
                params=params,
                score=score,
                progress=f"{idx+1}/{total}",
            )

        # 점수순 정렬
        results.sort(key=lambda x: x.score, reverse=True)

        # 상위 결과
        top_n = min(10, len(results))
        top_results = results[:top_n]

        best = results[0] if results else None

        return OptimizationResult(
            strategy=self._config.strategy,
            optimize_target=self._config.optimize_target,
            total_combinations=total,
            tested_combinations=len(results),
            best_parameters=best.parameters if best else {},
            best_metrics=best.metrics if best else self._empty_metrics(),
            best_score=best.score if best else 0,
            top_results=top_results,
            all_results=results,
            created_at=datetime.utcnow(),
        )

    def _generate_combinations(self) -> list[dict]:
        """파라미터 조합 생성"""
        param_grid = self._config.param_grid

        if self._config.method == "grid":
            # 그리드 서치: 모든 조합
            keys = list(param_grid.keys())
            values = [param_grid[k] for k in keys]

            combinations = []
            for combo in itertools.product(*values):
                combinations.append(dict(zip(keys, combo)))

            return combinations

        elif self._config.method == "random":
            # 랜덤 서치: 랜덤 샘플링
            max_iter = self._config.max_iterations
            combinations = []

            for _ in range(max_iter):
                combo = {}
                for key, values in param_grid.items():
                    combo[key] = random.choice(values)
                combinations.append(combo)

            # 중복 제거
            unique = []
            seen = set()
            for combo in combinations:
                key = tuple(sorted(combo.items()))
                if key not in seen:
                    seen.add(key)
                    unique.append(combo)

            return unique

        else:
            raise ValueError(f"Unknown optimization method: {self._config.method}")

    def _get_target_score(self, metrics: BacktestMetricsSchema) -> float:
        """목표 지표 점수 추출"""
        target = self._config.optimize_target

        score_map = {
            "sharpe_ratio": metrics.sharpe_ratio,
            "sortino_ratio": metrics.sortino_ratio,
            "calmar_ratio": metrics.calmar_ratio,
            "total_return_pct": metrics.total_return_pct,
            "win_rate": metrics.win_rate,
            "profit_factor": metrics.profit_factor,
            "max_drawdown_pct": -metrics.max_drawdown_pct,  # MDD는 낮을수록 좋음
        }

        return score_map.get(target, 0)

    def _empty_metrics(self) -> BacktestMetricsSchema:
        """빈 메트릭스"""
        return BacktestMetricsSchema(
            total_return_pct=0,
            max_drawdown_pct=0,
            total_trades=0,
            win_rate=0,
        )


async def run_walk_forward_optimization(
    config: OptimizationConfig,
    data_loader: BacktestDataLoader,
    signal_generator_factory: callable,
    exit_checker_factory: callable,
    window_days: int = 30,
    step_days: int = 7,
) -> list[OptimizationResult]:
    """
    Walk-Forward 최적화

    과적합 방지를 위해 롤링 윈도우로 최적화

    Args:
        config: 기본 최적화 설정
        data_loader: 데이터 로더
        signal_generator_factory: 시그널 생성기 팩토리
        exit_checker_factory: 청산 체커 팩토리
        window_days: 최적화 윈도우 (일)
        step_days: 스텝 크기 (일)

    Returns:
        윈도우별 최적화 결과 리스트
    """
    from datetime import timedelta

    results = []
    current_start = config.start_date

    while current_start + timedelta(days=window_days) <= config.end_date:
        window_end = current_start + timedelta(days=window_days)

        # 윈도우 설정
        window_config = OptimizationConfig(
            strategy=config.strategy,
            symbols=config.symbols,
            start_date=current_start,
            end_date=window_end,
            interval=config.interval,
            initial_capital=config.initial_capital,
            param_grid=config.param_grid,
            method=config.method,
            max_iterations=config.max_iterations,
            optimize_target=config.optimize_target,
        )

        optimizer = ParameterOptimizer(
            window_config,
            data_loader,
            signal_generator_factory,
            exit_checker_factory,
        )

        result = await optimizer.optimize()
        results.append(result)

        logger.info(
            "Walk-forward window complete",
            start=current_start.isoformat(),
            end=window_end.isoformat(),
            best_params=result.best_parameters,
            best_score=result.best_score,
        )

        current_start += timedelta(days=step_days)

    return results
