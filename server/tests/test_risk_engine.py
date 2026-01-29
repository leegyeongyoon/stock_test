"""Risk Engine 테스트"""

import pytest
from src.risk.modes import ModeManager
from src.risk.risk_engine import RiskEngine
from src.models.schemas import TradingMode


class TestModeManager:
    """ModeManager 테스트"""

    @pytest.fixture
    def mode_manager(self):
        return ModeManager()

    @pytest.mark.asyncio
    async def test_initial_mode_is_normal(self, mode_manager):
        """초기 모드는 NORMAL"""
        assert mode_manager.mode == TradingMode.NORMAL
        assert mode_manager.is_normal

    @pytest.mark.asyncio
    async def test_transition_to_safe(self, mode_manager):
        """NORMAL → SAFE 전환"""
        result = await mode_manager.to_safe("Test reason")

        assert result is True
        assert mode_manager.mode == TradingMode.SAFE
        assert mode_manager.is_safe
        assert "Test reason" in mode_manager.reason

    @pytest.mark.asyncio
    async def test_transition_to_halt(self, mode_manager):
        """NORMAL → HALT 전환"""
        result = await mode_manager.to_halt("Emergency")

        assert result is True
        assert mode_manager.mode == TradingMode.HALT
        assert mode_manager.is_halt

    @pytest.mark.asyncio
    async def test_cannot_go_normal_from_halt(self, mode_manager):
        """HALT에서 바로 NORMAL로 갈 수 없음"""
        await mode_manager.to_halt("Emergency")

        result = await mode_manager.to_normal("Try resume")

        assert result is False
        assert mode_manager.mode == TradingMode.HALT

    @pytest.mark.asyncio
    async def test_safe_resume_from_halt(self, mode_manager):
        """HALT → SAFE 전환 가능"""
        await mode_manager.to_halt("Emergency")

        result = await mode_manager.safe_to_normal_from_halt("Safe resume")

        assert result is True
        assert mode_manager.mode == TradingMode.SAFE

    @pytest.mark.asyncio
    async def test_normal_from_safe(self, mode_manager):
        """SAFE → NORMAL 전환"""
        await mode_manager.to_safe("Test")

        result = await mode_manager.to_normal("Resume")

        assert result is True
        assert mode_manager.mode == TradingMode.NORMAL

    @pytest.mark.asyncio
    async def test_can_open_position_only_in_normal(self, mode_manager):
        """NORMAL 모드에서만 신규 포지션 가능"""
        assert mode_manager.can_open_new_position is True

        await mode_manager.to_safe("Test")
        assert mode_manager.can_open_new_position is False

        await mode_manager.to_halt("Emergency")
        assert mode_manager.can_open_new_position is False


class TestRiskEngine:
    """RiskEngine 테스트"""

    @pytest.fixture
    def risk_engine(self):
        return RiskEngine()

    def test_initial_state(self, risk_engine):
        """초기 상태 확인"""
        assert risk_engine.mode == TradingMode.NORMAL
        assert risk_engine.can_open_position is True

    def test_order_success_tracking(self, risk_engine):
        """주문 성공 추적"""
        risk_engine.on_order_success()
        risk_engine.on_order_success()

        status = risk_engine.get_status()
        assert status["order_total_count"] == 2
        assert status["order_failure_count"] == 0

    def test_order_failure_tracking(self, risk_engine):
        """주문 실패 추적"""
        risk_engine.on_order_failure()
        risk_engine.on_order_failure()

        status = risk_engine.get_status()
        assert status["order_total_count"] == 2
        assert status["order_failure_count"] == 2

    def test_ws_disconnect_tracking(self, risk_engine):
        """WS 연결 끊김 추적"""
        risk_engine.on_ws_disconnect()
        risk_engine.on_ws_disconnect()

        status = risk_engine.get_status()
        assert status["ws_disconnect_count"] == 2

    def test_ws_reconnect_resets_count(self, risk_engine):
        """WS 재연결 시 카운터 리셋"""
        risk_engine.on_ws_disconnect()
        risk_engine.on_ws_disconnect()
        risk_engine.on_ws_reconnect()

        status = risk_engine.get_status()
        assert status["ws_disconnect_count"] == 0

    def test_reset_counters(self, risk_engine):
        """카운터 리셋"""
        risk_engine.on_order_failure()
        risk_engine.on_ws_disconnect()
        risk_engine.on_auth_failure()

        risk_engine.reset_counters()

        status = risk_engine.get_status()
        assert status["order_total_count"] == 0
        assert status["ws_disconnect_count"] == 0
        assert status["auth_failure_count"] == 0

    @pytest.mark.asyncio
    async def test_pause_resume(self, risk_engine):
        """수동 pause/resume"""
        result = await risk_engine.pause("Manual test")
        assert result is True
        assert risk_engine.mode == TradingMode.SAFE

        result = await risk_engine.resume("Manual resume")
        assert result is True
        assert risk_engine.mode == TradingMode.NORMAL
