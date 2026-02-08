"""전략 통합 테스트 - 시그널 필터링 및 실행 시뮬레이션"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock


class TestPullbackIntegration:
    """Pullback 전략 통합 테스트"""

    @pytest.mark.asyncio
    async def test_pullback_signal_blocked_by_blacklist(self):
        """블랙리스트 코인 시그널이 차단되는지 확인"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        strategy._enabled = True

        # SENT는 블랙리스트에 포함
        market_data_list = [
            {
                "symbol": "KRW-SENT",
                "price": 100,
                "change_1m": -0.05,  # -5% 급락 (임계값 충족)
                "atr_pct": 0.02,
                "volume_24h_krw": 5_000_000_000,  # 50억 (필터 통과)
                "rvol": 3.0,
                "bid_volume": 1000,
                "ask_volume": 800,
            }
        ]

        signals = strategy.scan_for_dips(market_data_list)

        # 블랙리스트이므로 시그널 없어야 함
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_dip_signal_blocked_by_low_volume(self):
        """거래대금 부족 시 시그널 차단 확인"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        strategy._enabled = True

        # 거래대금 10억 (30억 미만)
        market_data_list = [
            {
                "symbol": "KRW-XRP",
                "price": 1000,
                "change_1m": -0.05,  # -5% 급락
                "atr_pct": 0.02,
                "volume_24h_krw": 1_000_000_000,  # 10억 (필터 차단)
                "rvol": 3.0,
                "bid_volume": 1000,
                "ask_volume": 800,
            }
        ]

        signals = strategy.scan_for_dips(market_data_list)

        # 거래대금 부족으로 시그널 없어야 함
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_dip_signal_generated_with_valid_data(self):
        """유효한 데이터로 시그널 생성 확인"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        strategy._enabled = True

        # 모든 조건 충족
        market_data_list = [
            {
                "symbol": "KRW-XRP",
                "price": 1000,
                "change_1m": -0.05,  # -5% 급락 (임계값 충족)
                "change_5m": 0.01,  # 급등주 아님
                "atr_pct": 0.02,
                "volume_24h_krw": 5_000_000_000,  # 50억 (필터 통과)
                "rvol": 3.0,  # 거래량 급증
                "bid_volume": 1000,
                "ask_volume": 800,
            }
        ]

        signals = strategy.scan_for_dips(market_data_list)

        # 시그널 생성되어야 함
        assert len(signals) == 1
        assert signals[0].symbol == "KRW-XRP"


class TestReboundIntegration:
    """Rebound 전략 통합 테스트"""

    @pytest.mark.asyncio
    async def test_rebound_signal_blocked_by_blacklist(self):
        """REBOUND 블랙리스트 차단 확인"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy

        strategy = ReboundScalperStrategy()
        strategy.set_mode("NORMAL")

        # ELSA는 블랙리스트에 포함
        base = "ELSA"
        assert base in strategy._BLACKLIST

    @pytest.mark.asyncio
    async def test_rebound_volume_filter(self):
        """REBOUND 거래대금 필터 확인"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy

        strategy = ReboundScalperStrategy()

        # 20억 거래대금 (30억 미만)
        low_volume = 2_000_000_000
        assert low_volume < strategy._MIN_VOLUME_24H

        # 50억 거래대금 (30억 이상)
        high_volume = 5_000_000_000
        assert high_volume >= strategy._MIN_VOLUME_24H


