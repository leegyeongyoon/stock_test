"""알고리즘 업그레이드 테스트 - Phase 1, 2, 3 검증"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# === Phase 1: CandleManager Tests ===


class TestCandleManager:
    """CandleManager 테스트"""

    def test_candle_buffer_add(self):
        """캔들 버퍼 추가 테스트"""
        from src.data.candle_manager import Candle, CandleBuffer

        buffer = CandleBuffer(symbol="BTCUSDT", interval="5m", max_size=10)

        # 캔들 추가
        for i in range(5):
            candle = Candle(
                symbol="BTCUSDT",
                interval="5m",
                open_time=datetime.utcnow() + timedelta(minutes=i * 5),
                close_time=datetime.utcnow() + timedelta(minutes=(i + 1) * 5),
                open=100.0 + i,
                high=105.0 + i,
                low=95.0 + i,
                close=102.0 + i,
                volume=1000.0,
                quote_volume=100000.0,
            )
            buffer.add(candle)

        assert len(buffer.candles) == 5
        assert buffer.get_latest(1)[0].close == 106.0  # 마지막 캔들

    def test_candle_buffer_max_size(self):
        """캔들 버퍼 최대 크기 테스트"""
        from src.data.candle_manager import Candle, CandleBuffer

        buffer = CandleBuffer(symbol="BTCUSDT", interval="5m", max_size=5)

        # 10개 추가
        for i in range(10):
            candle = Candle(
                symbol="BTCUSDT",
                interval="5m",
                open_time=datetime.utcnow() + timedelta(minutes=i * 5),
                close_time=datetime.utcnow() + timedelta(minutes=(i + 1) * 5),
                open=100.0,
                high=105.0,
                low=95.0,
                close=102.0,
                volume=1000.0,
                quote_volume=100000.0,
            )
            buffer.add(candle)

        # 최대 5개만 유지
        assert len(buffer.candles) == 5

    def test_candle_manager_calc_sma(self):
        """SMA 계산 테스트"""
        from src.data.candle_manager import Candle, CandleManager

        manager = CandleManager()

        # 버퍼 초기화 및 캔들 추가
        manager._buffers["BTCUSDT"] = {}
        from src.data.candle_manager import CandleBuffer

        buffer = CandleBuffer(symbol="BTCUSDT", interval="1h", max_size=100)
        manager._buffers["BTCUSDT"]["1h"] = buffer

        # 20개 캔들 추가 (close: 100, 101, ..., 119)
        for i in range(20):
            candle = Candle(
                symbol="BTCUSDT",
                interval="1h",
                open_time=datetime.utcnow() + timedelta(hours=i),
                close_time=datetime.utcnow() + timedelta(hours=i + 1),
                open=100.0 + i,
                high=105.0 + i,
                low=95.0 + i,
                close=100.0 + i,  # 100, 101, ..., 119
                volume=1000.0,
                quote_volume=100000.0,
            )
            buffer.add(candle)

        # SMA(20) = (100 + 101 + ... + 119) / 20 = 109.5
        sma = manager.calc_sma("BTCUSDT", "1h", 20)
        assert sma == pytest.approx(109.5, rel=0.01)

    def test_candle_manager_calc_atr(self):
        """ATR 계산 테스트"""
        from src.data.candle_manager import Candle, CandleBuffer, CandleManager

        manager = CandleManager()
        manager._buffers["BTCUSDT"] = {}
        buffer = CandleBuffer(symbol="BTCUSDT", interval="1h", max_size=100)
        manager._buffers["BTCUSDT"]["1h"] = buffer

        # 15개 캔들 추가 (ATR(14) 계산 필요)
        for i in range(15):
            candle = Candle(
                symbol="BTCUSDT",
                interval="1h",
                open_time=datetime.utcnow() + timedelta(hours=i),
                close_time=datetime.utcnow() + timedelta(hours=i + 1),
                open=100.0,
                high=105.0,  # TR = 10 (high - low)
                low=95.0,
                close=100.0,
                volume=1000.0,
                quote_volume=100000.0,
            )
            buffer.add(candle)

        atr = manager.calc_atr("BTCUSDT", "1h", 14)
        assert atr is not None
        assert atr == pytest.approx(10.0, rel=0.01)  # TR = 10

    def test_candle_manager_calc_rvol(self):
        """RVOL 계산 테스트"""
        from src.data.candle_manager import Candle, CandleBuffer, CandleManager

        manager = CandleManager()
        manager._buffers["BTCUSDT"] = {}
        buffer = CandleBuffer(symbol="BTCUSDT", interval="5m", max_size=100)
        manager._buffers["BTCUSDT"]["5m"] = buffer

        # 20개 캔들 (평균 거래량 100,000)
        for i in range(20):
            candle = Candle(
                symbol="BTCUSDT",
                interval="5m",
                open_time=datetime.utcnow() + timedelta(minutes=i * 5),
                close_time=datetime.utcnow() + timedelta(minutes=(i + 1) * 5),
                open=100.0,
                high=105.0,
                low=95.0,
                close=100.0,
                volume=1000.0,
                quote_volume=100000.0,  # 평균 100,000
            )
            buffer.add(candle)

        # 마지막 캔들 거래량 300,000 (RVOL = 3.0)
        last_candle = Candle(
            symbol="BTCUSDT",
            interval="5m",
            open_time=datetime.utcnow() + timedelta(minutes=100),
            close_time=datetime.utcnow() + timedelta(minutes=105),
            open=100.0,
            high=105.0,
            low=95.0,
            close=100.0,
            volume=3000.0,
            quote_volume=300000.0,  # 3배
        )
        buffer.add(last_candle)

        rvol = manager.calc_rvol("BTCUSDT", "5m", 20)
        assert rvol is not None
        assert rvol == pytest.approx(3.0, rel=0.1)


# === Phase 2: Risk Engine Tests ===


class TestRiskEngine:
    """Risk Engine 테스트 (강화 버전)"""

    def test_order_failure_rate_tracking(self):
        """주문 실패율 추적 테스트"""
        from src.risk.risk_engine import RiskEngine

        engine = RiskEngine()

        # 10개 주문 중 3개 실패 (30%)
        for i in range(7):
            engine.on_order_success()
        for i in range(3):
            engine.on_order_failure()

        failure_rate = engine._get_recent_order_failure_rate()
        assert failure_rate == pytest.approx(0.30, rel=0.01)

    def test_exposure_limit_check(self):
        """노출 제한 체크 테스트"""
        from src.risk.risk_engine import RiskEngine

        engine = RiskEngine()

        # 노출 정보 설정 (equity: 100,000, gross: 50,000)
        engine.update_exposure(
            equity=100000,
            gross_exposure=50000,
            symbol_exposures={"BTCUSDT": 5000},  # 이미 5,000 노출
        )

        # Core 진입 가능 (현재 5k + 추가 3k = 8k, 10% = 10k 미만)
        can_enter, reason = engine.check_exposure_limit(
            symbol="BTCUSDT",
            additional_exposure_usd=3000,
            strategy="core",
        )
        assert can_enter is True

        # 심볼 노출 초과 (5k + 8k = 13k > 10k)
        can_enter, reason = engine.check_exposure_limit(
            symbol="BTCUSDT",
            additional_exposure_usd=8000,
            strategy="core",
        )
        assert can_enter is False
        assert "Symbol exposure" in reason

        # 총 노출 초과 (1.2x 초과)
        can_enter, reason = engine.check_exposure_limit(
            symbol="ETHUSDT",  # 새 심볼
            additional_exposure_usd=80000,  # 50k + 80k = 130k > 120k (1.2x)
            strategy="core",
        )
        assert can_enter is False
        assert "Gross exposure" in reason

    def test_satellite_disable_on_weekly_loss(self):
        """주간 손실 시 Satellite 비활성화 테스트"""
        from src.risk.risk_engine import RiskEngine

        engine = RiskEngine()

        # 초기 상태: Satellite 활성화
        assert engine.satellite_enabled is True

        # 주간 손실 플래그 설정
        engine._satellite_disabled_by_weekly_loss = True

        # Satellite 비활성화
        assert engine.satellite_enabled is False

        # 수동 재활성화
        engine.enable_satellite()
        assert engine.satellite_enabled is True


# === Phase 3: Core Strategy Tests ===


class TestCoreCarryStrategy:
    """Core 캐시앤캐리 전략 테스트"""

    def test_edge_calculation(self):
        """Edge 계산 테스트 (수수료/슬리피지 차감)"""
        from src.strategies.core_carry import CoreCarryStrategy

        strategy = CoreCarryStrategy()

        # spot: 100, perp: 99.5 → basis = 0.5%
        edge_calc = strategy.calculate_edge(spot_price=100.0, perp_price=99.5)

        assert edge_calc.basis_pct == pytest.approx(0.005, rel=0.01)  # 0.5%

        # net_edge = basis - fee - slippage - safety
        # = 0.005 - 0.0008 - 0.0006 - 0.0002 = 0.0034 (0.34%)
        expected_net = 0.005 - 0.0008 - 0.0006 - 0.0002
        assert edge_calc.net_edge_pct == pytest.approx(expected_net, rel=0.01)

        # 0.20% 기준에서 0.34%면 수익성 있음
        assert edge_calc.is_profitable is True

    def test_edge_calculation_unprofitable(self):
        """Edge 계산 - 수익성 없는 경우"""
        from src.strategies.core_carry import CoreCarryStrategy

        strategy = CoreCarryStrategy()

        # spot: 100, perp: 99.9 → basis = 0.1%
        edge_calc = strategy.calculate_edge(spot_price=100.0, perp_price=99.9)

        # basis 0.1% - 비용 ≈ 0.16% = 음수
        # net_edge = 0.001 - 0.0008 - 0.0006 - 0.0002 = -0.0006
        assert edge_calc.net_edge_pct < 0
        assert edge_calc.is_profitable is False

    def test_tranche_creation(self):
        """분할 진입 트랜치 생성 테스트"""
        from src.strategies.core_carry import CoreCarryStrategy, TrancheStatus

        strategy = CoreCarryStrategy()
        tranches = strategy._create_tranches()

        assert len(tranches) == 3

        # 비율 확인
        assert tranches[0].percentage == pytest.approx(0.40, rel=0.01)
        assert tranches[1].percentage == pytest.approx(0.30, rel=0.01)
        assert tranches[2].percentage == pytest.approx(0.30, rel=0.01)

        # 초기 상태
        for t in tranches:
            assert t.status == TrancheStatus.PENDING

    @pytest.mark.asyncio
    async def test_generate_signal_with_profitable_edge(self):
        """수익성 있는 Edge에서 시그널 생성"""
        from src.strategies.core_carry import CoreCarryStrategy

        strategy = CoreCarryStrategy()
        strategy._enabled = True

        market_data = {
            "symbol": "BTCUSDT",
            "spot_price": 100.0,
            "perp_price": 99.5,  # 0.5% basis
            "funding_rate": 0.0003,  # 양수 (숏에게 유리)
            "btc_regime": "BULLISH",
        }

        signal = await strategy.generate_signal(market_data)

        assert signal is not None
        assert signal.symbol == "BTCUSDT"
        assert "Carry T1" in signal.reason
        assert signal.metadata["tranche"] == 1

    @pytest.mark.asyncio
    async def test_generate_signal_rejected_in_volatile(self):
        """VOLATILE 레짐에서 시그널 거부"""
        from src.strategies.core_carry import CoreCarryStrategy

        strategy = CoreCarryStrategy()
        strategy._enabled = True

        market_data = {
            "symbol": "BTCUSDT",
            "spot_price": 100.0,
            "perp_price": 99.5,
            "funding_rate": 0.0003,
            "btc_regime": "VOLATILE",  # 급변장
        }

        signal = await strategy.generate_signal(market_data)

        # VOLATILE에서는 진입하지 않음
        assert signal is None

    @pytest.mark.asyncio
    async def test_should_exit_on_edge_convergence(self):
        """Edge 수렴 시 청산"""
        from src.strategies.core_carry import CoreCarryStrategy

        strategy = CoreCarryStrategy()

        position = {
            "symbol": "BTCUSDT",
            "entry_edge": 0.005,  # 0.5%
            "quantity": 1.0,
            "spot_quantity": 1.0,
            "perp_quantity": 1.0,
        }

        market_data = {
            "spot_price": 100.0,
            "perp_price": 99.95,  # 0.05% (수렴)
            "funding_rate": 0.0001,
            "margin_ratio": 0.5,
        }

        signal = await strategy.should_exit(position, market_data)

        assert signal is not None
        assert "converged" in signal.reason.lower()

    @pytest.mark.asyncio
    async def test_should_exit_on_hedge_error(self):
        """Hedge error 발생 시 리밸런싱"""
        from src.strategies.core_carry import CoreCarryStrategy
        from src.models.schemas import OrderSide

        strategy = CoreCarryStrategy()

        position = {
            "symbol": "BTCUSDT",
            "entry_edge": 0.005,
            "quantity": 1.0,
            "spot_quantity": 1.0,
            "perp_quantity": 0.9,  # 10% 차이
        }

        market_data = {
            "spot_price": 100.0,
            "perp_price": 99.5,
            "funding_rate": 0.0001,
            "margin_ratio": 0.5,
        }

        signal = await strategy.should_exit(position, market_data)

        assert signal is not None
        assert "rebalance" in signal.reason.lower()
        assert signal.side == OrderSide.SELL  # spot이 더 많으니 매도


# === Liquidity Filter Tests ===


class TestLiquidityFilter:
    """유동성 필터 테스트"""

    @pytest.mark.asyncio
    async def test_check_volume_pass(self):
        """거래량 조건 통과"""
        from src.risk.liquidity_filter import LiquidityFilter

        filter = LiquidityFilter()

        # Mock exchange
        mock_exchange = AsyncMock()
        mock_exchange.get_24h_volume = AsyncMock(return_value=100_000_000)  # 100M
        mock_exchange.get_ticker = AsyncMock(
            return_value=MagicMock(bid=99.9, ask=100.1)
        )  # 20bps spread
        mock_exchange.get_orderbook_depth = AsyncMock(return_value=None)

        # 스프레드가 20bps로 8bps 초과
        result = await filter.check(mock_exchange, "BTCUSDT")

        # 스프레드 때문에 실패해야 함
        assert result.passed is False
        assert "spread" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_check_spread_pass(self):
        """스프레드 조건 통과"""
        from src.risk.liquidity_filter import LiquidityFilter

        filter = LiquidityFilter()

        # Mock exchange (좁은 스프레드)
        mock_exchange = AsyncMock()
        mock_exchange.get_24h_volume = AsyncMock(return_value=100_000_000)
        mock_exchange.get_ticker = AsyncMock(
            return_value=MagicMock(bid=99.99, ask=100.01)
        )  # 2bps spread

        result = await filter.check(mock_exchange, "BTCUSDT")

        assert result.passed is True
        assert result.spread_bps < 8


# === Integration Test ===


class TestIntegration:
    """통합 테스트"""

    @pytest.mark.asyncio
    async def test_full_entry_flow(self):
        """전체 진입 플로우 테스트"""
        from src.risk.risk_engine import RiskEngine
        from src.strategies.core_carry import CoreCarryStrategy

        # 1. Risk Engine 준비
        risk_engine = RiskEngine()
        risk_engine.update_exposure(
            equity=100000,
            gross_exposure=20000,
            symbol_exposures={},
        )

        # 2. 노출 제한 체크
        can_enter, _ = risk_engine.check_exposure_limit(
            symbol="BTCUSDT",
            additional_exposure_usd=5000,
            strategy="core",
        )
        assert can_enter is True

        # 3. Core 전략 시그널
        strategy = CoreCarryStrategy()
        strategy._enabled = True

        market_data = {
            "symbol": "BTCUSDT",
            "spot_price": 100.0,
            "perp_price": 99.5,
            "funding_rate": 0.0003,
            "btc_regime": "BULLISH",
        }

        signal = await strategy.generate_signal(market_data)
        assert signal is not None

        # 4. 포지션 사이즈 계산
        size = strategy.get_position_size(signal, available_capital=50000)
        assert size > 0

        # 5. 트랜치 확인
        pending = strategy.get_pending_tranches("BTCUSDT")
        assert len(pending) == 2  # 2, 3차 트랜치


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
