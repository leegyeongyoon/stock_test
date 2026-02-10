"""
모멘텀 전략 (1-2번)

1. Momentum Breakout - 20일 고점 돌파 + 거래량 확대
2. Gap Fade - 갭상승 후 평균회귀
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
# 전략 1: Momentum Breakout (모멘텀 돌파)
# ============================================================================

class MomentumBreakoutStrategy(BaseKRStockStrategy):
    """
    전략 1: Momentum Breakout

    진입 조건:
    - 20일 고점 돌파
    - 거래량 2배 이상 확대
    - MACD 양수
    - 20일선 위

    손실 방지:
    - 다층 필터 통과 필수
    - 외국인/기관 순매도 시 진입 금지
    - RSI 과매수 구간 진입 금지

    목표 승률: 45-50%
    손익비: 1:2 이상
    """

    def __init__(self):
        # 필터 설정 강화
        filter_config = MultiLayerFilterConfig(
            technical=TechnicalFilterConfig(
                require_above_sma_20=True,
                require_macd_above_signal=True,
                rsi_overbought=75.0,  # RSI 75 이상 진입 금지
                max_atr_pct=4.0,  # 변동성 과다 필터
            ),
            supply_demand=SupplyDemandFilterConfig(
                require_foreign_positive=False,  # 외국인 순매수 권장
                require_inst_positive=False,
            ),
        )

        config = StrategyConfig(
            strategy_name="MOMENTUM_BREAKOUT",
            strategy_type="MOMENTUM",
            stop_loss_pct=-3.0,        # 손절 -3%
            take_profit_pct=6.0,       # 익절 +6%
            trailing_trigger_pct=4.0,  # 4% 수익 시 추적손절 활성화
            trailing_stop_pct=2.0,     # 고점 대비 -2%
            max_holding_days=10,       # 최대 10일 보유
            max_position_pct=10.0,
            default_position_pct=5.0,
            filter_config=filter_config,
            min_signal_score=65.0,
            cooldown_minutes=60,       # 1시간 쿨다운
        )
        super().__init__(config)

        # 돌파 관련 파라미터
        self.breakout_period = 20  # 20일 고점
        self.volume_multiplier = 2.0  # 거래량 2배

    def calculate_signal_score(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> float:
        """신호 점수 계산"""
        if len(historical_candles) < self.breakout_period:
            return 0.0

        # 20일 고점 계산
        highs = [c.high for c in historical_candles[-self.breakout_period:]]
        high_20d = max(highs)

        # 기본 점수 구성
        score = 0.0

        # 1. 돌파 점수 (30점)
        if candle.close > high_20d:
            breakout_pct = (candle.close / high_20d - 1) * 100
            if breakout_pct >= 3.0:
                score += 30  # 강한 돌파
            elif breakout_pct >= 1.0:
                score += 20  # 중간 돌파
            else:
                score += 10  # 약한 돌파

        # 2. 거래량 점수 (25점)
        volume_score = calculate_volume_score(indicators)
        score += volume_score * 0.25

        # 3. 추세 점수 (25점)
        trend_score = calculate_trend_score(indicators, candle.close)
        score += trend_score * 0.25

        # 4. 모멘텀 점수 (20점)
        momentum_score = calculate_momentum_score(indicators)
        score += momentum_score * 0.20

        return min(100, score)

    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """진입 조건 확인"""
        if len(historical_candles) < self.breakout_period:
            return False, "데이터 부족"

        # 20일 고점
        highs = [c.high for c in historical_candles[-self.breakout_period:]]
        high_20d = max(highs)

        # 1. 고점 돌파 확인
        if candle.close <= high_20d:
            return False, f"20일 고점({high_20d:,.0f}) 미돌파"

        # 2. 거래량 확대 확인
        if indicators.rvol is None or indicators.rvol < self.volume_multiplier:
            return False, f"거래량 부족 (RVOL {indicators.rvol:.1f}x < {self.volume_multiplier}x)"

        # 3. MACD 양수 확인
        if indicators.macd is None or indicators.macd <= 0:
            return False, "MACD 음수"

        # 4. 20일선 위 확인
        if indicators.sma_20 is None or candle.close < indicators.sma_20:
            return False, "20일선 하회"

        # 5. 양봉 확인
        if candle.close < candle.open:
            return False, "음봉 (양봉 필요)"

        breakout_pct = (candle.close / high_20d - 1) * 100
        return True, f"20일 고점 돌파 (+{breakout_pct:.1f}%), RVOL {indicators.rvol:.1f}x"


# ============================================================================
# 전략 2: Gap Fade (갭 페이드 - 평균회귀)
# ============================================================================

class GapFadeStrategy(BaseKRStockStrategy):
    """
    전략 2: Gap Fade (갭 페이드)

    진입 조건:
    - 3% 이상 갭상승으로 시작
    - 첫 음봉 출현 (갭 메우기 시작)
    - RSI 과매수 상태
    - 거래량 정상 수준

    손실 방지:
    - 급등 후 조정 매매 (평균회귀)
    - 상한가 근처 진입 금지
    - 외국인/기관 대량 매도 시 진입 금지

    목표 승률: 55-60%
    손익비: 1:1
    """

    def __init__(self):
        # 보수적 필터 설정
        filter_config = MultiLayerFilterConfig(
            technical=TechnicalFilterConfig(
                require_above_sma_20=False,  # 갭상승 후라 무관
                require_macd_above_signal=False,
                rsi_overbought=85.0,  # 너무 과매수면 위험
                max_atr_pct=5.0,
            ),
            supply_demand=SupplyDemandFilterConfig(
                require_foreign_positive=False,
                require_inst_positive=False,
                max_individual_ratio=0.9,  # 개인 쏠림 방지
            ),
        )

        config = StrategyConfig(
            strategy_name="GAP_FADE",
            strategy_type="MEAN_REVERSION",
            stop_loss_pct=-2.0,        # 손절 -2% (타이트)
            take_profit_pct=2.0,       # 익절 +2%
            trailing_trigger_pct=1.5,
            trailing_stop_pct=0.5,
            max_holding_days=2,        # 단기 보유
            max_position_pct=8.0,
            default_position_pct=4.0,
            filter_config=filter_config,
            min_signal_score=60.0,
            cooldown_minutes=120,      # 2시간 쿨다운
        )
        super().__init__(config)

        self.min_gap_pct = 3.0  # 최소 갭 크기

    def calculate_signal_score(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> float:
        """신호 점수 계산"""
        if len(historical_candles) < 2:
            return 0.0

        prev_candle = historical_candles[-1]
        score = 0.0

        # 1. 갭 크기 점수 (30점)
        gap_pct = (candle.open / prev_candle.close - 1) * 100
        if gap_pct >= 5.0:
            score += 30
        elif gap_pct >= 4.0:
            score += 25
        elif gap_pct >= 3.0:
            score += 20
        else:
            return 0.0  # 갭 조건 미충족

        # 2. 음봉 확인 점수 (25점)
        if candle.close < candle.open:
            fade_pct = (candle.open - candle.close) / candle.open * 100
            if fade_pct >= 1.5:
                score += 25
            elif fade_pct >= 1.0:
                score += 20
            elif fade_pct >= 0.5:
                score += 15
        else:
            return 0.0  # 음봉이 아니면 제외

        # 3. RSI 과매수 점수 (25점)
        if indicators.rsi_14 is not None:
            if indicators.rsi_14 >= 75:
                score += 25
            elif indicators.rsi_14 >= 70:
                score += 20
            elif indicators.rsi_14 >= 65:
                score += 15

        # 4. 수급 점수 (20점)
        supply_score = calculate_supply_demand_score(
            candle.foreign_net,
            candle.inst_net,
            candle.individual_net,
            candle.volume
        )
        # 평균회귀 전략이므로 수급이 나빠야 점수 증가 (역발상)
        score += (100 - supply_score) * 0.20

        return min(100, score)

    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """진입 조건 확인"""
        if len(historical_candles) < 2:
            return False, "데이터 부족"

        prev_candle = historical_candles[-1]

        # 1. 갭상승 확인
        gap_pct = (candle.open / prev_candle.close - 1) * 100
        if gap_pct < self.min_gap_pct:
            return False, f"갭 부족 ({gap_pct:.1f}% < {self.min_gap_pct}%)"

        # 2. 상한가 근처 진입 금지 (안전 장치)
        if gap_pct >= 25:
            return False, f"상한가 근처 진입 금지 ({gap_pct:.1f}%)"

        # 3. 음봉 확인 (갭 메우기 시작)
        if candle.close >= candle.open:
            return False, "양봉 (음봉 필요)"

        # 4. RSI 과매수 확인
        if indicators.rsi_14 is None or indicators.rsi_14 < 65:
            return False, f"RSI 과매수 아님 ({indicators.rsi_14:.1f})"

        return True, f"갭상승 +{gap_pct:.1f}% 후 음봉, RSI {indicators.rsi_14:.1f}"


# ============================================================================
# 팩토리 함수
# ============================================================================

def create_momentum_breakout_strategy() -> MomentumBreakoutStrategy:
    """Momentum Breakout 전략 생성"""
    return MomentumBreakoutStrategy()


def create_gap_fade_strategy() -> GapFadeStrategy:
    """Gap Fade 전략 생성"""
    return GapFadeStrategy()
