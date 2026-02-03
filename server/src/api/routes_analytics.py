"""분석 API 엔드포인트"""

from fastapi import APIRouter, Depends, Query

from src.models.analytics_schemas import (
    EquityCurveResponse,
    HourlyPnlResponse,
    PeriodReturnsResponse,
    PeriodType,
    StrategyPnlResponse,
    SymbolPnlResponse,
)
from src.models.database import AsyncSession, get_db
from src.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _get_engine():
    """TradingEngine 싱글톤 가져오기"""
    from src.api.routes import get_engine
    return get_engine()


@router.get("/returns", response_model=PeriodReturnsResponse)
async def get_period_returns(
    period: PeriodType = Query(PeriodType.MONTH, description="조회 기간"),
    db: AsyncSession = Depends(get_db),
):
    """기간별 수익률 조회

    - **period**: 조회 기간 (1w, 1m, 3m, 6m, 1y)
    """
    service = AnalyticsService(db)
    return await service.get_period_returns(period)


@router.get("/symbols", response_model=SymbolPnlResponse)
async def get_symbol_pnl(
    period: PeriodType = Query(PeriodType.MONTH, description="조회 기간"),
    db: AsyncSession = Depends(get_db),
):
    """종목별 수익 분석

    어떤 종목에서 수익을 많이 냈는지 분석합니다.

    - **period**: 조회 기간 (1w, 1m, 3m, 6m, 1y)
    """
    service = AnalyticsService(db)
    return await service.get_symbol_pnl(period)


@router.get("/strategies", response_model=StrategyPnlResponse)
async def get_strategy_pnl(
    period: PeriodType = Query(PeriodType.MONTH, description="조회 기간"),
    db: AsyncSession = Depends(get_db),
):
    """전략별 수익 분석 (Core vs Satellite)

    - **period**: 조회 기간 (1w, 1m, 3m, 6m, 1y)
    """
    service = AnalyticsService(db)
    return await service.get_strategy_pnl(period)


@router.get("/hourly", response_model=HourlyPnlResponse)
async def get_hourly_pnl(
    period: PeriodType = Query(PeriodType.MONTH, description="조회 기간"),
    db: AsyncSession = Depends(get_db),
):
    """시간대별 수익 분석

    어느 시간대에 수익을 많이 냈는지 분석합니다 (0-23시 UTC).

    - **period**: 조회 기간 (1w, 1m, 3m, 6m, 1y)
    """
    service = AnalyticsService(db)
    return await service.get_hourly_pnl(period)


@router.get("/equity-curve", response_model=EquityCurveResponse)
async def get_equity_curve(
    period: PeriodType = Query(PeriodType.MONTH, description="조회 기간"),
    db: AsyncSession = Depends(get_db),
):
    """자산 곡선 조회

    기간 동안의 자산 변화를 시계열로 조회합니다.

    - **period**: 조회 기간 (1w, 1m, 3m, 6m, 1y)
    """
    service = AnalyticsService(db)
    return await service.get_equity_curve(period)


@router.get("/realtime")
async def get_realtime_analytics():
    """
    실시간 수익 분석 (Upbit 잔고 기반)

    Upbit API에서 직접 조회한 잔고와 평균 매수가를 기반으로
    정확한 미실현 손익을 계산합니다.

    Returns:
        - total_equity: 총 자산 (KRW)
        - total_invested: 총 투자금
        - total_unrealized_pnl: 총 미실현 손익
        - total_unrealized_pnl_pct: 총 미실현 손익률
        - positions: 종목별 상세 정보
    """
    engine = _get_engine()
    if not engine:
        return {"error": "Engine not initialized"}

    try:
        # Upbit 잔고 조회
        balances = await engine.exchange.get_all_balances()

        # 현재가 조회를 위한 티커
        positions = []
        total_invested = 0.0
        total_current_value = 0.0
        krw_balance = 0.0

        for balance in balances:
            if balance.asset == "KRW":
                krw_balance = balance.total
                continue

            if balance.total <= 0:
                continue

            symbol = f"KRW-{balance.asset}"
            entry_price = balance.avg_buy_price

            if entry_price <= 0:
                continue

            # 현재가 조회
            ticker = await engine.exchange.get_ticker(symbol)
            current_price = float(ticker.get("trade_price", 0)) if ticker else entry_price

            # 계산
            invested = entry_price * balance.total
            current_value = current_price * balance.total
            unrealized_pnl = current_value - invested
            unrealized_pnl_pct = (unrealized_pnl / invested * 100) if invested > 0 else 0

            total_invested += invested
            total_current_value += current_value

            positions.append({
                "symbol": symbol,
                "asset": balance.asset,
                "quantity": balance.total,
                "entry_price": entry_price,
                "current_price": current_price,
                "invested": round(invested, 0),
                "current_value": round(current_value, 0),
                "unrealized_pnl": round(unrealized_pnl, 0),
                "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
            })

        # 전체 계산
        total_unrealized_pnl = total_current_value - total_invested
        total_unrealized_pnl_pct = (
            (total_unrealized_pnl / total_invested * 100) if total_invested > 0 else 0
        )
        total_equity = krw_balance + total_current_value

        # 손익순 정렬
        positions.sort(key=lambda x: x["unrealized_pnl"], reverse=True)

        return {
            "total_equity": round(total_equity, 0),
            "krw_balance": round(krw_balance, 0),
            "total_invested": round(total_invested, 0),
            "total_current_value": round(total_current_value, 0),
            "total_unrealized_pnl": round(total_unrealized_pnl, 0),
            "total_unrealized_pnl_pct": round(total_unrealized_pnl_pct, 2),
            "position_count": len(positions),
            "positions": positions,
        }

    except Exception as e:
        return {"error": str(e)}


