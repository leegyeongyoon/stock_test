"""Attack Score Calculator

급등주 공격 점수 계산기

Attack Score 구성 (0~100점):
- Breakout Strength (0~25): 고점 돌파 강도
- Volume Expansion (0~25): RVOL, 거래대금 증가
- Trend Context (0~20): 1h 추세, SMA 위치
- Orderbook Quality (0~15): 스프레드, 깊이
- Overheat Penalty (-15~0): 과열 감점
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class ScoreComponent:
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
class AttackScoreResult:
    """Attack Score 결과"""

    symbol: str
    total_score: float  # 0~100
    level: int  # 0, 1, 2, 3
    target_allocation: float  # 0.0, 0.10, 0.30, 0.50
    components: list[ScoreComponent]
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_score": self.total_score,
            "level": self.level,
            "target_allocation": self.target_allocation,
            "components": [c.to_dict() for c in self.components],
            "timestamp": self.timestamp.isoformat(),
        }


class AttackScoreCalculator:
    """
    Attack Score 계산기

    급등주의 공격 적합성을 0~100점으로 평가

    점수 구성:
    - Breakout Strength (0~25): 고점 돌파 강도
    - Volume Expansion (0~25): RVOL, 거래대금
    - Trend Context (0~20): 1h 추세, SMA 위치
    - Orderbook Quality (0~15): 스프레드, 깊이
    - Overheat Penalty (-15~0): 과열 감점
    """

    def __init__(self):
        # 임계값 설정
        self.score_l1 = settings.attack_score_l1  # 70
        self.score_l2 = settings.attack_score_l2  # 80
        self.score_l3 = settings.attack_score_l3  # 90

        self.alloc_l1 = settings.attack_alloc_l1  # 0.10
        self.alloc_l2 = settings.attack_alloc_l2  # 0.30
        self.alloc_l3 = settings.attack_alloc_l3  # 0.50

        self.overheat_threshold = settings.attack_overheat_threshold  # 0.15

    def calculate(self, market_data: dict) -> AttackScoreResult:
        """
        Attack Score 계산

        Args:
            market_data: 시장 데이터
                - symbol: str
                - price: float (현재가)
                - highest_12_5m: float (최근 12개 5m 고점)
                - highest_24h: float (24h 고점, optional)
                - rvol: float (RVOL_5m)
                - volume_24h: float (24h 거래대금)
                - close_pos: float (ClosePos 0~1)
                - vwap: float (VWAP_5m)
                - sma_50_1h: float (1h SMA50, optional)
                - atr_pct_1h: float (1h ATR%, optional)
                - spread_bps: float (스프레드 bps)
                - change_rate: float (전일 대비 변화율)

        Returns:
            AttackScoreResult: 점수 결과
        """
        symbol = market_data.get("symbol", "UNKNOWN")
        components = []

        # v5.1: Anti-Chase Gate - 일일 +10% 이상 급등 시 진입 차단
        change_rate = market_data.get("change_rate", 0)
        if change_rate >= 0.10:  # +10% 이상
            logger.info(
                "Attack blocked by anti-chase gate",
                symbol=symbol,
                change_rate=f"{change_rate*100:.1f}%",
            )
            return AttackScoreResult(
                symbol=symbol,
                total_score=0,
                level=0,  # 진입 불가
                target_allocation=0,
                components=[ScoreComponent(
                    name="Anti-Chase Gate",
                    score=0,
                    max_score=0,
                    details={"reason": f"Daily change {change_rate*100:.1f}% >= 10% threshold"},
                )],
            )

        # 1. Breakout Strength (0~25)
        breakout_score = self._calc_breakout_strength(market_data)
        components.append(breakout_score)

        # 2. Volume Expansion (0~25)
        volume_score = self._calc_volume_expansion(market_data)
        components.append(volume_score)

        # 3. Trend Context (0~20)
        trend_score = self._calc_trend_context(market_data)
        components.append(trend_score)

        # 4. Orderbook Quality (0~15)
        orderbook_score = self._calc_orderbook_quality(market_data)
        components.append(orderbook_score)

        # 5. Overheat Penalty (-15~0)
        overheat_penalty = self._calc_overheat_penalty(market_data)
        components.append(overheat_penalty)

        # 총점 계산
        total_score = sum(c.score for c in components)
        total_score = max(0, min(100, total_score))  # 0~100 클램프

        # 레벨 결정
        level = self._determine_level(total_score)
        target_alloc = self._get_target_allocation(level)

        result = AttackScoreResult(
            symbol=symbol,
            total_score=total_score,
            level=level,
            target_allocation=target_alloc,
            components=components,
        )

        logger.debug(
            "Attack score calculated",
            symbol=symbol,
            total_score=f"{total_score:.1f}",
            level=level,
            target_alloc=f"{target_alloc:.0%}",
        )

        return result

    def _calc_breakout_strength(self, market_data: dict) -> ScoreComponent:
        """
        Breakout Strength (0~25)

        고점 돌파 강도 평가:
        - 12개봉 고점 돌파 여부
        - 돌파 폭 (%)
        - VWAP 위 위치
        """
        price = market_data.get("price", 0)
        highest_12 = market_data.get("highest_12_5m", 0)
        vwap = market_data.get("vwap", 0)
        close_pos = market_data.get("close_pos", 0.5)

        score = 0.0
        details = {}

        if price <= 0 or highest_12 <= 0:
            return ScoreComponent(
                name="Breakout Strength",
                score=0,
                max_score=25,
                details={"error": "Invalid price data"},
            )

        # 돌파 여부 (+10)
        breakout_pct = (price - highest_12) / highest_12
        details["breakout_pct"] = breakout_pct

        if breakout_pct >= 0:
            # 돌파 성공
            score += 10
            # 돌파 폭 추가 점수 (+0~5)
            if breakout_pct >= 0.02:
                score += 5
            elif breakout_pct >= 0.01:
                score += 3
            elif breakout_pct >= 0.005:
                score += 1
        else:
            # 돌파 근접 (99% 이상)
            if breakout_pct >= -0.01:
                score += 5

        details["breakout_score"] = score

        # VWAP 위 위치 (+0~5)
        if vwap > 0:
            vwap_distance = (price - vwap) / vwap
            details["vwap_distance"] = vwap_distance

            if vwap_distance >= 0.01:
                score += 5
            elif vwap_distance >= 0.005:
                score += 3
            elif vwap_distance >= 0:
                score += 2

        # ClosePos 강도 (+0~5)
        details["close_pos"] = close_pos
        if close_pos >= 0.90:
            score += 5
        elif close_pos >= 0.80:
            score += 3
        elif close_pos >= 0.70:
            score += 1

        return ScoreComponent(
            name="Breakout Strength",
            score=min(25, score),
            max_score=25,
            details=details,
        )

    def _calc_volume_expansion(self, market_data: dict) -> ScoreComponent:
        """
        Volume Expansion (0~25)

        거래량 폭증 평가:
        - RVOL (상대 거래량)
        - 24h 거래대금
        """
        rvol = market_data.get("rvol", 1.0)
        volume_24h = market_data.get("volume_24h", 0)

        score = 0.0
        details = {
            "rvol": rvol,
            "volume_24h": volume_24h,
        }

        # RVOL 점수 (+0~15)
        if rvol >= 5.0:
            score += 15
        elif rvol >= 4.0:
            score += 12
        elif rvol >= 3.0:
            score += 9
        elif rvol >= 2.5:
            score += 6
        elif rvol >= 2.0:
            score += 3
        elif rvol >= 1.5:
            score += 1

        details["rvol_score"] = score

        # 거래대금 점수 (+0~10)
        # Upbit 기준: 10억 이상이면 유동성 충분
        min_liquidity = settings.min_liquidity_krw  # 10억

        if volume_24h >= min_liquidity * 5:  # 50억+
            score += 10
        elif volume_24h >= min_liquidity * 3:  # 30억+
            score += 7
        elif volume_24h >= min_liquidity * 2:  # 20억+
            score += 5
        elif volume_24h >= min_liquidity:  # 10억+
            score += 3

        return ScoreComponent(
            name="Volume Expansion",
            score=min(25, score),
            max_score=25,
            details=details,
        )

    def _calc_trend_context(self, market_data: dict) -> ScoreComponent:
        """
        Trend Context (0~20)

        추세 맥락 평가:
        - 1h SMA50 위 위치
        - 고점 갱신 추세
        - 가격 모멘텀
        """
        price = market_data.get("price", 0)
        sma_50 = market_data.get("sma_50_1h", 0)
        highest_24h = market_data.get("highest_24h", 0)
        change_rate = market_data.get("change_rate", 0)

        score = 0.0
        details = {}

        # SMA50 위 위치 (+0~8)
        if sma_50 > 0 and price > 0:
            sma_distance = (price - sma_50) / sma_50
            details["sma_distance"] = sma_distance

            if sma_distance >= 0.05:
                score += 8
            elif sma_distance >= 0.02:
                score += 5
            elif sma_distance >= 0:
                score += 3
        else:
            # SMA 데이터 없으면 중립 점수
            score += 3

        # 24h 고점 갱신 (+0~7)
        if highest_24h > 0 and price > 0:
            high_distance = (price - highest_24h) / highest_24h
            details["high_24h_distance"] = high_distance

            if high_distance >= 0:
                score += 7  # 24h 고점 돌파
            elif high_distance >= -0.02:
                score += 4  # 근접
            elif high_distance >= -0.05:
                score += 2

        # 양호한 모멘텀 (+0~5)
        details["change_rate"] = change_rate
        if 0.03 <= change_rate < 0.10:
            # 적정 상승 (3~10%)
            score += 5
        elif 0.01 <= change_rate < 0.03:
            score += 3
        elif 0 <= change_rate < 0.01:
            score += 1

        return ScoreComponent(
            name="Trend Context",
            score=min(20, score),
            max_score=20,
            details=details,
        )

    def _calc_orderbook_quality(self, market_data: dict) -> ScoreComponent:
        """
        Orderbook Quality (0~15)

        호가 품질 평가:
        - 스프레드
        - 주문 깊이 (depth)
        """
        spread_bps = market_data.get("spread_bps", 10)
        depth_ratio = market_data.get("depth_ratio", 1.0)  # 매수벽/매도벽 비율

        score = 0.0
        details = {
            "spread_bps": spread_bps,
            "depth_ratio": depth_ratio,
        }

        # 스프레드 점수 (+0~10)
        if spread_bps <= 3:
            score += 10
        elif spread_bps <= 5:
            score += 8
        elif spread_bps <= 8:
            score += 5
        elif spread_bps <= 10:
            score += 3
        elif spread_bps <= 15:
            score += 1

        # 깊이 점수 (+0~5)
        # depth_ratio > 1 이면 매수세 우위
        if depth_ratio >= 2.0:
            score += 5
        elif depth_ratio >= 1.5:
            score += 3
        elif depth_ratio >= 1.0:
            score += 1

        return ScoreComponent(
            name="Orderbook Quality",
            score=min(15, score),
            max_score=15,
            details=details,
        )

    def _calc_overheat_penalty(self, market_data: dict) -> ScoreComponent:
        """
        Overheat Penalty (-15~0)

        과열 감점:
        - 전일 대비 급등 (+15% 이상)
        - 1시간 급등 (+10% 이상)
        - 과도한 RVOL (6배 이상)
        """
        change_rate = market_data.get("change_rate", 0)
        change_1h = market_data.get("change_1h", 0)
        rvol = market_data.get("rvol", 1.0)

        penalty = 0.0
        details = {
            "change_rate": change_rate,
            "change_1h": change_1h,
            "rvol": rvol,
        }

        # v5.1: 전일 대비 과열 (강화: -0~15)
        if change_rate >= 0.15:
            penalty -= 15  # 기존 -8 → -15
        elif change_rate >= 0.10:
            penalty -= 12  # 기존 없음 → -12
        elif change_rate >= 0.08:
            penalty -= 8   # 기존 -5 → -8
        elif change_rate >= 0.05:
            penalty -= 5   # 기존 -3 → -5

        details["daily_penalty"] = penalty

        # 1시간 급등 (-0~5)
        hourly_penalty = 0
        if change_1h >= 0.10:
            hourly_penalty = -5
        elif change_1h >= 0.07:
            hourly_penalty = -3
        elif change_1h >= 0.05:
            hourly_penalty = -1

        penalty += hourly_penalty
        details["hourly_penalty"] = hourly_penalty

        # 과도한 RVOL (-0~2)
        rvol_penalty = 0
        if rvol >= 8.0:
            rvol_penalty = -2
        elif rvol >= 6.0:
            rvol_penalty = -1

        penalty += rvol_penalty
        details["rvol_penalty"] = rvol_penalty

        return ScoreComponent(
            name="Overheat Penalty",
            score=max(-15, penalty),
            max_score=0,
            details=details,
        )

    def _determine_level(self, total_score: float) -> int:
        """점수에 따른 Attack Level 결정"""
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


# Singleton
_attack_score_calculator: Optional[AttackScoreCalculator] = None


def get_attack_score_calculator() -> AttackScoreCalculator:
    """AttackScoreCalculator 싱글톤"""
    global _attack_score_calculator
    if _attack_score_calculator is None:
        _attack_score_calculator = AttackScoreCalculator()
    return _attack_score_calculator
