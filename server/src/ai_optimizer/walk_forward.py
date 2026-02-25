"""Walk-Forward 검증

3개월 데이터를 학습/테스트로 분할하여 과적합 검증.
통과 기준: OOS 수익률 > 0, OOS Sharpe > 0.5, OOS/IS 비율 > 50%
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

from src.ai_optimizer.schemas import StrategyCandidate
from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.models.schemas import BacktestConfig
from src.models.database import async_session

logger = structlog.get_logger()


@dataclass
class WalkForwardSplit:
    """학습/테스트 분할"""
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


@dataclass
class WalkForwardResult:
    """Walk-Forward 결과"""
    splits: list[WalkForwardSplit] = field(default_factory=list)

    # 각 split별 결과
    is_returns: list[float] = field(default_factory=list)  # In-Sample 수익률
    oos_returns: list[float] = field(default_factory=list)  # Out-of-Sample 수익률
    is_sharpes: list[float] = field(default_factory=list)
    oos_sharpes: list[float] = field(default_factory=list)

    # 요약
    avg_is_return: float = 0.0
    avg_oos_return: float = 0.0
    avg_is_sharpe: float = 0.0
    avg_oos_sharpe: float = 0.0
    oos_is_ratio: float = 0.0  # OOS/IS 비율 (%)
    passed: bool = False

    # 통과 기준
    min_oos_return: float = 0.0
    min_oos_sharpe: float = 0.5
    min_oos_is_ratio: float = 50.0


class WalkForwardValidator:
    """Walk-Forward 검증기"""

    def __init__(
        self,
        symbols: list[str],
        interval: str = "5m",
        initial_capital: float = 1_000_000,
    ):
        self._symbols = symbols
        self._interval = interval
        self._initial_capital = initial_capital

    def create_splits(
        self,
        start_date: datetime,
        end_date: datetime,
        train_days: int = 60,
        test_days: int = 30,
        step_days: int = 15,
    ) -> list[WalkForwardSplit]:
        """슬라이딩 윈도우 분할 생성"""
        splits = []
        current = start_date

        while current + timedelta(days=train_days + test_days) <= end_date:
            train_end = current + timedelta(days=train_days)
            test_end = train_end + timedelta(days=test_days)

            splits.append(WalkForwardSplit(
                train_start=current,
                train_end=train_end,
                test_start=train_end,
                test_end=test_end,
            ))

            current += timedelta(days=step_days)

        logger.info("Created walk-forward splits", count=len(splits))
        return splits

    async def validate(
        self,
        candidate: StrategyCandidate,
        strategy_name: str,
        start_date: datetime,
        end_date: datetime,
        train_days: int = 60,
        test_days: int = 30,
    ) -> WalkForwardResult:
        """Walk-Forward 검증 실행

        각 백테스트마다 전략을 새로 컴파일하여 내부 상태(_last_entry 등)
        누적으로 인한 거래 차단을 방지합니다.
        """
        from src.ai_optimizer.strategy_codegen import StrategyCodegen
        codegen = StrategyCodegen()

        splits = self.create_splits(
            start_date, end_date,
            train_days=train_days,
            test_days=test_days,
        )

        if not splits:
            logger.warning("No valid splits for walk-forward validation")
            return WalkForwardResult(passed=False)

        result = WalkForwardResult(splits=splits)

        for i, split in enumerate(splits):
            logger.info(
                "Running walk-forward split",
                split=i + 1,
                total=len(splits),
                train=f"{split.train_start.date()} ~ {split.train_end.date()}",
                test=f"{split.test_start.date()} ~ {split.test_end.date()}",
            )

            # In-Sample 백테스트 (새 인스턴스)
            sig_is, exit_is, errors_is = codegen.compile_strategy(candidate)
            if errors_is or sig_is is None:
                logger.error("WF IS compile failed", candidate=candidate.name, errors=errors_is)
                result.is_returns.append(0)
                result.oos_returns.append(0)
                result.is_sharpes.append(0)
                result.oos_sharpes.append(0)
                continue
            is_metrics = await self._run_backtest(
                sig_is, exit_is, strategy_name,
                split.train_start, split.train_end,
            )

            # Out-of-Sample 백테스트 (새 인스턴스)
            sig_oos, exit_oos, errors_oos = codegen.compile_strategy(candidate)
            if errors_oos or sig_oos is None:
                logger.error("WF OOS compile failed", candidate=candidate.name, errors=errors_oos)
                result.is_returns.append(is_metrics.get("return_pct", 0))
                result.oos_returns.append(0)
                result.is_sharpes.append(is_metrics.get("sharpe", 0))
                result.oos_sharpes.append(0)
                continue
            oos_metrics = await self._run_backtest(
                sig_oos, exit_oos, strategy_name,
                split.test_start, split.test_end,
            )

            result.is_returns.append(is_metrics.get("return_pct", 0))
            result.oos_returns.append(oos_metrics.get("return_pct", 0))
            result.is_sharpes.append(is_metrics.get("sharpe", 0))
            result.oos_sharpes.append(oos_metrics.get("sharpe", 0))

            logger.info(
                "Split result",
                split=i + 1,
                is_return=f"{is_metrics.get('return_pct', 0):+.2f}%",
                oos_return=f"{oos_metrics.get('return_pct', 0):+.2f}%",
            )

        # 요약 계산
        if result.is_returns:
            result.avg_is_return = sum(result.is_returns) / len(result.is_returns)
            result.avg_oos_return = sum(result.oos_returns) / len(result.oos_returns)
            result.avg_is_sharpe = sum(result.is_sharpes) / len(result.is_sharpes)
            result.avg_oos_sharpe = sum(result.oos_sharpes) / len(result.oos_sharpes)

            if result.avg_is_return != 0:
                result.oos_is_ratio = (result.avg_oos_return / result.avg_is_return) * 100
            else:
                result.oos_is_ratio = 100.0 if result.avg_oos_return > 0 else 0.0

        # 통과 판정
        result.passed = (
            result.avg_oos_return > result.min_oos_return
            and result.avg_oos_sharpe > result.min_oos_sharpe
            and result.oos_is_ratio > result.min_oos_is_ratio
        )

        logger.info(
            "Walk-forward validation complete",
            avg_is_return=f"{result.avg_is_return:+.2f}%",
            avg_oos_return=f"{result.avg_oos_return:+.2f}%",
            oos_is_ratio=f"{result.oos_is_ratio:.1f}%",
            passed=result.passed,
        )

        return result

    async def _run_backtest(
        self,
        signal_generator: callable,
        exit_checker: Optional[callable],
        strategy_name: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict:
        """단일 구간 백테스트 실행 (매번 새 DB 세션 사용)"""
        config = BacktestConfig(
            strategy=strategy_name,
            symbols=self._symbols,
            start_date=start_date,
            end_date=end_date,
            interval=self._interval,
            initial_capital=self._initial_capital,
        )

        try:
            async with async_session() as session:
                data_loader = BacktestDataLoader(session)
                engine = BacktestEngine(config, data_loader)
                result = await engine.run(signal_generator, exit_checker)
                return {
                    "return_pct": result.metrics.total_return_pct,
                    "sharpe": result.metrics.sharpe_ratio or 0,
                    "max_dd": result.metrics.max_drawdown_pct,
                    "win_rate": result.metrics.win_rate,
                    "trades": result.metrics.total_trades,
                    "pf": result.metrics.profit_factor or 0,
                }
        except Exception as e:
            logger.error("Backtest failed in walk-forward", error=str(e))
            return {"return_pct": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0, "trades": 0, "pf": 0}