class TestDipSmartExit:
    """DIP_SCALPER 스마트 청산 테스트 (v2.4)"""

    def test_tp_reached_records_time(self):
        """TP 도달 시 시간 기록 확인"""
        from src.strategies.dip_scalper import DipScalperStrategy, DipPosition

        strategy = DipScalperStrategy()
        strategy._enabled = True

        position = DipPosition(
            symbol="KRW-BTC",
            entry_price=50000000,
            quantity=0.002,
            entry_time=datetime.utcnow(),
            stop_loss=49400000,  # -1.2%
            take_profit=50400000,  # +0.8%
            highest_price=50000000,
        )
        strategy._active_positions["KRW-BTC"] = position

        # TP 도달
        current_price = 50500000  # TP 초과
        exit_result = strategy.should_exit("KRW-BTC", current_price)

        # 첫 TP 도달 시에는 청산하지 않고 기록만 함
        assert exit_result is None
        assert position.tp_reached_time is not None
        assert position.tp_reached_price > 0

    def test_sideways_after_tp_triggers_exit(self):
        """TP 도달 후 횡보 시 청산"""
        from src.strategies.dip_scalper import DipScalperStrategy, DipPosition

        strategy = DipScalperStrategy()
        strategy._enabled = True

        # TP 도달 후 1.5분 경과한 포지션
        tp_reached_time = datetime.utcnow() - timedelta(minutes=1.5)
        position = DipPosition(
            symbol="KRW-BTC",
            entry_price=50000000,
            quantity=0.002,
            entry_time=datetime.utcnow() - timedelta(minutes=5),
            stop_loss=49400000,
            take_profit=50400000,
            highest_price=50500000,
            tp_reached_time=tp_reached_time,
            tp_reached_price=50500000,
        )
        strategy._active_positions["KRW-BTC"] = position

        # 횡보 중 (TP 도달 가격 ± 0.2%)
        current_price = 50500000  # 동일 가격
        exit_result = strategy.should_exit("KRW-BTC", current_price)

        # 횡보로 인한 청산
        assert exit_result is not None
        assert exit_result["exit_type"] == "take_profit_sideways"


class TestDropThresholdCalculation:
    """급락 임계값 계산 테스트"""

    def test_high_volatility_coin_threshold(self):
        """고변동성 코인 급락 임계값"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()

        # ATR 5% 코인 → 임계값 = max(-8%, min(-2%, -5% * 1.2))
        threshold = strategy.calc_drop_threshold(0.05)
        # -5% * 1.2 = -6%, min(-2%, -6%) = -6%, max(-8%, -6%) = -6%
        assert threshold == -0.06

    def test_low_volatility_coin_threshold(self):
        """저변동성 코인 급락 임계값"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()

        # ATR 1% 코인 → 임계값 = max(-8%, min(-2%, -1% * 1.2))
        threshold = strategy.calc_drop_threshold(0.01)
        # -1% * 1.2 = -1.2%, min(-2%, -1.2%) = -2%, max(-8%, -2%) = -2%
        assert threshold == -0.02


class TestBTCStabilityCheck:
    """BTC 안정성 체크 테스트"""

    def test_btc_stable_allows_trading(self):
        """BTC 안정 시 거래 허용"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        strategy.set_btc_status(-0.01)  # -1% (안정)

        ok, reason = strategy._check_btc_stability()
        assert ok is True

    def test_btc_crash_blocks_trading(self):
        """BTC 급락 시 거래 차단"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        strategy.set_btc_status(-0.04)  # -4% (급락)

        ok, reason = strategy._check_btc_stability()
        assert ok is False
        assert "BTC 급락" in reason


class TestOrderbookSupport:
    """호가창 매수벽 체크 테스트"""

    def test_strong_bid_wall_allowed(self):
        """강한 매수벽 시 허용"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()

        bid_volume = 6000
        ask_volume = 4000

        ok, ratio, reason = strategy._check_orderbook_support(bid_volume, ask_volume)
        assert ok is True
        assert ratio == 0.6  # 60%

    def test_weak_bid_wall_blocks(self):
        """약한 매수벽 시 차단 (기록만, 실제 차단 안함)"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()

        bid_volume = 3000
        ask_volume = 7000

        ok, ratio, reason = strategy._check_orderbook_support(bid_volume, ask_volume)
        # v2.1: 호가창 필터는 차단 안함, 동적 익절에만 사용
        assert ok is False  # 통계는 기록
        assert ratio == 0.3  # 30%


