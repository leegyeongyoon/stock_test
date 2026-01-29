"""주문 상태 머신 - NEW → SENT → ACK → PARTIAL → FILLED/CANCELED/REJECTED"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

import structlog

from src.models.schemas import OrderSide, OrderStatus, OrderType, StrategyType

logger = structlog.get_logger()


class OrderStateError(Exception):
    """주문 상태 전환 오류"""

    pass


# 허용된 상태 전환
VALID_TRANSITIONS = {
    OrderStatus.NEW: {OrderStatus.SENT, OrderStatus.CANCELED},
    OrderStatus.SENT: {OrderStatus.ACK, OrderStatus.REJECTED, OrderStatus.CANCELED},
    OrderStatus.ACK: {OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELED},
    OrderStatus.PARTIAL: {OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELED},
    OrderStatus.FILLED: set(),  # 최종 상태
    OrderStatus.CANCELED: set(),  # 최종 상태
    OrderStatus.REJECTED: set(),  # 최종 상태
}


class OrderStateMachine:
    """
    주문 상태 머신

    상태 흐름:
    NEW → SENT → ACK → PARTIAL → FILLED
                   ↘        ↘
                    CANCELED  CANCELED
                   ↘
                    REJECTED
    """

    def __init__(
        self,
        symbol: str,
        strategy: StrategyType,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None,
    ) -> None:
        self.order_id = str(uuid4())
        self.exchange_order_id: Optional[str] = None
        self.symbol = symbol
        self.strategy = strategy
        self.side = side
        self.order_type = order_type
        self.quantity = quantity
        self.price = price

        self._status = OrderStatus.NEW
        self.filled_quantity: float = 0.0
        self.avg_fill_price: Optional[float] = None
        self.fee: float = 0.0

        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.sent_at: Optional[datetime] = None
        self.acked_at: Optional[datetime] = None
        self.filled_at: Optional[datetime] = None

        self._history: list[tuple[OrderStatus, datetime]] = [
            (OrderStatus.NEW, self.created_at)
        ]

    @property
    def status(self) -> OrderStatus:
        """현재 상태"""
        return self._status

    @property
    def is_terminal(self) -> bool:
        """최종 상태 여부"""
        return self._status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
        }

    @property
    def is_active(self) -> bool:
        """활성 주문 여부"""
        return not self.is_terminal

    @property
    def remaining_quantity(self) -> float:
        """미체결 수량"""
        return self.quantity - self.filled_quantity

    @property
    def fill_ratio(self) -> float:
        """체결 비율"""
        if self.quantity == 0:
            return 0.0
        return self.filled_quantity / self.quantity

    def transition_to(self, new_status: OrderStatus) -> None:
        """상태 전환"""
        if new_status not in VALID_TRANSITIONS.get(self._status, set()):
            raise OrderStateError(
                f"Invalid transition: {self._status.value} → {new_status.value}"
            )

        old_status = self._status
        self._status = new_status
        self.updated_at = datetime.utcnow()
        self._history.append((new_status, self.updated_at))

        # 특정 상태 진입 시 타임스탬프 기록
        if new_status == OrderStatus.SENT:
            self.sent_at = self.updated_at
        elif new_status == OrderStatus.ACK:
            self.acked_at = self.updated_at
        elif new_status == OrderStatus.FILLED:
            self.filled_at = self.updated_at

        logger.info(
            "Order state transition",
            order_id=self.order_id,
            from_status=old_status.value,
            to_status=new_status.value,
        )

    def mark_sent(self, exchange_order_id: str) -> None:
        """SENT 상태로 전환"""
        self.exchange_order_id = exchange_order_id
        self.transition_to(OrderStatus.SENT)

    def mark_acked(self) -> None:
        """ACK 상태로 전환"""
        self.transition_to(OrderStatus.ACK)

    def mark_partial_fill(
        self,
        filled_qty: float,
        fill_price: float,
        fee: float = 0.0,
    ) -> None:
        """부분 체결 처리"""
        if self._status not in {OrderStatus.ACK, OrderStatus.PARTIAL}:
            raise OrderStateError(f"Cannot partial fill from {self._status.value}")

        # 평균가 계산
        total_value = (self.filled_quantity * (self.avg_fill_price or 0)) + (
            filled_qty * fill_price
        )
        self.filled_quantity += filled_qty
        self.avg_fill_price = total_value / self.filled_quantity if self.filled_quantity > 0 else 0
        self.fee += fee

        if self._status != OrderStatus.PARTIAL:
            self.transition_to(OrderStatus.PARTIAL)

        self.updated_at = datetime.utcnow()

    def mark_filled(
        self,
        filled_qty: Optional[float] = None,
        fill_price: Optional[float] = None,
        fee: float = 0.0,
    ) -> None:
        """완전 체결 처리"""
        if filled_qty is not None:
            self.filled_quantity = filled_qty
        else:
            self.filled_quantity = self.quantity

        if fill_price is not None:
            self.avg_fill_price = fill_price

        self.fee += fee
        self.transition_to(OrderStatus.FILLED)

    def mark_canceled(self) -> None:
        """취소 처리"""
        if self.is_terminal:
            raise OrderStateError(f"Cannot cancel from {self._status.value}")
        self.transition_to(OrderStatus.CANCELED)

    def mark_rejected(self, reason: str = "") -> None:
        """거부 처리"""
        if self._status != OrderStatus.SENT:
            raise OrderStateError(f"Cannot reject from {self._status.value}")
        self.transition_to(OrderStatus.REJECTED)
        logger.warning("Order rejected", order_id=self.order_id, reason=reason)

    def to_dict(self) -> dict:
        """딕셔너리 변환"""
        return {
            "order_id": self.order_id,
            "exchange_order_id": self.exchange_order_id,
            "symbol": self.symbol,
            "strategy": self.strategy.value,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": self.price,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "fee": self.fee,
            "status": self._status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def get_history(self) -> list[dict]:
        """상태 변경 이력"""
        return [
            {"status": status.value, "timestamp": ts.isoformat()}
            for status, ts in self._history
        ]
