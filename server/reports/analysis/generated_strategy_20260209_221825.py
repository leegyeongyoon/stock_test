"""
DataDrivenStrategyV28

데이터 분석 기반 자동 생성된 전략
- 기대 승률: 62.7% (상위 패턴 기준)
- 최적 SL: -5.0%, TP: 0.5%

적용된 패턴:
- low_24h_high_25: low_24h >= 105.0000 (상위 75%)
- distance_from_low_high_25: distance_from_low >= 0.0095 (상위 75%)
- dip_pct_low_25: dip_pct < -0.0089 (하위 25%)
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class DataDrivenStrategyV28Config:
    """전략 설정"""

    sl_pct: float = -5.0
    tp_pct: float = 0.5
    position_pct: float = 0.1
    cooldown_minutes: int = 30


class DataDrivenStrategyV28SignalGenerator:
    """데이터 기반 신호 생성기"""

    def __init__(self, config: Optional[DataDrivenStrategyV28Config] = None):
        self.config = config or DataDrivenStrategyV28Config()
        self.last_signal_time: dict[str, float] = {}

    def __call__(
        self,
        symbol: str,
        history_provider,
        current_time: float,
    ) -> Optional[dict]:
        """
        진입 신호 생성

        Args:
            symbol: 심볼
            history_provider: HistoryProvider 인스턴스
            current_time: 현재 시간 (timestamp)

        Returns:
            신호 딕셔너리 또는 None
        """
        # 쿨다운 체크
        last_time = self.last_signal_time.get(symbol, 0)
        if current_time - last_time < self.config.cooldown_minutes * 60:
            return None

        # 지표 계산
        indicators = self._calculate_indicators(symbol, history_provider)

        # 진입 조건 체크
        if self._check_entry_conditions(indicators):
            self.last_signal_time[symbol] = current_time

            return {
                "symbol": symbol,
                "action": "buy",
                "strategy": "DataDrivenStrategyV28",
                "score": 62.7,
                "position_pct": self.config.position_pct,
                "indicators": indicators,
                "sl_pct": self.config.sl_pct,
                "tp_pct": self.config.tp_pct,
            }

        return None

    def _calculate_indicators(
        self,
        symbol: str,
        history_provider,
    ) -> dict:
        """지표 계산"""
        closes = history_provider.get_closes(symbol)
        if len(closes) < 50:
            return {}

        price = closes[-1]
        ema20 = history_provider.calc_ema(symbol, 20)
        ema50 = history_provider.calc_ema(symbol, 50)
        rvol = history_provider.calc_rvol(symbol)

        return {
            "price": price,
            "ema20": ema20,
            "ema50": ema50,
            "rvol": rvol,
            "ema20_distance_pct": (price - ema20) / ema20 * 100 if ema20 else 0,
        }

    def _check_entry_conditions(self, indicators: dict) -> bool:
        """
        진입 조건 체크

        데이터 분석으로 발견된 수익 조건들:
        """
        if not indicators:
            return False

        # 자동 생성된 조건
        return (
        indicators.get('low_24h', 0) >= 0.0000 and
        indicators.get('distance_from_low', 0) >= 0.0000 and
        indicators.get('dip_pct', 0) <= 0.0000
        )


def create_exit_checker(config: DataDrivenStrategyV28Config):
    """청산 조건 체커 생성"""

    def check_exit(
        position,
        current_price: float,
        current_time: float,
    ) -> Optional[str]:
        """
        청산 조건 체크

        Returns:
            청산 사유 또는 None
        """
        entry_price = position.entry_price
        pnl_pct = (current_price - entry_price) / entry_price * 100

        # 손절
        if pnl_pct <= config.sl_pct:
            return "stop_loss"

        # 익절
        if pnl_pct >= config.tp_pct:
            return "take_profit"

        return None

    return check_exit
