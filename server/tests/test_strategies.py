"""전략 테스트"""

import pytest
from src.strategies.core_carry import CoreCarryStrategy
from src.strategies.satellite import SatelliteStrategy, Regime
from src.models.schemas import OrderSide, StrategyType


class TestCoreCarryStrategy:
    """Core 캐시앤캐리 전략 테스트"""

    @pytest.fixture
    def strategy(self):
        return CoreCarryStrategy()

    @pytest.mark.asyncio
    async def test_signal_generation_with_edge(self, strategy):
        """Edge가 있을 때 시그널 생성"""
        market_data = {
            "symbol": "BTCUSDT",
            "spot_price": 50100.0,
            "perp_price": 50000.0,
            "funding_rate": 0.0005,  # 양수 펀딩 = 숏에게 유리
        }

        signal = await strategy.generate_signal(market_data)

        assert signal is not None
        assert signal.strategy == StrategyType.CORE
        assert signal.side == OrderSide.BUY
        assert "Carry edge" in signal.reason

    @pytest.mark.asyncio
    async def test_no_signal_without_edge(self, strategy):
        """Edge 없으면 시그널 없음"""
        market_data = {
            "symbol": "BTCUSDT",
            "spot_price": 50000.0,
            "perp_price": 50000.0,
            "funding_rate": 0.0005,
        }

        signal = await strategy.generate_signal(market_data)

        assert signal is None

    @pytest.mark.asyncio
    async def test_no_signal_negative_funding(self, strategy):
        """펀딩 음수면 시그널 없음 (롱에게 유리 = 숏에게 불리)"""
        market_data = {
            "symbol": "BTCUSDT",
            "spot_price": 50100.0,
            "perp_price": 50000.0,
            "funding_rate": -0.0005,
        }

        signal = await strategy.generate_signal(market_data)

        assert signal is None

    @pytest.mark.asyncio
    async def test_exit_on_edge_reversal(self, strategy):
        """Edge 역전 시 청산"""
        position = {
            "symbol": "BTCUSDT",
            "entry_edge": 0.002,
            "quantity": 0.1,
        }

        market_data = {
            "spot_price": 49900.0,  # spot < perp = edge 역전
            "perp_price": 50000.0,
            "funding_rate": 0.0005,
        }

        signal = await strategy.should_exit(position, market_data)

        assert signal is not None
        assert signal.side == OrderSide.SELL
        assert "reversed" in signal.reason.lower()

    def test_position_size_calculation(self, strategy):
        """포지션 사이즈 계산"""
        from src.strategies.base import Signal, SignalType

        signal = Signal(
            strategy=StrategyType.CORE,
            signal_type=SignalType.ENTRY,
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0,
        )

        # 캐리 기회 등록
        strategy._active_carries["BTCUSDT"] = type(
            "Opportunity",
            (),
            {"spot_price": 50000.0, "edge_pct": 0.002},
        )()

        size = strategy.get_position_size(signal, available_capital=20000.0)

        assert size > 0
        assert size < 0.5  # 최대 포지션 제한 확인


class TestSatelliteStrategy:
    """Satellite 전략 테스트"""

    @pytest.fixture
    def strategy(self):
        s = SatelliteStrategy()
        s._enabled = True
        return s

    @pytest.mark.asyncio
    async def test_no_signal_in_neutral_regime(self, strategy):
        """NEUTRAL 레짐에서는 시그널 없음"""
        strategy.update_btc_regime(Regime.NEUTRAL)

        market_data = {
            "symbol": "ETHUSDT",
            "price": 3100.0,
            "high_20": 3000.0,
            "low_20": 2900.0,
            "rvol": 2.0,
            "vwap": 3050.0,
        }

        signal = await strategy.generate_signal(market_data)

        assert signal is None

    @pytest.mark.asyncio
    async def test_long_signal_in_bullish_regime(self, strategy):
        """BULLISH 레짐 + 고점 돌파 = 롱 시그널"""
        strategy.update_btc_regime(Regime.BULLISH)

        market_data = {
            "symbol": "ETHUSDT",
            "price": 3100.0,  # > high_20
            "high_20": 3000.0,
            "low_20": 2900.0,
            "rvol": 2.0,  # > 1.5 threshold
            "vwap": 3050.0,  # price > vwap
        }

        signal = await strategy.generate_signal(market_data)

        assert signal is not None
        assert signal.side == OrderSide.BUY
        assert "Breakout HIGH" in signal.reason

    @pytest.mark.asyncio
    async def test_short_signal_in_bearish_regime(self, strategy):
        """BEARISH 레짐 + 저점 돌파 = 숏 시그널"""
        strategy.update_btc_regime(Regime.BEARISH)

        market_data = {
            "symbol": "ETHUSDT",
            "price": 2850.0,  # < low_20
            "high_20": 3000.0,
            "low_20": 2900.0,
            "rvol": 2.0,
            "vwap": 2950.0,  # price < vwap
        }

        signal = await strategy.generate_signal(market_data)

        assert signal is not None
        assert signal.side == OrderSide.SELL
        assert "Breakout LOW" in signal.reason

    @pytest.mark.asyncio
    async def test_no_signal_low_rvol(self, strategy):
        """RVOL 낮으면 시그널 없음"""
        strategy.update_btc_regime(Regime.BULLISH)

        market_data = {
            "symbol": "ETHUSDT",
            "price": 3100.0,
            "high_20": 3000.0,
            "low_20": 2900.0,
            "rvol": 1.0,  # < 1.5 threshold
            "vwap": 3050.0,
        }

        signal = await strategy.generate_signal(market_data)

        assert signal is None

    @pytest.mark.asyncio
    async def test_hard_stop_exit(self, strategy):
        """하드 손절 테스트"""
        position = {
            "symbol": "ETHUSDT",
            "avg_price": 3000.0,
            "side": "LONG",
            "quantity": 1.0,
            "opened_at": "2024-01-01T00:00:00",
        }

        market_data = {
            "price": 2900.0,  # -3.3% < -2% hard stop
        }

        # 포지션 추적 시작
        strategy.track_position(
            "ETHUSDT", OrderSide.BUY, 3000.0, 1.0
        )

        signal = await strategy.should_exit(position, market_data)

        assert signal is not None
        assert "Hard stop" in signal.reason

    @pytest.mark.asyncio
    async def test_regime_change_exit(self, strategy):
        """레짐 변화 시 청산"""
        from datetime import datetime, timedelta

        strategy.update_btc_regime(Regime.BEARISH)  # 베어리시로 변경

        # 최근 시간으로 설정 (타임스톱 방지)
        recent_time = (datetime.utcnow() - timedelta(minutes=5)).isoformat()

        position = {
            "symbol": "ETHUSDT",
            "avg_price": 3000.0,
            "side": "LONG",  # 롱인데 베어리시 = 청산
            "quantity": 1.0,
            "opened_at": recent_time,
        }

        market_data = {"price": 3050.0}

        strategy.track_position(
            "ETHUSDT", OrderSide.BUY, 3000.0, 1.0
        )

        signal = await strategy.should_exit(position, market_data)

        assert signal is not None
        assert "Regime" in signal.reason
