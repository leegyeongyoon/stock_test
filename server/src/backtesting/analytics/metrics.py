"""백테스트 성능 지표 계산

거래 내역에서 다양한 성능 지표를 계산
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from src.backtesting.models.schemas import BacktestMetricsSchema, BacktestTrade


@dataclass
class EquityPoint:
    """에쿼티 포인트"""

    timestamp: datetime
    equity: float
    drawdown_pct: float = 0.0


class BacktestMetrics:
    """
    백테스트 성능 지표 계산기

    계산 지표:
    - 수익률 (총/연환산)
    - 리스크 (MDD, 샤프, 소르티노, 칼마)
    - 거래 통계 (승률, 수익팩터, 평균 수익/손실)
    - 연속 승패
    """

    def __init__(
        self,
        trades: list[BacktestTrade],
        initial_capital: float,
        start_date: datetime,
        end_date: datetime,
    ) -> None:
        self._trades = trades
        self._initial_capital = initial_capital
        self._start_date = start_date
        self._end_date = end_date
        self._equity_curve: list[EquityPoint] = []

    def calculate(self) -> BacktestMetricsSchema:
        """모든 지표 계산"""
        if not self._trades:
            return self._empty_metrics()

        # 에쿼티 커브 생성
        self._build_equity_curve()

        # 수익률
        total_return_pct = self._calc_total_return()
        annualized_return_pct = self._calc_annualized_return(total_return_pct)

        # 리스크
        max_dd, max_dd_duration = self._calc_max_drawdown()
        sharpe = self._calc_sharpe_ratio()
        sortino = self._calc_sortino_ratio()
        calmar = self._calc_calmar_ratio(annualized_return_pct, max_dd)

        # 거래 통계
        winning = [t for t in self._trades if t.pnl > 0]
        losing = [t for t in self._trades if t.pnl < 0]

        win_rate = len(winning) / len(self._trades) * 100 if self._trades else 0
        profit_factor = self._calc_profit_factor(winning, losing)

        avg_win = (
            sum(t.pnl_pct for t in winning) / len(winning) * 100
            if winning
            else 0
        )
        avg_loss = (
            sum(t.pnl_pct for t in losing) / len(losing) * 100
            if losing
            else 0
        )

        avg_holding = self._calc_avg_holding_minutes()

        # 최대/최소
        best_trade = max(t.pnl_pct for t in self._trades) * 100 if self._trades else 0
        worst_trade = min(t.pnl_pct for t in self._trades) * 100 if self._trades else 0

        max_wins, max_losses = self._calc_consecutive_streaks()

        return BacktestMetricsSchema(
            total_return_pct=round(total_return_pct, 2),
            annualized_return_pct=round(annualized_return_pct, 2),
            max_drawdown_pct=round(max_dd, 2),
            max_drawdown_duration_days=max_dd_duration,
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            calmar_ratio=round(calmar, 2),
            total_trades=len(self._trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            avg_win_pct=round(avg_win, 2),
            avg_loss_pct=round(avg_loss, 2),
            avg_holding_minutes=round(avg_holding, 1),
            best_trade_pct=round(best_trade, 2),
            worst_trade_pct=round(worst_trade, 2),
            max_consecutive_wins=max_wins,
            max_consecutive_losses=max_losses,
        )

    def get_equity_curve(self) -> list[EquityPoint]:
        """에쿼티 커브 반환"""
        return self._equity_curve

    def _empty_metrics(self) -> BacktestMetricsSchema:
        """거래 없을 때 빈 지표"""
        return BacktestMetricsSchema(
            total_return_pct=0,
            annualized_return_pct=0,
            max_drawdown_pct=0,
            max_drawdown_duration_days=0,
            sharpe_ratio=0,
            sortino_ratio=0,
            calmar_ratio=0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            profit_factor=0,
            avg_win_pct=0,
            avg_loss_pct=0,
            avg_holding_minutes=0,
            best_trade_pct=0,
            worst_trade_pct=0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
        )

    def _build_equity_curve(self) -> None:
        """에쿼티 커브 생성"""
        self._equity_curve = []

        # 초기 포인트
        self._equity_curve.append(
            EquityPoint(
                timestamp=self._start_date,
                equity=self._initial_capital,
                drawdown_pct=0,
            )
        )

        equity = self._initial_capital
        peak = equity

        # 거래별 에쿼티 포인트
        sorted_trades = sorted(
            [t for t in self._trades if t.exit_time],
            key=lambda t: t.exit_time,
        )

        for trade in sorted_trades:
            equity += trade.pnl

            if equity > peak:
                peak = equity
                dd_pct = 0
            else:
                dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0

            self._equity_curve.append(
                EquityPoint(
                    timestamp=trade.exit_time,
                    equity=equity,
                    drawdown_pct=dd_pct,
                )
            )

    def _calc_total_return(self) -> float:
        """총 수익률 계산"""
        if not self._equity_curve:
            return 0

        final_equity = self._equity_curve[-1].equity
        return (final_equity - self._initial_capital) / self._initial_capital * 100

    def _calc_annualized_return(self, total_return_pct: float) -> float:
        """연환산 수익률"""
        days = (self._end_date - self._start_date).days
        if days <= 0:
            return 0

        years = days / 365.25
        if years < 0.01:  # 3.65일 미만
            return total_return_pct  # 연환산 무의미

        # (1 + r)^(1/years) - 1
        r = total_return_pct / 100
        if r <= -1:
            return -100

        return ((1 + r) ** (1 / years) - 1) * 100

    def _calc_max_drawdown(self) -> tuple[float, int]:
        """최대 낙폭 및 지속 기간"""
        if not self._equity_curve:
            return 0, 0

        max_dd = 0
        max_dd_duration = 0

        peak = self._equity_curve[0].equity
        peak_time = self._equity_curve[0].timestamp
        current_dd_start = None

        for point in self._equity_curve:
            if point.equity > peak:
                # 새 고점
                if current_dd_start:
                    duration = (point.timestamp - current_dd_start).days
                    max_dd_duration = max(max_dd_duration, duration)
                    current_dd_start = None

                peak = point.equity
                peak_time = point.timestamp
            else:
                # 낙폭 중
                dd = (peak - point.equity) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd

                if current_dd_start is None:
                    current_dd_start = peak_time

        # 마지막 낙폭이 회복 안됐으면
        if current_dd_start:
            duration = (self._end_date - current_dd_start).days
            max_dd_duration = max(max_dd_duration, duration)

        return max_dd, max_dd_duration

    def _calc_sharpe_ratio(self, risk_free_rate: float = 0.02) -> float:
        """
        샤프 비율 계산

        Sharpe = (평균 수익률 - 무위험 이자율) / 수익률 표준편차
        """
        if len(self._trades) < 2:
            return 0

        returns = [t.pnl_pct for t in self._trades]
        avg_return = sum(returns) / len(returns)

        # 일일 무위험 이자율
        days = (self._end_date - self._start_date).days or 1
        daily_rf = risk_free_rate / 365

        # 표준편차
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
        std = variance ** 0.5

        if std == 0:
            return 0

        # 연환산 샤프
        trades_per_day = len(self._trades) / days
        annual_factor = (252 * trades_per_day) ** 0.5 if trades_per_day > 0 else 1

        sharpe = (avg_return - daily_rf) / std * annual_factor
        return sharpe

    def _calc_sortino_ratio(self, risk_free_rate: float = 0.02) -> float:
        """
        소르티노 비율 계산

        Sortino = (평균 수익률 - 무위험 이자율) / 하방 표준편차
        """
        if len(self._trades) < 2:
            return 0

        returns = [t.pnl_pct for t in self._trades]
        avg_return = sum(returns) / len(returns)

        # 하방 편차만 계산
        downside_returns = [r for r in returns if r < 0]
        if not downside_returns:
            return 10.0  # 손실 없으면 최대값

        downside_variance = sum(r ** 2 for r in downside_returns) / len(downside_returns)
        downside_std = downside_variance ** 0.5

        if downside_std == 0:
            return 0

        days = (self._end_date - self._start_date).days or 1
        daily_rf = risk_free_rate / 365

        trades_per_day = len(self._trades) / days
        annual_factor = (252 * trades_per_day) ** 0.5 if trades_per_day > 0 else 1

        sortino = (avg_return - daily_rf) / downside_std * annual_factor
        return sortino

    def _calc_calmar_ratio(
        self, annualized_return: float, max_drawdown: float
    ) -> float:
        """
        칼마 비율 계산

        Calmar = 연환산 수익률 / 최대 낙폭
        """
        if max_drawdown <= 0:
            return 10.0 if annualized_return > 0 else 0

        return annualized_return / max_drawdown

    def _calc_profit_factor(
        self, winning: list[BacktestTrade], losing: list[BacktestTrade]
    ) -> float:
        """수익 팩터 = 총 이익 / 총 손실"""
        total_profit = sum(t.pnl for t in winning) if winning else 0
        total_loss = abs(sum(t.pnl for t in losing)) if losing else 0

        if total_loss == 0:
            return 10.0 if total_profit > 0 else 0

        return total_profit / total_loss

    def _calc_avg_holding_minutes(self) -> float:
        """평균 보유 시간 (분)"""
        holding_times = [
            t.holding_minutes for t in self._trades if t.holding_minutes is not None
        ]

        if not holding_times:
            return 0

        return sum(holding_times) / len(holding_times)

    def _calc_consecutive_streaks(self) -> tuple[int, int]:
        """최대 연속 승/패"""
        if not self._trades:
            return 0, 0

        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0

        for trade in self._trades:
            if trade.pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif trade.pnl < 0:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
            else:
                # 무승부
                current_wins = 0
                current_losses = 0

        return max_wins, max_losses


def calculate_metrics(
    trades: list[BacktestTrade],
    initial_capital: float,
    start_date: datetime,
    end_date: datetime,
) -> BacktestMetricsSchema:
    """편의 함수"""
    calculator = BacktestMetrics(trades, initial_capital, start_date, end_date)
    return calculator.calculate()