class TestCooldown:
    """쿨다운 테스트"""

    def test_cooldown_blocks_reentry(self):
        """쿨다운 중 재진입 차단"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        strategy._enabled = True

        # 최근 청산 기록
        strategy._last_exit["KRW-BTC"] = datetime.utcnow()

        # 쿨다운 체크
        can_enter = strategy._check_cooldown("KRW-BTC")
        assert can_enter is False

    def test_cooldown_allows_after_period(self):
        """쿨다운 경과 후 진입 허용"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        strategy._enabled = True

        # 10분 전 청산 (쿨다운 5분)
        strategy._last_exit["KRW-BTC"] = datetime.utcnow() - timedelta(minutes=10)

        can_enter = strategy._check_cooldown("KRW-BTC")
        assert can_enter is True


class TestPositionTracking:
    """포지션 추적 테스트"""

    def test_dip_position_tracking(self):
        """DIP_SCALPER 포지션 추적"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()

        strategy.track_position(
            symbol="KRW-BTC",
            entry_price=50000000,
            quantity=0.002,
            bid_ratio=0.6,
        )

        pos = strategy.get_position("KRW-BTC")
        assert pos is not None
        assert pos.entry_price == 50000000
        assert pos.quantity == 0.002

    def test_rebound_position_tracking(self):
        """REBOUND 포지션 추적"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy

        strategy = ReboundScalperStrategy()

        strategy.track_position(
            symbol="KRW-ETH",
            entry_price=4500000,
            quantity=0.01,
        )

        pos = strategy.get_position("KRW-ETH")
        assert pos is not None
        assert pos.entry_price == 4500000


class TestStatistics:
    """통계 테스트"""

    def test_dip_stats_update_on_close(self):
        """DIP_SCALPER 청산 시 통계 업데이트"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        strategy.track_position("KRW-BTC", 50000000, 0.002)

        # 수익 청산
        strategy.close_position("KRW-BTC", exit_type="tp1", pnl_pct=0.008)

        assert strategy._stats["total_trades"] == 1  # track에서 1 증가
        assert strategy._stats["winning_trades"] == 1
        assert strategy._stats["total_pnl_pct"] == 0.008

    def test_rebound_stats_update_on_close(self):
        """REBOUND 청산 시 통계 업데이트"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy

        strategy = ReboundScalperStrategy()
        strategy.track_position("KRW-ETH", 4500000, 0.01)

        initial_total = strategy._stats["total_trades"]

        # 손실 청산
        strategy.close_position("KRW-ETH", exit_type="stop_loss", pnl_pct=-0.007)

        assert strategy._stats["total_trades"] == initial_total + 1
        assert strategy._stats["losing_trades"] >= 1
        assert strategy._stats["stop_loss_hits"] >= 1


class TestHotStockFilter:
    """급등주 필터 테스트 (v2.3)"""

    def test_hot_stock_blocked(self):
        """최근 급등한 종목 차단"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        strategy._enabled = True

        # 5분봉 +10% 급등한 종목
        market_data_list = [
            {
                "symbol": "KRW-HOT",
                "price": 1000,
                "change_1m": -0.05,  # 급락
                "change_5m": 0.10,  # +10% 급등 (펌프&덤프 의심)
                "atr_pct": 0.02,
                "volume_24h_krw": 5_000_000_000,
                "rvol": 3.0,
                "bid_volume": 1000,
                "ask_volume": 800,
            }
        ]

        signals = strategy.scan_for_dips(market_data_list)

        # 급등주이므로 차단
        assert len(signals) == 0


class TestMarketWarmup:
    """장 초반 워밍업 테스트 (v2.1)"""

    def test_warmup_detection(self):
        """장 초반 워밍업 감지"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()

        # 현재 시간에 따라 결과가 달라지므로
        # 메서드 존재 여부만 확인
        warmup, reason = strategy._is_market_warmup()
        assert isinstance(warmup, bool)
        assert isinstance(reason, str)
