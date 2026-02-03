"""
거래량 건조 + 첫 팽창 감지 (Volume Dry-up → First Expansion)

급등 직전 공통 특징:
- "이미 폭증"이 아니라
- 평균 대비 '처음으로' 기준을 넘는 순간이 중요

패턴:
1. 거래량 < 평균의 50%가 5봉 이상 지속 (건조)
2. 이후 첫 150%+ 팽창시 신호 (첫 팽창)
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.config import get_settings
from src.data.candle_manager import CandleManager, get_candle_manager

logger = structlog.get_logger()


@dataclass
class DryupResult:
    """거래량 건조 감지 결과"""

    is_dryup: bool  # 현재 건조 상태
    dryup_duration_bars: int  # 건조 지속 봉 수
    current_vol_ratio: float  # 현재/평균 비율
    avg_vol_lookback: float  # 참조 평균 거래량
    first_expansion_detected: bool  # 첫 팽창 감지
    expansion_ratio: float  # 팽창 비율 (현재/건조기간평균)
    dryup_score: float  # 건조 점수 (0-100)


class VolumeDryupDetector:
    """
    거래량 건조 + 첫 팽창 감지기

    급등 직전 패턴:
    - 거래량 < 평균의 50%가 N봉 이상 지속
    - 이후 첫 150%+ 팽창시 신호
    """

    # 임계값
    DRYUP_RATIO_THRESHOLD = 0.5  # 평균의 50% 이하면 건조
    DRYUP_MIN_DURATION = 5  # 최소 5봉 건조 지속
    EXPANSION_RATIO_THRESHOLD = 1.5  # 건조기간 대비 150% 이상이면 첫 팽창

    def __init__(self) -> None:
        self._settings = get_settings()
        self._candle_manager = get_candle_manager()

    def detect(
        self,
        symbol: str,
        interval: str = "1h",
    ) -> DryupResult:
        """
        거래량 건조 + 첫 팽창 감지

        Args:
            symbol: 심볼
            interval: 캔들 인터벌 (기본 1시간)

        Returns:
            DryupResult
        """
        cm = self._candle_manager

        # 캔들 조회
        candles = cm.get_candles(symbol, interval, 30)
        if len(candles) < 15:
            return self._empty_result()

        volumes = [c.quote_volume for c in candles]

        # 평균 거래량 (최근 20봉, 마지막 5봉 제외)
        if len(volumes) >= 25:
            avg_vol = sum(volumes[-25:-5]) / 20
        else:
            avg_vol = sum(volumes[:-5]) / max(len(volumes) - 5, 1)

        if avg_vol <= 0:
            return self._empty_result()

        # 현재 거래량 비율
        current_vol = volumes[-1]
        current_ratio = current_vol / avg_vol

        # 건조 기간 계산 (마지막 봉 제외하고 역순으로)
        dryup_duration = 0
        dryup_volumes = []

        for i in range(-2, -min(len(volumes), 15), -1):
            ratio = volumes[i] / avg_vol
            if ratio <= self.DRYUP_RATIO_THRESHOLD:
                dryup_duration += 1
                dryup_volumes.append(volumes[i])
            else:
                break

        # 건조 상태 여부
        is_dryup = dryup_duration >= self.DRYUP_MIN_DURATION

        # 첫 팽창 감지
        first_expansion = False
        expansion_ratio = 0

        if is_dryup and dryup_volumes:
            dryup_avg = sum(dryup_volumes) / len(dryup_volumes)
            if dryup_avg > 0:
                expansion_ratio = current_vol / dryup_avg
                first_expansion = expansion_ratio >= self.EXPANSION_RATIO_THRESHOLD

        # 건조 점수 계산
        dryup_score = self._calc_dryup_score(
            is_dryup, dryup_duration, current_ratio, expansion_ratio
        )

        return DryupResult(
            is_dryup=is_dryup,
            dryup_duration_bars=dryup_duration,
            current_vol_ratio=current_ratio,
            avg_vol_lookback=avg_vol,
            first_expansion_detected=first_expansion,
            expansion_ratio=expansion_ratio,
            dryup_score=dryup_score,
        )

    def detect_with_1m(
        self,
        symbol: str,
        candles_1m: list,
    ) -> DryupResult:
        """
        1분봉 기반 거래량 건조 + 첫 팽창 감지

        실시간 스캐닝용
        """
        if len(candles_1m) < 15:
            return self._empty_result()

        volumes = [c.volume for c in candles_1m]

        # 평균 거래량 (최근 봉 제외)
        avg_vol = sum(volumes[:-5]) / max(len(volumes) - 5, 1)
        if avg_vol <= 0:
            return self._empty_result()

        current_vol = volumes[-1]
        current_ratio = current_vol / avg_vol

        # 건조 기간 계산
        dryup_duration = 0
        dryup_volumes = []

        for i in range(-2, -min(len(volumes), 10), -1):
            ratio = volumes[i] / avg_vol
            if ratio <= self.DRYUP_RATIO_THRESHOLD:
                dryup_duration += 1
                dryup_volumes.append(volumes[i])
            else:
                break

        is_dryup = dryup_duration >= 3  # 1분봉은 3봉 이상

        # 첫 팽창 감지
        first_expansion = False
        expansion_ratio = 0

        if is_dryup and dryup_volumes:
            dryup_avg = sum(dryup_volumes) / len(dryup_volumes)
            if dryup_avg > 0:
                expansion_ratio = current_vol / dryup_avg
                first_expansion = expansion_ratio >= self.EXPANSION_RATIO_THRESHOLD

        dryup_score = self._calc_dryup_score(
            is_dryup, dryup_duration, current_ratio, expansion_ratio
        )

        return DryupResult(
            is_dryup=is_dryup,
            dryup_duration_bars=dryup_duration,
            current_vol_ratio=current_ratio,
            avg_vol_lookback=avg_vol,
            first_expansion_detected=first_expansion,
            expansion_ratio=expansion_ratio,
            dryup_score=dryup_score,
        )

    def _calc_dryup_score(
        self,
        is_dryup: bool,
        dryup_duration: int,
        current_ratio: float,
        expansion_ratio: float,
    ) -> float:
        """
        건조 점수 계산 (0-100)

        높을수록 "폭풍 전의 고요함" 상태
        """
        if not is_dryup:
            return 0

        score = 0

        # 건조 지속 시간 (40점)
        duration_score = min(dryup_duration * 6, 40)
        score += duration_score

        # 첫 팽창 (30점)
        if expansion_ratio >= 2.0:
            score += 30
        elif expansion_ratio >= 1.5:
            score += 20
        elif expansion_ratio >= 1.2:
            score += 10

        # 현재 거래량 수준 (30점)
        # 건조 중이면서 아직 폭등 전이 가장 좋음
        if 0.3 <= current_ratio <= 0.7:
            score += 30  # 아직 건조 상태
        elif current_ratio <= 0.3:
            score += 20  # 너무 건조
        elif current_ratio <= 1.0:
            score += 15  # 평균 수준

        return score

    def _empty_result(self) -> DryupResult:
        """빈 결과 반환"""
        return DryupResult(
            is_dryup=False,
            dryup_duration_bars=0,
            current_vol_ratio=0,
            avg_vol_lookback=0,
            first_expansion_detected=False,
            expansion_ratio=0,
            dryup_score=0,
        )


# 싱글톤
_volume_dryup_detector: Optional[VolumeDryupDetector] = None


def get_volume_dryup_detector() -> VolumeDryupDetector:
    """VolumeDryupDetector 싱글톤"""
    global _volume_dryup_detector
    if _volume_dryup_detector is None:
        _volume_dryup_detector = VolumeDryupDetector()
    return _volume_dryup_detector
