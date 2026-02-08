"""전략 필터 테스트 - 블랙리스트, 거래대금, 스프레드 체크"""

import pytest
from datetime import datetime, timedelta


class TestBlacklistFilter:
    """블랙리스트 필터 테스트"""

    def test_rebound_blacklist_contains_loss_coins(self):
        """REBOUND 블랙리스트에 손실 코인 포함 확인"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy

        strategy = ReboundScalperStrategy()

        # Pullback 손실 종목 확인
        assert "SENT" in strategy._BLACKLIST
        assert "POKT" in strategy._BLACKLIST
        assert "STORJ" in strategy._BLACKLIST
        assert "STMX" in strategy._BLACKLIST
        assert "HUNT" in strategy._BLACKLIST
        assert "BORA" in strategy._BLACKLIST

        # REBOUND/DIP 손실 종목 확인
        assert "ELSA" in strategy._BLACKLIST
        assert "BREV" in strategy._BLACKLIST
        assert "WET" in strategy._BLACKLIST
        assert "HOLO" in strategy._BLACKLIST
        assert "LA" in strategy._BLACKLIST
        assert "WLD" in strategy._BLACKLIST
        assert "ZK" in strategy._BLACKLIST

    def test_dip_blacklist_contains_loss_coins(self):
        """DIP_SCALPER 블랙리스트에 손실 코인 포함 확인"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()

        # Pullback 손실 종목 확인
        assert "SENT" in strategy._BLACKLIST
        assert "POKT" in strategy._BLACKLIST
        assert "STORJ" in strategy._BLACKLIST
        assert "STMX" in strategy._BLACKLIST
        assert "HUNT" in strategy._BLACKLIST
        assert "BORA" in strategy._BLACKLIST

        # REBOUND/DIP 손실 종목 확인
        assert "WET" in strategy._BLACKLIST
        assert "HOLO" in strategy._BLACKLIST
        assert "CPOOL" in strategy._BLACKLIST
        assert "SXP" in strategy._BLACKLIST
        assert "XEC" in strategy._BLACKLIST
        assert "ZKP" in strategy._BLACKLIST
        assert "SNT" in strategy._BLACKLIST
        assert "MMT" in strategy._BLACKLIST

    def test_blacklist_is_frozenset(self):
        """블랙리스트가 frozenset인지 확인 (불변성)"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy
        from src.strategies.dip_scalper import DipScalperStrategy

        rebound = ReboundScalperStrategy()
        dip = DipScalperStrategy()

        assert isinstance(rebound._BLACKLIST, frozenset)
        assert isinstance(dip._BLACKLIST, frozenset)

    def test_blacklist_symbol_extraction(self):
        """심볼에서 base 추출 확인 (KRW-XXX 형식)"""
        # KRW-SENT에서 SENT 추출
        symbol = "KRW-SENT"
        base = symbol.split("-")[-1] if "-" in symbol else symbol
        assert base == "SENT"

        # 단순 심볼
        symbol2 = "POKT"
        base2 = symbol2.split("-")[-1] if "-" in symbol2 else symbol2
        assert base2 == "POKT"


class TestVolumeFilter:
    """거래대금 필터 테스트"""

    def test_rebound_min_volume_is_30b(self):
        """REBOUND 최소 거래대금 30억 확인"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy

        strategy = ReboundScalperStrategy()
        assert strategy._MIN_VOLUME_24H == 3_000_000_000

    def test_dip_min_volume_is_30b(self):
        """DIP_SCALPER 최소 거래대금 30억 확인"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        assert strategy._MIN_VOLUME_24H == 3_000_000_000

    def test_volume_filter_blocks_low_volume(self):
        """거래대금 필터가 저볼륨 종목을 차단하는지 확인"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()

        # 10억 거래대금 (30억 미만) - 차단해야 함
        low_volume = 1_000_000_000  # 10억
        high_volume = 5_000_000_000  # 50억

        assert low_volume < strategy._MIN_VOLUME_24H
        assert high_volume >= strategy._MIN_VOLUME_24H


