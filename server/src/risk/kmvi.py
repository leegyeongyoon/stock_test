"""
KMVI (KRW Market Volatility Index) - 업비트판 VIX (v4.2)

목적:
- 전체 시장 변동성 측정
- KMVI가 높으면 Micro Scalper OFF, 신규 진입 제한
- 급변장에서 연속 손실 방어

계산:
- 상위 20개 코인의 ATR(14, 5m) / Price의 80% 분위수
- T1=1.2%, T2=2.0%, T3=2.8%
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Optional

import structlog

from src.config import get_settings

if TYPE_CHECKING:
    from src.data.candle_manager import CandleManager

logger = structlog.get_logger()


class KMVITier(str, Enum):
    """KMVI 단계"""

    NORMAL = "NORMAL"      # KMVI < T1
    ELEVATED = "ELEVATED"  # T1 <= KMVI < T2
    HIGH = "HIGH"          # T2 <= KMVI < T3
    CRISIS = "CRISIS"      # KMVI >= T3


@dataclass
class KMVIState:
    """KMVI 상태"""

    value: float                      # 현재 KMVI 값 (소수점, 0.012 = 1.2%)
    value_pct: float                  # 퍼센트 값 (1.2)
    tier: KMVITier
    micro_scalper_allowed: bool       # KMVI < T2
    new_entry_allowed: bool           # KMVI < T3
    sizing_multiplier: float          # KMVI 기반 사이징
    top_coins: list[tuple[str, float]] = field(default_factory=list)  # (symbol, atr_pct)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "value": round(self.value, 5),
            "value_pct": round(self.value_pct, 2),
            "tier": self.tier.value,
            "micro_scalper_allowed": self.micro_scalper_allowed,
            "new_entry_allowed": self.new_entry_allowed,
            "sizing_multiplier": round(self.sizing_multiplier, 2),
            "top_coins_count": len(self.top_coins),
            "timestamp": self.timestamp.isoformat(),
        }


class KMVICalculator:
    """
    KMVI Calculator

    KRW 마켓 상위 N개 코인의 변동성을 합성하여
    시장 전체 변동성 지표를 산출
    """

    def __init__(self) -> None:
        settings = get_settings()

        # 임계값
        self.T1 = getattr(settings, "kmvi_t1", 0.012)  # 1.2%
        self.T2 = getattr(settings, "kmvi_t2", 0.020)  # 2.0%
        self.T3 = getattr(settings, "kmvi_t3", 0.028)  # 2.8%

        # 상위 N개 코인
        self.top_n = getattr(settings, "kmvi_top_n", 20)

        # 분위수
        self.percentile = getattr(settings, "kmvi_percentile", 80) / 100.0

        # ATR 기간
        self.atr_period = 14

        # 캐시
        self._last_state: Optional[KMVIState] = None
        self._last_update: Optional[datetime] = None
        self._update_interval = timedelta(minutes=1)  # 1분마다 업데이트

        # 히스토리 (트렌드 분석용)
        self._history: list[tuple[datetime, float]] = []
        self._max_history = 60  # 최근 60개 (1시간)

        logger.info(
            "KMVI Calculator initialized",
            T1=f"{self.T1:.1%}",
            T2=f"{self.T2:.1%}",
            T3=f"{self.T3:.1%}",
            top_n=self.top_n,
        )

    def update(
        self,
        symbols: list[str],
        candle_manager: "CandleManager",
    ) -> KMVIState:
        """
        KMVI 업데이트

        Args:
            symbols: 거래량 순으로 정렬된 심볼 리스트
            candle_manager: 캔들 데이터 매니저

        Returns:
            KMVIState
        """
        now = datetime.utcnow()

        # 업데이트 간격 체크
        if self._last_update and (now - self._last_update) < self._update_interval:
            if self._last_state:
                return self._last_state

        # 상위 N개 코인의 ATR% 계산
        atr_pcts: list[tuple[str, float]] = []

        for symbol in symbols[:self.top_n * 2]:  # 여유분 포함
            if len(atr_pcts) >= self.top_n:
                break

            buffer = candle_manager.get_buffer(symbol, "5m")
            if not buffer or len(buffer.candles) < self.atr_period + 1:
                continue

            candles = buffer.get_latest(self.atr_period + 1)
            if len(candles) < self.atr_period + 1:
                continue

            # ATR 계산
            atr = self._calc_atr(candles)
            price = candles[-1].close if candles else 0

            if price > 0 and atr > 0:
                atr_pct = atr / price
                atr_pcts.append((symbol, atr_pct))

        # 데이터 부족
        if len(atr_pcts) < 5:
            logger.warning("KMVI: Not enough data", count=len(atr_pcts))
            return self._last_state or self._default_state()

        # 80% 분위수 계산
        sorted_pcts = sorted([p[1] for p in atr_pcts])
        idx = int(len(sorted_pcts) * self.percentile)
        idx = min(idx, len(sorted_pcts) - 1)
        kmvi = sorted_pcts[idx]

        # Tier 및 정책 결정
        tier, micro_allowed, entry_allowed, sizing_mult = self._determine_tier(kmvi)

        state = KMVIState(
            value=kmvi,
            value_pct=kmvi * 100,
            tier=tier,
            micro_scalper_allowed=micro_allowed,
            new_entry_allowed=entry_allowed,
            sizing_multiplier=sizing_mult,
            top_coins=atr_pcts[:self.top_n],
            timestamp=now,
        )

        # 캐시 업데이트
        self._last_state = state
        self._last_update = now

        # 히스토리 추가
        self._history.append((now, kmvi))
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # 로그 (Tier 변경 시)
        if self._last_state and self._last_state.tier != tier:
            logger.warning(
                "KMVI tier changed",
                kmvi=f"{kmvi:.2%}",
                tier=tier.value,
                micro_allowed=micro_allowed,
                entry_allowed=entry_allowed,
            )
        else:
            logger.debug(
                "KMVI updated",
                kmvi=f"{kmvi:.2%}",
                tier=tier.value,
            )

        return state

    def _determine_tier(
        self,
        kmvi: float,
    ) -> tuple[KMVITier, bool, bool, float]:
        """
        KMVI 기반 Tier 및 정책 결정

        Returns:
            (tier, micro_scalper_allowed, new_entry_allowed, sizing_multiplier)
        """
        if kmvi >= self.T3:
            # CRISIS: 모든 진입 OFF
            return KMVITier.CRISIS, False, False, 0.3
        elif kmvi >= self.T2:
            # HIGH: Micro Scalper OFF
            return KMVITier.HIGH, False, True, 0.6
        elif kmvi >= self.T1:
            # ELEVATED: 사이징 축소
            return KMVITier.ELEVATED, True, True, 0.8
        else:
            # NORMAL
            return KMVITier.NORMAL, True, True, 1.0

    def _calc_atr(self, candles: list) -> float:
        """ATR (Average True Range) 계산"""
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

        if not true_ranges:
            return 0

        return sum(true_ranges) / len(true_ranges)

    def _default_state(self) -> KMVIState:
        """기본 상태 (데이터 부족 시)"""
        return KMVIState(
            value=0.01,  # 기본값 1%
            value_pct=1.0,
            tier=KMVITier.NORMAL,
            micro_scalper_allowed=True,
            new_entry_allowed=True,
            sizing_multiplier=1.0,
        )

    def get_state(self) -> Optional[KMVIState]:
        """현재 상태 반환"""
        return self._last_state

    def get_trend(self, minutes: int = 30) -> Optional[str]:
        """
        KMVI 트렌드 반환

        Returns:
            "RISING", "FALLING", "STABLE", or None
        """
        if len(self._history) < 2:
            return None

        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        recent = [v for t, v in self._history if t >= cutoff]

        if len(recent) < 2:
            return None

        first_half = sum(recent[:len(recent)//2]) / (len(recent)//2)
        second_half = sum(recent[len(recent)//2:]) / (len(recent) - len(recent)//2)

        change = (second_half - first_half) / first_half if first_half > 0 else 0

        if change > 0.1:  # +10% 이상
            return "RISING"
        elif change < -0.1:  # -10% 이상
            return "FALLING"
        else:
            return "STABLE"

    def get_status(self) -> dict:
        """상태 조회"""
        state = self._last_state
        return {
            "enabled": True,
            "current_state": state.to_dict() if state else None,
            "trend": self.get_trend(),
            "thresholds": {
                "T1": f"{self.T1:.1%}",
                "T2": f"{self.T2:.1%}",
                "T3": f"{self.T3:.1%}",
            },
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "history_count": len(self._history),
        }


# 싱글톤 인스턴스
_kmvi_calculator: Optional[KMVICalculator] = None


def get_kmvi_calculator() -> KMVICalculator:
    """KMVI Calculator 싱글톤"""
    global _kmvi_calculator
    if _kmvi_calculator is None:
        _kmvi_calculator = KMVICalculator()
    return _kmvi_calculator
