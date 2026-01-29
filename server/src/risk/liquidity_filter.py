"""Liquidity Filter - 유동성 기반 진입 필터"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class LiquidityCheck:
    """유동성 체크 결과"""

    passed: bool
    reason: Optional[str] = None
    volume_24h: float = 0
    spread_bps: float = 0
    orderbook_depth: float = 0  # 0.5% 범위 내 누적 호가량


class LiquidityFilter:
    """
    유동성 필터

    통과 조건:
    - 24h quote volume >= 50M USDT
    - spread_bps <= 8 (spot/perp 각각)
    - 오더북 깊이: 0.5% 범위 누적호가 >= 주문수량 * 5
    """

    def __init__(self) -> None:
        self._min_volume_usdt = settings.min_liquidity_usdt  # 50M
        self._max_spread_bps = settings.max_spread_bps  # 8bps
        self._depth_multiplier = 5  # 주문수량의 5배

        # 캐시 (심볼별 최근 체크 결과)
        self._cache: dict[str, LiquidityCheck] = {}

    async def check(
        self,
        exchange,
        symbol: str,
        order_quantity_usd: float = 0,
    ) -> LiquidityCheck:
        """
        유동성 체크

        Args:
            exchange: BinanceSpotExchange 또는 BinancePerpExchange
            symbol: 심볼 (e.g., "BTCUSDT")
            order_quantity_usd: 주문 예정 금액 (USD)

        Returns:
            LiquidityCheck 결과
        """
        try:
            # 1. 24h 거래대금 체크
            volume_24h = await exchange.get_24h_volume(symbol)
            if volume_24h is None:
                return LiquidityCheck(
                    passed=False,
                    reason="Failed to get 24h volume",
                )

            if volume_24h < self._min_volume_usdt:
                return LiquidityCheck(
                    passed=False,
                    reason=f"Volume too low: ${volume_24h:,.0f} < ${self._min_volume_usdt:,.0f}",
                    volume_24h=volume_24h,
                )

            # 2. 스프레드 체크
            ticker = await exchange.get_ticker(symbol)
            if not ticker:
                return LiquidityCheck(
                    passed=False,
                    reason="Failed to get ticker",
                    volume_24h=volume_24h,
                )

            mid_price = (ticker.bid + ticker.ask) / 2
            spread = ticker.ask - ticker.bid
            spread_bps = (spread / mid_price) * 10000 if mid_price > 0 else 0

            if spread_bps > self._max_spread_bps:
                return LiquidityCheck(
                    passed=False,
                    reason=f"Spread too wide: {spread_bps:.1f}bps > {self._max_spread_bps}bps",
                    volume_24h=volume_24h,
                    spread_bps=spread_bps,
                )

            # 3. 오더북 깊이 체크 (주문 금액이 있는 경우)
            orderbook_depth = 0
            if order_quantity_usd > 0:
                depth = await exchange.get_orderbook_depth(symbol, limit=100)
                if depth:
                    # 0.5% 범위 내 누적 호가량 계산
                    price_range = mid_price * 0.005  # 0.5%

                    # Bid side
                    bid_depth = 0
                    for price, qty in depth.get("bids", []):
                        if price >= mid_price - price_range:
                            bid_depth += price * qty

                    # Ask side
                    ask_depth = 0
                    for price, qty in depth.get("asks", []):
                        if price <= mid_price + price_range:
                            ask_depth += price * qty

                    orderbook_depth = min(bid_depth, ask_depth)
                    min_required_depth = order_quantity_usd * self._depth_multiplier

                    if orderbook_depth < min_required_depth:
                        return LiquidityCheck(
                            passed=False,
                            reason=f"Orderbook depth too shallow: ${orderbook_depth:,.0f} < ${min_required_depth:,.0f}",
                            volume_24h=volume_24h,
                            spread_bps=spread_bps,
                            orderbook_depth=orderbook_depth,
                        )

            # 모든 조건 통과
            result = LiquidityCheck(
                passed=True,
                volume_24h=volume_24h,
                spread_bps=spread_bps,
                orderbook_depth=orderbook_depth,
            )

            self._cache[symbol] = result
            return result

        except Exception as e:
            logger.error("Liquidity check error", symbol=symbol, error=str(e))
            return LiquidityCheck(
                passed=False,
                reason=f"Check error: {str(e)}",
            )

    async def check_both_exchanges(
        self,
        spot_exchange,
        perp_exchange,
        symbol: str,
        order_quantity_usd: float = 0,
    ) -> LiquidityCheck:
        """
        양쪽 거래소 유동성 체크 (Cash & Carry용)

        Args:
            spot_exchange: BinanceSpotExchange
            perp_exchange: BinancePerpExchange
            symbol: 심볼
            order_quantity_usd: 주문 예정 금액

        Returns:
            양쪽 모두 통과해야 통과
        """
        # Spot 체크
        spot_check = await self.check(spot_exchange, symbol, order_quantity_usd)
        if not spot_check.passed:
            return LiquidityCheck(
                passed=False,
                reason=f"Spot: {spot_check.reason}",
                volume_24h=spot_check.volume_24h,
                spread_bps=spot_check.spread_bps,
                orderbook_depth=spot_check.orderbook_depth,
            )

        # Perp 체크
        perp_check = await self.check(perp_exchange, symbol, order_quantity_usd)
        if not perp_check.passed:
            return LiquidityCheck(
                passed=False,
                reason=f"Perp: {perp_check.reason}",
                volume_24h=perp_check.volume_24h,
                spread_bps=perp_check.spread_bps,
                orderbook_depth=perp_check.orderbook_depth,
            )

        # 양쪽 모두 통과
        return LiquidityCheck(
            passed=True,
            volume_24h=min(spot_check.volume_24h, perp_check.volume_24h),
            spread_bps=max(spot_check.spread_bps, perp_check.spread_bps),
            orderbook_depth=min(spot_check.orderbook_depth, perp_check.orderbook_depth),
        )

    def get_cached(self, symbol: str) -> Optional[LiquidityCheck]:
        """캐시된 결과 조회"""
        return self._cache.get(symbol)

    def clear_cache(self) -> None:
        """캐시 클리어"""
        self._cache.clear()


# 싱글톤 인스턴스
_liquidity_filter: Optional[LiquidityFilter] = None


def get_liquidity_filter() -> LiquidityFilter:
    """유동성 필터 싱글톤"""
    global _liquidity_filter
    if _liquidity_filter is None:
        _liquidity_filter = LiquidityFilter()
    return _liquidity_filter