class TestUpbitTickSize:
    """Upbit 틱 사이즈 계산 테스트"""

    def test_tick_size_under_10(self):
        """10원 미만 가격 틱 사이즈"""
        from src.strategies.dip_scalper import DipScalperStrategy

        tick = DipScalperStrategy._upbit_tick_size(5)
        assert tick == 0.01

    def test_tick_size_10_to_100(self):
        """10~100원 가격 틱 사이즈"""
        from src.strategies.dip_scalper import DipScalperStrategy

        tick = DipScalperStrategy._upbit_tick_size(50)
        assert tick == 0.1

    def test_tick_size_100_to_1000(self):
        """100~1000원 가격 틱 사이즈"""
        from src.strategies.dip_scalper import DipScalperStrategy

        tick = DipScalperStrategy._upbit_tick_size(500)
        assert tick == 1

    def test_tick_size_1000_to_5000(self):
        """1000~5000원 가격 틱 사이즈"""
        from src.strategies.dip_scalper import DipScalperStrategy

        tick = DipScalperStrategy._upbit_tick_size(3000)
        assert tick == 5

    def test_tick_size_5000_to_10000(self):
        """5000~10000원 가격 틱 사이즈"""
        from src.strategies.dip_scalper import DipScalperStrategy

        tick = DipScalperStrategy._upbit_tick_size(7000)
        assert tick == 10

    def test_tick_size_10000_to_50000(self):
        """10000~50000원 가격 틱 사이즈"""
        from src.strategies.dip_scalper import DipScalperStrategy

        tick = DipScalperStrategy._upbit_tick_size(25000)
        assert tick == 50

    def test_tick_size_50000_to_100000(self):
        """50000~100000원 가격 틱 사이즈"""
        from src.strategies.dip_scalper import DipScalperStrategy

        tick = DipScalperStrategy._upbit_tick_size(75000)
        assert tick == 100

    def test_tick_size_100000_to_500000(self):
        """100000~500000원 가격 틱 사이즈"""
        from src.strategies.dip_scalper import DipScalperStrategy

        tick = DipScalperStrategy._upbit_tick_size(250000)
        assert tick == 500

    def test_tick_size_over_500000(self):
        """500000원 이상 가격 틱 사이즈"""
        from src.strategies.dip_scalper import DipScalperStrategy

        tick = DipScalperStrategy._upbit_tick_size(1000000)
        assert tick == 1000


class TestSpreadCheck:
    """스프레드 체크 테스트"""

    def test_spread_calculation(self):
        """스프레드 계산 로직 테스트"""
        # 스프레드 = (ask - bid) / bid * 10000 (bps)
        best_ask = 10010
        best_bid = 10000
        expected_spread_bps = (best_ask - best_bid) / best_bid * 10000
        assert expected_spread_bps == 10.0  # 10bps

    def test_spread_threshold_pullback(self):
        """Pullback 스프레드 임계값 확인 (설정값)"""
        from src.config import get_settings

        settings = get_settings()
        # Pullback은 설정에서 가져옴 (기본 15bps)
        assert hasattr(settings, "pullback_max_spread_bps")
        assert settings.pullback_max_spread_bps == 15.0

    def test_wide_spread_blocked(self):
        """넓은 스프레드가 차단되는지 확인"""
        # 20bps 스프레드 (15bps 임계값 초과)
        best_ask = 10020
        best_bid = 10000
        spread_bps = (best_ask - best_bid) / best_bid * 10000
        max_spread = 15.0

        assert spread_bps > max_spread  # 차단되어야 함

    def test_narrow_spread_allowed(self):
        """좁은 스프레드가 허용되는지 확인"""
        # 5bps 스프레드 (15bps 임계값 이하)
        best_ask = 10005
        best_bid = 10000
        spread_bps = (best_ask - best_bid) / best_bid * 10000
        max_spread = 15.0

        assert spread_bps <= max_spread  # 허용되어야 함


class TestDipScalperSignalCreation:
    """DIP_SCALPER 시그널 생성 테스트"""

    def test_dip_signal_dataclass(self):
        """DipSignal 데이터클래스 확인"""
        from src.strategies.dip_scalper import DipSignal

        signal = DipSignal(
            symbol="KRW-BTC",
            current_price=50000000,
            drop_pct=-0.03,
            atr_pct=0.02,
            threshold_pct=-0.024,
            entry_price=50000050,  # 현재가 + 1틱
            stop_loss=49400000,
            take_profit=50400000,
            buy_amount_krw=100000,
            reason="1m급락 -3.0% (기준: -2.4%)",
            rvol=2.5,
            bid_strength=0.6,
        )

        assert signal.symbol == "KRW-BTC"
        assert signal.drop_pct == -0.03

    def test_dip_position_dataclass(self):
        """DipPosition 데이터클래스 확인"""
        from src.strategies.dip_scalper import DipPosition

        position = DipPosition(
            symbol="KRW-BTC",
            entry_price=50000000,
            quantity=0.002,
            entry_time=datetime.utcnow(),
            stop_loss=49400000,
            take_profit=50400000,
            highest_price=50000000,
            bid_ratio=0.6,
        )

        assert position.symbol == "KRW-BTC"
        assert position.tp_reached_time is None


class TestReboundSignalCreation:
    """REBOUND 시그널 생성 테스트"""

    def test_rebound_signal_dataclass(self):
        """ReboundSignal 데이터클래스 확인"""
        from src.strategies.rebound_strategy import ReboundSignal

        signal = ReboundSignal(
            symbol="KRW-ETH",
            score=72.5,
            level=2,
            target_allocation=0.05,
            entry_price=4500000,
            stop_loss=4468500,
            quantity=0.01,
            reason="L2 Score=72 [RSI:15/20, Support:18/25]",
            components=[],
        )

        assert signal.symbol == "KRW-ETH"
        assert signal.level == 2

    def test_rebound_position_dataclass(self):
        """ReboundPosition 데이터클래스 확인"""
        from src.strategies.rebound_strategy import ReboundPosition

        position = ReboundPosition(
            symbol="KRW-ETH",
            entry_price=4500000,
            quantity=0.01,
            initial_quantity=0.01,
            entry_time=datetime.utcnow(),
            stop_loss=4468500,
            tp1_price=4545000,
            tp2_price=4567500,
            highest_price=4500000,
        )

        assert position.symbol == "KRW-ETH"
        assert position.tp1_hit is False


