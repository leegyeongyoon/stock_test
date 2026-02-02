"""거래소 연결 베이스 클래스 (인터페이스)"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.models.schemas import OrderSide, OrderStatus, OrderType


@dataclass
class TickerData:
    """시세 데이터"""

    symbol: str
    bid: float
    ask: float
    last: float
    volume_24h: float
    timestamp: datetime


@dataclass
class OrderResult:
    """주문 결과"""

    success: bool
    order_id: Optional[str] = None
    exchange_order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.NEW
    filled_qty: float = 0.0
    avg_price: float = 0.0
    error: Optional[str] = None
    commission: float = 0.0  # 수수료
    commission_asset: str = "USDT"  # 수수료 자산


@dataclass
class BalanceInfo:
    """잔고 정보"""

    asset: str
    free: float
    locked: float
    total: float


@dataclass
class PositionInfo:
    """포지션 정보 (선물)"""

    symbol: str
    side: str
    quantity: float
    entry_price: float
    unrealized_pnl: float
    leverage: int
    liquidation_price: float


class BaseExchange(ABC):
    """거래소 연결 베이스 클래스"""

    def __init__(
        self,
        api_key: str,
        secret: str,
        testnet: bool = True,
    ) -> None:
        self.api_key = api_key
        self.secret = secret
        self.testnet = testnet
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """연결 상태"""
        return self._connected

    @abstractmethod
    async def connect(self) -> bool:
        """거래소 연결"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """거래소 연결 해제"""
        pass

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Optional[TickerData]:
        """시세 조회"""
        pass

    @abstractmethod
    async def get_balance(self, asset: str = "USDT") -> Optional[BalanceInfo]:
        """잔고 조회"""
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """주문 취소"""
        pass

    @abstractmethod
    async def get_order_status(self, symbol: str, order_id: str) -> Optional[OrderStatus]:
        """주문 상태 조회"""
        pass

    @abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> Optional[dict]:
        """주문 상세 정보 조회"""
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """미체결 주문 조회"""
        pass

    async def health_check(self) -> bool:
        """연결 상태 체크"""
        try:
            # 간단한 API 호출로 연결 확인
            ticker = await self.get_ticker("BTCUSDT")
            return ticker is not None
        except Exception:
            return False
