"""
변동성 압축 감지 (Volatility Squeeze)

급등 직전 공통 특징:
- 최근 N봉의 고저폭이 감소
- ATR%가 하락 추세
- BB Width가 백분위 하위 20%

"폭풍 전의 고요함"을 감지
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.config import get_settings
from src.data.candle_manager import CandleManager, get_candle_manager

logger = structlog.get_logger()


@dataclass
class SqueezeResult:
    """변동성 압축 감지 결과"""

    is_squeeze: bool  # 압축 상태 여부
    bb_width_percentile: float  # BB Width 백분위 (0-100, 낮을수록 수축)
    atr_pct_current: float  # 현재 ATR%
    atr_pct_trend: str  # FALLING/FLAT/RISING
    squeeze_duration_bars: int  # 압축 지속 봉 수
    squeeze_score: float  # 압축 점수 (0-100)


class VolatilitySqueezeDetector:
    """
    변동성 압축 감지기

    급등 직전 패턴:
    - BB Width 백분위 < 20%
    - ATR% 최근 5봉 하락 추세
    """

    # 임계값
    BB_WIDTH_PERCENTILE_THRESHOLD = 20.0  # 하위 20% 이하면 압축
    ATR_FALLING_THRESHOLD = -0.1  # ATR% 10% 이상 하락이면 FALLING

    def __init__(self) -> None:
        self._settings = get_settings()
        self._candle_manager = get_candle_manager()

    def detect(
        self,
        symbol: str,
        interval: str = "1h",
    ) -> SqueezeResult:
        """
        변동성 압축 감지

        Args:
            symbol: 심볼
            interval: 캔들 인터벌 (기본 1시간)

        Returns:
            SqueezeResult
        """
        cm = self._candle_manager

        # BB Width 백분위 계산
        bb_percentile = cm.calc_bb_width_percentile(
            symbol, interval, period=20, lookback=50
        )
        if bb_percentile is None:
            bb_percentile = 50.0  # 데이터 부족시 중립

        # ATR% 계산 및 추세 판단
        atr_pct = cm.calc_atr_percent(symbol, interval, period=14)
        if atr_pct is None:
            atr_pct = 0.02  # 기본 2%

        atr_trend = self._calc_atr_trend(symbol, interval)

        # 압축 지속 봉 수 계산
        squeeze_duration = self._calc_squeeze_duration(symbol, interval)

        # 압축 여부 판단
        is_squeeze = (
            bb_percentile <= self.BB_WIDTH_PERCENTILE_THRESHOLD
            and atr_trend in ["FALLING", "FLAT"]
        )

        # 압축 점수 계산 (0-100)
        squeeze_score = self._calc_squeeze_score(
            bb_percentile, atr_pct, atr_trend, squeeze_duration
        )

        return SqueezeResult(
            is_squeeze=is_squeeze,
            bb_width_percentile=bb_percentile,
            atr_pct_current=atr_pct,
            atr_pct_trend=atr_trend,
            squeeze_duration_bars=squeeze_duration,
            squeeze_score=squeeze_score,
        )

    def _calc_atr_trend(self, symbol: str, interval: str) -> str:
        """ATR% 추세 계산 (최근 5봉)"""
        cm = self._candle_manager
        candles = cm.get_candles(symbol, interval, 10)

        if len(candles) < 6:
            return "FLAT"

        # 최근 5봉의 ATR% 계산
        atr_values = []
        for i in range(-5, 0):
            if i + 1 >= 0:
                break
            subset = candles[: len(candles) + i + 1]
            if len(subset) < 3:
                continue

            # 간단한 TR 계산
            tr = max(
                candles[i].high - candles[i].low,
                abs(candles[i].high - candles[i - 1].close),
                abs(candles[i].low - candles[i - 1].close),
            )
            atr_pct = tr / candles[i].close if candles[i].close > 0 else 0
            atr_values.append(atr_pct)

        if len(atr_values) < 3:
            return "FLAT"

        # 추세 판단 (처음과 마지막 비교)
        first_avg = sum(atr_values[:2]) / 2
        last_avg = sum(atr_values[-2:]) / 2

        if first_avg <= 0:
            return "FLAT"

        change = (last_avg - first_avg) / first_avg

        if change <= self.ATR_FALLING_THRESHOLD:
            return "FALLING"
        elif change >= abs(self.ATR_FALLING_THRESHOLD):
            return "RISING"
        return "FLAT"

    def _calc_squeeze_duration(self, symbol: str, interval: str) -> int:
        """압축 상태 지속 봉 수"""
        cm = self._candle_manager
        candles = cm.get_candles(symbol, interval, 20)

        if len(candles) < 10:
            return 0

        # 최근부터 거꾸로 검사
        duration = 0
        for i in range(len(candles) - 1, 4, -1):
            # 최근 5봉의 고저폭 평균
            recent_range = sum(
                c.high - c.low for c in candles[i - 4 : i + 1]
            ) / 5

            # 이전 5봉의 고저폭 평균
            prev_range = sum(
                c.high - c.low for c in candles[i - 9 : i - 4]
            ) / 5 if i >= 9 else recent_range

            # 고저폭이 이전보다 작으면 압축 지속
            if recent_range < prev_range:
                duration += 1
            else:
                break

        return duration

    def _calc_squeeze_score(
        self,
        bb_percentile: float,
        atr_pct: float,
        atr_trend: str,
        squeeze_duration: int,
    ) -> float:
        """
        압축 점수 계산 (0-100)

        높을수록 강한 압축 (급등 가능성 높음)
        """
        score = 0

        # BB Width 백분위 (40점)
        if bb_percentile <= 10:
            score += 40
        elif bb_percentile <= 20:
            score += 30
        elif bb_percentile <= 30:
            score += 20
        elif bb_percentile <= 40:
            score += 10

        # ATR 추세 (30점)
        if atr_trend == "FALLING":
            score += 30
        elif atr_trend == "FLAT":
            score += 15

        # 압축 지속 시간 (30점)
        duration_score = min(squeeze_duration * 5, 30)
        score += duration_score

        return score


# 싱글톤
_volatility_squeeze_detector: Optional[VolatilitySqueezeDetector] = None


def get_volatility_squeeze_detector() -> VolatilitySqueezeDetector:
    """VolatilitySqueezeDetector 싱글톤"""
    global _volatility_squeeze_detector
    if _volatility_squeeze_detector is None:
        _volatility_squeeze_detector = VolatilitySqueezeDetector()
    return _volatility_squeeze_detector
