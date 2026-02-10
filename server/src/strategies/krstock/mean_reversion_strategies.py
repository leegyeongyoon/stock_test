"""
평균회귀 전략 (3-4번)

3. VWAP Pullback - VWAP 접근 후 반등
4. Support Bounce - 60일 저점 + RSI 과매도 반등
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
    RiskFilterConfig,
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
# 전략 3: VWAP Pullback (VWAP 풀백)
# ============================================================================

class VWAPPullbackStrategy(BaseKRStockStrategy):
    """
    전략 3: VWAP Pullback

    진입 조건:
    - 상승 추세 (20일선 위)
    - VWAP 접근 (±0.5% 이내)
    - 반등 시그널 (양봉 전환)
    - RSI 40-60 사이

    손실 방지:
    - VWAP 아래로 이탈 시 즉시 손절
    - 추세 확인 후 진입
    - 거래량 확인

    목표 승률: 52-58%
    손익비: 1:1.5
    """

    def __init__(self):
        filter_config = MultiLayerFilterConfig(
            technical=TechnicalFilterConfig(
                require_above_sma_20=True,   # 상승 추세 필수
                require_macd_above_signal=False,
                rsi_overbought=70.0,
                rsi_oversold=30.0,
                max_atr_pct=4.0,
            ),
            supply_demand=SupplyDemandFilterConfig(
                require_foreign_positive=False,
                require_inst_positive=False,
            ),
        )

        config = StrategyConfig(
            strategy_name="VWAP_PULLBACK",
            strategy_type="MEAN_REVERSION",
            stop_loss_pct=-1.5,        # 타이트한 손절
            take_profit_pct=2.5,       # 익절
            trailing_trigger_pct=1.5,
            trailing_stop_pct=0.7,
            max_holding_days=3,        # 단기 보유
            max_position_pct=8.0,
            default_position_pct=5.0,
            filter_config=filter_config,
            min_signal_score=60.0,
            cooldown_minutes=60,
        )
        super().__init__(config)

        self.vwap_tolerance = 0.005  # VWAP ±0.5%

    def calculate_signal_score(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> float:
        """신호 점수 계산"""
        if indicators.vwap is None:
            return 0.0

        score = 0.0

        # 1. VWAP 접근도 점수 (30점)
        vwap_diff = abs(candle.close - indicators.vwap) / indicators.vwap
        if vwap_diff <= 0.002:  # 0.2% 이내
            score += 30
        elif vwap_diff <= 0.005:  # 0.5% 이내
            score += 25
        elif vwap_diff <= 0.01:   # 1% 이내
            score += 15
        else:
            return 0.0  # VWAP 너무 멀리

        # 2. 양봉 전환 점수 (25점)
        if len(historical_candles) >= 2:
            prev_candle = historical_candles[-1]
            # 이전 음봉 -> 현재 양봉
            if prev_candle.close < prev_candle.open and candle.close > candle.open:
                score += 25
            elif candle.close > candle.open:
                score += 15

        # 3. RSI 중립 점수 (25점)
        if indicators.rsi_14 is not None:
            if 40 <= indicators.rsi_14 <= 55:
                score += 25  # 최적
            elif 35 <= indicators.rsi_14 <= 60:
                score += 20
            elif 30 <= indicators.rsi_14 <= 65:
                score += 10

        # 4. 추세 점수 (20점)
        trend_score = calculate_trend_score(indicators, candle.close)
        score += trend_score * 0.20

        return min(100, score)

    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """진입 조건 확인"""
        if indicators.vwap is None:
            return False, "VWAP 없음"

        if len(historical_candles) < 2:
            return False, "데이터 부족"

        # 1. VWAP 접근 확인
        vwap_diff = (candle.close - indicators.vwap) / indicators.vwap
        if abs(vwap_diff) > self.vwap_tolerance:
            return False, f"VWAP 거리 초과 ({vwap_diff*100:.2f}%)"

        # 2. 20일선 위 확인 (상승 추세)
        if indicators.sma_20 is None or candle.close < indicators.sma_20:
            return False, "20일선 하회 (상승 추세 아님)"

        # 3. 양봉 확인
        if candle.close <= candle.open:
            return False, "음봉 (양봉 전환 필요)"

        # 4. 이전 캔들이 음봉이면 더 좋음 (풀백 확인)
        prev_candle = historical_candles[-1]
        is_pullback = prev_candle.close < prev_candle.open

        return True, f"VWAP 접근 ({vwap_diff*100:+.2f}%), 양봉 전환" + (" (풀백 확인)" if is_pullback else "")


# ============================================================================
# 전략 4: Support Bounce (지지선 반등)
# ============================================================================

class SupportBounceStrategy(BaseKRStockStrategy):
    """
    전략 4: Support Bounce (지지선 반등)

    진입 조건:
    - 60일 저점 근처 (저점 대비 5% 이내)
    - RSI 과매도 (35 이하)
    - 양봉 전환
    - 거래량 증가

    손실 방지:
    - 저점 이탈 시 즉시 손절
    - 기관/외국인 연속 매도 시 진입 금지
    - 관리종목, 공매도 과다 종목 필터

    목표 승률: 50-55%
    손익비: 1:1.5
    """

    def __init__(self):
        # 보수적 필터 (저점 매수는 위험할 수 있음)
        filter_config = MultiLayerFilterConfig(
            technical=TechnicalFilterConfig(
                require_above_sma_20=False,  # 저점이므로 20일선 아래일 수 있음
                require_macd_above_signal=False,
                rsi_overbought=70.0,
                rsi_oversold=20.0,  # 극단적 과매도는 주의
                max_atr_pct=5.0,
            ),
            supply_demand=SupplyDemandFilterConfig(
                require_foreign_positive=False,
                require_inst_positive=False,
                max_consecutive_sell_days=3,  # 3일 연속 매도 시 진입 금지
            ),
            risk=RiskFilterConfig(
                max_short_ratio=8.0,   # 공매도 비율 제한
                max_lending_ratio=4.0,
            ),
        )

        config = StrategyConfig(
            strategy_name="SUPPORT_BOUNCE",
            strategy_type="MEAN_REVERSION",
            stop_loss_pct=-3.0,        # 저점 이탈 시 손절
            take_profit_pct=5.0,       # 반등 목표
            trailing_trigger_pct=3.0,
            trailing_stop_pct=1.5,
            max_holding_days=10,
            max_position_pct=8.0,
            default_position_pct=4.0,  # 리스크 관리
            filter_config=filter_config,
            min_signal_score=65.0,
            cooldown_minutes=240,      # 4시간 쿨다운
        )
        super().__init__(config)

        self.support_period = 60  # 60일 저점
        self.support_tolerance = 0.05  # 저점 대비 5% 이내

    def calculate_signal_score(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> float:
        """신호 점수 계산"""
        if len(historical_candles) < self.support_period:
            return 0.0

        # 60일 저점 계산
        lows = [c.low for c in historical_candles[-self.support_period:]]
        low_60d = min(lows)

        score = 0.0

        # 1. 지지선 근접도 점수 (30점)
        price_from_low = (candle.close - low_60d) / low_60d
        if price_from_low <= 0.02:  # 저점 대비 2% 이내
            score += 30
        elif price_from_low <= 0.05:  # 5% 이내
            score += 25
        elif price_from_low <= 0.08:  # 8% 이내
            score += 15
        else:
            return 0.0  # 저점에서 너무 멀리

        # 2. RSI 과매도 점수 (30점)
        if indicators.rsi_14 is not None:
            if indicators.rsi_14 <= 25:
                score += 30  # 극단적 과매도
            elif indicators.rsi_14 <= 30:
                score += 25
            elif indicators.rsi_14 <= 35:
                score += 20
            elif indicators.rsi_14 <= 40:
                score += 10
            else:
                return 0.0  # 과매도 아님

        # 3. 양봉 전환 점수 (25점)
        if candle.close > candle.open:
            body_pct = (candle.close - candle.open) / candle.open * 100
            if body_pct >= 2.0:
                score += 25  # 강한 양봉
            elif body_pct >= 1.0:
                score += 20
            else:
                score += 15
        else:
            score += 5  # 음봉이면 낮은 점수

        # 4. 거래량 점수 (15점)
        if indicators.rvol is not None:
            if indicators.rvol >= 1.5:
                score += 15
            elif indicators.rvol >= 1.0:
                score += 10
            elif indicators.rvol >= 0.7:
                score += 5

        return min(100, score)

    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """진입 조건 확인"""
        if len(historical_candles) < self.support_period:
            return False, "데이터 부족"

        # 60일 저점
        lows = [c.low for c in historical_candles[-self.support_period:]]
        low_60d = min(lows)

        # 1. 저점 근처 확인
        price_from_low = (candle.close - low_60d) / low_60d
        if price_from_low > self.support_tolerance:
            return False, f"저점에서 너무 멀리 ({price_from_low*100:.1f}%)"

        # 2. RSI 과매도 확인
        if indicators.rsi_14 is None or indicators.rsi_14 > 35:
            return False, f"RSI 과매도 아님 ({indicators.rsi_14:.1f})"

        # 3. 양봉 확인
        if candle.close <= candle.open:
            return False, "음봉 (양봉 필요)"

        # 4. 하한가 근처 진입 금지
        day_change = (candle.close - historical_candles[-1].close) / historical_candles[-1].close * 100
        if day_change <= -25:
            return False, f"하한가 근처 진입 금지 ({day_change:.1f}%)"

        return True, f"60일 저점 근처 (저점대비 +{price_from_low*100:.1f}%), RSI {indicators.rsi_14:.1f}"


# ============================================================================
# 팩토리 함수
# ============================================================================

def create_vwap_pullback_strategy() -> VWAPPullbackStrategy:
    """VWAP Pullback 전략 생성"""
    return VWAPPullbackStrategy()


def create_support_bounce_strategy() -> SupportBounceStrategy:
    """Support Bounce 전략 생성"""
    return SupportBounceStrategy()
