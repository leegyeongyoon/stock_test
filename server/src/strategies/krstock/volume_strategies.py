"""
거래량 기반 전략 (5-6번)

5. Volume Surge Scalp - 거래량 급증 스캘핑
6. Sector Rotation - 강세 섹터 선도주 매매
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import numpy as np

from src.krstock.indicators import TechnicalIndicators
from src.krstock.filters import (
    MultiLayerFilterConfig,
    TechnicalFilterConfig,
    SupplyDemandFilterConfig,
    MarketFilterConfig,
    BasicFilterConfig,
)
from src.strategies.krstock.base_krstock import (
    BaseKRStockStrategy,
    StrategyConfig,
    CandleData,
    Signal,
    SignalType,
    calculate_trend_score,
    calculate_momentum_score,
    calculate_volume_score,
    calculate_supply_demand_score,
)


# ============================================================================
# 전략 5: Volume Surge Scalp (거래량 급증 스캘핑)
# ============================================================================

class VolumeSurgeScalpStrategy(BaseKRStockStrategy):
    """
    전략 5: Volume Surge Scalp

    진입 조건:
    - 5분 거래량 평균 대비 5배 이상
    - 가격 상승 중 (양봉)
    - RSI 중립 구간 (40-65)
    - 스프레드 적정

    손실 방지:
    - 급등주 추격 매수 방지 (10% 이상 상승 시 진입 금지)
    - 타이트한 손절
    - 빠른 익절

    목표 승률: 55-60%
    손익비: 1:1.2

    주의: 장중 전략 (분봉 데이터 필요)
    """

    def __init__(self):
        filter_config = MultiLayerFilterConfig(
            basic=BasicFilterConfig(
                min_daily_value=30.0,  # 거래대금 30억 이상
                max_price_limit_ratio=0.20,  # 20% 이상 급등 시 진입 금지
            ),
            technical=TechnicalFilterConfig(
                require_above_sma_20=False,  # 단기 전략
                require_macd_above_signal=False,
                rsi_overbought=70.0,
                max_atr_pct=6.0,
            ),
            supply_demand=SupplyDemandFilterConfig(
                require_foreign_positive=False,
                max_individual_ratio=0.85,  # 개인 쏠림 방지
            ),
        )

        config = StrategyConfig(
            strategy_name="VOLUME_SURGE_SCALP",
            strategy_type="VOLUME",
            stop_loss_pct=-1.5,        # 타이트한 손절
            take_profit_pct=2.0,       # 빠른 익절
            trailing_trigger_pct=1.2,
            trailing_stop_pct=0.5,
            max_holding_days=1,        # 당일 청산 원칙
            max_holding_minutes=60,    # 최대 1시간
            max_position_pct=6.0,
            default_position_pct=3.0,  # 작은 포지션 (리스크 관리)
            filter_config=filter_config,
            min_signal_score=65.0,
            cooldown_minutes=30,
        )
        super().__init__(config)

        self.volume_multiplier = 5.0  # 거래량 5배
        self.max_chase_pct = 10.0     # 10% 이상 상승 시 진입 금지

    def calculate_signal_score(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> float:
        """신호 점수 계산"""
        if indicators.rvol is None:
            return 0.0

        score = 0.0

        # 1. 거래량 폭발 점수 (40점)
        if indicators.rvol >= 10.0:
            score += 40
        elif indicators.rvol >= 7.0:
            score += 35
        elif indicators.rvol >= 5.0:
            score += 30
        elif indicators.rvol >= 3.0:
            score += 20
        else:
            return 0.0  # 거래량 조건 미충족

        # 2. 가격 상승 점수 (30점)
        if candle.close > candle.open:
            rise_pct = (candle.close - candle.open) / candle.open * 100
            if 1.0 <= rise_pct <= 5.0:
                score += 30  # 적당한 상승
            elif 0.5 <= rise_pct < 1.0:
                score += 25
            elif rise_pct > 5.0:
                score += 15  # 너무 급등하면 추격 위험
            else:
                score += 10
        else:
            return 0.0  # 음봉은 제외

        # 3. RSI 점수 (15점)
        if indicators.rsi_14 is not None:
            if 40 <= indicators.rsi_14 <= 55:
                score += 15  # 중립 (상승 여지)
            elif 55 < indicators.rsi_14 <= 65:
                score += 10
            elif indicators.rsi_14 > 70:
                score -= 10  # 과매수 패널티

        # 4. 수급 점수 (15점)
        supply_score = calculate_supply_demand_score(
            candle.foreign_net, candle.inst_net,
            candle.individual_net, candle.volume
        )
        score += supply_score * 0.15

        return min(100, max(0, score))

    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """진입 조건 확인"""
        # 1. 거래량 급증 확인
        if indicators.rvol is None or indicators.rvol < self.volume_multiplier:
            return False, f"거래량 부족 (RVOL {indicators.rvol:.1f}x < {self.volume_multiplier}x)"

        # 2. 양봉 확인
        if candle.close <= candle.open:
            return False, "음봉 (양봉 필요)"

        # 3. 추격 매수 방지
        if len(historical_candles) >= 1:
            prev_close = historical_candles[-1].close
            day_change = (candle.close / prev_close - 1) * 100
            if day_change >= self.max_chase_pct:
                return False, f"급등주 추격 금지 ({day_change:.1f}% >= {self.max_chase_pct}%)"

        # 4. RSI 확인
        if indicators.rsi_14 is not None and indicators.rsi_14 > 70:
            return False, f"RSI 과매수 ({indicators.rsi_14:.1f})"

        return True, f"거래량 급증 (RVOL {indicators.rvol:.1f}x), 양봉"


# ============================================================================
# 전략 6: Sector Rotation (섹터 로테이션)
# ============================================================================

class SectorRotationStrategy(BaseKRStockStrategy):
    """
    전략 6: Sector Rotation (섹터 로테이션)

    진입 조건:
    - 강세 섹터 소속 (상위 10위 이내)
    - 섹터 내 상대강도 상위 20%
    - 외국인/기관 순매수
    - 20일선 위

    손실 방지:
    - 약세 섹터 진입 금지
    - 섹터 추세 전환 시 조기 청산
    - 분산 투자 (동일 섹터 과집중 방지)

    목표 승률: 48-52%
    손익비: 1:1.8
    """

    def __init__(self):
        filter_config = MultiLayerFilterConfig(
            technical=TechnicalFilterConfig(
                require_above_sma_20=True,
                require_above_sma_60=True,  # 중기 추세도 확인
                require_macd_above_signal=True,
                rsi_overbought=75.0,
                max_atr_pct=4.5,
            ),
            supply_demand=SupplyDemandFilterConfig(
                require_foreign_positive=True,  # 외국인 순매수 필수!
                require_inst_positive=False,
            ),
            market=MarketFilterConfig(
                require_market_uptrend=True,
                min_sector_rs_rank=10,  # 섹터 상위 10위 이내
            ),
        )

        config = StrategyConfig(
            strategy_name="SECTOR_ROTATION",
            strategy_type="RELATIVE_STRENGTH",
            stop_loss_pct=-4.0,
            take_profit_pct=8.0,       # 높은 익절 목표
            trailing_trigger_pct=5.0,
            trailing_stop_pct=2.5,
            max_holding_days=20,       # 중기 보유
            max_position_pct=10.0,
            default_position_pct=6.0,
            filter_config=filter_config,
            min_signal_score=70.0,     # 높은 진입 기준
            cooldown_minutes=480,      # 8시간 쿨다운
        )
        super().__init__(config)

    def calculate_signal_score(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> float:
        """신호 점수 계산"""
        score = 0.0

        # 1. 추세 점수 (35점)
        trend_score = calculate_trend_score(indicators, candle.close)
        score += trend_score * 0.35

        # 2. 수급 점수 (35점) - 섹터 전략에서 수급 중요
        supply_score = calculate_supply_demand_score(
            candle.foreign_net, candle.inst_net,
            candle.individual_net, candle.volume
        )
        score += supply_score * 0.35

        # 3. 모멘텀 점수 (20점)
        momentum_score = calculate_momentum_score(indicators)
        score += momentum_score * 0.20

        # 4. 거래량 점수 (10점)
        volume_score = calculate_volume_score(indicators)
        score += volume_score * 0.10

        return min(100, score)

    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """진입 조건 확인"""
        # 1. 20일선 위 확인
        if indicators.sma_20 is None or candle.close < indicators.sma_20:
            return False, "20일선 하회"

        # 2. 60일선 위 확인
        if indicators.sma_60 is None or candle.close < indicators.sma_60:
            return False, "60일선 하회"

        # 3. MACD 상승 신호
        if indicators.macd is None or indicators.macd_signal is None:
            return False, "MACD 데이터 없음"
        if indicators.macd <= indicators.macd_signal:
            return False, "MACD 하락 신호"

        # 4. 외국인 순매수 확인 (중요!)
        if candle.foreign_net <= 0:
            return False, "외국인 순매도"

        # 5. RSI 확인
        if indicators.rsi_14 is not None and indicators.rsi_14 > 75:
            return False, f"RSI 과매수 ({indicators.rsi_14:.1f})"

        # 수급 요약
        supply_info = []
        if candle.foreign_net > 0:
            supply_info.append(f"외국인 +{candle.foreign_net:,}")
        if candle.inst_net > 0:
            supply_info.append(f"기관 +{candle.inst_net:,}")

        return True, f"강세 섹터 선도주, {', '.join(supply_info)}"


# ============================================================================
# 팩토리 함수
# ============================================================================

def create_volume_surge_scalp_strategy() -> VolumeSurgeScalpStrategy:
    """Volume Surge Scalp 전략 생성"""
    return VolumeSurgeScalpStrategy()


def create_sector_rotation_strategy() -> SectorRotationStrategy:
    """Sector Rotation 전략 생성"""
    return SectorRotationStrategy()
