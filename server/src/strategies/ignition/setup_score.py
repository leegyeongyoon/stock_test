"""
Setup Score Calculator - 5대 전조 패턴 점수화

S1: Volatility Contraction (변동성 수축) - 0~20점
S2: Volume Dry-up (거래량 건조) - 0~20점
S3: Accumulation Structure (매집 구조) - 0~25점
S4: Relative Strength (상대강도) - 0~20점
S5: Pressure to Range High (레인지 압박) - 0~15점

총점: 0~100점
Watchlist 진입 기준: 70점 이상
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from src.config import get_settings
from src.data.candle_manager import CandleManager, get_candle_manager

logger = structlog.get_logger()


@dataclass
class SetupScoreComponent:
    """개별 Setup 점수 컴포넌트"""

    name: str
    raw_value: float
    normalized_score: float  # 0~1
    weighted_score: float  # 가중치 적용 후
    max_score: float
    description: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "raw_value": round(self.raw_value, 4),
            "normalized_score": round(self.normalized_score, 4),
            "weighted_score": round(self.weighted_score, 2),
            "max_score": self.max_score,
            "description": self.description,
        }


@dataclass
class SetupScoreResult:
    """Setup Score 계산 결과"""

    symbol: str
    total_score: float
    passes_threshold: bool
    components: list[SetupScoreComponent]
    range_high: float
    range_low: float
    atr_1h: float
    current_price: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_score": round(self.total_score, 2),
            "passes_threshold": self.passes_threshold,
            "components": [c.to_dict() for c in self.components],
            "range_high": round(self.range_high, 2),
            "range_low": round(self.range_low, 2),
            "atr_1h": round(self.atr_1h, 4),
            "current_price": round(self.current_price, 2),
            "timestamp": self.timestamp.isoformat(),
        }


class SetupScoreCalculator:
    """
    Setup Score Calculator

    5대 전조 패턴을 분석해서 급등 가능성 점수화
    15m/1h 타임프레임 기반
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._candle_manager = get_candle_manager()

    def calculate(
        self,
        symbol: str,
        market_data: dict,
        btc_change_24h: float = 0.0,
    ) -> SetupScoreResult:
        """
        Setup Score 계산

        Args:
            symbol: 심볼 (예: KRW-BTC)
            market_data: 시장 데이터
            btc_change_24h: BTC 24시간 변화율 (상대강도 계산용)
        """
        settings = self._settings
        cm = self._candle_manager

        components: list[SetupScoreComponent] = []

        # 기본 데이터 추출
        current_price = market_data.get("price", 0)
        change_24h = market_data.get("change_rate", 0)

        # 레인지 계산 (1h 기준 24봉 = 24시간)
        range_high, range_low = cm.calc_range_high_low(
            symbol, CandleManager.INTERVAL_1H, period=24
        )
        range_high = range_high or current_price * 1.01
        range_low = range_low or current_price * 0.99

        # ATR 계산 (1h 기준)
        atr_1h = cm.calc_atr(symbol, CandleManager.INTERVAL_1H, period=14)
        atr_1h = atr_1h or current_price * 0.01  # 기본값 1%

        # S1: Volatility Contraction (변동성 수축)
        s1 = self._calc_s1_volatility_contraction(symbol)
        components.append(s1)

        # S2: Volume Dry-up (거래량 건조)
        s2 = self._calc_s2_volume_dryup(symbol)
        components.append(s2)

        # S3: Accumulation Structure (매집 구조)
        s3 = self._calc_s3_accumulation(symbol)
        components.append(s3)

        # S4: Relative Strength (상대강도)
        s4 = self._calc_s4_relative_strength(symbol, change_24h, btc_change_24h)
        components.append(s4)

        # S5: Pressure to Range High (레인지 압박)
        s5 = self._calc_s5_range_pressure(symbol, current_price, range_high, range_low)
        components.append(s5)

        # 총점 계산
        total_score = sum(c.weighted_score for c in components)
        passes_threshold = total_score >= settings.ignition_setup_min_score

        return SetupScoreResult(
            symbol=symbol,
            total_score=total_score,
            passes_threshold=passes_threshold,
            components=components,
            range_high=range_high,
            range_low=range_low,
            atr_1h=atr_1h,
            current_price=current_price,
        )

    def _calc_s1_volatility_contraction(self, symbol: str) -> SetupScoreComponent:
        """
        S1: 변동성 수축 (Volatility Contraction)

        BB Width Percentile이 낮을수록 수축 상태
        낮을수록 높은 점수
        """
        settings = self._settings
        cm = self._candle_manager
        max_score = settings.ignition_s1_weight

        # 1h 기준 BB Width Percentile
        bb_pct = cm.calc_bb_width_percentile(
            symbol, CandleManager.INTERVAL_1H, period=20, lookback=50
        )

        if bb_pct is None:
            return SetupScoreComponent(
                name="S1_VolatilityContraction",
                raw_value=100.0,
                normalized_score=0.0,
                weighted_score=0.0,
                max_score=max_score,
                description="데이터 부족",
            )

        # 낮을수록 높은 점수 (20% 이하면 만점)
        threshold = settings.ignition_bb_width_pct_max
        if bb_pct <= threshold:
            normalized = 1.0
        elif bb_pct >= 80:
            normalized = 0.0
        else:
            # threshold ~ 80 사이 선형 감소
            normalized = 1.0 - (bb_pct - threshold) / (80 - threshold)

        weighted = normalized * max_score

        return SetupScoreComponent(
            name="S1_VolatilityContraction",
            raw_value=bb_pct,
            normalized_score=normalized,
            weighted_score=weighted,
            max_score=max_score,
            description=f"BB Width Percentile: {bb_pct:.1f}%",
        )

    def _calc_s2_volume_dryup(self, symbol: str) -> SetupScoreComponent:
        """
        S2: 거래량 건조 (Volume Dry-up)

        최근 거래량이 평균 대비 낮으면서 가격이 유지되는 상태
        """
        settings = self._settings
        cm = self._candle_manager
        max_score = settings.ignition_s2_weight

        # 최근 5봉 평균 vs 20봉 평균
        volumes = cm.get_volumes(symbol, CandleManager.INTERVAL_1H, 20)

        if len(volumes) < 20:
            return SetupScoreComponent(
                name="S2_VolumeDryup",
                raw_value=1.0,
                normalized_score=0.0,
                weighted_score=0.0,
                max_score=max_score,
                description="데이터 부족",
            )

        recent_avg = sum(volumes[-5:]) / 5
        historical_avg = sum(volumes[:-5]) / len(volumes[:-5])

        if historical_avg <= 0:
            return SetupScoreComponent(
                name="S2_VolumeDryup",
                raw_value=1.0,
                normalized_score=0.0,
                weighted_score=0.0,
                max_score=max_score,
                description="거래량 0",
            )

        volume_ratio = recent_avg / historical_avg
        threshold = settings.ignition_volume_dryup_ratio

        # OBV 추세 확인 (건조하면서 OBV가 상승/플랫이면 좋음)
        obv_trend = cm.calc_obv_trend(symbol, CandleManager.INTERVAL_1H, period=20)

        # 거래량이 낮을수록 좋지만, 너무 낮으면 유동성 문제
        if volume_ratio <= threshold:
            if obv_trend in ["UP", "FLAT"]:
                normalized = 1.0
            else:
                normalized = 0.7  # OBV 하락이면 감점
        elif volume_ratio >= 1.0:
            normalized = 0.0
        else:
            normalized = 1.0 - (volume_ratio - threshold) / (1.0 - threshold)
            if obv_trend == "DOWN":
                normalized *= 0.7

        weighted = normalized * max_score

        return SetupScoreComponent(
            name="S2_VolumeDryup",
            raw_value=volume_ratio,
            normalized_score=normalized,
            weighted_score=weighted,
            max_score=max_score,
            description=f"Vol Ratio: {volume_ratio:.2f}, OBV: {obv_trend}",
        )

    def _calc_s3_accumulation(self, symbol: str) -> SetupScoreComponent:
        """
        S3: 매집 구조 (Accumulation Structure)

        - Higher Lows 패턴
        - 평균 Close Position (캔들 상단 마감)
        - OBV 상승
        """
        settings = self._settings
        cm = self._candle_manager
        max_score = settings.ignition_s3_weight

        # Higher Lows 카운트
        hl_count = cm.calc_higher_lows_count(
            symbol, CandleManager.INTERVAL_1H, period=10
        )
        hl_min = settings.ignition_higher_lows_min

        # 평균 Close Position
        avg_close_pos = cm.calc_avg_close_position(
            symbol, CandleManager.INTERVAL_1H, period=10
        )
        avg_close_pos = avg_close_pos or 0.5
        cp_min = settings.ignition_avg_close_pos_min

        # OBV 추세
        obv_trend = cm.calc_obv_trend(symbol, CandleManager.INTERVAL_1H, period=20)

        # 각 항목 점수화
        hl_score = min(hl_count / hl_min, 1.0) if hl_min > 0 else 0.0
        cp_score = (
            min((avg_close_pos - 0.5) / (cp_min - 0.5), 1.0)
            if avg_close_pos >= 0.5
            else 0.0
        )
        obv_score = 1.0 if obv_trend == "UP" else (0.5 if obv_trend == "FLAT" else 0.0)

        # 가중 평균 (Higher Lows 40%, Close Pos 35%, OBV 25%)
        normalized = hl_score * 0.40 + cp_score * 0.35 + obv_score * 0.25
        weighted = normalized * max_score

        return SetupScoreComponent(
            name="S3_Accumulation",
            raw_value=avg_close_pos,
            normalized_score=normalized,
            weighted_score=weighted,
            max_score=max_score,
            description=f"HL:{hl_count}, CP:{avg_close_pos:.2f}, OBV:{obv_trend}",
        )

    def _calc_s4_relative_strength(
        self,
        symbol: str,
        change_24h: float,
        btc_change_24h: float,
    ) -> SetupScoreComponent:
        """
        S4: 상대강도 (Relative Strength)

        BTC 대비 상대 성과
        """
        settings = self._settings
        max_score = settings.ignition_s4_weight

        # 상대강도 = 종목 변화율 - BTC 변화율
        relative_strength = change_24h - btc_change_24h

        # -5% ~ +10% 범위에서 정규화
        if relative_strength >= 0.10:
            normalized = 1.0
        elif relative_strength <= -0.05:
            normalized = 0.0
        else:
            # -5% ~ +10% 선형 매핑
            normalized = (relative_strength + 0.05) / 0.15

        weighted = normalized * max_score

        return SetupScoreComponent(
            name="S4_RelativeStrength",
            raw_value=relative_strength,
            normalized_score=normalized,
            weighted_score=weighted,
            max_score=max_score,
            description=f"RS: {relative_strength*100:.2f}% (vs BTC)",
        )

    def _calc_s5_range_pressure(
        self,
        symbol: str,
        current_price: float,
        range_high: float,
        range_low: float,
    ) -> SetupScoreComponent:
        """
        S5: 레인지 압박 (Pressure to Range High)

        - 현재가가 레인지 상단 근처
        - 레인지 상단 터치 횟수
        """
        settings = self._settings
        cm = self._candle_manager
        max_score = settings.ignition_s5_weight

        # 레인지 내 위치 (0~1)
        range_hl = range_high - range_low
        if range_hl <= 0:
            range_position = 0.5
        else:
            range_position = (current_price - range_low) / range_hl

        pos_min = settings.ignition_range_position_min

        # 레인지 상단 터치 횟수
        touch_count = cm.calc_range_touch_count(
            symbol, CandleManager.INTERVAL_1H, period=24, threshold=0.02
        )
        touch_min = settings.ignition_range_touch_min

        # 점수화
        pos_score = (
            min((range_position - 0.5) / (pos_min - 0.5), 1.0)
            if range_position >= 0.5
            else 0.0
        )
        touch_score = min(touch_count / touch_min, 1.0) if touch_min > 0 else 0.0

        # 가중 평균 (Position 60%, Touch 40%)
        normalized = pos_score * 0.60 + touch_score * 0.40
        weighted = normalized * max_score

        return SetupScoreComponent(
            name="S5_RangePressure",
            raw_value=range_position,
            normalized_score=normalized,
            weighted_score=weighted,
            max_score=max_score,
            description=f"Pos:{range_position:.2f}, Touch:{touch_count}",
        )


# 싱글톤 인스턴스
_setup_score_calculator: Optional[SetupScoreCalculator] = None


def get_setup_score_calculator() -> SetupScoreCalculator:
    """Setup Score Calculator 싱글톤"""
    global _setup_score_calculator
    if _setup_score_calculator is None:
        _setup_score_calculator = SetupScoreCalculator()
    return _setup_score_calculator