@router.get("/summary-realtime")
async def get_summary_realtime():
    """
    실시간 요약 (대시보드용)

    - 오늘 수익: 오늘 시작 자산 대비 현재 자산
    - 총 미실현 손익: 현재 포지션 기준
    """
    engine = _get_engine()
    if not engine:
        return {"error": "Engine not initialized"}

    try:
        # Upbit 잔고 조회
        balances = await engine.exchange.get_all_balances()

        total_invested = 0.0
        total_current_value = 0.0
        krw_balance = 0.0
        profitable_count = 0
        losing_count = 0

        for balance in balances:
            if balance.asset == "KRW":
                krw_balance = balance.total
                continue

            if balance.total <= 0 or balance.avg_buy_price <= 0:
                continue

            symbol = f"KRW-{balance.asset}"
            entry_price = balance.avg_buy_price

            # 현재가 조회
            ticker = await engine.exchange.get_ticker(symbol)
            current_price = float(ticker.get("trade_price", 0)) if ticker else entry_price

            invested = entry_price * balance.total
            current_value = current_price * balance.total

            total_invested += invested
            total_current_value += current_value

            if current_price >= entry_price:
                profitable_count += 1
            else:
                losing_count += 1

        total_equity = krw_balance + total_current_value
        total_unrealized_pnl = total_current_value - total_invested
        total_unrealized_pnl_pct = (
            (total_unrealized_pnl / total_invested * 100) if total_invested > 0 else 0
        )

        # 오늘 시작 자산 (엔진에서 가져오기)
        starting_equity = engine._starting_equity or total_equity
        today_pnl = total_equity - starting_equity
        today_pnl_pct = (today_pnl / starting_equity * 100) if starting_equity > 0 else 0

        return {
            "total_equity": round(total_equity, 0),
            "starting_equity": round(starting_equity, 0),
            "today_pnl": round(today_pnl, 0),
            "today_pnl_pct": round(today_pnl_pct, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 0),
            "unrealized_pnl_pct": round(total_unrealized_pnl_pct, 2),
            "profitable_positions": profitable_count,
            "losing_positions": losing_count,
            "total_positions": profitable_count + losing_count,
        }

    except Exception as e:
        return {"error": str(e)}


