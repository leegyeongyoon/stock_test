"""시장 추세 감지 및 전략 자동 전환 모듈

BTC 기준으로 시장 추세를 감지하고 롱/숏 전략을 자동 전환합니다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class MarketRegime(Enum):
    """시장 추세 상태"""
    BULLISH = "bullish"      # 상승장 → 롱 전략 활성화
    BEARISH = "bearish"      # 하락장 → 숏 전략 활성화
    SIDEWAYS = "sideways"    # 횡보장 → 모든 전략 비활성화 또는 보수적 운영
    UNKNOWN = "unknown"      # 판단 불가


@dataclass
class RegimeSignal:
    """추세 감지 결과"""
    regime: MarketRegime
    confidence: float        # 0.0 ~ 1.0 (확신도)
    btc_change_1h: float     # BTC 1시간 변화율
    btc_change_4h: float     # BTC 4시간 변화율
    btc_change_24h: float    # BTC 24시간 변화율
    ema_trend: str           # "up", "down", "flat"
    volume_trend: str        # "increasing", "decreasing", "stable"
    timestamp: datetime
    recommended_strategies: list[str]


class MarketRegimeDetector:
    """시장 추세 감지기

    BTC 가격과 거래량을 분석하여 시장 추세를 판단합니다.

    판단 기준:
    - 상승장: BTC 4시간 +2% 이상 + EMA 상승 + 거래량 증가
    - 하락장: BTC 4시간 -2% 이상 + EMA 하락 + 거래량 증가
    - 횡보장: BTC 4시간 ±1% 이내 + EMA 평탄
    """

    def __init__(self, history_provider=None):
        self.history_provider = history_provider
        self._last_regime: Optional[RegimeSignal] = None
        self._regime_change_cooldown = timedelta(minutes=30)  # 최소 30분마다 전환
        self._last_change_time: Optional[datetime] = None

        # 추세 판단 임계값
        self.bullish_threshold = 0.02    # +2% 상승장
        self.bearish_threshold = -0.02   # -2% 하락장
        self.sideways_threshold = 0.01   # ±1% 횡보장

        # 전략 매핑
        self.long_strategies = ["VOLATILE_OVERSOLD_BOUNCE", "CRASH_RECOVERY", "TRIPLE_BEARISH_REVERSAL"]
        self.short_strategies = []
        self.conservative_strategies = ["TRIPLE_BEARISH_REVERSAL"]

    def detect(self, candles: list, current_time: datetime = None) -> RegimeSignal:
        """시장 추세 감지

        Args:
            candles: BTC 5분봉 캔들 리스트 (최소 288개 = 24시간)
            current_time: 현재 시간 (None이면 마지막 캔들 시간)

        Returns:
            RegimeSignal: 추세 감지 결과
        """
        if not candles or len(candles) < 50:
            return self._unknown_signal(current_time or datetime.utcnow())

        current_time = current_time or candles[-1].open_time

        # 가격 변화율 계산
        current_price = candles[-1].close

        # 1시간 (12개 5분봉)
        price_1h_ago = candles[-12].close if len(candles) >= 12 else candles[0].close
        change_1h = (current_price - price_1h_ago) / price_1h_ago

        # 4시간 (48개 5분봉)
        price_4h_ago = candles[-48].close if len(candles) >= 48 else candles[0].close
        change_4h = (current_price - price_4h_ago) / price_4h_ago

        # 24시간 (288개 5분봉)
        price_24h_ago = candles[-288].close if len(candles) >= 288 else candles[0].close
        change_24h = (current_price - price_24h_ago) / price_24h_ago

        # EMA 계산 (20기간)
        ema_trend = self._calculate_ema_trend(candles)

        # 거래량 추세
        volume_trend = self._calculate_volume_trend(candles)

        # 추세 판단
        regime, confidence = self._determine_regime(
            change_1h, change_4h, change_24h, ema_trend, volume_trend
        )

        # 쿨다운 체크 (급격한 전환 방지)
        if self._last_regime and self._last_change_time:
            if (current_time - self._last_change_time) < self._regime_change_cooldown:
                if regime != self._last_regime.regime:
                    # 쿨다운 중이면 이전 추세 유지
                    regime = self._last_regime.regime
                    confidence *= 0.8  # 확신도 감소

        # 전략 추천
        recommended = self._get_recommended_strategies(regime, confidence)

        signal = RegimeSignal(
            regime=regime,
            confidence=confidence,
            btc_change_1h=change_1h,
            btc_change_4h=change_4h,
            btc_change_24h=change_24h,
            ema_trend=ema_trend,
            volume_trend=volume_trend,
            timestamp=current_time,
            recommended_strategies=recommended,
        )

        # 상태 저장
        if self._last_regime is None or regime != self._last_regime.regime:
            self._last_change_time = current_time
        self._last_regime = signal

        return signal

    def _calculate_ema_trend(self, candles: list, period: int = 20) -> str:
        """EMA 추세 계산"""
        if len(candles) < period * 2:
            return "flat"

        closes = [c.close for c in candles]

        # EMA 계산
        multiplier = 2 / (period + 1)
        ema = [closes[0]]
        for price in closes[1:]:
            ema.append((price - ema[-1]) * multiplier + ema[-1])

        # 최근 EMA vs 10봉 전 EMA
        current_ema = ema[-1]
        past_ema = ema[-10] if len(ema) >= 10 else ema[0]

        ema_change = (current_ema - past_ema) / past_ema

        if ema_change > 0.005:  # +0.5%
            return "up"
        elif ema_change < -0.005:  # -0.5%
            return "down"
        return "flat"

    def _calculate_volume_trend(self, candles: list) -> str:
        """거래량 추세 계산"""
        if len(candles) < 24:
            return "stable"

        # 최근 12봉 vs 이전 12봉 거래량 비교
        recent_vol = sum(c.quote_volume for c in candles[-12:]) / 12
        past_vol = sum(c.quote_volume for c in candles[-24:-12]) / 12

        if past_vol == 0:
            return "stable"

        vol_change = (recent_vol - past_vol) / past_vol

        if vol_change > 0.3:  # +30%
            return "increasing"
        elif vol_change < -0.3:  # -30%
            return "decreasing"
        return "stable"

    def _determine_regime(
        self,
        change_1h: float,
        change_4h: float,
        change_24h: float,
        ema_trend: str,
        volume_trend: str,
    ) -> tuple[MarketRegime, float]:
        """추세 판단"""
        score = 0.0

        # 4시간 변화율 기준 (주요 지표)
        if change_4h >= self.bullish_threshold:
            score += 40  # 상승장 +40점
        elif change_4h <= self.bearish_threshold:
            score -= 40  # 하락장 -40점
        else:
            score += change_4h * 1000  # 비례 점수

        # 1시간 변화율 (단기 모멘텀)
        if change_1h > 0.01:
            score += 20
        elif change_1h < -0.01:
            score -= 20

        # 24시간 변화율 (장기 추세)
        if change_24h > 0.03:
            score += 15
        elif change_24h < -0.03:
            score -= 15

        # EMA 추세
        if ema_trend == "up":
            score += 15
        elif ema_trend == "down":
            score -= 15

        # 거래량 (추세 확인)
        if volume_trend == "increasing":
            score = score * 1.2 if score != 0 else 0  # 추세 강화

        # 최종 판단
        if score >= 30:
            regime = MarketRegime.BULLISH
            confidence = min(abs(score) / 100, 1.0)
        elif score <= -30:
            regime = MarketRegime.BEARISH
            confidence = min(abs(score) / 100, 1.0)
        elif -15 <= score <= 15:
            regime = MarketRegime.SIDEWAYS
            confidence = 1.0 - abs(score) / 30
        else:
            regime = MarketRegime.UNKNOWN
            confidence = 0.3

        return regime, confidence

    def _get_recommended_strategies(
        self, regime: MarketRegime, confidence: float
    ) -> list[str]:
        """추천 전략 반환"""
        if confidence < 0.5:
            # 확신도 낮으면 보수적 운영
            return self.conservative_strategies

        if regime == MarketRegime.BULLISH:
            return self.long_strategies
        elif regime == MarketRegime.BEARISH:
            return self.short_strategies
        elif regime == MarketRegime.SIDEWAYS:
            return self.conservative_strategies
        else:
            return []

    def _unknown_signal(self, timestamp: datetime) -> RegimeSignal:
        """알 수 없는 상태 반환"""
        return RegimeSignal(
            regime=MarketRegime.UNKNOWN,
            confidence=0.0,
            btc_change_1h=0.0,
            btc_change_4h=0.0,
            btc_change_24h=0.0,
            ema_trend="flat",
            volume_trend="stable",
            timestamp=timestamp,
            recommended_strategies=[],
        )


class AdaptiveStrategyManager:
    """적응형 전략 관리자

    시장 추세에 따라 자동으로 전략을 활성화/비활성화합니다.
    """

    def __init__(self, regime_detector: MarketRegimeDetector):
        self.regime_detector = regime_detector
        self._active_strategies: list[str] = []
        self._last_signal: Optional[RegimeSignal] = None

        # 전략별 설정
        self.strategy_configs = {
            "VOLATILE_OVERSOLD_BOUNCE": {
                "type": "long",
                "min_confidence": 0.5,
                "exit_params": {"stop_loss_pct": -0.02, "take_profit_pct": 0.02},
            },
            "CRASH_RECOVERY": {
                "type": "long",
                "min_confidence": 0.6,
                "exit_params": {"stop_loss_pct": -0.015, "take_profit_pct": 0.02},
            },
            "TRIPLE_BEARISH_REVERSAL": {
                "type": "long",
                "min_confidence": 0.5,
                "exit_params": {"stop_loss_pct": -0.02, "take_profit_pct": 0.02},
            },
        }

    def update(self, btc_candles: list, current_time: datetime = None) -> dict:
        """전략 상태 업데이트

        Args:
            btc_candles: BTC 캔들 데이터
            current_time: 현재 시간

        Returns:
            dict: 업데이트 결과
        """
        signal = self.regime_detector.detect(btc_candles, current_time)
        self._last_signal = signal

        # 활성화할 전략 결정
        new_active = []
        for strategy in signal.recommended_strategies:
            config = self.strategy_configs.get(strategy, {})
            if signal.confidence >= config.get("min_confidence", 0.5):
                new_active.append(strategy)

        # 전략 변경 로깅
        added = set(new_active) - set(self._active_strategies)
        removed = set(self._active_strategies) - set(new_active)

        self._active_strategies = new_active

        return {
            "regime": signal.regime.value,
            "confidence": signal.confidence,
            "active_strategies": self._active_strategies,
            "added_strategies": list(added),
            "removed_strategies": list(removed),
            "btc_change_4h": signal.btc_change_4h,
            "ema_trend": signal.ema_trend,
            "timestamp": signal.timestamp.isoformat(),
        }

    def get_active_strategies(self) -> list[str]:
        """현재 활성화된 전략 목록"""
        return self._active_strategies

    def get_exit_params(self, strategy: str) -> dict:
        """전략별 청산 파라미터"""
        config = self.strategy_configs.get(strategy, {})
        return config.get("exit_params", {"stop_loss_pct": -0.01, "take_profit_pct": 0.01})

    def is_strategy_active(self, strategy: str) -> bool:
        """전략 활성화 여부"""
        return strategy in self._active_strategies

    def get_status(self) -> dict:
        """현재 상태 반환"""
        if not self._last_signal:
            return {"status": "not_initialized"}

        return {
            "regime": self._last_signal.regime.value,
            "confidence": self._last_signal.confidence,
            "active_strategies": self._active_strategies,
            "btc_change_1h": f"{self._last_signal.btc_change_1h*100:.2f}%",
            "btc_change_4h": f"{self._last_signal.btc_change_4h*100:.2f}%",
            "btc_change_24h": f"{self._last_signal.btc_change_24h*100:.2f}%",
            "ema_trend": self._last_signal.ema_trend,
            "volume_trend": self._last_signal.volume_trend,
            "last_update": self._last_signal.timestamp.isoformat(),
        }


# === 사용 예시 ===

def example_usage():
    """사용 예시"""
    from datetime import datetime

    # 1. 추세 감지기 초기화
    detector = MarketRegimeDetector()

    # 2. 적응형 전략 관리자 초기화
    manager = AdaptiveStrategyManager(detector)

    # 3. BTC 캔들 데이터로 업데이트 (실제로는 API에서 가져옴)
    # btc_candles = fetch_btc_candles()  # 실제 구현 필요
    # result = manager.update(btc_candles)

    # 4. 활성화된 전략 확인
    # active = manager.get_active_strategies()
    # print(f"Active strategies: {active}")

    # 5. 전략별 청산 파라미터 가져오기
    # for strategy in active:
    #     params = manager.get_exit_params(strategy)
    #     print(f"{strategy}: {params}")

    pass


if __name__ == "__main__":
    example_usage()