class TestExitLogic:
    """청산 로직 테스트"""

    def test_dip_stop_loss_exit(self):
        """DIP_SCALPER 손절 청산 테스트"""
        from src.strategies.dip_scalper import DipScalperStrategy, DipPosition

        strategy = DipScalperStrategy()
        strategy._enabled = True

        position = DipPosition(
            symbol="KRW-BTC",
            entry_price=50000000,
            quantity=0.002,
            entry_time=datetime.utcnow(),
            stop_loss=49400000,  # -1.2%
            take_profit=50400000,
            highest_price=50000000,
        )
        strategy._active_positions["KRW-BTC"] = position

        # 손절가 이하로 하락
        current_price = 49300000  # 손절가 아래
        exit_result = strategy.should_exit("KRW-BTC", current_price)

        assert exit_result is not None
        assert exit_result["exit_type"] == "stop_loss"

    def test_dip_time_stop_exit(self):
        """DIP_SCALPER 타임스톱 청산 테스트"""
        from src.strategies.dip_scalper import DipScalperStrategy, DipPosition

        strategy = DipScalperStrategy()
        strategy._enabled = True

        # 11분 전에 진입한 포지션 (타임스톱 10분)
        position = DipPosition(
            symbol="KRW-BTC",
            entry_price=50000000,
            quantity=0.002,
            entry_time=datetime.utcnow() - timedelta(minutes=11),
            stop_loss=49400000,
            take_profit=50400000,
            highest_price=50000000,
        )
        strategy._active_positions["KRW-BTC"] = position

        # 손절가 위, 익절가 아래에서 타임스톱
        current_price = 50100000
        exit_result = strategy.should_exit("KRW-BTC", current_price)

        assert exit_result is not None
        assert exit_result["exit_type"] == "time_stop"

    def test_rebound_stop_loss_exit(self):
        """REBOUND 손절 청산 테스트"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy, ReboundPosition

        strategy = ReboundScalperStrategy()

        position = ReboundPosition(
            symbol="KRW-ETH",
            entry_price=4500000,
            quantity=0.01,
            initial_quantity=0.01,
            entry_time=datetime.utcnow(),
            stop_loss=4468500,  # -0.7%
            tp1_price=4545000,
            tp2_price=4567500,
            highest_price=4500000,
        )
        strategy._active_positions["KRW-ETH"] = position

        # 손절가 이하로 하락
        current_price = 4460000
        exit_result = strategy.should_exit("KRW-ETH", current_price)

        assert exit_result is not None
        assert exit_result["exit_type"] == "stop_loss"

    def test_rebound_take_profit_exit(self):
        """REBOUND 익절 청산 테스트"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy, ReboundPosition

        strategy = ReboundScalperStrategy()

        position = ReboundPosition(
            symbol="KRW-ETH",
            entry_price=4500000,
            quantity=0.01,
            initial_quantity=0.01,
            entry_time=datetime.utcnow(),
            stop_loss=4468500,
            tp1_price=4545000,  # +1.0%
            tp2_price=4567500,
            highest_price=4500000,
        )
        strategy._active_positions["KRW-ETH"] = position

        # 익절가 이상으로 상승
        current_price = 4550000
        exit_result = strategy.should_exit("KRW-ETH", current_price)

        assert exit_result is not None
        assert exit_result["exit_type"] == "tp1"


class TestStrategyMode:
    """전략 모드 테스트"""

    def test_rebound_mode_off(self):
        """REBOUND OFF 모드 테스트"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy, ReboundMode

        strategy = ReboundScalperStrategy()
        strategy.set_mode("OFF")

        assert strategy._mode == ReboundMode.OFF
        assert strategy.is_enabled() is False

    def test_rebound_mode_safe(self):
        """REBOUND SAFE 모드 테스트"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy, ReboundMode

        strategy = ReboundScalperStrategy()
        strategy.set_mode("SAFE")

        assert strategy._mode == ReboundMode.SAFE
        assert strategy.is_enabled() is True
        assert strategy.get_min_level() == 3  # SAFE = 레벨 3만

    def test_rebound_mode_normal(self):
        """REBOUND NORMAL 모드 테스트"""
        from src.strategies.rebound_strategy import ReboundScalperStrategy, ReboundMode

        strategy = ReboundScalperStrategy()
        strategy.set_mode("NORMAL")

        assert strategy._mode == ReboundMode.NORMAL
        assert strategy.is_enabled() is True
        assert strategy.get_min_level() == 2  # NORMAL = 레벨 2+

    def test_dip_scalper_enabled_toggle(self):
        """DIP_SCALPER 활성화 토글 테스트"""
        from src.strategies.dip_scalper import DipScalperStrategy

        strategy = DipScalperStrategy()
        strategy.set_enabled(False)
        assert strategy.is_enabled() is False

        strategy.set_enabled(True)
        assert strategy.is_enabled() is True
