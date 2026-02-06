"""Pullback Score Calculator - 눌림목 매수 점수 계산기

기존 Attack Score의 문제점:
- 급등 후 고점에서 진입 (후행 지표)
- RVOL, Breakout 모두 "이미 급등한 후" 측정
- 결과: 고점 매수 → 하락 → 손실

새로운 Pullback Score 접근법:
- "급등 추격" 대신 "급등 후 눌림목 매수"
- 선행 지표 사용: 축적 신호, 지지선, 반등 조짐
- 결과: 저점 근처 매수 → 상승 → 수익

Pullback Score 구성 (0~105점, 100점 캡):
1. Recent Surge (0~20): 최근 급등 이력 (매수 대상 자격)
2. Pullback Depth (0~25): 눌림 깊이 (저점 근접도)
3. Support Level (0~25): 지지선 근접도 (VWAP, MA, RSI)
4. Accumulation Signal (0~20): 축적 신호 (호가 불균형)
5. Reversal Sign (0~15): 반등 조짐 (하락 둔화, 양봉 출현)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class PullbackScoreComponent:
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
class PullbackScoreResult:
    """Pullback Score 결과"""

    symbol: str
    total_score: float  # 0~100
    level: int  # 0, 1, 2, 3
    target_allocation: float  # 0.0, 0.05, 0.08, 0.10
    components: list[PullbackScoreComponent]
    entry_price_target: float  # 권장 진입가
    stop_loss_price: float  # 손절가
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_score": self.total_score,
            "level": self.level,
            "target_allocation": self.target_allocation,
            "entry_price_target": self.entry_price_target,
            "stop_loss_price": self.stop_loss_price,
            "components": [c.to_dict() for c in self.components],
            "timestamp": self.timestamp.isoformat(),
        }


class PullbackScoreCalculator:
    """
    Pullback Score 계산기 - 눌림목 매수 적합성 평가

    핵심 철학:
    - "급등을 쫓지 말고, 급등 후 눌림을 사라"
    - 선행 지표 중심 (축적, 지지선, 반등 조짐)

    점수 구성:
    1. Recent Surge (0~20): 최근 급등 이력 확인
    2. Pullback Depth (0~25): 고점 대비 눌림 깊이
    3. Support Level (0~25): 지지선 근접도 + RSI 과매도
    4. Accumulation Signal (0~20): 매수세 축적 신호
    5. Reversal Sign (0~15): 반등 조짐

    진입 조건:
    - 최근 급등 이력 필수 (상승 추세 확인)
    - 충분한 눌림 발생 (저점 매수 기회)
    - 지지선 근처에서 반등 조짐
    """

    def __init__(self):
        # 레벨별 임계값 (보수적 설정)
        self.score_l1 = getattr(settings, "pullback_score_l1", 55)
        self.score_l2 = getattr(settings, "pullback_score_l2", 70)
        self.score_l3 = getattr(settings, "pullback_score_l3", 85)

        # 레벨별 배분 (분산 투자 - 소액)
        self.alloc_l1 = getattr(settings, "pullback_alloc_l1", 0.05)  # 5%
        self.alloc_l2 = getattr(settings, "pullback_alloc_l2", 0.08)  # 8%
        self.alloc_l3 = getattr(settings, "pullback_alloc_l3", 0.10)  # 10%

        # 최대 포지션 수 (분산)
        self.max_positions = getattr(settings, "pullback_max_positions", 10)

        # 눌림 깊이 기준
        self.min_pullback_pct = 0.03  # 최소 3% 눌림
        self.ideal_pullback_pct = 0.05  # 이상적 5% 눌림
        self.max_pullback_pct = 0.15  # 15% 이상은 하락 추세 의심

        # 최근 급등 기준
        self.min_recent_surge_pct = 0.05  # 최소 5% 급등 이력
        self.surge_lookback_hours = 24  # 24시간 내 급등

    def calculate(self, market_data: dict) -> PullbackScoreResult:
        """
        Pullback Score 계산

        Args:
            market_data: 시장 데이터
                - symbol: str
                - price: float (현재가)
                - highest_24h: float (24h 고점)
                - lowest_24h: float (24h 저점)
                - change_rate: float (전일 대비 변화율)
                - change_1h: float (1시간 변화율)
                - vwap: float (VWAP)
                - sma_20: float (20 SMA)
                - rvol: float (RVOL)
                - bid_volume: float (매수 호가 잔량)
                - ask_volume: float (매도 호가 잔량)
                - spread_bps: float (스프레드)
                - recent_candles: list (최근 캔들 데이터)

        Returns:
            PullbackScoreResult: 눌림목 점수 결과
        """
        symbol = market_data.get("symbol", "UNKNOWN")
        price = market_data.get("price", 0)
        components = []

        # 1. Recent Surge (0~20) - 최근 급등 이력
        surge_score = self._calc_recent_surge(market_data)
        components.append(surge_score)

        # 급등 이력이 없으면 눌림목 대상 아님 (최소한의 상승 이력만 확인)
        if surge_score.score < 5:
            return PullbackScoreResult(
                symbol=symbol,
                total_score=0,
                level=0,
                target_allocation=0,
                components=components,
                entry_price_target=0,
                stop_loss_price=0,
            )

        # 2. Pullback Depth (0~25) - 눌림 깊이
        pullback_score = self._calc_pullback_depth(market_data)
        components.append(pullback_score)

        # 3. Support Level (0~20) - 지지선 근접도
        support_score = self._calc_support_level(market_data)
        components.append(support_score)

        # 4. Accumulation Signal (0~20) - 축적 신호
        accum_score = self._calc_accumulation_signal(market_data)
        components.append(accum_score)

        # 5. Reversal Sign (0~15) - 반등 조짐
        reversal_score = self._calc_reversal_sign(market_data)
        components.append(reversal_score)

        # 총점 계산
        total_score = sum(c.score for c in components)
        total_score = max(0, min(100, total_score))

        # 레벨 및 배분 결정
        level = self._determine_level(total_score)
        target_alloc = self._get_target_allocation(level)

        # 진입가/손절가 계산
        entry_price = self._calc_entry_price(market_data)
        stop_loss = self._calc_stop_loss(market_data, entry_price)

        result = PullbackScoreResult(
            symbol=symbol,
            total_score=total_score,
            level=level,
            target_allocation=target_alloc,
            components=components,
            entry_price_target=entry_price,
            stop_loss_price=stop_loss,
        )

        if level > 0:
            logger.info(
                "Pullback score calculated",
                symbol=symbol,
                total_score=f"{total_score:.1f}",
                level=level,
                target_alloc=f"{target_alloc:.0%}",
                entry_price=f"{entry_price:,.0f}",
            )

        return result

    def _calc_recent_surge(self, market_data: dict) -> PullbackScoreComponent:
        """
        Recent Surge (0~20) - 최근 급등 이력 확인

        급등 이력이 있어야 눌림목 매수 대상
        - 24h 내 5% 이상 상승 이력
        - 고점 대비 현재 위치
        """
        price = market_data.get("price", 0)
        highest_24h = market_data.get("highest_24h", 0)
        lowest_24h = market_data.get("lowest_24h", 0)
        change_rate = market_data.get("change_rate", 0)

        score = 0.0
        details = {}

        if price <= 0 or highest_24h <= 0 or lowest_24h <= 0:
            return PullbackScoreComponent(
                name="Recent Surge",
                score=0,
                max_score=20,
                details={"error": "Invalid price data"},
            )

        # 24h 범위 계산
        range_24h = (highest_24h - lowest_24h) / lowest_24h if lowest_24h > 0 else 0
        details["range_24h"] = range_24h

        # 급등 범위 점수 (+0~12)
        if range_24h >= 0.15:  # 15% 이상 변동
            score += 12
        elif range_24h >= 0.10:  # 10% 이상
            score += 10
        elif range_24h >= 0.07:  # 7% 이상
            score += 7
        elif range_24h >= 0.05:  # 5% 이상
            score += 5

        details["range_score"] = score

        # 전일 대비 상승 여부 (+0~8)
        details["change_rate"] = change_rate
        if change_rate >= 0.10:
            score += 8
        elif change_rate >= 0.05:
            score += 6
        elif change_rate >= 0.03:
            score += 4
        elif change_rate >= 0.01:
            score += 2
        elif change_rate >= 0:
            score += 1

        return PullbackScoreComponent(
            name="Recent Surge",
            score=min(20, score),
            max_score=20,
            details=details,
        )

    def _calc_pullback_depth(self, market_data: dict) -> PullbackScoreComponent:
        """
        Pullback Depth (0~25) - 눌림 깊이

        핵심: 고점에서 얼마나 눌렸는가?
        - 3~5% 눌림: 이상적
        - 5~10% 눌림: 좋음
        - 10~15% 눌림: 주의 (하락 추세?)
        - 15%+ 눌림: 대상 제외
        """
        price = market_data.get("price", 0)
        highest_24h = market_data.get("highest_24h", 0)

        score = 0.0
        details = {}

        if price <= 0 or highest_24h <= 0:
            return PullbackScoreComponent(
                name="Pullback Depth",
                score=0,
                max_score=25,
                details={"error": "Invalid data"},
            )

        # 고점 대비 눌림 계산
        pullback_pct = (highest_24h - price) / highest_24h
        details["pullback_pct"] = pullback_pct

        # 눌림 깊이 점수 (이상적: 3~8%)
        if 0.03 <= pullback_pct <= 0.05:
            # 이상적 눌림 (3~5%)
            score = 25
        elif 0.05 < pullback_pct <= 0.08:
            # 좋은 눌림 (5~8%)
            score = 22
        elif 0.02 <= pullback_pct < 0.03:
            # 약간 눌림 (2~3%)
            score = 18
        elif 0.08 < pullback_pct <= 0.10:
            # 다소 깊은 눌림 (8~10%)
            score = 15
        elif 0.10 < pullback_pct <= 0.15:
            # 깊은 눌림 - 주의 (10~15%)
            score = 8
        elif pullback_pct > 0.15:
            # 너무 깊음 - 하락 추세 의심
            score = 0
        elif pullback_pct < 0.02:
            # 눌림 부족 - 아직 고점권
            score = 5

        details["depth_score"] = score

        return PullbackScoreComponent(
            name="Pullback Depth",
            score=score,
            max_score=25,
            details=details,
        )

    def _calc_support_level(self, market_data: dict) -> PullbackScoreComponent:
        """
        Support Level (0~20) - 지지선 근접도

        지지선:
        - VWAP (거래량 가중 평균)
        - 20 SMA
        - 24h 저점 영역
        """
        price = market_data.get("price", 0)
        vwap = market_data.get("vwap", 0)
        sma_20 = market_data.get("sma_20", 0)
        lowest_24h = market_data.get("lowest_24h", 0)

        score = 0.0
        details = {}

        if price <= 0:
            return PullbackScoreComponent(
                name="Support Level",
                score=0,
                max_score=20,
                details={"error": "Invalid price"},
            )

        # VWAP 근접도 (+0~8)
        if vwap > 0:
            vwap_dist = (price - vwap) / vwap
            details["vwap_dist"] = vwap_dist

            if -0.01 <= vwap_dist <= 0.01:
                # VWAP 근접 (±1%)
                score += 8
            elif -0.02 <= vwap_dist < -0.01:
                # VWAP 살짝 아래
                score += 6
            elif 0.01 < vwap_dist <= 0.02:
                # VWAP 살짝 위
                score += 5
            elif vwap_dist > 0.02:
                # VWAP 위 (지지 받은 상태)
                score += 3

        # SMA20 근접도 (+0~7)
        if sma_20 > 0:
            sma_dist = (price - sma_20) / sma_20
            details["sma_dist"] = sma_dist

            if -0.01 <= sma_dist <= 0.01:
                score += 7
            elif -0.02 <= sma_dist < -0.01:
                score += 5
            elif 0.01 < sma_dist <= 0.03:
                score += 4
            elif sma_dist > 0.03:
                score += 2

        # 24h 저점 대비 위치 (+0~5)
        if lowest_24h > 0:
            low_dist = (price - lowest_24h) / lowest_24h
            details["low_dist"] = low_dist

            if low_dist <= 0.02:
                # 저점 매우 근접 (2% 이내)
                score += 5
            elif low_dist <= 0.05:
                # 저점 근접 (5% 이내)
                score += 3
            elif low_dist <= 0.08:
                score += 1

        # RSI 과매도 보너스 (+0~5)
        rsi = market_data.get("rsi", 50)
        details["rsi"] = rsi
        if rsi <= 30:
            score += 5  # 과매도 → 지지 가능성 높음
        elif rsi <= 40:
            score += 3
        elif rsi <= 45:
            score += 1

        return PullbackScoreComponent(
            name="Support Level",
            score=min(25, score),
            max_score=25,
            details=details,
        )

    def _calc_accumulation_signal(self, market_data: dict) -> PullbackScoreComponent:
        """
        Accumulation Signal (0~20) - 축적 신호 (선행 지표!)

        핵심 선행 지표:
        - 호가창 불균형 (매수벽 > 매도벽)
        - 거래량 패턴 (하락 시 거래량 감소)
        - 대량 매수 주문 출현
        """
        bid_volume = market_data.get("bid_volume", 0)
        ask_volume = market_data.get("ask_volume", 0)
        rvol = market_data.get("rvol", 1.0)
        volume_trend = market_data.get("volume_trend", "neutral")

        score = 0.0
        details = {}

        # 호가창 불균형 (+0~12) - 핵심 선행 지표!
        if bid_volume > 0 and ask_volume > 0:
            bid_ask_ratio = bid_volume / ask_volume
            details["bid_ask_ratio"] = bid_ask_ratio

            if bid_ask_ratio >= 2.0:
                # 매수세 2배 이상 - 강한 축적
                score += 12
            elif bid_ask_ratio >= 1.5:
                # 매수세 우위
                score += 9
            elif bid_ask_ratio >= 1.2:
                # 약간 매수 우위
                score += 6
            elif bid_ask_ratio >= 1.0:
                # 균형
                score += 3
            else:
                # 매도세 우위 - 감점은 안 함 (0점)
                pass

        # RVOL 패턴 (+0~5)
        # 눌림 중 RVOL이 낮으면 좋음 (매도 압력 약함)
        details["rvol"] = rvol
        if rvol <= 0.8:
            # 거래량 감소 - 매도 압력 약함
            score += 5
        elif rvol <= 1.2:
            # 정상 거래량
            score += 3
        elif rvol <= 1.5:
            score += 1
        # RVOL > 1.5면 패닉셀 가능성 - 0점

        # 거래량 추세 (+0~3)
        details["volume_trend"] = volume_trend
        if volume_trend == "decreasing":
            # 하락 둔화 신호
            score += 3
        elif volume_trend == "neutral":
            score += 1

        return PullbackScoreComponent(
            name="Accumulation Signal",
            score=min(20, score),
            max_score=20,
            details=details,
        )

    def _calc_reversal_sign(self, market_data: dict) -> PullbackScoreComponent:
        """
        Reversal Sign (0~15) - 반등 조짐

        반등 신호:
        - 최근 캔들 패턴 (양봉 출현)
        - 하락 모멘텀 둔화
        - 가격 안정화
        """
        change_1h = market_data.get("change_1h", 0)
        change_5m = market_data.get("change_5m", 0)
        recent_candles = market_data.get("recent_candles", [])

        score = 0.0
        details = {}

        # 1시간 변화율 (+0~6)
        details["change_1h"] = change_1h
        if -0.01 <= change_1h <= 0.01:
            # 안정화 (±1%)
            score += 6
        elif 0.01 < change_1h <= 0.03:
            # 반등 시작
            score += 5
        elif -0.02 <= change_1h < -0.01:
            # 약한 하락
            score += 3
        elif change_1h > 0.03:
            # 강한 반등 (이미 반등 진행 중)
            score += 2

        # 5분 변화율 (+0~5)
        details["change_5m"] = change_5m
        if 0 < change_5m <= 0.01:
            # 미세 반등
            score += 5
        elif change_5m > 0.01:
            # 반등 중
            score += 3
        elif -0.005 <= change_5m <= 0:
            # 안정
            score += 2

        # 캔들 패턴 (+0~4)
        if recent_candles:
            bullish_count = sum(
                1 for c in recent_candles[-3:] if c.close > c.open
            )
            details["bullish_candles"] = bullish_count

            if bullish_count >= 2:
                # 최근 3개 중 2개 이상 양봉
                score += 4
            elif bullish_count >= 1:
                score += 2

        return PullbackScoreComponent(
            name="Reversal Sign",
            score=min(15, score),
            max_score=15,
            details=details,
        )

    def _determine_level(self, total_score: float) -> int:
        """점수에 따른 레벨 결정"""
        if total_score >= self.score_l3:
            return 3
        elif total_score >= self.score_l2:
            return 2
        elif total_score >= self.score_l1:
            return 1
        return 0

    def _get_target_allocation(self, level: int) -> float:
        """레벨에 따른 목표 비중"""
        if level == 3:
            return self.alloc_l3
        elif level == 2:
            return self.alloc_l2
        elif level == 1:
            return self.alloc_l1
        return 0.0

    def _calc_entry_price(self, market_data: dict) -> float:
        """권장 진입가 계산 (현재가 또는 지지선)"""
        price = market_data.get("price", 0)
        vwap = market_data.get("vwap", 0)

        # VWAP이 현재가보다 낮으면 VWAP 근처로 지정가
        if vwap > 0 and vwap < price:
            return vwap * 1.001  # VWAP + 0.1%

        return price

    def _calc_stop_loss(self, market_data: dict, entry_price: float) -> float:
        """손절가 계산 (ATR 기반 동적 손절)"""
        lowest_24h = market_data.get("lowest_24h", 0)
        atr_pct = market_data.get("atr_pct", 0.02)

        if entry_price <= 0:
            return 0

        # ATR 1.5배, 최소 -1.5%, 최대 -3%
        atr_stop = max(0.015, min(0.03, atr_pct * 1.5))
        stop_loss = entry_price * (1 - atr_stop)

        # 24h 저점 아래로는 안 잡음 (저점 -0.5%)
        if lowest_24h > 0:
            stop_loss = max(stop_loss, lowest_24h * 0.995)

        return stop_loss


# Singleton
_pullback_score_calculator: Optional[PullbackScoreCalculator] = None


def get_pullback_score_calculator() -> PullbackScoreCalculator:
    """PullbackScoreCalculator 싱글톤"""
    global _pullback_score_calculator
    if _pullback_score_calculator is None:
        _pullback_score_calculator = PullbackScoreCalculator()
    return _pullback_score_calculator
