"""Binance Spot 거래소 연결"""

import asyncio
import hashlib
import hmac
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import httpx
import structlog

from src.exchange.base import (
    BalanceInfo,
    BaseExchange,
    OrderResult,
    PositionInfo,
    TickerData,
)
from src.models.schemas import OrderSide, OrderStatus, OrderType

logger = structlog.get_logger()


class BinanceSpotExchange(BaseExchange):
    """Binance Spot 거래소 클라이언트"""

    MAINNET_REST = "https://api.binance.com"
    MAINNET_WS = "wss://stream.binance.com:9443/ws"
    TESTNET_REST = "https://testnet.binance.vision"
    TESTNET_WS = "wss://testnet.binance.vision/ws"

    def __init__(
        self,
        api_key: str,
        secret: str,
        testnet: bool = True,
    ) -> None:
        super().__init__(api_key, secret, testnet)

        self.base_url = self.TESTNET_REST if testnet else self.MAINNET_REST
        self.ws_url = self.TESTNET_WS if testnet else self.MAINNET_WS

        self._client: Optional[httpx.AsyncClient] = None
        self._rate_limit_remaining = 1200
        self._rate_limit_reset = 0

    async def connect(self) -> bool:
        """거래소 연결"""
        try:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                headers={"X-MBX-APIKEY": self.api_key},
            )

            # 연결 테스트
            response = await self._client.get("/api/v3/ping")
            if response.status_code == 200:
                self._connected = True
                logger.info(
                    "Binance Spot connected",
                    testnet=self.testnet,
                )
                return True

            logger.error("Binance Spot connection failed", status=response.status_code)
            return False

        except Exception as e:
            logger.error("Binance Spot connection error", error=str(e))
            return False

    async def disconnect(self) -> None:
        """거래소 연결 해제"""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        logger.info("Binance Spot disconnected")

    def _sign(self, params: dict) -> str:
        """HMAC SHA256 서명 생성"""
        query_string = urlencode(params)
        signature = hmac.new(
            self.secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        signed: bool = False,
    ) -> Optional[dict]:
        """API 요청 (재시도 로직 포함)"""
        if not self._client:
            logger.error("Client not connected")
            return None

        params = params or {}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)

        max_retries = 3
        backoff = 1

        for attempt in range(max_retries):
            try:
                if method == "GET":
                    response = await self._client.get(endpoint, params=params)
                elif method == "POST":
                    response = await self._client.post(endpoint, params=params)
                elif method == "DELETE":
                    response = await self._client.delete(endpoint, params=params)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                # Rate limit 헤더 처리
                if "X-MBX-USED-WEIGHT-1M" in response.headers:
                    self._rate_limit_remaining = 1200 - int(
                        response.headers["X-MBX-USED-WEIGHT-1M"]
                    )

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 429:  # Rate limit
                    wait_time = int(response.headers.get("Retry-After", 60))
                    logger.warning("Rate limited", wait=wait_time)
                    await asyncio.sleep(wait_time)
                    continue

                if response.status_code >= 500:  # 서버 오류
                    logger.warning(
                        "Server error, retrying",
                        status=response.status_code,
                        attempt=attempt + 1,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue

                # 클라이언트 오류
                logger.error(
                    "API error",
                    status=response.status_code,
                    response=response.text,
                )
                return None

            except httpx.TimeoutException:
                logger.warning("Request timeout, retrying", attempt=attempt + 1)
                await asyncio.sleep(backoff)
                backoff *= 2
            except Exception as e:
                logger.error("Request error", error=str(e))
                return None

        return None

    async def get_ticker(self, symbol: str) -> Optional[TickerData]:
        """시세 조회"""
        data = await self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": symbol})
        if not data:
            return None

        price_data = await self._request("GET", "/api/v3/ticker/24hr", {"symbol": symbol})

        return TickerData(
            symbol=symbol,
            bid=float(data["bidPrice"]),
            ask=float(data["askPrice"]),
            last=float(price_data["lastPrice"]) if price_data else 0,
            volume_24h=float(price_data["volume"]) if price_data else 0,
            timestamp=datetime.utcnow(),
        )

    async def get_balance(self, asset: str = "USDT") -> Optional[BalanceInfo]:
        """잔고 조회"""
        data = await self._request("GET", "/api/v3/account", signed=True)
        if not data:
            return None

        for balance in data.get("balances", []):
            if balance["asset"] == asset:
                free = float(balance["free"])
                locked = float(balance["locked"])
                return BalanceInfo(
                    asset=asset,
                    free=free,
                    locked=locked,
                    total=free + locked,
                )

        return BalanceInfo(asset=asset, free=0, locked=0, total=0)

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        post_only: bool = False,
    ) -> OrderResult:
        """주문 실행"""
        params = {
            "symbol": symbol,
            "side": side.value,
            "type": order_type.value,
            "quantity": str(quantity),
        }

        if order_type == OrderType.LIMIT and price:
            params["price"] = str(price)
            # post_only: GTX (maker only)
            params["timeInForce"] = "GTX" if post_only else "GTC"

        data = await self._request("POST", "/api/v3/order", params, signed=True)

        if not data:
            return OrderResult(success=False, error="Order failed")

        status_map = {
            "NEW": OrderStatus.ACK,
            "PARTIALLY_FILLED": OrderStatus.PARTIAL,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.CANCELED,  # GTX rejected시 EXPIRED 반환
        }

        return OrderResult(
            success=True,
            order_id=str(data.get("clientOrderId", "")),
            exchange_order_id=str(data.get("orderId", "")),
            status=status_map.get(data.get("status", ""), OrderStatus.NEW),
            filled_qty=float(data.get("executedQty", 0)),
            avg_price=float(data.get("avgPrice", 0) or data.get("price", 0)),
        )

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """주문 취소"""
        params = {
            "symbol": symbol,
            "orderId": order_id,
        }
        data = await self._request("DELETE", "/api/v3/order", params, signed=True)
        return data is not None and data.get("status") == "CANCELED"

    async def get_order_status(self, symbol: str, order_id: str) -> Optional[OrderStatus]:
        """주문 상태 조회"""
        params = {
            "symbol": symbol,
            "orderId": order_id,
        }
        data = await self._request("GET", "/api/v3/order", params, signed=True)

        if not data:
            return None

        status_map = {
            "NEW": OrderStatus.ACK,
            "PARTIALLY_FILLED": OrderStatus.PARTIAL,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.CANCELED,
        }
        return status_map.get(data.get("status"), OrderStatus.NEW)

    async def get_order(self, symbol: str, order_id: str) -> Optional[dict]:
        """주문 상세 정보 조회"""
        params = {
            "symbol": symbol,
            "orderId": order_id,
        }
        data = await self._request("GET", "/api/v3/order", params, signed=True)

        if not data:
            return None

        return {
            "order_id": str(data.get("orderId", "")),
            "symbol": data.get("symbol", ""),
            "side": data.get("side", ""),
            "status": data.get("status", ""),
            "filled_qty": float(data.get("executedQty", 0)),
            "avg_price": float(data.get("avgPrice", 0) or data.get("price", 0)),
            "quantity": float(data.get("origQty", 0)),
            "price": float(data.get("price", 0)),
        }

    async def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """미체결 주문 조회"""
        params = {}
        if symbol:
            params["symbol"] = symbol

        data = await self._request("GET", "/api/v3/openOrders", params, signed=True)
        return data or []

    async def get_all_balances(self) -> list[BalanceInfo]:
        """모든 잔고 조회"""
        data = await self._request("GET", "/api/v3/account", signed=True)
        if not data:
            return []

        balances = []
        for balance in data.get("balances", []):
            free = float(balance["free"])
            locked = float(balance["locked"])
            total = free + locked
            if total > 0:
                balances.append(
                    BalanceInfo(
                        asset=balance["asset"],
                        free=free,
                        locked=locked,
                        total=total,
                    )
                )
        return balances

    async def get_klines(
        self, symbol: str, interval: str, limit: int = 100
    ) -> list[list]:
        """캔들(Kline) 데이터 조회"""
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        data = await self._request("GET", "/api/v3/klines", params)
        return data or []

    async def get_24h_volume(self, symbol: str) -> Optional[float]:
        """24시간 거래대금 조회 (USDT)"""
        data = await self._request("GET", "/api/v3/ticker/24hr", {"symbol": symbol})
        if data:
            return float(data.get("quoteVolume", 0))
        return None

    async def get_orderbook_depth(
        self, symbol: str, limit: int = 100
    ) -> Optional[dict]:
        """오더북 깊이 조회"""
        data = await self._request("GET", "/api/v3/depth", {"symbol": symbol, "limit": limit})
        if not data:
            return None

        return {
            "bids": [(float(p), float(q)) for p, q in data.get("bids", [])],
            "asks": [(float(p), float(q)) for p, q in data.get("asks", [])],
        }
