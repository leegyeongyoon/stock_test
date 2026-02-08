"""
Trade Recorder Service - 거래 기록을 DB에 저장

v6.0: 진입 시점 지표 기록 기능 추가
- 손실 패턴 분석을 위한 지표 스냅샷
- 전략별 일별 PnL 집계
"""

import uuid
from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.database import (
    DailyStatsModel,
    EquitySnapshotModel,
    OrderModel,
    TradeModel,
    TradeDetailModel,
    StrategyDailyPnlModel,
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

    # === v6.0: 지표 기록 기능 ===

    def __init__(self):
        """초기화 - 활성 진입 추적용 딕셔너리"""
        self._active_entries: dict[str, dict] = {}

    async def record_entry_with_indicators(
        self,
        symbol: str,
        strategy: str,
        entry_price: float,
        quantity: float,
        market_data: dict,
        strategy_score: Optional[float] = None,
        strategy_level: Optional[int] = None,
    ) -> str:
        """
        진입 기록 (지표 포함)

        Args:
            symbol: 종목 심볼
            strategy: 전략 이름 (PULLBACK, REBOUND, DIP_SCALPER, ATTACK)
            entry_price: 진입 가격
            quantity: 진입 수량
            market_data: 진입 시점 시장 데이터
            strategy_score: 전략 점수 (optional)
            strategy_level: 전략 레벨 (optional)

        Returns:
            trade_id: 거래 ID
        """
        trade_id = f"{strategy}_{symbol}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
        entry_time = datetime.utcnow()
        trade_date = entry_time.strftime("%Y-%m-%d")

        # 진입 시점 지표 스냅샷
        indicators = self._extract_indicators(market_data)

        # 메모리에 진입 데이터 저장
        key = f"{strategy}:{symbol}"
        self._active_entries[key] = {
            "trade_id": trade_id,
            "symbol": symbol,
            "strategy": strategy,
            "entry_time": entry_time,
            "entry_price": entry_price,
            "quantity": quantity,
            "indicators": indicators,
            "strategy_score": strategy_score,
            "strategy_level": strategy_level,
            "trade_date": trade_date,
            "highest_price": entry_price,
            "lowest_price": entry_price,
        }

        # DB에 저장
        try:
            async with async_session() as session:
                trade_detail = TradeDetailModel(
                    trade_id=trade_id,
                    symbol=symbol,
                    strategy=strategy,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    quantity=quantity,
                    indicators_json=indicators,
                    strategy_score=strategy_score,
                    strategy_level=strategy_level,
                    trade_date=trade_date,
                )
                session.add(trade_detail)
                await session.commit()

            logger.info(
                "Trade entry recorded with indicators",
                trade_id=trade_id,
                symbol=symbol,
                strategy=strategy,
                entry_price=entry_price,
                score=strategy_score,
            )
        except Exception as e:
            logger.error("Failed to record entry", error=str(e))

        return trade_id

    def update_extremes(
        self,
        symbol: str,
        strategy: str,
        current_price: float,
    ) -> None:
        """
        최고가/최저가 업데이트 (실시간, 동기)

        Args:
            symbol: 종목 심볼
            strategy: 전략 이름
            current_price: 현재 가격
        """
        key = f"{strategy}:{symbol}"
        entry_data = self._active_entries.get(key)

        if not entry_data:
            return

        if current_price > entry_data["highest_price"]:
            entry_data["highest_price"] = current_price

        if current_price < entry_data["lowest_price"]:
            entry_data["lowest_price"] = current_price

    async def record_exit_with_indicators(
        self,
        symbol: str,
        strategy: str,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        pnl_pct: float,
    ) -> Optional[str]:
        """
        청산 기록 (지표 포함)

        Args:
            symbol: 종목 심볼
            strategy: 전략 이름
            exit_price: 청산 가격
            exit_reason: 청산 사유 (stop_loss, take_profit, time_stop 등)
            pnl: 손익 금액
            pnl_pct: 손익률

        Returns:
            trade_id: 거래 ID (없으면 None)
        """
        key = f"{strategy}:{symbol}"
        entry_data = self._active_entries.pop(key, None)

        if not entry_data:
            logger.warning(
                "No entry data found for exit",
                symbol=symbol,
                strategy=strategy,
            )
            return None

        trade_id = entry_data["trade_id"]
        exit_time = datetime.utcnow()
        hold_duration_sec = int((exit_time - entry_data["entry_time"]).total_seconds())

        # 최대 수익/손실 계산
        entry_price = entry_data["entry_price"]
        max_profit_pct = (entry_data["highest_price"] - entry_price) / entry_price if entry_price > 0 else 0
        max_drawdown_pct = (entry_price - entry_data["lowest_price"]) / entry_price if entry_price > 0 else 0

        try:
            # DB 업데이트
            async with async_session() as session:
                stmt = (
                    update(TradeDetailModel)
                    .where(TradeDetailModel.trade_id == trade_id)
                    .values(
                        exit_time=exit_time,
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        hold_duration_sec=hold_duration_sec,
                        max_profit_pct=max_profit_pct,
                        max_drawdown_pct=max_drawdown_pct,
                    )
                )
                await session.execute(stmt)
                await session.commit()

            # 일별 전략 PnL 업데이트
            await self._update_strategy_daily_pnl(
                strategy=strategy,
                trade_date=entry_data["trade_date"],
                pnl=pnl,
                pnl_pct=pnl_pct,
                is_win=pnl >= 0,
            )

            logger.info(
                "Trade exit recorded with indicators",
                trade_id=trade_id,
                symbol=symbol,
                strategy=strategy,
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl=pnl,
                pnl_pct=f"{pnl_pct:.2%}",
                hold_sec=hold_duration_sec,
                max_profit=f"{max_profit_pct:.2%}",
                max_dd=f"{max_drawdown_pct:.2%}",
            )
        except Exception as e:
            logger.error("Failed to record exit", error=str(e))

        return trade_id

    def _extract_indicators(self, market_data: dict) -> dict:
        """
        시장 데이터에서 지표 추출

        Args:
            market_data: 시장 데이터 딕셔너리

        Returns:
            indicators: 지표 딕셔너리
        """
        return {
            # 가격 지표
            "price": market_data.get("price", 0),
            "price_change_pct": market_data.get("price_change_pct", 0),
            "change_1m": market_data.get("change_1m", 0),
            "change_5m": market_data.get("change_5m", 0),
            "change_30m": market_data.get("change_30m", 0),

            # 거래량 지표
            "rvol_1m": market_data.get("rvol_1m", 1.0),
            "rvol_5m": market_data.get("rvol_5m", 1.0),
            "rvol_30m": market_data.get("rvol_30m", 1.0),
            "volume_24h": market_data.get("volume_24h", 0),

            # 호가창 지표
            "spread_bps": market_data.get("spread_bps", 0),
            "bid_ratio": market_data.get("bid_ratio", 0.5),
            "orderbook_imbalance": market_data.get("orderbook_imbalance", 0),

            # 기술적 지표
            "close_pos": market_data.get("close_pos", 0.5),
            "vwap": market_data.get("vwap", 0),
            "high_20": market_data.get("high_20", 0),
            "low_20": market_data.get("low_20", 0),

            # 시간 정보
            "hour_utc": datetime.utcnow().hour,
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def _update_strategy_daily_pnl(
        self,
        strategy: str,
        trade_date: str,
        pnl: float,
        pnl_pct: float,
        is_win: bool,
    ) -> None:
        """
        일별 전략 PnL 업데이트

        Args:
            strategy: 전략 이름
            trade_date: 거래 날짜
            pnl: 손익 금액
            pnl_pct: 손익률
            is_win: 승리 여부
        """
        try:
            async with async_session() as session:
                # 기존 레코드 조회
                query = select(StrategyDailyPnlModel).where(
                    StrategyDailyPnlModel.date == trade_date,
                    StrategyDailyPnlModel.strategy == strategy,
                )
                result = await session.execute(query)
                record = result.scalar_one_or_none()

                if record:
                    # 기존 레코드 업데이트
                    record.total_pnl += pnl
                    new_count = record.trade_count + 1
                    record.total_pnl_pct = (record.total_pnl_pct * record.trade_count + pnl_pct) / new_count
                    record.trade_count = new_count

                    if is_win:
                        record.win_count += 1
                        if record.win_count == 1:
                            record.avg_win_pct = pnl_pct
                        else:
                            record.avg_win_pct = (record.avg_win_pct * (record.win_count - 1) + pnl_pct) / record.win_count
                        record.max_win_pct = max(record.max_win_pct, pnl_pct)
                    else:
                        record.loss_count += 1
                        if record.loss_count == 1:
                            record.avg_loss_pct = pnl_pct
                        else:
                            record.avg_loss_pct = (record.avg_loss_pct * (record.loss_count - 1) + pnl_pct) / record.loss_count
                        record.max_loss_pct = min(record.max_loss_pct, pnl_pct)

                    record.win_rate = record.win_count / record.trade_count * 100 if record.trade_count > 0 else 0
                else:
                    # 새 레코드 생성
                    record = StrategyDailyPnlModel(
                        date=trade_date,
                        strategy=strategy,
                        total_pnl=pnl,
                        total_pnl_pct=pnl_pct,
                        trade_count=1,
                        win_count=1 if is_win else 0,
                        loss_count=0 if is_win else 1,
                        win_rate=100.0 if is_win else 0.0,
                        avg_win_pct=pnl_pct if is_win else 0.0,
                        avg_loss_pct=0.0 if is_win else pnl_pct,
                        max_win_pct=pnl_pct if is_win else 0.0,
                        max_loss_pct=0.0 if is_win else pnl_pct,
                    )
                    session.add(record)

                await session.commit()
        except Exception as e:
            logger.error("Failed to update strategy daily PnL", error=str(e))

    def get_active_entries(self) -> dict[str, dict]:
        """현재 활성 진입 목록 반환"""
        return dict(self._active_entries)


# 싱글톤 인스턴스
trade_recorder = TradeRecorder()
