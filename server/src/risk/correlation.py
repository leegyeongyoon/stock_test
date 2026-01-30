"""Correlation Spike Guard

하락장에서 알트 상관 0.9+ → 분산이 동일 베팅이 됨

상관 급증 + BTC 충격 조합 시:
- Satellite 신규 진입 OFF
- 기존 알트 노출 강제 축소
- BTC 헤지 ON 고려
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

import structlog

from src.risk.config import get_risk_config

if TYPE_CHECKING:
    from src.data.candle_manager import CandleManager

logger = structlog.get_logger()


class GuardAction(str, Enum):
    """상관 가드 액션"""

    NONE = "NONE"  # 정상
    WARN = "WARN"  # 경고 (필터 강화)
    REDUCE = "REDUCE"  # 노출 축소
    BLOCK = "BLOCK"  # 신규 진입 차단
    HEDGE = "HEDGE"  # 헤지 필요


@dataclass
class CorrelationState:
    """상관 상태"""

    avg_correlation: float = 0.0
    btc_return_1h: float = 0.0
    btc_volatility_ratio: float = 1.0
    is_spike: bool = False
    is_btc_shock: bool = False
    action: GuardAction = GuardAction.NONE
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "avg_correlation": self.avg_correlation,
            "btc_return_1h": self.btc_return_1h,
            "btc_volatility_ratio": self.btc_volatility_ratio,
            "is_spike": self.is_spike,
            "is_btc_shock": self.is_btc_shock,
            "action": self.action.value,
            "timestamp": self.timestamp.isoformat(),
        }


class CorrelationGuard:
    """
    상관관계 급증 감시

    알트코인들 간의 상관관계가 급증하면
    분산 효과가 사라지므로 노출을 줄여야 함
    """

    # 모니터링 대상 심볼
    ALT_SYMBOLS = ["ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    BTC_SYMBOL = "BTCUSDT"

    def __init__(self, candle_manager: "CandleManager"):
        self.candle_manager = candle_manager
        self.config = get_risk_config()
        self._last_state: Optional[CorrelationState] = None

    def calculate_rolling_correlation(
        self,
        symbols: list[str] = None,
        window: int = None,
    ) -> float:
        """
        심볼들 간의 평균 상관관계 계산

        Args:
            symbols: 대상 심볼 리스트
            window: 룩백 기간 (봉 수)

        Returns:
            평균 상관계수 (0~1)
        """
        if symbols is None:
            symbols = self.ALT_SYMBOLS
        if window is None:
            window = self.config.corr_lookback_bars

        # 각 심볼의 수익률 시계열 수집
        returns_data = {}
        for symbol in symbols:
            closes = self.candle_manager.get_closes(symbol, "1h", window + 1)
            if len(closes) < window + 1:
                continue

            # 수익률 계산
            returns = []
            for i in range(1, len(closes)):
                ret = (closes[i] - closes[i - 1]) / closes[i - 1]
                returns.append(ret)

            if len(returns) >= window:
                returns_data[symbol] = returns[-window:]

        if len(returns_data) < 2:
            return 0.0

        # 페어별 상관 계산
        correlations = []
        symbols_list = list(returns_data.keys())

        for i in range(len(symbols_list)):
            for j in range(i + 1, len(symbols_list)):
                r1 = returns_data[symbols_list[i]]
                r2 = returns_data[symbols_list[j]]
                corr = self._pearson_correlation(r1, r2)
                correlations.append(abs(corr))

        if not correlations:
            return 0.0

        return sum(correlations) / len(correlations)

    def _pearson_correlation(self, x: list[float], y: list[float]) -> float:
        """피어슨 상관계수 계산"""
        n = min(len(x), len(y))
        if n < 2:
            return 0.0

        x = x[:n]
        y = y[:n]

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        denom_x = sum((x[i] - mean_x) ** 2 for i in range(n)) ** 0.5
        denom_y = sum((y[i] - mean_y) ** 2 for i in range(n)) ** 0.5

        if denom_x == 0 or denom_y == 0:
            return 0.0

        return numerator / (denom_x * denom_y)

    def check_correlation_spike(self) -> bool:
        """상관 급증 여부 확인"""
        avg_corr = self.calculate_rolling_correlation()
        return avg_corr >= self.config.corr_spike_threshold

    def check_btc_shock(self) -> tuple[bool, float, float]:
        """
        BTC 충격 여부 확인

        Returns:
            (is_shock, return_1h, volatility_ratio)
        """
        cfg = self.config
        closes = self.candle_manager.get_closes(self.BTC_SYMBOL, "1h", 25)

        if len(closes) < 25:
            return False, 0.0, 1.0

        # 1시간 수익률
        return_1h = (closes[-1] - closes[-2]) / closes[-2] if closes[-2] > 0 else 0

        # 변동성 계산 (최근 vs 이전)
        recent_volatility = self._calc_volatility(closes[-5:])
        prev_volatility = self._calc_volatility(closes[-25:-5])

        volatility_ratio = (
            recent_volatility / prev_volatility if prev_volatility > 0 else 1.0
        )

        # 충격 판단: 급락 + 변동성 급증
        is_shock = (
            return_1h <= cfg.corr_btc_drop_pct
            or volatility_ratio >= cfg.corr_btc_volatility_spike
        )

        return is_shock, return_1h, volatility_ratio

    def _calc_volatility(self, prices: list[float]) -> float:
        """가격 리스트의 변동성 계산"""
        if len(prices) < 2:
            return 0.0

        returns = []
        for i in range(1, len(prices)):
            ret = (prices[i] - prices[i - 1]) / prices[i - 1]
            returns.append(ret)

        if not returns:
            return 0.0

        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return variance ** 0.5

    def get_guard_action(self) -> CorrelationState:
        """
        상관 가드 액션 결정

        Returns:
            CorrelationState with recommended action
        """
        avg_corr = self.calculate_rolling_correlation()
        is_spike = avg_corr >= self.config.corr_spike_threshold
        is_btc_shock, btc_return, volatility_ratio = self.check_btc_shock()

        # 액션 결정
        action = GuardAction.NONE

        if is_spike and is_btc_shock:
            # 최악의 조합: 상관 급증 + BTC 충격
            action = GuardAction.HEDGE
            logger.warning(
                "Correlation guard: HEDGE required",
                avg_correlation=avg_corr,
                btc_return=btc_return,
                volatility_ratio=volatility_ratio,
            )
        elif is_spike:
            # 상관 급증만
            action = GuardAction.REDUCE
            logger.warning(
                "Correlation guard: REDUCE exposure",
                avg_correlation=avg_corr,
            )
        elif is_btc_shock:
            # BTC 충격만
            action = GuardAction.BLOCK
            logger.warning(
                "Correlation guard: BLOCK new entries",
                btc_return=btc_return,
                volatility_ratio=volatility_ratio,
            )
        elif avg_corr >= self.config.corr_spike_threshold * 0.8:
            # 경고 수준
            action = GuardAction.WARN
            logger.info(
                "Correlation guard: WARN",
                avg_correlation=avg_corr,
            )

        state = CorrelationState(
            avg_correlation=avg_corr,
            btc_return_1h=btc_return,
            btc_volatility_ratio=volatility_ratio,
            is_spike=is_spike,
            is_btc_shock=is_btc_shock,
            action=action,
        )

        self._last_state = state
        return state

    def get_exposure_multiplier(self) -> float:
        """
        상관 상태에 따른 노출 배수

        Returns:
            0.0 ~ 1.0
        """
        state = self.get_guard_action()

        if state.action == GuardAction.HEDGE:
            return 0.0  # 신규 진입 완전 차단
        elif state.action == GuardAction.BLOCK:
            return 0.0  # 신규 진입 차단
        elif state.action == GuardAction.REDUCE:
            return 0.3  # 30%만 허용
        elif state.action == GuardAction.WARN:
            return 0.7  # 70% 허용
        else:
            return 1.0  # 정상

    def get_last_state(self) -> Optional[CorrelationState]:
        """마지막 상태 조회"""
        return self._last_state
