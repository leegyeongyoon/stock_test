"""분석 서비스 - 비즈니스 로직"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.analytics_schemas import (
    DailyReturn,
    EquityCurveResponse,
    EquityPoint,
    HourlyPnl,
    HourlyPnlResponse,
    PeriodReturnsResponse,
    PeriodType,
    StrategyPnl,
    StrategyPnlResponse,
    SymbolPnl,
    SymbolPnlResponse,
)
from src.models.database import (
    DailyStatsModel,
    EquitySnapshotModel,
    TradeModel,
)


class AnalyticsService:
    """분석 서비스"""

    def __init__(self, session: AsyncSession):
        self._session = session

    def _get_date_range(self, period: PeriodType) -> tuple[datetime, datetime]:
        """기간에 따른 날짜 범위 계산"""
        end_date = datetime.utcnow()
        period_days = {
            PeriodType.WEEK: 7,
            PeriodType.MONTH: 30,
            PeriodType.THREE_MONTHS: 90,
            PeriodType.SIX_MONTHS: 180,
            PeriodType.YEAR: 365,
        }
        start_date = end_date - timedelta(days=period_days[period])
        return start_date, end_date

    async def get_period_returns(self, period: PeriodType) -> PeriodReturnsResponse:
        """기간별 수익률 조회"""
        start_date, end_date = self._get_date_range(period)

        stmt = (
            select(DailyStatsModel)
            .where(
                DailyStatsModel.date >= start_date.strftime("%Y-%m-%d"),
                DailyStatsModel.date <= end_date.strftime("%Y-%m-%d"),
            )
            .order_by(DailyStatsModel.date)
        )

        result = await self._session.execute(stmt)
        daily_stats = result.scalars().all()

        daily_returns = []
        total_pnl = 0.0
        max_equity = 0.0
        max_drawdown = 0.0
        wins = 0
        losses = 0
        starting_equity = 10000.0  # 기본값

        for stat in daily_stats:
            if starting_equity == 10000.0 and stat.starting_equity > 0:
                starting_equity = stat.starting_equity

            pnl_pct = stat.pnl / stat.starting_equity if stat.starting_equity > 0 else 0

            daily_returns.append(
                DailyReturn(
                    date=stat.date,
                    pnl=stat.pnl,
                    pnl_pct=pnl_pct,
                    equity=stat.ending_equity,
                    core_pnl=stat.core_pnl,
                    satellite_pnl=stat.satellite_pnl,
                )
            )

            total_pnl += stat.pnl

            if stat.ending_equity > max_equity:
                max_equity = stat.ending_equity

            dd = (stat.ending_equity - max_equity) / max_equity if max_equity > 0 else 0
            if dd < max_drawdown:
                max_drawdown = dd

            if stat.pnl > 0:
                wins += 1
            elif stat.pnl < 0:
                losses += 1

        total_pnl_pct = total_pnl / starting_equity if starting_equity > 0 else 0
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0

        return PeriodReturnsResponse(
            period=period,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            daily_returns=daily_returns,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
        )

    async def get_symbol_pnl(self, period: PeriodType) -> SymbolPnlResponse:
        """종목별 수익 조회"""
        start_date, end_date = self._get_date_range(period)

        stmt = (
            select(
                TradeModel.symbol,
                TradeModel.strategy,
                func.sum(TradeModel.realized_pnl).label("realized_pnl"),
                func.count().label("trades_count"),
                func.sum(func.iif(TradeModel.realized_pnl > 0, 1, 0)).label("wins"),
            )
            .where(
                TradeModel.executed_at >= start_date,
                TradeModel.executed_at <= end_date,
            )
            .group_by(TradeModel.symbol, TradeModel.strategy)
        )

        result = await self._session.execute(stmt)
        rows = result.all()

        symbols = []
        total_realized = 0.0

        for row in rows:
            win_rate = row.wins / row.trades_count if row.trades_count > 0 else 0
            avg_pnl = row.realized_pnl / row.trades_count if row.trades_count > 0 else 0

            symbols.append(
                SymbolPnl(
                    symbol=row.symbol,
                    strategy=row.strategy.value if hasattr(row.strategy, "value") else str(row.strategy),
                    realized_pnl=row.realized_pnl or 0.0,
                    unrealized_pnl=0.0,
                    total_pnl=row.realized_pnl or 0.0,
                    trades_count=row.trades_count,
                    win_rate=win_rate,
                    avg_pnl_per_trade=avg_pnl,
                )
            )
            total_realized += row.realized_pnl or 0.0

        # 수익 순으로 정렬
        symbols.sort(key=lambda x: x.total_pnl, reverse=True)

        return SymbolPnlResponse(
            period=period,
            symbols=symbols,
            total_realized=total_realized,
            total_unrealized=0.0,
        )

    async def get_strategy_pnl(self, period: PeriodType) -> StrategyPnlResponse:
        """전략별 수익 조회"""
        start_date, end_date = self._get_date_range(period)

        # DailyStatsModel에서 전략별 집계
        stmt = select(
            func.sum(DailyStatsModel.core_pnl).label("core_pnl"),
            func.sum(DailyStatsModel.satellite_pnl).label("satellite_pnl"),
            func.sum(DailyStatsModel.trades_count).label("trades_count"),
        ).where(
            DailyStatsModel.date >= start_date.strftime("%Y-%m-%d"),
            DailyStatsModel.date <= end_date.strftime("%Y-%m-%d"),
        )

        result = await self._session.execute(stmt)
        row = result.one()

        # 전략별 거래 수 조회
        core_trades_stmt = (
            select(
                func.count().label("count"),
                func.sum(func.iif(TradeModel.realized_pnl > 0, 1, 0)).label("wins"),
            )
            .where(
                TradeModel.executed_at >= start_date,
                TradeModel.executed_at <= end_date,
                TradeModel.strategy == "CORE",
            )
        )

        satellite_trades_stmt = (
            select(
                func.count().label("count"),
                func.sum(func.iif(TradeModel.realized_pnl > 0, 1, 0)).label("wins"),
            )
            .where(
                TradeModel.executed_at >= start_date,
                TradeModel.executed_at <= end_date,
                TradeModel.strategy == "SATELLITE",
            )
        )

        core_result = await self._session.execute(core_trades_stmt)
        core_row = core_result.one()

        satellite_result = await self._session.execute(satellite_trades_stmt)
        satellite_row = satellite_result.one()

        strategies = [
            StrategyPnl(
                strategy="CORE",
                realized_pnl=row.core_pnl or 0.0,
                unrealized_pnl=0.0,
                trades_count=core_row.count or 0,
                win_rate=(core_row.wins or 0) / core_row.count if core_row.count else 0.0,
                avg_holding_time_minutes=0.0,
            ),
            StrategyPnl(
                strategy="SATELLITE",
                realized_pnl=row.satellite_pnl or 0.0,
                unrealized_pnl=0.0,
                trades_count=satellite_row.count or 0,
                win_rate=(satellite_row.wins or 0) / satellite_row.count if satellite_row.count else 0.0,
                avg_holding_time_minutes=0.0,
            ),
        ]

        return StrategyPnlResponse(
            period=period,
            strategies=strategies,
        )

    async def get_hourly_pnl(self, period: PeriodType) -> HourlyPnlResponse:
        """시간대별 수익 조회"""
        start_date, end_date = self._get_date_range(period)

        # TradeModel에서 시간대별 집계 (SQLite 용)
        stmt = (
            select(
                func.strftime("%H", TradeModel.executed_at).label("hour"),
                func.sum(TradeModel.realized_pnl).label("pnl"),
                func.count().label("trades_count"),
            )
            .where(
                TradeModel.executed_at >= start_date,
                TradeModel.executed_at <= end_date,
            )
            .group_by(func.strftime("%H", TradeModel.executed_at))
        )

        result = await self._session.execute(stmt)
        rows = result.all()

        hourly_map = {int(row.hour): row for row in rows if row.hour is not None}
        hourly_data = []
        best_hour = 0
        best_pnl = float("-inf")
        worst_hour = 0
        worst_pnl = float("inf")

        for hour in range(24):
            if hour in hourly_map:
                row = hourly_map[hour]
                pnl = row.pnl or 0.0
                trades_count = row.trades_count
                avg_pnl = pnl / trades_count if trades_count > 0 else 0
            else:
                pnl = 0.0
                trades_count = 0
                avg_pnl = 0.0

            hourly_data.append(
                HourlyPnl(
                    hour=hour,
                    pnl=pnl,
                    trades_count=trades_count,
                    avg_pnl=avg_pnl,
                )
            )

            if pnl > best_pnl:
                best_pnl = pnl
                best_hour = hour
            if pnl < worst_pnl:
                worst_pnl = pnl
                worst_hour = hour

        return HourlyPnlResponse(
            period=period,
            hourly_data=hourly_data,
            best_hour=best_hour,
            worst_hour=worst_hour,
        )

    async def get_equity_curve(self, period: PeriodType) -> EquityCurveResponse:
        """자산 곡선 조회"""
        start_date, end_date = self._get_date_range(period)

        stmt = (
            select(EquitySnapshotModel)
            .where(
                EquitySnapshotModel.timestamp >= start_date,
                EquitySnapshotModel.timestamp <= end_date,
            )
            .order_by(EquitySnapshotModel.timestamp)
        )

        result = await self._session.execute(stmt)
        snapshots = result.scalars().all()

        # 스냅샷이 없으면 DailyStats에서 생성
        if not snapshots:
            daily_stmt = (
                select(DailyStatsModel)
                .where(
                    DailyStatsModel.date >= start_date.strftime("%Y-%m-%d"),
                    DailyStatsModel.date <= end_date.strftime("%Y-%m-%d"),
                )
                .order_by(DailyStatsModel.date)
            )
            daily_result = await self._session.execute(daily_stmt)
            daily_stats = daily_result.scalars().all()

            data = [
                EquityPoint(
                    timestamp=f"{s.date}T00:00:00",
                    equity=s.ending_equity,
                    pnl=s.pnl,
                )
                for s in daily_stats
            ]
        else:
            data = [
                EquityPoint(
                    timestamp=s.timestamp.isoformat(),
                    equity=s.equity,
                    pnl=s.realized_pnl + s.unrealized_pnl,
                )
                for s in snapshots
            ]

        start_equity = data[0].equity if data else 10000.0
        end_equity = data[-1].equity if data else 10000.0
        total_return_pct = (
            (end_equity - start_equity) / start_equity if start_equity > 0 else 0
        )

        return EquityCurveResponse(
            period=period,
            data=data,
            start_equity=start_equity,
            end_equity=end_equity,
            total_return_pct=total_return_pct,
        )
