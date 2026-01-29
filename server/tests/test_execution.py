"""Execution Engine 테스트"""

import pytest
from src.execution.order_state import OrderStateMachine, OrderStateError
from src.models.schemas import OrderSide, OrderStatus, OrderType, StrategyType


class TestOrderStateMachine:
    """OrderStateMachine 테스트"""

    @pytest.fixture
    def order(self):
        return OrderStateMachine(
            symbol="BTCUSDT",
            strategy=StrategyType.CORE,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.1,
            price=50000.0,
        )

    def test_initial_state_is_new(self, order):
        """초기 상태는 NEW"""
        assert order.status == OrderStatus.NEW
        assert order.is_active
        assert not order.is_terminal

    def test_transition_new_to_sent(self, order):
        """NEW → SENT"""
        order.mark_sent("EX123")

        assert order.status == OrderStatus.SENT
        assert order.exchange_order_id == "EX123"
        assert order.sent_at is not None

    def test_transition_sent_to_ack(self, order):
        """SENT → ACK"""
        order.mark_sent("EX123")
        order.mark_acked()

        assert order.status == OrderStatus.ACK
        assert order.acked_at is not None

    def test_partial_fill(self, order):
        """부분 체결"""
        order.mark_sent("EX123")
        order.mark_acked()
        order.mark_partial_fill(0.05, 49900.0, fee=0.5)

        assert order.status == OrderStatus.PARTIAL
        assert order.filled_quantity == 0.05
        assert order.avg_fill_price == 49900.0
        assert order.fee == 0.5
        assert order.remaining_quantity == 0.05

    def test_multiple_partial_fills(self, order):
        """다중 부분 체결"""
        order.mark_sent("EX123")
        order.mark_acked()
        order.mark_partial_fill(0.03, 49900.0, fee=0.3)
        order.mark_partial_fill(0.02, 50100.0, fee=0.2)

        assert order.filled_quantity == 0.05
        # 평균가: (0.03 * 49900 + 0.02 * 50100) / 0.05 = 49980
        assert abs(order.avg_fill_price - 49980.0) < 0.01
        assert order.fee == 0.5

    def test_full_fill(self, order):
        """완전 체결"""
        order.mark_sent("EX123")
        order.mark_acked()
        order.mark_filled(filled_qty=0.1, fill_price=49950.0)

        assert order.status == OrderStatus.FILLED
        assert order.is_terminal
        assert order.filled_quantity == 0.1
        assert order.filled_at is not None

    def test_cancel(self, order):
        """취소"""
        order.mark_sent("EX123")
        order.mark_acked()
        order.mark_canceled()

        assert order.status == OrderStatus.CANCELED
        assert order.is_terminal

    def test_reject(self, order):
        """거부"""
        order.mark_sent("EX123")
        order.mark_rejected("Insufficient balance")

        assert order.status == OrderStatus.REJECTED
        assert order.is_terminal

    def test_invalid_transition_raises_error(self, order):
        """잘못된 전환은 에러"""
        # NEW에서 바로 FILLED로 갈 수 없음
        with pytest.raises(OrderStateError):
            order.transition_to(OrderStatus.FILLED)

    def test_cannot_cancel_filled_order(self, order):
        """체결된 주문은 취소 불가"""
        order.mark_sent("EX123")
        order.mark_acked()
        order.mark_filled()

        with pytest.raises(OrderStateError):
            order.mark_canceled()

    def test_fill_ratio(self, order):
        """체결 비율 계산"""
        order.mark_sent("EX123")
        order.mark_acked()
        order.mark_partial_fill(0.05, 50000.0)

        assert order.fill_ratio == 0.5

    def test_to_dict(self, order):
        """딕셔너리 변환"""
        order.mark_sent("EX123")

        data = order.to_dict()

        assert data["symbol"] == "BTCUSDT"
        assert data["strategy"] == "CORE"
        assert data["side"] == "BUY"
        assert data["status"] == "SENT"
        assert data["exchange_order_id"] == "EX123"

    def test_history_tracking(self, order):
        """상태 변경 이력 추적"""
        order.mark_sent("EX123")
        order.mark_acked()
        order.mark_partial_fill(0.05, 50000.0)
        order.mark_filled()

        history = order.get_history()

        assert len(history) == 5  # NEW -> SENT -> ACK -> PARTIAL -> FILLED
        assert history[0]["status"] == "NEW"
        assert history[1]["status"] == "SENT"
        assert history[2]["status"] == "ACK"
        assert history[3]["status"] == "PARTIAL"
        assert history[4]["status"] == "FILLED"
