"""분석 서비스 - 비즈니스 로직"""

from datetime import datetime, timedelta

from sqlalchemy import case, func, select
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
        """기간별 수익률 조회 - 실제 거래 기록 기반"""
        start_date, end_date = self._get_date_range(period)

        # 1. 실제 거래에서 총 수익 계산
        trade_stmt = (
            select(
                func.sum(TradeModel.realized_pnl).label("total_pnl"),
                func.count().label("total_trades"),
                func.sum(case((TradeModel.realized_pnl > 0, 1), else_=0)).label("wins"),
                func.sum(case((TradeModel.realized_pnl < 0, 1), else_=0)).label("losses"),
            )
            .where(
                TradeModel.executed_at >= start_date,
                TradeModel.executed_at <= end_date,
            )
        )
        trade_result = await self._session.execute(trade_stmt)
        trade_row = trade_result.one()

        total_pnl = trade_row.total_pnl or 0.0
        total_trades = trade_row.total_trades or 0
        wins = trade_row.wins or 0
        losses = trade_row.losses or 0

        # 2. 일별 통계에서 equity 정보 (드로다운 계산용)
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
        max_equity = 0.0
        max_drawdown = 0.0
        # 기간 내 첫 날의 시작 자산 사용, 없으면 기본값 500,000원
        starting_equity = 500000.0

        for stat in daily_stats:
            # 첫 번째 유효한 starting_equity 사용
            if starting_equity == 500000.0 and stat.starting_equity > 100000:
                starting_equity = stat.starting_equity

            # 일별 거래 수익은 전략별 수익 합계 사용 (실제 거래 기록)
            daily_trade_pnl = (stat.core_pnl or 0.0) + (stat.satellite_pnl or 0.0)

            pnl_pct = daily_trade_pnl / stat.starting_equity if stat.starting_equity > 0 else 0

            daily_returns.append(
                DailyReturn(
                    date=stat.date,
                    pnl=daily_trade_pnl,  # 전략별 거래 수익만
                    pnl_pct=pnl_pct,
                    equity=stat.ending_equity,
                    core_pnl=stat.core_pnl or 0.0,
                    satellite_pnl=stat.satellite_pnl or 0.0,
                )
            )

            # 유효한 equity 값만 드로다운 계산에 사용 (최소 10,000원 이상)
            if stat.ending_equity > 10000:
                if stat.ending_equity > max_equity:
                    max_equity = stat.ending_equity

                if max_equity > 0:
                    dd = (stat.ending_equity - max_equity) / max_equity
                    if dd < max_drawdown:
                        max_drawdown = dd

        total_pnl_pct = total_pnl / starting_equity if starting_equity > 0 else 0
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0

        return PeriodReturnsResponse(
            period=period,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            total_pnl=total_pnl,  # 실제 거래 수익만
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
                func.sum(case((TradeModel.realized_pnl > 0, 1), else_=0)).label("wins"),
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
        """전략별 수익 조회 - TradeModel에서 직접 계산"""
        start_date, end_date = self._get_date_range(period)

        # TradeModel에서 전략별 직접 집계
        stmt = (
            select(
                TradeModel.strategy,
                func.sum(TradeModel.realized_pnl).label("realized_pnl"),
                func.count().label("count"),
                func.sum(case((TradeModel.realized_pnl > 0, 1), else_=0)).label("wins"),
            )
            .where(
                TradeModel.executed_at >= start_date,
                TradeModel.executed_at <= end_date,
            )
            .group_by(TradeModel.strategy)
        )

        result = await self._session.execute(stmt)
        rows = result.all()

        # 결과를 딕셔너리로 변환
        strategy_data = {}
        for row in rows:
            strategy_name = row.strategy.value if hasattr(row.strategy, "value") else str(row.strategy)
            strategy_data[strategy_name] = {
                "realized_pnl": row.realized_pnl or 0.0,
                "count": row.count or 0,
                "wins": row.wins or 0,
            }

        strategies = []
        for strategy_name in ["CORE", "SATELLITE"]:
            data = strategy_data.get(strategy_name, {"realized_pnl": 0.0, "count": 0, "wins": 0})
            win_rate = data["wins"] / data["count"] if data["count"] > 0 else 0.0
            strategies.append(
                StrategyPnl(
                    strategy=strategy_name,
                    realized_pnl=data["realized_pnl"],
                    unrealized_pnl=0.0,
                    trades_count=data["count"],
                    win_rate=win_rate,
                    avg_holding_time_minutes=0.0,
                )
            )

        return StrategyPnlResponse(
            period=period,
            strategies=strategies,
        )

    async def get_hourly_pnl(self, period: PeriodType) -> HourlyPnlResponse:
        """시간대별 수익 조회"""
        start_date, end_date = self._get_date_range(period)

        # TradeModel에서 시간대별 집계 (KST 기준, PostgreSQL AT TIME ZONE)
        kst_executed_at = func.timezone('Asia/Seoul', TradeModel.executed_at)
        stmt = (
            select(
                func.extract('hour', kst_executed_at).label("hour"),
                func.sum(TradeModel.realized_pnl).label("pnl"),
                func.count().label("trades_count"),
            )
            .where(
                TradeModel.executed_at >= start_date,
                TradeModel.executed_at <= end_date,
            )
            .group_by(func.extract('hour', kst_executed_at))
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
        """자산 곡선 조회 - 실제 거래 기반 수익률"""
        start_date, end_date = self._get_date_range(period)

        # 실제 거래에서 총 수익 계산
        trade_stmt = (
            select(func.sum(TradeModel.realized_pnl).label("total_pnl"))
            .where(
                TradeModel.executed_at >= start_date,
                TradeModel.executed_at <= end_date,
            )
        )
        trade_result = await self._session.execute(trade_stmt)
        total_trade_pnl = trade_result.scalar() or 0.0

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

            # 유효한 equity만 포함 (10,000원 이상)
            data = [
                EquityPoint(
                    timestamp=f"{s.date}T00:00:00",
                    equity=s.ending_equity,
                    pnl=(s.core_pnl or 0) + (s.satellite_pnl or 0),
                )
                for s in daily_stats
                if s.ending_equity > 10000
            ]
        else:
            # 유효한 equity만 포함 (10,000원 이상)
            data = [
                EquityPoint(
                    timestamp=s.timestamp.isoformat(),
                    equity=s.equity,
                    pnl=s.realized_pnl or 0,
                )
                for s in snapshots
                if s.equity > 10000
            ]

        # 시작 자산은 첫 유효 데이터 또는 기본값 500,000원
        start_equity = data[0].equity if data else 500000.0
        end_equity = data[-1].equity if data else 500000.0

        # start_equity가 너무 작으면 기본값 사용
        if start_equity < 100000:
            start_equity = 500000.0

        # 수익률은 실제 거래 기반으로 계산
        total_return_pct = total_trade_pnl / start_equity if start_equity > 0 else 0

        return EquityCurveResponse(
            period=period,
            data=data,
            start_equity=start_equity,
            end_equity=end_equity,
            total_return_pct=total_return_pct,
        )
