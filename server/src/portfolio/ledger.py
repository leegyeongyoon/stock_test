"""Ledger - 체결/수수료/PNL 관리"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

import structlog

from src.models.schemas import OrderSide, StrategyType

logger = structlog.get_logger()


@dataclass
class Trade:
    """체결 기록"""

    trade_id: str
    order_id: str
    symbol: str
    strategy: StrategyType
    side: OrderSide
    quantity: float
    price: float
    fee: float
    fee_asset: str
    realized_pnl: float
    executed_at: datetime


@dataclass
class DailyStats:
    """일별 통계"""

    date: str  # YYYY-MM-DD
    starting_equity: float
    current_equity: float = 0.0
    pnl: float = 0.0
    core_pnl: float = 0.0
    satellite_pnl: float = 0.0
    fees: float = 0.0
    trades_count: int = 0
    max_equity: float = 0.0
    min_equity: float = 0.0

    @property
    def pnl_pct(self) -> float:
        """PnL 퍼센트"""
        if self.starting_equity == 0:
            return 0.0
        return self.pnl / self.starting_equity

    @property
    def drawdown(self) -> float:
        """현재 드로다운"""
        if self.max_equity == 0:
            return 0.0
        return (self.current_equity - self.max_equity) / self.max_equity


class Ledger:
    """
    Ledger - 거래 장부

    책임:
    1. 체결 기록 관리
    2. PnL 계산 (Core/Satellite 분리)
    3. 수수료 추적
    4. 일별 통계 관리
    """

    def __init__(self, starting_equity: float = 10000.0) -> None:
        self._trades: list[Trade] = []
        self._daily_stats: dict[str, DailyStats] = {}
        self._starting_equity = starting_equity
        self._current_equity = starting_equity
        self._lock = asyncio.Lock()

        # 오늘 통계 초기화
        today = date.today().isoformat()
        self._daily_stats[today] = DailyStats(
            date=today,
            starting_equity=starting_equity,
            current_equity=starting_equity,
            max_equity=starting_equity,
            min_equity=starting_equity,
        )

    @property
    def current_equity(self) -> float:
        """현재 자산"""
        return self._current_equity

    @property
    def today_stats(self) -> DailyStats:
        """오늘 통계"""
        today = date.today().isoformat()
        if today not in self._daily_stats:
            self._daily_stats[today] = DailyStats(
                date=today,
                starting_equity=self._current_equity,
                current_equity=self._current_equity,
                max_equity=self._current_equity,
                min_equity=self._current_equity,
            )
        return self._daily_stats[today]

    async def record_trade(
        self,
        trade_id: str,
        order_id: str,
        symbol: str,
        strategy: StrategyType,
        side: OrderSide,
        quantity: float,
        price: float,
        fee: float = 0.0,
        fee_asset: str = "USDT",
        realized_pnl: float = 0.0,
    ) -> Trade:
        """체결 기록"""
        trade = Trade(
            trade_id=trade_id,
            order_id=order_id,
            symbol=symbol,
            strategy=strategy,
            side=side,
            quantity=quantity,
            price=price,
            fee=fee,
            fee_asset=fee_asset,
            realized_pnl=realized_pnl,
            executed_at=datetime.utcnow(),
        )

        async with self._lock:
            self._trades.append(trade)

            # 일별 통계 업데이트
            stats = self.today_stats
            stats.trades_count += 1
            stats.fees += fee
            stats.pnl += realized_pnl

            if strategy == StrategyType.CORE:
                stats.core_pnl += realized_pnl
            else:
                stats.satellite_pnl += realized_pnl

        logger.info(
            "Trade recorded",
            trade_id=trade_id,
            symbol=symbol,
            side=side.value,
            quantity=quantity,
            price=price,
            pnl=realized_pnl,
        )

        return trade

    async def update_equity(self, new_equity: float) -> None:
        """자산 업데이트"""
        async with self._lock:
            self._current_equity = new_equity

            stats = self.today_stats
            stats.current_equity = new_equity
            stats.pnl = new_equity - stats.starting_equity

            if new_equity > stats.max_equity:
                stats.max_equity = new_equity
            if new_equity < stats.min_equity:
                stats.min_equity = new_equity

    def get_daily_pnl(self) -> float:
        """오늘 PnL"""
        return self.today_stats.pnl

    def get_daily_pnl_pct(self) -> float:
        """오늘 PnL 퍼센트"""
        return self.today_stats.pnl_pct

    def get_drawdown(self) -> float:
        """현재 드로다운"""
        return self.today_stats.drawdown

    def get_core_pnl(self) -> float:
        """Core 전략 PnL"""
        return self.today_stats.core_pnl

    def get_satellite_pnl(self) -> float:
        """Satellite 전략 PnL"""
        return self.today_stats.satellite_pnl

    def get_total_fees(self) -> float:
        """오늘 총 수수료"""
        return self.today_stats.fees

    def get_trades(
        self,
        symbol: Optional[str] = None,
        strategy: Optional[StrategyType] = None,
        limit: int = 100,
    ) -> list[Trade]:
        """체결 기록 조회"""
        trades = self._trades

        if symbol:
            trades = [t for t in trades if t.symbol == symbol]

        if strategy:
            trades = [t for t in trades if t.strategy == strategy]

        return trades[-limit:]

    def get_daily_history(self, days: int = 30) -> list[DailyStats]:
        """일별 통계 이력"""
        stats = sorted(
            self._daily_stats.values(),
            key=lambda x: x.date,
            reverse=True,
        )
        return stats[:days]

    def get_summary(self) -> dict:
        """요약 정보"""
        stats = self.today_stats
        return {
            "equity": self._current_equity,
            "starting_equity": stats.starting_equity,
            "pnl_today": stats.pnl,
            "pnl_today_pct": stats.pnl_pct,
            "core_pnl": stats.core_pnl,
            "satellite_pnl": stats.satellite_pnl,
            "fees_today": stats.fees,
            "trades_today": stats.trades_count,
            "drawdown": stats.drawdown,
            "max_equity": stats.max_equity,
        }

    async def new_day(self, new_starting_equity: Optional[float] = None) -> None:
        """새로운 날 시작"""
        async with self._lock:
            today = date.today().isoformat()

            if new_starting_equity is None:
                new_starting_equity = self._current_equity

            self._daily_stats[today] = DailyStats(
                date=today,
                starting_equity=new_starting_equity,
                current_equity=new_starting_equity,
                max_equity=new_starting_equity,
                min_equity=new_starting_equity,
            )

            logger.info(
                "New day started",
                date=today,
                starting_equity=new_starting_equity,
            )
