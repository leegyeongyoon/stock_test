"""Rebound Score Calculator - 반등 스캘핑 점수 계산기

v5.0: 반등 확인 강화 + 게이팅 조건 추가

Rebound Score 구성 (0~100점):
1. Support Proximity (0~20): VWAP/SMA20/BB하단 근접도
2. RSI Oversold (0~20): RSI 30 이하 과매도 영역
3. Orderbook Imbalance (0~20): 매수호가 우세 구조
4. Reversal Pattern (0~30): 반등 패턴 (양봉, 연속 양봉, 모멘텀) - 핵심
5. Volatility Score (0~10): 적정 변동성

핵심 변경:
- Reversal Pattern 가중치 20→30 (반등 확인이 핵심)
- 연속 양봉 패턴 추가 (0~8점)
- Reversal Pattern < 8점이면 진입 금지 (게이팅)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class ReboundScoreComponent:
    """점수 구성요소"""

    name: str
    score: float
    max_score: float
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "score": self.score,
            "max_score": self.max_score,
            "details": self.details,
        }


@dataclass
class ReboundScoreResult:
    """Rebound Score 결과"""

    symbol: str
    total_score: float  # 0~100
    level: int  # 0, 1, 2, 3
    target_allocation: float  # 0.0, 0.03, 0.05, 0.08
    components: list[ReboundScoreComponent]
    entry_price_target: float  # 권장 진입가
    stop_loss_price: float  # 손절가
    support_distance_pct: float  # 지지선까지 거리
    rsi: float  # 현재 RSI
    orderbook_ratio: float  # 매수/매도 호가 비율
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_score": self.total_score,
            "level": self.level,
            "target_allocation": self.target_allocation,
            "entry_price_target": self.entry_price_target,
            "stop_loss_price": self.stop_loss_price,
            "support_distance_pct": self.support_distance_pct,
            "rsi": self.rsi,
            "orderbook_ratio": self.orderbook_ratio,
            "components": [c.to_dict() for c in self.components],
            "timestamp": self.timestamp.isoformat(),
        }


class ReboundScoreCalculator:
    """
    Rebound Score 계산기 v5.0 - 반등 매수 적합성 평가

    점수 구성:
    1. Support Proximity (0~20): 지지선 근접도
    2. RSI Oversold (0~20): 과매도 영역
    3. Orderbook Imbalance (0~20): 매수호가 우세
    4. Reversal Pattern (0~30): 반등 패턴 (핵심, 게이팅 적용)
    5. Volatility Score (0~10): 적정 변동성
    """

    def __init__(self):
        # 레벨별 임계값
        self.score_l1 = getattr(settings, "rebound_score_l1", 55)
        self.score_l2 = getattr(settings, "rebound_score_l2", 70)
        self.score_l3 = getattr(settings, "rebound_score_l3", 85)

        # 레벨별 배분 (소액 분산)
        self.alloc_l1 = getattr(settings, "rebound_alloc_l1", 0.03)  # 3%
        self.alloc_l2 = getattr(settings, "rebound_alloc_l2", 0.05)  # 5%
        self.alloc_l3 = getattr(settings, "rebound_alloc_l3", 0.08)  # 8%

        # RSI 설정
        self.rsi_oversold = getattr(settings, "rebound_rsi_oversold", 30.0)
        self.rsi_confirm = getattr(settings, "rebound_rsi_confirm", 40.0)

        # Orderbook 설정
        self.orderbook_imbalance_threshold = getattr(
            settings, "rebound_orderbook_imbalance_threshold", 1.5
        )

    def calculate(self, market_data: dict) -> ReboundScoreResult:
        """
        Rebound Score 계산

        Args:
            market_data: 시장 데이터
                - symbol: str
                - price: float (현재가)
                - vwap: float (VWAP)
                - sma_20: float (20 SMA, 5분봉 기준)
                - bb_lower: float (볼린저밴드 하단)
                - rsi: float (RSI 14)
                - atr: float (ATR)
                - bid_volume: float (매수 호가 잔량)
                - ask_volume: float (매도 호가 잔량)
                - recent_candles: list (최근 캔들)
                - change_1m: float (1분 변화율)
                - change_5m: float (5분 변화율)

        Returns:
            ReboundScoreResult: 반등 점수 결과
        """
        symbol = market_data.get("symbol", "UNKNOWN")
        price = market_data.get("price", 0)
        components = []

        if price <= 0:
            return self._empty_result(symbol)

        # 1. Support Proximity (0~20) - 지지선 근접도
        support_comp = self._calc_support_proximity(market_data)
        components.append(support_comp)

        # 2. RSI Oversold (0~20) - 과매도 영역
        rsi_comp = self._calc_rsi_oversold(market_data)
        components.append(rsi_comp)

        # 3. Orderbook Imbalance (0~20) - 매수호가 우세
        orderbook_comp = self._calc_orderbook_imbalance(market_data)
        components.append(orderbook_comp)

        # 4. Reversal Pattern (0~30) - 반등 패턴 (핵심)
        reversal_comp = self._calc_reversal_pattern(market_data)
        components.append(reversal_comp)

        # 5. Volatility Score (0~10) - 적정 변동성
        volatility_comp = self._calc_volatility_score(market_data)
        components.append(volatility_comp)

        # 반등 확인 게이팅: Reversal < 8점이면 진입 금지
        if reversal_comp.score < 8:
            return ReboundScoreResult(
                symbol=symbol,
                total_score=0,
                level=0,
                target_allocation=0,
                components=components,
                entry_price_target=price,
                stop_loss_price=self._calc_stop_loss(market_data, price),
                support_distance_pct=support_comp.details.get("min_distance_pct", 0),
                rsi=market_data.get("rsi", 50),
                orderbook_ratio=orderbook_comp.details.get("bid_ask_ratio", 1.0),
            )

        # 총점 계산
        total_score = sum(c.score for c in components)
        total_score = max(0, min(100, total_score))

        # 레벨 및 배분 결정
        level = self._determine_level(total_score)
        target_alloc = self._get_target_allocation(level)

        # 진입가/손절가 계산
        entry_price = price  # 현재가 진입
        stop_loss = self._calc_stop_loss(market_data, entry_price)

        # 추가 정보
        support_distance = support_comp.details.get("min_distance_pct", 0)
        rsi = market_data.get("rsi", 50)
        orderbook_ratio = orderbook_comp.details.get("bid_ask_ratio", 1.0)

        result = ReboundScoreResult(
            symbol=symbol,
            total_score=total_score,
            level=level,
            target_allocation=target_alloc,
            components=components,
            entry_price_target=entry_price,
            stop_loss_price=stop_loss,
            support_distance_pct=support_distance,
            rsi=rsi,
            orderbook_ratio=orderbook_ratio,
        )

        if level > 0:
            logger.info(
                "Rebound score calculated",
                symbol=symbol,
                total_score=f"{total_score:.1f}",
                level=level,
                target_alloc=f"{target_alloc:.0%}",
                rsi=f"{rsi:.1f}",
            )

        return result

    def _calc_support_proximity(self, market_data: dict) -> ReboundScoreComponent:
        """
        Support Proximity (0~20) - 지지선 근접도

        지지선:
        - VWAP (거래량 가중 평균)
        - 20 SMA (5분봉)
        - 볼린저밴드 하단
        - 24h 저점
        """
        price = market_data.get("price", 0)
        vwap = market_data.get("vwap", 0)
        sma_20 = market_data.get("sma_20", 0)
        bb_lower = market_data.get("bb_lower", 0)
        lowest_24h = market_data.get("lowest_24h", 0)

        score = 0.0
        details = {}

        if price <= 0:
            return ReboundScoreComponent(
                name="Support Proximity",
                score=0,
                max_score=20,
                details={"error": "Invalid price"},
            )

        distances = []

        # VWAP 대비 거리 (0~6점)
        if vwap > 0:
            vwap_dist = (price - vwap) / vwap
            details["vwap_distance"] = vwap_dist
            distances.append(abs(vwap_dist))

            if vwap_dist <= -0.02:
                score += 6
            elif vwap_dist <= 0:
                score += 4
            elif vwap_dist <= 0.01:
                score += 3
            elif vwap_dist <= 0.02:
                score += 1

        # SMA20 대비 거리 (0~5점)
        if sma_20 > 0:
            sma_dist = (price - sma_20) / sma_20
            details["sma20_distance"] = sma_dist
            distances.append(abs(sma_dist))

            if sma_dist <= -0.02:
                score += 5
            elif sma_dist <= 0:
                score += 4
            elif sma_dist <= 0.01:
                score += 2
            elif sma_dist <= 0.02:
                score += 1

        # 볼린저밴드 하단 대비 거리 (0~5점)
        if bb_lower > 0:
            bb_dist = (price - bb_lower) / bb_lower
            details["bb_lower_distance"] = bb_dist
            distances.append(abs(bb_dist))

            if bb_dist <= 0:
                score += 5
            elif bb_dist <= 0.01:
                score += 3
            elif bb_dist <= 0.02:
                score += 1

        # 24h 저점 대비 (0~4점)
        if lowest_24h > 0:
            low_dist = (price - lowest_24h) / lowest_24h
            details["lowest_24h_distance"] = low_dist

            if low_dist <= 0.01:
                score += 4
            elif low_dist <= 0.03:
                score += 2

        # 최소 거리 기록
        details["min_distance_pct"] = min(distances) if distances else 1.0

        return ReboundScoreComponent(
            name="Support Proximity",
            score=min(20, score),
            max_score=20,
            details=details,
        )

    def _calc_rsi_oversold(self, market_data: dict) -> ReboundScoreComponent:
        """
        RSI Oversold (0~20) - 과매도 영역

        RSI 30 이하: 과매도 (만점)
        RSI 30~40: 확인 영역 (부분점수)
        RSI 40+: 중립~과매수 (낮은 점수)
        """
        rsi = market_data.get("rsi", 50)

        score = 0.0
        details = {"rsi": rsi}

        if rsi <= 20:
            # 극단적 과매도
            score = 20
            details["zone"] = "EXTREME_OVERSOLD"
        elif rsi <= 30:
            # 과매도
            score = 18
            details["zone"] = "OVERSOLD"
        elif rsi <= 35:
            # 과매도 근접
            score = 14
            details["zone"] = "NEAR_OVERSOLD"
        elif rsi <= 40:
            # 확인 영역
            score = 10
            details["zone"] = "CONFIRM_ZONE"
        elif rsi <= 45:
            # 중립 하단
            score = 6
            details["zone"] = "NEUTRAL_LOW"
        elif rsi <= 50:
            # 중립
            score = 3
            details["zone"] = "NEUTRAL"
        else:
            # 과매수 영역 (반등 매수 부적합)
            score = 0
            details["zone"] = "OVERBOUGHT"

        return ReboundScoreComponent(
            name="RSI Oversold",
            score=score,
            max_score=20,
            details=details,
        )

    def _calc_orderbook_imbalance(self, market_data: dict) -> ReboundScoreComponent:
        """
        Orderbook Imbalance (0~20) - 매수호가 우세 구조

        매수호가 잔량이 매도호가보다 많으면 지지력 확인
        """
        bid_volume = market_data.get("bid_volume", 0)
        ask_volume = market_data.get("ask_volume", 0)

        score = 0.0
        details = {
            "bid_volume": bid_volume,
            "ask_volume": ask_volume,
        }

        if ask_volume <= 0:
            details["bid_ask_ratio"] = 1.0
            return ReboundScoreComponent(
                name="Orderbook Imbalance",
                score=5,  # 기본 점수
                max_score=20,
                details=details,
            )

        ratio = bid_volume / ask_volume
        details["bid_ask_ratio"] = ratio

        if ratio >= 3.0:
            # 매수 압도적 우세
            score = 20
            details["imbalance"] = "STRONG_BID"
        elif ratio >= 2.0:
            # 매수 강세
            score = 16
            details["imbalance"] = "BID_DOMINANT"
        elif ratio >= 1.5:
            # 매수 우세
            score = 12
            details["imbalance"] = "BID_FAVOR"
        elif ratio >= 1.2:
            # 약간 매수 우세
            score = 8
            details["imbalance"] = "SLIGHT_BID"
        elif ratio >= 1.0:
            # 균형
            score = 5
            details["imbalance"] = "BALANCED"
        elif ratio >= 0.8:
            # 약간 매도 우세 (경고)
            score = 2
            details["imbalance"] = "SLIGHT_ASK"
        else:
            # 매도 우세 (반등 어려움)
            score = 0
            details["imbalance"] = "ASK_DOMINANT"

        return ReboundScoreComponent(
            name="Orderbook Imbalance",
            score=score,
            max_score=20,
            details=details,
        )

    def _calc_reversal_pattern(self, market_data: dict) -> ReboundScoreComponent:
        """
        Reversal Pattern (0~30) - 반등 패턴 (핵심 컴포넌트)

        확인 지표:
        - 1분봉 양봉 (0~8점)
        - 5분봉 반등 모멘텀 (0~8점)
        - 연속 양봉 패턴 (0~8점) - 신규
        - RVOL 확인 (0~3점)
        - Close Position (0~3점)

        게이팅: 이 점수가 8점 미만이면 calculate()에서 진입 차단
        """
        change_1m = market_data.get("change_1m", 0)
        change_5m = market_data.get("change_5m", 0)
        rvol = market_data.get("rvol", 1.0)
        close_pos = market_data.get("close_pos", 0.5)
        recent_candles = market_data.get("candles_5m", [])

        score = 0.0
        details = {
            "change_1m": change_1m,
            "change_5m": change_5m,
            "rvol": rvol,
            "close_pos": close_pos,
        }

        # 1분봉 양봉 (0~8점)
        if change_1m > 0.005:
            score += 8
            details["candle_1m"] = "STRONG_GREEN"
        elif change_1m > 0.002:
            score += 6
            details["candle_1m"] = "GREEN"
        elif change_1m > 0:
            score += 3
            details["candle_1m"] = "SLIGHT_GREEN"
        elif change_1m > -0.002:
            score += 1
            details["candle_1m"] = "FLAT"
        else:
            details["candle_1m"] = "RED"

        # 5분봉 반등 모멘텀 (0~8점)
        if change_5m > 0.01:
            score += 8
            details["momentum_5m"] = "STRONG_REVERSAL"
        elif change_5m > 0.005:
            score += 6
            details["momentum_5m"] = "REVERSAL"
        elif change_5m > 0:
            score += 3
            details["momentum_5m"] = "TURNING"
        elif change_5m > -0.005:
            score += 1
            details["momentum_5m"] = "SLOWING"
        else:
            details["momentum_5m"] = "FALLING"

        # 연속 양봉 패턴 (0~8점) - 최근 5분봉 3개 중 양봉 카운트
        bullish_count = 0
        if recent_candles and len(recent_candles) >= 3:
            for c in recent_candles[-3:]:
                if isinstance(c, dict):
                    c_close = c.get("close", 0)
                    c_open = c.get("open", 0)
                else:
                    c_close = getattr(c, "close", 0)
                    c_open = getattr(c, "open", 0)
                if c_close > c_open:
                    bullish_count += 1

            if bullish_count >= 3:
                score += 8
                details["consecutive_bullish"] = "3_BULLISH"
            elif bullish_count >= 2:
                score += 5
                details["consecutive_bullish"] = "2_BULLISH"
            elif bullish_count >= 1:
                score += 2
                details["consecutive_bullish"] = "1_BULLISH"
            else:
                details["consecutive_bullish"] = "NONE"
        else:
            details["consecutive_bullish"] = "NO_DATA"

        # RVOL 확인 (0~3점)
        if rvol >= 2.0:
            score += 3
            details["volume_confirm"] = "HIGH"
        elif rvol >= 1.5:
            score += 2
            details["volume_confirm"] = "ELEVATED"
        elif rvol >= 1.0:
            score += 1
            details["volume_confirm"] = "NORMAL"
        else:
            details["volume_confirm"] = "LOW"

        # Close Position (0~3점)
        if close_pos >= 0.8:
            score += 3
            details["close_strength"] = "STRONG"
        elif close_pos >= 0.6:
            score += 2
            details["close_strength"] = "GOOD"
        elif close_pos >= 0.4:
            score += 1
            details["close_strength"] = "NEUTRAL"
        else:
            details["close_strength"] = "WEAK"

        return ReboundScoreComponent(
            name="Reversal Pattern",
            score=min(30, score),
            max_score=30,
            details=details,
        )

    def _calc_volatility_score(self, market_data: dict) -> ReboundScoreComponent:
        """
        Volatility Score (0~10) - 적정 변동성

        너무 높음: 휩쏘 위험 → 감점
        너무 낮음: 반등 기대 어려움 → 감점
        적정: 스캘핑 적합 → 가점
        """
        atr_pct = market_data.get("atr_pct", 0.01)
        price_range_pct = market_data.get("price_range_pct", 0.02)

        score = 0.0
        details = {
            "atr_pct": atr_pct,
            "price_range_pct": price_range_pct,
        }

        # ATR 기반 (0~6점)
        if 0.005 <= atr_pct <= 0.02:
            score += 6
            details["atr_zone"] = "OPTIMAL"
        elif 0.003 <= atr_pct <= 0.03:
            score += 4
            details["atr_zone"] = "ACCEPTABLE"
        elif atr_pct < 0.003:
            score += 1
            details["atr_zone"] = "TOO_LOW"
        else:
            score += 0
            details["atr_zone"] = "TOO_HIGH"

        # 24h 변동폭 (0~4점)
        if 0.02 <= price_range_pct <= 0.08:
            score += 4
            details["range_zone"] = "OPTIMAL"
        elif 0.01 <= price_range_pct <= 0.12:
            score += 2
            details["range_zone"] = "ACCEPTABLE"
        elif price_range_pct < 0.01:
            score += 1
            details["range_zone"] = "TOO_LOW"
        else:
            score += 0
            details["range_zone"] = "TOO_HIGH"

        return ReboundScoreComponent(
            name="Volatility Score",
            score=min(10, score),
            max_score=10,
            details=details,
        )

    def _determine_level(self, score: float) -> int:
        """점수 기반 레벨 결정"""
        if score >= self.score_l3:
            return 3
        elif score >= self.score_l2:
            return 2
        elif score >= self.score_l1:
            return 1
        return 0

    def _get_target_allocation(self, level: int) -> float:
        """레벨별 목표 배분"""
        if level == 3:
            return self.alloc_l3
        elif level == 2:
            return self.alloc_l2
        elif level == 1:
            return self.alloc_l1
        return 0.0

    def _calc_stop_loss(self, market_data: dict, entry_price: float) -> float:
        """손절가 계산"""
        stop_pct = getattr(settings, "rebound_stop_loss_pct", -0.012)
        return entry_price * (1 + stop_pct)

    def _empty_result(self, symbol: str) -> ReboundScoreResult:
        """빈 결과 반환"""
        return ReboundScoreResult(
            symbol=symbol,
            total_score=0,
            level=0,
            target_allocation=0,
            components=[],
            entry_price_target=0,
            stop_loss_price=0,
            support_distance_pct=0,
            rsi=50,
            orderbook_ratio=1.0,
        )


# 싱글톤 인스턴스
_rebound_score_calculator: Optional[ReboundScoreCalculator] = None


def get_rebound_score_calculator() -> ReboundScoreCalculator:
    """Rebound Score Calculator 싱글톤"""
    global _rebound_score_calculator
    if _rebound_score_calculator is None:
        _rebound_score_calculator = ReboundScoreCalculator()
    return _rebound_score_calculator
