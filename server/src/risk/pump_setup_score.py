"""
Pump Setup Score v2 - 선행 포착 시스템 (v4.2)

목적:
- "이미 오른 종목" 추격 대신 "곧 오를 종목" 선행 포착
- 압축 + 돌파 근접 + 호가 지지 + 적정 거래량 조합

Score = w1*Comp + w2*Exp + w3*Vol + w4*OB - w5*HeatPenalty

진입 조건:
- Score >= 0.72
- Comp >= 0.55 (압축)
- Exp >= 0.75 (돌파 근접)
- OB >= 0.65 (호가 지지)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import structlog

from src.config import get_settings

if TYPE_CHECKING:
    from src.data.candle_manager import Candle

logger = structlog.get_logger()


@dataclass
class PumpSetupScore:
    """Pump Setup Score 결과"""

    symbol: str
    total_score: float

    # 개별 점수
    compression_score: float      # ATR(20)/ATR(100) 기반 압축도
    expansion_score: float        # 박스 상단 근접도
    volume_score: float           # 적정 거래량 (2~3x 최적)
    orderbook_support_score: float  # 호가 지지 (TW_Imb + Persist)
    heat_penalty: float           # 과열 페널티

    # 통과 여부
    passed: bool
    reasons: list[str] = field(default_factory=list)

    # 메타데이터
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_score": round(self.total_score, 3),
            "compression": round(self.compression_score, 3),
            "expansion": round(self.expansion_score, 3),
            "volume": round(self.volume_score, 3),
            "orderbook_support": round(self.orderbook_support_score, 3),
            "heat_penalty": round(self.heat_penalty, 3),
            "passed": self.passed,
            "reasons": self.reasons,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class OrderbookSnapshot:
    """호가 스냅샷"""

    bid_depth: float  # 매수 총량
    ask_depth: float  # 매도 총량
    imbalance: float  # bid / (bid + ask)
    timestamp: datetime = field(default_factory=datetime.utcnow)


class PumpSetupScoreCalculator:
    """
    Pump Setup Score Calculator

    5가지 요소를 조합하여 "선행 포착" 점수 산출:
    1. Compression (압축) - 변동성이 줄어든 상태
    2. Expansion (확장) - 박스 상단 근접
    3. Volume (거래량) - 2~3x 적정 구간
    4. Orderbook Support (호가 지지) - 매수벽 지지
    5. Heat Penalty (과열) - 이미 많이 오르면 감점
    """

    def __init__(self) -> None:
        settings = get_settings()

        # 가중치
        self.w_comp = getattr(settings, "pump_setup_w_comp", 0.25)
        self.w_exp = getattr(settings, "pump_setup_w_exp", 0.20)
        self.w_vol = getattr(settings, "pump_setup_w_vol", 0.15)
        self.w_ob = getattr(settings, "pump_setup_w_ob", 0.30)
        self.w_heat = getattr(settings, "pump_setup_w_heat", 0.20)

        # 임계값
        self.score_threshold = getattr(settings, "pump_setup_score_threshold", 0.72)
        self.comp_min = getattr(settings, "pump_setup_comp_min", 0.55)
        self.exp_min = getattr(settings, "pump_setup_exp_min", 0.75)
        self.ob_min = getattr(settings, "pump_setup_ob_min", 0.65)
        self.vol_min = getattr(settings, "pump_setup_vol_min", 1.8)
        self.vol_max = getattr(settings, "pump_setup_vol_max", 4.0)
        self.vol_penalty_threshold = getattr(settings, "pump_setup_vol_penalty_threshold", 10.0)

        # 과열 임계값
        self.heat_1h_threshold = 0.05   # 1시간 +5% 이상이면 페널티 시작
        self.heat_24h_threshold = 0.15  # 24시간 +15% 이상이면 페널티 시작

        # 호가 분석 히스토리 (심볼별)
        self._ob_history: dict[str, list[OrderbookSnapshot]] = {}
        self._ob_max_history = 120  # 30초 * 4 = 120개 (250ms 샘플링)

        logger.info(
            "PumpSetupScore initialized",
            weights=f"Comp={self.w_comp}, Exp={self.w_exp}, Vol={self.w_vol}, OB={self.w_ob}, Heat={self.w_heat}",
            threshold=self.score_threshold,
        )

    def calculate(
        self,
        symbol: str,
        candles_5m: list["Candle"],
        orderbook: Optional[dict] = None,
        ticker: Optional[dict] = None,
    ) -> PumpSetupScore:
        """
        Pump Setup Score 계산

        Args:
            symbol: 심볼
            candles_5m: 5분 캔들 리스트 (최소 100개 권장)
            orderbook: 호가 데이터 {"bids": [...], "asks": [...]}
            ticker: 시세 데이터 {"change_1h": float, "change_24h": float, ...}

        Returns:
            PumpSetupScore
        """
        reasons = []

        # 1. Compression Score (압축도)
        compression = self._calc_compression(candles_5m)
        if compression < self.comp_min:
            reasons.append(f"Comp {compression:.2f} < {self.comp_min}")

        # 2. Expansion Score (돌파 근접도)
        expansion = self._calc_expansion(candles_5m)
        if expansion < self.exp_min:
            reasons.append(f"Exp {expansion:.2f} < {self.exp_min}")

        # 3. Volume Score (적정 거래량)
        volume = self._calc_volume_score(candles_5m)
        if volume < 0.3:  # 너무 낮은 거래량
            reasons.append(f"Vol too low: {volume:.2f}")

        # 4. Orderbook Support (호가 지지)
        ob_support = self._calc_orderbook_support(symbol, orderbook)
        if ob_support < self.ob_min:
            reasons.append(f"OB {ob_support:.2f} < {self.ob_min}")

        # 5. Heat Penalty (과열 페널티)
        heat_penalty = self._calc_heat_penalty(ticker)
        if heat_penalty > 0.3:
            reasons.append(f"Heat penalty: {heat_penalty:.2f}")

        # Total Score 계산
        total_score = (
            self.w_comp * compression +
            self.w_exp * expansion +
            self.w_vol * volume +
            self.w_ob * ob_support -
            self.w_heat * heat_penalty
        )

        # 최종 통과 여부
        passed = (
            total_score >= self.score_threshold and
            compression >= self.comp_min and
            expansion >= self.exp_min and
            ob_support >= self.ob_min
        )

        if not passed and not reasons:
            reasons.append(f"Score {total_score:.2f} < {self.score_threshold}")

        result = PumpSetupScore(
            symbol=symbol,
            total_score=total_score,
            compression_score=compression,
            expansion_score=expansion,
            volume_score=volume,
            orderbook_support_score=ob_support,
            heat_penalty=heat_penalty,
            passed=passed,
            reasons=reasons,
        )

        # 로그
        if passed:
            logger.info(
                "PumpSetup PASSED",
                symbol=symbol,
                score=f"{total_score:.2f}",
                comp=f"{compression:.2f}",
                exp=f"{expansion:.2f}",
                vol=f"{volume:.2f}",
                ob=f"{ob_support:.2f}",
            )
        else:
            logger.debug(
                "PumpSetup failed",
                symbol=symbol,
                score=f"{total_score:.2f}",
                reasons=reasons,
            )

        return result

    def _calc_compression(self, candles: list["Candle"]) -> float:
        """
        Compression Score (압축도)

        ATR(20) / ATR(100) 비율이 낮을수록 압축
        - ratio < 0.5 → score = 1.0
        - ratio > 1.0 → score = 0.0
        """
        if len(candles) < 100:
            # 캔들 부족 시 부분 계산
            if len(candles) < 20:
                return 0.5  # 기본값
            atr_short = self._calc_atr(candles[-20:])
            atr_long = self._calc_atr(candles)
        else:
            atr_short = self._calc_atr(candles[-20:])
            atr_long = self._calc_atr(candles[-100:])

        if atr_long <= 0:
            return 0.5

        ratio = atr_short / atr_long

        # ratio가 낮을수록 압축 → 점수 높음
        # Comp = clamp(1 - ratio, 0, 1) 에서 조정
        # ratio = 0.5 → score = 1.0
        # ratio = 1.0 → score = 0.5
        # ratio = 1.5 → score = 0.0
        score = max(0, min(1, 1.5 - ratio))

        return score

    def _calc_expansion(self, candles: list["Candle"]) -> float:
        """
        Expansion Score (돌파 근접도)

        (Price - BoxBottom) / BoxHeight
        - 박스 상단 근처면 1.0
        - 박스 하단이면 0.0
        """
        if len(candles) < 24:
            return 0.5

        # 최근 24봉(2시간) 박스
        recent = candles[-24:]
        box_high = max(c.high for c in recent)
        box_low = min(c.low for c in recent)
        current_price = candles[-1].close

        box_height = box_high - box_low
        if box_height <= 0:
            return 0.5

        # 현재가의 박스 내 위치
        position = (current_price - box_low) / box_height

        return max(0, min(1, position))

    def _calc_volume_score(self, candles: list["Candle"]) -> float:
        """
        Volume Score (적정 거래량)

        RVOL = 현재 거래량 / 평균 거래량
        - 2~3x → 1.0 (최적)
        - < 2x → 선형 증가
        - 3~10x → 선형 감소
        - > 10x → 0 (펌프앤덤프 의심)
        """
        if len(candles) < 24:
            return 0.5

        # 평균 거래량 (최근 24봉 제외한 이전 봉들)
        if len(candles) > 48:
            avg_vol = sum(c.quote_volume for c in candles[-48:-1]) / 47
        else:
            avg_vol = sum(c.quote_volume for c in candles[:-1]) / max(1, len(candles) - 1)

        if avg_vol <= 0:
            return 0.5

        current_vol = candles[-1].quote_volume
        rvol = current_vol / avg_vol

        # 점수 계산
        if rvol >= self.vol_penalty_threshold:
            # 10x 이상: 펌프앤덤프 의심 → 0점
            return 0.0
        elif self.vol_min <= rvol <= self.vol_max:
            # 2~3x (또는 설정값): 최적 → 1.0
            return 1.0
        elif rvol < self.vol_min:
            # 2x 미만: 선형 증가
            return rvol / self.vol_min
        else:
            # 3~10x: 선형 감소
            return max(0, 1.0 - (rvol - self.vol_max) / (self.vol_penalty_threshold - self.vol_max))

    def _calc_orderbook_support(
        self,
        symbol: str,
        orderbook: Optional[dict],
    ) -> float:
        """
        Orderbook Support Score (호가 지지)

        TW_Imb (시간가중 불균형) + Persist (지속성) - CancelPenalty (취소율)
        """
        if not orderbook:
            return 0.5  # 데이터 없음

        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if not bids or not asks:
            return 0.5

        # 현재 스냅샷 불균형 계산
        # bids/asks: [[price, quantity], ...]
        try:
            bid_depth = sum(float(b[1]) * float(b[0]) for b in bids[:10])  # 상위 10호가
            ask_depth = sum(float(a[1]) * float(a[0]) for a in asks[:10])
        except (IndexError, ValueError, TypeError):
            return 0.5

        total_depth = bid_depth + ask_depth
        if total_depth <= 0:
            return 0.5

        imbalance = bid_depth / total_depth  # 0.5 = 균형, > 0.5 = 매수 우세

        # 히스토리에 추가
        snapshot = OrderbookSnapshot(
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            imbalance=imbalance,
        )

        if symbol not in self._ob_history:
            self._ob_history[symbol] = []

        self._ob_history[symbol].append(snapshot)
        if len(self._ob_history[symbol]) > self._ob_max_history:
            self._ob_history[symbol] = self._ob_history[symbol][-self._ob_max_history:]

        # 시간가중 불균형 (EMA)
        history = self._ob_history[symbol]
        if len(history) < 5:
            tw_imb = imbalance
        else:
            # 간단한 EMA (최근 20개)
            recent = history[-20:]
            weights = [0.9 ** (len(recent) - i - 1) for i in range(len(recent))]
            tw_imb = sum(s.imbalance * w for s, w in zip(recent, weights)) / sum(weights)

        # 지속성 점수 (최근 히스토리에서 일관되게 매수 우세인지)
        if len(history) >= 10:
            recent_10 = history[-10:]
            bullish_count = sum(1 for s in recent_10 if s.imbalance > 0.55)
            persist = bullish_count / 10
        else:
            persist = 0.5

        # 최종 OB Score
        # tw_imb: 0.5~1.0 → 0~1로 변환
        imb_score = max(0, min(1, (tw_imb - 0.4) * 2.5))  # 0.4 미만이면 0, 0.8 이상이면 1

        ob_score = 0.6 * imb_score + 0.4 * persist

        return ob_score

    def _calc_heat_penalty(self, ticker: Optional[dict]) -> float:
        """
        Heat Penalty (과열 페널티)

        1시간/24시간 변동률 기반
        """
        if not ticker:
            return 0

        change_1h = abs(ticker.get("change_1h", 0))
        change_24h = abs(ticker.get("change_24h", 0))

        penalty = 0

        # 1시간 페널티: +5% 초과분
        if change_1h > self.heat_1h_threshold:
            penalty += (change_1h - self.heat_1h_threshold) * 3

        # 24시간 페널티: +15% 초과분
        if change_24h > self.heat_24h_threshold:
            penalty += (change_24h - self.heat_24h_threshold) * 1.5

        return min(1.0, penalty)

    def _calc_atr(self, candles: list["Candle"]) -> float:
        """ATR 계산"""
        if len(candles) < 2:
            return 0

        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i - 1].close

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)

        return sum(true_ranges) / len(true_ranges) if true_ranges else 0

    def update_orderbook(self, symbol: str, orderbook: dict) -> None:
        """호가 데이터 업데이트 (실시간 호출용)"""
        self._calc_orderbook_support(symbol, orderbook)

    def get_status(self) -> dict:
        """상태 조회"""
        return {
            "enabled": True,
            "thresholds": {
                "score": self.score_threshold,
                "compression": self.comp_min,
                "expansion": self.exp_min,
                "orderbook": self.ob_min,
            },
            "weights": {
                "compression": self.w_comp,
                "expansion": self.w_exp,
                "volume": self.w_vol,
                "orderbook": self.w_ob,
                "heat_penalty": self.w_heat,
            },
            "tracked_symbols": len(self._ob_history),
        }


# 싱글톤
_pump_setup_calculator: Optional[PumpSetupScoreCalculator] = None


def get_pump_setup_calculator() -> PumpSetupScoreCalculator:
    """PumpSetupScoreCalculator 싱글톤"""
    global _pump_setup_calculator
    if _pump_setup_calculator is None:
        _pump_setup_calculator = PumpSetupScoreCalculator()
    return _pump_setup_calculator