@router.post("/fix-trade-prices")
async def fix_trade_prices(
    db: AsyncSession = Depends(get_db),
):
    """
    Trade 테이블의 price 보정

    price가 0인 거래들을 Order 테이블의 avg_fill_price로 보정하고,
    realized_pnl도 재계산합니다.
    """
    from sqlalchemy import select, update

    from src.models.database import OrderModel, TradeModel

    try:
        # price가 0인 거래 조회
        stmt = select(TradeModel).where(TradeModel.price == 0)
        result = await db.execute(stmt)
        trades = result.scalars().all()

        fixed_count = 0
        recalculated_pnl = 0
        details = []

        # 종목별 매수 평균가 추적
        symbol_buys: dict = {}  # symbol -> [(qty, price), ...]

        for trade in trades:
            # Order 테이블에서 가격 찾기
            order_stmt = select(OrderModel).where(
                OrderModel.order_id == trade.order_id
            )
            order_result = await db.execute(order_stmt)
            order = order_result.scalar_one_or_none()

            if order and order.avg_fill_price and order.avg_fill_price > 0:
                trade.price = order.avg_fill_price
                fixed_count += 1
                details.append({
                    "trade_id": trade.id,
                    "symbol": trade.symbol,
                    "side": trade.side.value,
                    "price": order.avg_fill_price,
                    "status": "fixed"
                })
            else:
                details.append({
                    "trade_id": trade.id,
                    "symbol": trade.symbol,
                    "side": trade.side.value,
                    "status": "no_order_found"
                })

        await db.commit()

        # realized_pnl 재계산
        all_trades_stmt = (
            select(TradeModel)
            .where(TradeModel.symbol.like("KRW-%"))
            .order_by(TradeModel.symbol, TradeModel.executed_at)
        )
        all_result = await db.execute(all_trades_stmt)
        all_trades = all_result.scalars().all()

        # 종목별로 그룹화하여 손익 재계산
        from collections import defaultdict
        symbol_trades = defaultdict(list)
        for t in all_trades:
            symbol_trades[t.symbol].append(t)

        for symbol, trades_list in symbol_trades.items():
            buy_total_cost = 0
            buy_total_qty = 0

            for t in trades_list:
                if t.side.value == "BUY":
                    buy_total_cost += t.price * t.quantity
                    buy_total_qty += t.quantity
                    t.realized_pnl = 0  # 매수는 손익 없음
                elif t.side.value == "SELL" and buy_total_qty > 0:
                    avg_buy_price = buy_total_cost / buy_total_qty
                    # 손익 = (매도가 - 평균매수가) × 매도수량
                    new_pnl = (t.price - avg_buy_price) * t.quantity
                    old_pnl = t.realized_pnl
                    t.realized_pnl = new_pnl
                    recalculated_pnl += 1

                    # 매도 후 보유량 차감
                    buy_total_qty -= t.quantity
                    buy_total_cost = avg_buy_price * buy_total_qty

        await db.commit()

        return {
            "trades_fixed": fixed_count,
            "pnl_recalculated": recalculated_pnl,
            "details": details[:50]
        }

    except Exception as e:
        return {"error": str(e)}


@router.post("/fix-order-prices")
async def fix_order_prices(
    db: AsyncSession = Depends(get_db),
):
    """
    기존 주문 데이터 보정

    avg_fill_price가 0인 주문들을 찾아서 가능한 경우 보정합니다.
    - exchange_order_id가 있는 경우: Upbit API로 실제 체결가 조회
    - 최근 24시간 내 주문: 현재가로 추정
    """
    from datetime import datetime, timedelta

    from sqlalchemy import select, or_

    from src.models.database import OrderModel

    engine = _get_engine()
    if not engine:
        return {"error": "Engine not initialized"}

    try:
        # avg_fill_price가 0이거나 NULL인 주문 조회
        stmt = select(OrderModel).where(
            or_(
                OrderModel.avg_fill_price == 0,
                OrderModel.avg_fill_price == None,  # noqa: E711
            )
        )
        result = await db.execute(stmt)
        orders = result.scalars().all()

        fixed_count = 0
        failed_count = 0
        skipped_count = 0
        details = []

        for order in orders:
            try:
                # KRW 마켓 주문만 처리 (Upbit)
                if not order.symbol.startswith("KRW-"):
                    skipped_count += 1
                    details.append({
                        "order_id": order.order_id,
                        "symbol": order.symbol,
                        "status": "skipped",
                        "reason": "Not a KRW market order"
                    })
                    continue

                # exchange_order_id가 있으면 Upbit API로 조회
                if order.exchange_order_id:
                    order_info = await engine.exchange.get_order_with_trades(
                        order.exchange_order_id
                    )

                    if order_info and order_info.get("avg_price"):
                        avg_price = float(order_info["avg_price"])
                        if avg_price > 0:
                            order.avg_fill_price = avg_price
                            fixed_count += 1
                            details.append({
                                "order_id": order.order_id,
                                "exchange_order_id": order.exchange_order_id,
                                "symbol": order.symbol,
                                "avg_fill_price": avg_price,
                                "status": "fixed_from_upbit"
                            })
                            continue

                # 최근 24시간 내 주문은 현재가로 추정
                if order.created_at and order.created_at > datetime.utcnow() - timedelta(hours=24):
                    ticker = await engine.exchange.get_ticker(order.symbol)
                    if ticker and ticker.last > 0:
                        order.avg_fill_price = ticker.last
                        fixed_count += 1
                        details.append({
                            "order_id": order.order_id,
                            "symbol": order.symbol,
                            "avg_fill_price": ticker.last,
                            "status": "estimated_from_current"
                        })
                        continue

                failed_count += 1
                details.append({
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "exchange_order_id": order.exchange_order_id,
                    "status": "failed",
                    "reason": "Could not retrieve price (no exchange_order_id or old order)"
                })

            except Exception as e:
                failed_count += 1
                details.append({
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "status": "error",
                    "reason": str(e)
                })

        await db.commit()

        return {
            "total_checked": len(orders),
            "fixed_count": fixed_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "details": details[:100]  # 최대 100개만 반환
        }

    except Exception as e:
        return {"error": str(e)}
