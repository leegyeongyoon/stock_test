"""업비트 거래소 연결 클래스"""

import asyncio
import hashlib
import hmac
import time
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt

from src.exchange.base import (
    BalanceInfo,
    BaseExchange,
    OrderResult,
    TickerData,
)
from src.models.schemas import OrderSide, OrderStatus, OrderType


class UpbitExchange(BaseExchange):
    """
    업비트 거래소 연결 클래스

    Features:
    - KRW 마켓 현물 거래
    - JWT 인증
    - Rate limiting (초당 10회)
    """

    BASE_URL = "https://api.upbit.com/v1"

    # Upbit 주문 상태 → 내부 OrderStatus 매핑
    ORDER_STATUS_MAP = {
        "wait": OrderStatus.NEW,
        "watch": OrderStatus.NEW,
        "open": OrderStatus.PARTIAL,
        "done": OrderStatus.FILLED,
        "cancel": OrderStatus.CANCELED,
    }

    def __init__(
        self,
        api_key: str,
        secret: str,
        testnet: bool = False,  # Upbit은 testnet 없음
    ) -> None:
        super().__init__(api_key, secret, testnet)
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request_time = 0.0
        self._min_request_interval = 0.15  # 150ms (초당 ~6회) - 429 에러 방지

    def _create_token(self, query_string: Optional[str] = None) -> str:
        """JWT 토큰 생성 (HMAC SHA512)"""
        payload = {
            "access_key": self.api_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
        }

        if query_string:
            query_hash = hashlib.sha512(query_string.encode("utf-8")).hexdigest()
            payload["query_hash"] = query_hash
            payload["query_hash_alg"] = "SHA512"

        return jwt.encode(payload, self.secret, algorithm="HS256")

    async def _rate_limit(self) -> None:
        """Rate limiting 적용"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        auth_required: bool = True,
    ) -> dict:
        """API 요청"""
        await self._rate_limit()

        url = f"{self.BASE_URL}{endpoint}"
        headers = {"Accept": "application/json"}

        if auth_required:
            query_string = urlencode(params) if params else None
            token = self._create_token(query_string)
            headers["Authorization"] = f"Bearer {token}"

        try:
            if method.upper() == "GET":
                response = await self._client.get(url, params=params, headers=headers)
            elif method.upper() == "POST":
                response = await self._client.post(url, params=params, headers=headers)
            elif method.upper() == "DELETE":
                response = await self._client.delete(url, params=params, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            error_body = e.response.text
            raise Exception(f"Upbit API error: {e.response.status_code} - {error_body}")
        except Exception as e:
            raise Exception(f"Upbit request failed: {str(e)}")

    # === BaseExchange 구현 ===

    async def connect(self) -> bool:
        """거래소 연결"""
        try:
            self._client = httpx.AsyncClient(timeout=30.0)

            # API 키 확인
            if self.api_key and self.secret:
                accounts = await self._request("GET", "/accounts")
                if accounts:
                    self._connected = True
                    return True
            else:
                # 공개 API 테스트
                markets = await self.get_markets()
                if markets:
                    self._connected = True
                    return True

            return False
        except Exception as e:
            print(f"[Upbit] Connection failed: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """거래소 연결 해제"""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    async def get_ticker(self, symbol: str) -> Optional[TickerData]:
        """시세 조회 (KRW-BTC 형식)"""
        try:
            data = await self._request(
                "GET",
                "/ticker",
                params={"markets": symbol},
                auth_required=False,
            )

            if not data or len(data) == 0:
                return None

            ticker = data[0]
            return TickerData(
                symbol=symbol,
                bid=float(ticker.get("trade_price", 0)),  # Upbit은 bid/ask 별도 없음
                ask=float(ticker.get("trade_price", 0)),
                last=float(ticker.get("trade_price", 0)),
                volume_24h=float(ticker.get("acc_trade_price_24h", 0)),
                timestamp=datetime.now(),
            )
        except Exception as e:
            print(f"[Upbit] get_ticker error: {e}")
            return None

    async def get_balance(self, asset: str = "KRW") -> Optional[BalanceInfo]:
        """잔고 조회"""
        try:
            accounts = await self._request("GET", "/accounts")

            for account in accounts:
                if account.get("currency") == asset:
                    free = float(account.get("balance", 0))
                    locked = float(account.get("locked", 0))
                    return BalanceInfo(
                        asset=asset,
                        free=free,
                        locked=locked,
                        total=free + locked,
                    )

            # 해당 자산 없으면 0 반환
            return BalanceInfo(asset=asset, free=0, locked=0, total=0)
        except Exception as e:
            print(f"[Upbit] get_balance error: {e}")
            return None

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        post_only: bool = False,
    ) -> OrderResult:
        """
        주문 실행

        Args:
            symbol: 마켓 코드 (KRW-BTC)
            side: BUY/SELL
            order_type: MARKET/LIMIT
            quantity: 주문 수량 (SELL) 또는 주문 금액 (BUY MARKET)
            price: 지정가 (LIMIT만)
            post_only: Upbit은 미지원
        """
        import asyncio

        try:
            # Upbit 주문 파라미터 생성
            params = {
                "market": symbol,
                "side": "bid" if side == OrderSide.BUY else "ask",
            }

            if order_type == OrderType.MARKET:
                if side == OrderSide.BUY:
                    # 시장가 매수: 금액 기준
                    params["ord_type"] = "price"
                    params["price"] = str(int(quantity))  # KRW 금액
                else:
                    # 시장가 매도: 수량 기준
                    params["ord_type"] = "market"
                    params["volume"] = str(quantity)
            else:
                # 지정가 주문
                params["ord_type"] = "limit"
                params["volume"] = str(quantity)
                params["price"] = str(int(price))

            result = await self._request("POST", "/orders", params=params)

            order_id = result.get("uuid", "")

            # 시장가 주문은 체결 대기 후 상태 확인
            if order_type == OrderType.MARKET and order_id:
                # 최대 3초 동안 체결 대기 (100ms 간격)
                for _ in range(30):
                    await asyncio.sleep(0.1)
                    order_detail = await self.get_order(symbol, order_id)
                    if order_detail:
                        state = order_detail.get("state", "wait")
                        if state in ("done", "cancel"):
                            # 체결 완료
                            filled_qty = float(order_detail.get("executed_volume", 0))
                            avg_price = float(order_detail.get("avg_price", 0) or 0)
                            status = self.ORDER_STATUS_MAP.get(state, OrderStatus.FILLED)
                            return OrderResult(
                                success=filled_qty > 0,
                                order_id=order_id,
                                exchange_order_id=order_id,
                                status=status,
                                filled_qty=filled_qty,
                                avg_price=avg_price,
                                commission=0.0,
                                commission_asset="KRW",
                            )

            # 기본 응답 (지정가 또는 시장가 체결 대기 타임아웃)
            status = self.ORDER_STATUS_MAP.get(
                result.get("state", "wait"),
                OrderStatus.NEW,
            )

            return OrderResult(
                success=True,
                order_id=order_id,
                exchange_order_id=order_id,
                status=status,
                filled_qty=float(result.get("executed_volume", 0)),
                avg_price=float(result.get("avg_price", 0) or 0),
                commission=0.0,
                commission_asset="KRW",
            )
        except Exception as e:
            return OrderResult(
                success=False,
                error=str(e),
            )

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """주문 취소"""
        try:
            params = {"uuid": order_id}
            await self._request("DELETE", "/order", params=params)
            return True
        except Exception as e:
            print(f"[Upbit] cancel_order error: {e}")
            return False

    async def get_order_status(self, symbol: str, order_id: str) -> Optional[OrderStatus]:
        """주문 상태 조회"""
        try:
            order = await self.get_order(symbol, order_id)
            if order:
                state = order.get("state", "wait")
                return self.ORDER_STATUS_MAP.get(state, OrderStatus.NEW)
            return None
        except Exception:
            return None

    async def get_order(self, symbol: str, order_id: str) -> Optional[dict]:
        """주문 상세 정보 조회"""
        try:
            params = {"uuid": order_id}
            result = await self._request("GET", "/order", params=params)
            return result
        except Exception as e:
            print(f"[Upbit] get_order error: {e}")
            return None

    async def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """미체결 주문 조회"""
        try:
            params = {"state": "wait"}
            if symbol:
                params["market"] = symbol

            result = await self._request("GET", "/orders", params=params)
            return result if result else []
        except Exception as e:
            print(f"[Upbit] get_open_orders error: {e}")
            return []

    # === Upbit 특화 메서드 ===

    async def get_markets(self) -> list[dict]:
        """마켓 목록 조회"""
        try:
            result = await self._request(
                "GET",
                "/market/all",
                params={"isDetails": "true"},
                auth_required=False,
            )
            return result if result else []
        except Exception as e:
            print(f"[Upbit] get_markets error: {e}")
            return []

    async def get_krw_markets(self) -> list[str]:
        """KRW 마켓 심볼 목록 조회"""
        markets = await self.get_markets()
        return [m["market"] for m in markets if m["market"].startswith("KRW-")]

    async def get_all_balances(self) -> list[BalanceInfo]:
        """전체 잔고 조회"""
        try:
            accounts = await self._request("GET", "/accounts")
            balances = []

            for account in accounts:
                free = float(account.get("balance", 0))
                locked = float(account.get("locked", 0))
                total = free + locked
                avg_buy_price = float(account.get("avg_buy_price", 0))

                if total > 0:
                    balances.append(
                        BalanceInfo(
                            asset=account.get("currency", ""),
                            free=free,
                            locked=locked,
                            total=total,
                            avg_buy_price=avg_buy_price,
                        )
                    )

            return balances
        except Exception as e:
            print(f"[Upbit] get_all_balances error: {e}")
            return []

    async def get_all_tickers(self, markets: Optional[list[str]] = None) -> dict[str, dict]:
        """전체 시세 조회"""
        try:
            if not markets:
                markets = await self.get_krw_markets()

            if not markets:
                return {}

            # Upbit은 최대 100개씩 조회 가능
            result = {}
            for i in range(0, len(markets), 100):
                batch = markets[i : i + 100]
                market_param = ",".join(batch)

                data = await self._request(
                    "GET",
                    "/ticker",
                    params={"markets": market_param},
                    auth_required=False,
                )

                for ticker in data:
                    market = ticker.get("market", "")
                    result[market] = {
                        "trade_price": ticker.get("trade_price", 0),
                        "acc_trade_price_24h": ticker.get("acc_trade_price_24h", 0),
                        "signed_change_rate": ticker.get("signed_change_rate", 0),
                        "highest_52_week_price": ticker.get("highest_52_week_price", 0),
                        "lowest_52_week_price": ticker.get("lowest_52_week_price", 0),
                        "prev_closing_price": ticker.get("prev_closing_price", 0),
                        "opening_price": ticker.get("opening_price", 0),
                        "high_price": ticker.get("high_price", 0),
                        "low_price": ticker.get("low_price", 0),
                        "acc_trade_volume_24h": ticker.get("acc_trade_volume_24h", 0),
                    }

            return result
        except Exception as e:
            print(f"[Upbit] get_all_tickers error: {e}")
            return {}

    async def get_orderbook(self, symbol: str) -> Optional[dict]:
        """호가창 조회"""
        try:
            data = await self._request(
                "GET",
                "/orderbook",
                params={"markets": symbol},
                auth_required=False,
            )

            if data and len(data) > 0:
                return data[0]
            return None
        except Exception as e:
            print(f"[Upbit] get_orderbook error: {e}")
            return None

    async def get_candles(
        self,
        symbol: str,
        interval: str = "5",  # 분 단위: 1, 3, 5, 15, 30, 60, 240
        count: int = 200,
    ) -> list[dict]:
        """
        캔들 데이터 조회

        Args:
            symbol: 마켓 코드 (KRW-BTC)
            interval: 분 단위 (1, 3, 5, 15, 30, 60, 240)
            count: 조회 개수 (최대 200)
        """
        try:
            endpoint = f"/candles/minutes/{interval}"
            params = {
                "market": symbol,
                "count": min(count, 200),
            }

            result = await self._request("GET", endpoint, params=params, auth_required=False)
            return result if result else []
        except Exception as e:
            print(f"[Upbit] get_candles error: {e}")
            return []

    async def get_day_candles(
        self,
        symbol: str,
        count: int = 200,
    ) -> list[dict]:
        """일봉 데이터 조회"""
        try:
            params = {
                "market": symbol,
                "count": min(count, 200),
            }

            result = await self._request(
                "GET",
                "/candles/days",
                params=params,
                auth_required=False,
            )
            return result if result else []
        except Exception as e:
            print(f"[Upbit] get_day_candles error: {e}")
            return []

    async def health_check(self) -> bool:
        """연결 상태 체크"""
        try:
            ticker = await self.get_ticker("KRW-BTC")
            return ticker is not None
        except Exception:
            return False


class SymbolConverter:
    """심볼 변환 유틸리티"""

    @staticmethod
    def to_upbit(binance_symbol: str) -> str:
        """Binance → Upbit 심볼 변환 (BTCUSDT → KRW-BTC)"""
        base = binance_symbol.replace("USDT", "").replace("BUSD", "")
        return f"KRW-{base}"

    @staticmethod
    def from_upbit(upbit_symbol: str) -> str:
        """Upbit → Binance 심볼 변환 (KRW-BTC → BTCUSDT)"""
        if upbit_symbol.startswith("KRW-"):
            base = upbit_symbol.replace("KRW-", "")
            return f"{base}USDT"
        return upbit_symbol

    @staticmethod
    def get_base_asset(upbit_symbol: str) -> str:
        """심볼에서 기초 자산 추출 (KRW-BTC → BTC)"""
        if upbit_symbol.startswith("KRW-"):
            return upbit_symbol.replace("KRW-", "")
        return upbit_symbol
