"""Trade Recorder Service - 거래 기록을 DB에 저장"""

import uuid
from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.database import (
    DailyStatsModel,
    EquitySnapshotModel,
    OrderModel,
    TradeModel,
    async_session,
)
from src.models.schemas import OrderSide, OrderStatus, OrderType, StrategyType

logger = structlog.get_logger()


class TradeRecorder:
    """거래 기록 서비스"""

    async def record_order(
        self,
        order_id: str,
        symbol: str,
        strategy: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        filled_quantity: float = 0.0,
        avg_fill_price: Optional[float] = None,
        status: str = "PENDING",
        exchange_order_id: Optional[str] = None,
    ) -> bool:
        """주문 기록"""
        try:
            async with async_session() as session:
                order = OrderModel(
                    order_id=order_id,
                    exchange_order_id=exchange_order_id,
                    symbol=symbol,
                    strategy=StrategyType(strategy),
                    side=OrderSide(side),
                    order_type=OrderType(order_type),
                    quantity=quantity,
                    price=price,
                    filled_quantity=filled_quantity,
                    avg_fill_price=avg_fill_price,
                    status=OrderStatus(status),
                )
                session.add(order)
                await session.commit()
                logger.info("Order recorded", order_id=order_id, symbol=symbol)
                return True
        except Exception as e:
            logger.error("Failed to record order", error=str(e))
            return False

    async def record_trade(
        self,
        order_id: str,
        symbol: str,
        strategy: str,
        side: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        fee_asset: str = "USDT",
        realized_pnl: float = 0.0,
    ) -> bool:
        """체결 기록"""
        try:
            async with async_session() as session:
                trade = TradeModel(
                    order_id=order_id,
                    symbol=symbol,
                    strategy=StrategyType(strategy),
                    side=OrderSide(side),
                    quantity=quantity,
                    price=price,
                    fee=fee,
                    fee_asset=fee_asset,
                    realized_pnl=realized_pnl,
                    executed_at=datetime.utcnow(),
                )
                session.add(trade)
                await session.commit()
                logger.info("Trade recorded", order_id=order_id, symbol=symbol, price=price)
                return True
        except Exception as e:
            logger.error("Failed to record trade", error=str(e))
            return False

    async def record_equity_snapshot(
        self,
        equity: float,
        unrealized_pnl: float = 0.0,
        realized_pnl: float = 0.0,
    ) -> bool:
        """자산 스냅샷 기록"""
        try:
            async with async_session() as session:
                snapshot = EquitySnapshotModel(
                    equity=equity,
                    unrealized_pnl=unrealized_pnl,
                    realized_pnl=realized_pnl,
                    timestamp=datetime.utcnow(),
                )
                session.add(snapshot)
                await session.commit()
                return True
        except Exception as e:
            logger.error("Failed to record equity snapshot", error=str(e))
            return False

    async def update_daily_stats(
        self,
        date: Optional[str] = None,
        starting_equity: Optional[float] = None,
        ending_equity: Optional[float] = None,
        pnl: float = 0.0,
        core_pnl: float = 0.0,
        satellite_pnl: float = 0.0,
        fees: float = 0.0,
        trades_count: int = 0,
        max_drawdown: float = 0.0,
    ) -> bool:
        """일별 통계 업데이트 (upsert)"""
        try:
            if date is None:
                date = datetime.utcnow().strftime("%Y-%m-%d")

            async with async_session() as session:
                # 기존 레코드 조회
                stmt = select(DailyStatsModel).where(DailyStatsModel.date == date)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    # 업데이트
                    if ending_equity is not None:
                        existing.ending_equity = ending_equity
                    existing.pnl += pnl
                    existing.core_pnl += core_pnl
                    existing.satellite_pnl += satellite_pnl
                    existing.fees += fees
                    existing.trades_count += trades_count
                    if max_drawdown < existing.max_drawdown:
                        existing.max_drawdown = max_drawdown
                else:
                    # 새로 생성
                    daily_stat = DailyStatsModel(
                        date=date,
                        starting_equity=starting_equity or 10000.0,
                        ending_equity=ending_equity or starting_equity or 10000.0,
                        pnl=pnl,
                        core_pnl=core_pnl,
                        satellite_pnl=satellite_pnl,
                        fees=fees,
                        trades_count=trades_count,
                        max_drawdown=max_drawdown,
                    )
                    session.add(daily_stat)

                await session.commit()
                logger.debug("Daily stats updated", date=date)
                return True
        except Exception as e:
            logger.error("Failed to update daily stats", error=str(e))
            return False

    async def get_today_stats(self) -> Optional[dict]:
        """오늘 통계 조회"""
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            async with async_session() as session:
                stmt = select(DailyStatsModel).where(DailyStatsModel.date == today)
                result = await session.execute(stmt)
                stat = result.scalar_one_or_none()

                if stat:
                    return {
                        "date": stat.date,
                        "starting_equity": stat.starting_equity,
                        "ending_equity": stat.ending_equity,
                        "pnl": stat.pnl,
                        "core_pnl": stat.core_pnl,
                        "satellite_pnl": stat.satellite_pnl,
                        "fees": stat.fees,
                        "trades_count": stat.trades_count,
                        "max_drawdown": stat.max_drawdown,
                    }
                return None
        except Exception as e:
            logger.error("Failed to get today stats", error=str(e))
            return None


# 싱글톤 인스턴스
trade_recorder = TradeRecorder()
