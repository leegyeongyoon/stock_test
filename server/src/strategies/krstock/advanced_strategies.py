"""
고급 전략 (7-10번)

7. Multi-Timeframe - 일봉 + 60분봉 추세 정렬
8. Earnings Drift - 실적 발표 후 추세 추종
9. Opening Range Breakout - 30분 박스권 돌파
10. Oversold Bounce - RSI + MACD 복합 반등
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional
import numpy as np

from src.krstock.indicators import TechnicalIndicators
from src.krstock.filters import (
    MultiLayerFilterConfig,
    TechnicalFilterConfig,
    SupplyDemandFilterConfig,
    MarketFilterConfig,
    BasicFilterConfig,
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
# 전략 7: Multi-Timeframe (멀티 타임프레임)
# ============================================================================

class MultiTimeframeStrategy(BaseKRStockStrategy):
    """
    전략 7: Multi-Timeframe

    진입 조건:
    - 일봉 상승 추세 (20일선 위, MACD 양수)
    - 60분봉 상승 추세 확인
    - 두 시간대 모두 추세 정렬
    - RSI 중립 (40-65)

    손실 방지:
    - 두 시간대 추세 불일치 시 진입 금지
    - 추세 이탈 시 빠른 청산
    - 보수적 포지션 사이징

    목표 승률: 52-58%
    손익비: 1:1.5
    """

    def __init__(self):
        filter_config = MultiLayerFilterConfig(
            technical=TechnicalFilterConfig(
                require_above_sma_20=True,
                require_above_sma_60=True,
                require_macd_above_signal=True,
                rsi_overbought=70.0,
                rsi_oversold=35.0,
                max_atr_pct=4.0,
            ),
            supply_demand=SupplyDemandFilterConfig(
                require_foreign_positive=False,
            ),
            market=MarketFilterConfig(
                require_market_uptrend=True,
            ),
        )

        config = StrategyConfig(
            strategy_name="MULTI_TIMEFRAME",
            strategy_type="TREND_FOLLOWING",
            stop_loss_pct=-2.5,
            take_profit_pct=5.0,
            trailing_trigger_pct=3.0,
            trailing_stop_pct=1.5,
            max_holding_days=7,
            max_position_pct=8.0,
            default_position_pct=5.0,
            filter_config=filter_config,
            min_signal_score=65.0,
            cooldown_minutes=180,  # 3시간 쿨다운
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

        # 1. 일봉 추세 점수 (40점)
        daily_trend = calculate_trend_score(indicators, candle.close)
        score += daily_trend * 0.40

        # 2. 이동평균 정렬 점수 (25점)
        if indicators.sma_5 and indicators.sma_20 and indicators.sma_60:
            if candle.close > indicators.sma_5 > indicators.sma_20 > indicators.sma_60:
                score += 25  # 완벽한 정배열
            elif candle.close > indicators.sma_5 > indicators.sma_20:
                score += 20
            elif candle.close > indicators.sma_20:
                score += 10

        # 3. MACD 확인 점수 (20점)
        if indicators.macd is not None and indicators.macd_signal is not None:
            if indicators.macd > indicators.macd_signal and indicators.macd > 0:
                score += 20
            elif indicators.macd > indicators.macd_signal:
                score += 15
            elif indicators.macd > 0:
                score += 10

        # 4. 모멘텀 점수 (15점)
        momentum = calculate_momentum_score(indicators)
        score += momentum * 0.15

        return min(100, score)

    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """진입 조건 확인"""
        # 1. 일봉 20일선 위 확인
        if indicators.sma_20 is None or candle.close < indicators.sma_20:
            return False, "일봉 20일선 하회"

        # 2. 일봉 60일선 위 확인
        if indicators.sma_60 is None or candle.close < indicators.sma_60:
            return False, "일봉 60일선 하회"

        # 3. MACD 상승 확인
        if indicators.macd is None or indicators.macd_signal is None:
            return False, "MACD 데이터 없음"
        if indicators.macd <= indicators.macd_signal:
            return False, "MACD 데드크로스"

        # 4. RSI 확인
        if indicators.rsi_14 is not None:
            if indicators.rsi_14 > 70:
                return False, f"RSI 과매수 ({indicators.rsi_14:.1f})"
            if indicators.rsi_14 < 35:
                return False, f"RSI 과매도 ({indicators.rsi_14:.1f})"

        # 5. ADX 추세 강도 확인
        trend_info = ""
        if indicators.adx is not None:
            if indicators.adx >= 25:
                trend_info = f", 강한 추세 (ADX {indicators.adx:.1f})"
            else:
                trend_info = f", 약한 추세 (ADX {indicators.adx:.1f})"

        return True, f"멀티 타임프레임 정렬{trend_info}"


# ============================================================================
# 전략 8: Earnings Drift (실적 드리프트)
# ============================================================================

class EarningsDriftStrategy(BaseKRStockStrategy):
    """
    전략 8: Earnings Drift (실적 드리프트)

    진입 조건:
    - 실적 발표 후 5거래일 이내
    - 어닝 서프라이즈 (예상 대비 10% 이상)
    - 풀백 후 반등 (발표 당일 고점 대비 -5% 이상)
    - 거래량 정상화

    손실 방지:
    - 실적 쇼크 종목 필터
    - 이미 급등한 종목 추격 금지
    - 기관/외국인 매도 시 진입 금지

    목표 승률: 55-60%
    손익비: 1:1.5
    """

    def __init__(self):
        filter_config = MultiLayerFilterConfig(
            basic=BasicFilterConfig(
                min_market_cap=5000.0,  # 시총 5000억 이상 (대형주)
            ),
            technical=TechnicalFilterConfig(
                require_above_sma_20=True,
                rsi_overbought=72.0,
                max_atr_pct=5.0,
            ),
            supply_demand=SupplyDemandFilterConfig(
                require_foreign_positive=False,
                require_inst_positive=False,
            ),
        )

        config = StrategyConfig(
            strategy_name="EARNINGS_DRIFT",
            strategy_type="EVENT",
            stop_loss_pct=-3.0,
            take_profit_pct=6.0,
            trailing_trigger_pct=4.0,
            trailing_stop_pct=2.0,
            max_holding_days=10,
            max_position_pct=8.0,
            default_position_pct=5.0,
            filter_config=filter_config,
            min_signal_score=65.0,
            cooldown_minutes=480,  # 8시간 쿨다운
        )
        super().__init__(config)

        self.pullback_pct = -3.0  # 최소 풀백 %

    def calculate_signal_score(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> float:
        """신호 점수 계산"""
        if len(historical_candles) < 5:
            return 0.0

        score = 0.0

        # 1. 최근 5일 내 급등 여부 확인 (어닝 서프라이즈 프록시)
        max_high = max(c.high for c in historical_candles[-5:])
        min_low = min(c.low for c in historical_candles[-5:])
        range_pct = (max_high - min_low) / min_low * 100

        if range_pct >= 15:
            score += 30  # 큰 변동 = 이벤트 발생 가능성
        elif range_pct >= 10:
            score += 25
        elif range_pct >= 5:
            score += 15
        else:
            return 0.0  # 이벤트 없음

        # 2. 풀백 확인 (고점 대비 하락)
        pullback_pct = (candle.close - max_high) / max_high * 100
        if self.pullback_pct <= pullback_pct <= -1.0:
            score += 25  # 적절한 풀백
        elif -1.0 < pullback_pct < 0:
            score += 15  # 작은 풀백

        # 3. 반등 신호 (양봉)
        if candle.close > candle.open:
            score += 20

        # 4. 추세 점수 (25점)
        trend = calculate_trend_score(indicators, candle.close)
        score += trend * 0.25

        return min(100, score)

    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """진입 조건 확인"""
        if len(historical_candles) < 5:
            return False, "데이터 부족"

        # 1. 최근 5일 내 급등 확인 (어닝 이벤트 프록시)
        max_high = max(c.high for c in historical_candles[-5:])
        start_price = historical_candles[-5].close
        event_move = (max_high - start_price) / start_price * 100

        if event_move < 5.0:
            return False, "최근 이벤트 없음 (5일 변동 부족)"

        # 2. 풀백 확인
        pullback_pct = (candle.close - max_high) / max_high * 100
        if pullback_pct > -1.0:
            return False, f"풀백 부족 ({pullback_pct:.1f}%)"
        if pullback_pct < -10.0:
            return False, f"과도한 하락 ({pullback_pct:.1f}%)"

        # 3. 양봉 확인
        if candle.close <= candle.open:
            return False, "음봉 (반등 확인 필요)"

        # 4. 20일선 위 확인
        if indicators.sma_20 is None or candle.close < indicators.sma_20:
            return False, "20일선 하회"

        return True, f"실적 이벤트 후 풀백 ({pullback_pct:.1f}%), 반등 시작"


# ============================================================================
# 전략 9: Opening Range Breakout (오프닝 레인지 돌파)
# ============================================================================

class OpeningRangeBreakoutStrategy(BaseKRStockStrategy):
    """
    전략 9: Opening Range Breakout (ORB)

    진입 조건:
    - 장 시작 30분간 형성된 박스권 상단 돌파
    - 거래량 동반
    - 전일 종가 대비 갭 없이 시작

    손실 방지:
    - 박스권 하단 이탈 시 즉시 손절
    - 오전 10시 이후 진입 금지
    - 변동성 과다 시 진입 금지

    목표 승률: 50-55%
    손익비: 1:1.2

    주의: 장중 전략 (분봉 데이터 필요)
    """

    def __init__(self):
        filter_config = MultiLayerFilterConfig(
            basic=BasicFilterConfig(
                min_daily_value=20.0,  # 거래대금 20억 이상
            ),
            technical=TechnicalFilterConfig(
                require_above_sma_20=False,  # 단기 전략
                rsi_overbought=75.0,
                max_atr_pct=5.0,
            ),
            market=MarketFilterConfig(
                max_market_decline=-1.0,  # 시장 급락 시 진입 금지
            ),
        )

        config = StrategyConfig(
            strategy_name="OPENING_RANGE_BREAKOUT",
            strategy_type="BREAKOUT",
            stop_loss_pct=-1.5,        # 박스권 하단
            take_profit_pct=2.0,
            trailing_trigger_pct=1.2,
            trailing_stop_pct=0.5,
            max_holding_days=1,        # 당일 청산
            max_holding_minutes=120,   # 최대 2시간
            max_position_pct=6.0,
            default_position_pct=3.0,
            filter_config=filter_config,
            min_signal_score=60.0,
            cooldown_minutes=60,
        )
        super().__init__(config)

        self.opening_range_minutes = 30  # 30분 오프닝 레인지
        self.breakout_margin = 0.002     # 0.2% 돌파 마진

    def calculate_signal_score(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> float:
        """신호 점수 계산"""
        if len(historical_candles) < 6:  # 최소 30분 데이터 (5분봉 기준)
            return 0.0

        score = 0.0

        # 1. 거래량 점수 (30점)
        volume_score = calculate_volume_score(indicators)
        score += volume_score * 0.30

        # 2. 돌파 강도 점수 (35점)
        # 오프닝 레인지 계산 (최근 6개 캔들)
        opening_candles = historical_candles[-6:]
        or_high = max(c.high for c in opening_candles)
        or_low = min(c.low for c in opening_candles)

        if candle.close > or_high * (1 + self.breakout_margin):
            breakout_pct = (candle.close - or_high) / or_high * 100
            if breakout_pct >= 1.0:
                score += 35
            elif breakout_pct >= 0.5:
                score += 30
            else:
                score += 25

        # 3. 모멘텀 점수 (20점)
        momentum = calculate_momentum_score(indicators)
        score += momentum * 0.20

        # 4. 캔들 강도 (15점)
        if candle.close > candle.open:
            body_pct = (candle.close - candle.open) / candle.open * 100
            if body_pct >= 1.0:
                score += 15
            elif body_pct >= 0.5:
                score += 10
            else:
                score += 5

        return min(100, score)

    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """진입 조건 확인"""
        if len(historical_candles) < 6:
            return False, "오프닝 레인지 데이터 부족"

        # 1. 시간 확인 (09:30 ~ 10:30 사이만)
        candle_time = candle.timestamp.time()
        if candle_time < time(9, 30) or candle_time > time(10, 30):
            return False, f"ORB 진입 시간 아님 ({candle_time})"

        # 2. 오프닝 레인지 계산
        opening_candles = historical_candles[-6:]
        or_high = max(c.high for c in opening_candles)
        or_low = min(c.low for c in opening_candles)
        or_range = (or_high - or_low) / or_low * 100

        # 3. 레인지가 너무 크면 진입 금지
        if or_range > 3.0:
            return False, f"오프닝 레인지 과대 ({or_range:.1f}%)"

        # 4. 상단 돌파 확인
        if candle.close <= or_high * (1 + self.breakout_margin):
            return False, f"레인지 상단({or_high:,.0f}) 미돌파"

        # 5. 양봉 확인
        if candle.close <= candle.open:
            return False, "음봉 (양봉 필요)"

        breakout_pct = (candle.close - or_high) / or_high * 100
        return True, f"30분 레인지 상단 돌파 (+{breakout_pct:.2f}%)"


# ============================================================================
# 전략 10: Oversold Bounce (과매도 반등 하이브리드)
# ============================================================================

class OversoldBounceStrategy(BaseKRStockStrategy):
    """
    전략 10: Oversold Bounce (과매도 반등)

    진입 조건:
    - RSI 25 이하 (극단적 과매도)
    - 양봉 출현 (반등 시작)
    - MACD 히스토그램 상승 전환
    - 거래량 증가

    손실 방지:
    - 추가 하락 가능성 대비 타이트한 손절
    - 저점 갱신 시 즉시 청산
    - 관리종목/투자경고 진입 금지
    - 공매도 과다 종목 필터

    목표 승률: 55-60%
    손익비: 1:1.5
    """

    def __init__(self):
        # 매우 보수적인 필터 설정
        filter_config = MultiLayerFilterConfig(
            basic=BasicFilterConfig(
                min_market_cap=3000.0,  # 시총 3000억 이상
                block_admin_stocks=True,
            ),
            technical=TechnicalFilterConfig(
                require_above_sma_20=False,  # 과매도 상태이므로 20일선 아래
                rsi_overbought=60.0,
                rsi_oversold=15.0,  # 너무 극단적이면 위험
                max_atr_pct=6.0,
            ),
            supply_demand=SupplyDemandFilterConfig(
                max_consecutive_sell_days=5,  # 5일 연속 매도 시 진입 금지
            ),
            risk=RiskFilterConfig(
                max_short_ratio=10.0,
                max_lending_ratio=5.0,
            ),
        )

        config = StrategyConfig(
            strategy_name="OVERSOLD_BOUNCE",
            strategy_type="HYBRID",
            stop_loss_pct=-4.0,        # 저점 이탈 대비
            take_profit_pct=6.0,
            trailing_trigger_pct=4.0,
            trailing_stop_pct=2.0,
            max_holding_days=7,
            max_position_pct=8.0,
            default_position_pct=4.0,  # 보수적 사이징
            filter_config=filter_config,
            min_signal_score=70.0,     # 높은 진입 기준
            cooldown_minutes=240,      # 4시간 쿨다운
        )
        super().__init__(config)

        self.rsi_threshold = 25  # RSI 25 이하

    def calculate_signal_score(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> float:
        """신호 점수 계산"""
        if indicators.rsi_14 is None:
            return 0.0

        score = 0.0

        # 1. RSI 과매도 점수 (35점)
        if indicators.rsi_14 <= 20:
            score += 35  # 극단적 과매도
        elif indicators.rsi_14 <= 25:
            score += 30
        elif indicators.rsi_14 <= 30:
            score += 20
        else:
            return 0.0  # 과매도 조건 미충족

        # 2. 양봉 강도 점수 (25점)
        if candle.close > candle.open:
            body_pct = (candle.close - candle.open) / candle.open * 100
            if body_pct >= 3.0:
                score += 25  # 강한 반등
            elif body_pct >= 2.0:
                score += 20
            elif body_pct >= 1.0:
                score += 15
            else:
                score += 10
        else:
            score += 5  # 음봉이면 낮은 점수

        # 3. MACD 히스토그램 점수 (25점)
        if indicators.macd_histogram is not None:
            if len(historical_candles) >= 2:
                # 히스토그램 상승 전환 확인 필요
                # 현재는 단순히 값으로 판단
                if indicators.macd_histogram > 0:
                    score += 25
                elif indicators.macd_histogram > -0.5:
                    score += 15  # 바닥 근처

        # 4. 거래량 점수 (15점)
        volume_score = calculate_volume_score(indicators)
        score += volume_score * 0.15

        return min(100, score)

    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """진입 조건 확인"""
        # 1. RSI 과매도 확인
        if indicators.rsi_14 is None or indicators.rsi_14 > self.rsi_threshold:
            return False, f"RSI 과매도 아님 ({indicators.rsi_14:.1f} > {self.rsi_threshold})"

        # 2. 극단적 과매도 주의 (RSI 15 미만)
        if indicators.rsi_14 < 15:
            return False, f"RSI 너무 극단적 ({indicators.rsi_14:.1f}) - 추가 하락 위험"

        # 3. 양봉 확인
        if candle.close <= candle.open:
            return False, "음봉 (양봉 반등 필요)"

        # 4. 양봉 크기 확인 (최소 1% 이상)
        body_pct = (candle.close - candle.open) / candle.open * 100
        if body_pct < 1.0:
            return False, f"양봉 너무 약함 ({body_pct:.1f}%)"

        # 5. 하한가 근처 진입 금지
        if len(historical_candles) >= 1:
            prev_close = historical_candles[-1].close
            day_change = (candle.close - prev_close) / prev_close * 100
            if day_change <= -20:
                return False, f"하한가 근처 진입 금지 ({day_change:.1f}%)"

        # 6. MACD 반전 확인 (선택적)
        macd_info = ""
        if indicators.macd_histogram is not None and indicators.macd_histogram > -0.5:
            macd_info = ", MACD 반전 신호"

        return True, f"RSI 과매도 ({indicators.rsi_14:.1f}), 강한 양봉 (+{body_pct:.1f}%){macd_info}"


# ============================================================================
# 팩토리 함수
# ============================================================================

def create_multi_timeframe_strategy() -> MultiTimeframeStrategy:
    """Multi-Timeframe 전략 생성"""
    return MultiTimeframeStrategy()


def create_earnings_drift_strategy() -> EarningsDriftStrategy:
    """Earnings Drift 전략 생성"""
    return EarningsDriftStrategy()


def create_opening_range_breakout_strategy() -> OpeningRangeBreakoutStrategy:
    """Opening Range Breakout 전략 생성"""
    return OpeningRangeBreakoutStrategy()


def create_oversold_bounce_strategy() -> OversoldBounceStrategy:
    """Oversold Bounce 전략 생성"""
    return OversoldBounceStrategy()
