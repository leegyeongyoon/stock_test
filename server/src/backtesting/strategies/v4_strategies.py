"""v4 신규 전략 (백테스트 전용)

5분봉 암호화폐 시장에서 데이터 분석 기반 검증:
- VolumeSurgeReversal: 극단적 거래량 + 급락 반전 패턴

v3_live.py 라이브 파라미터는 변경하지 않음.
"""

from datetime import datetime
from typing import Optional

from src.backtesting.data.data_loader import HistoryProvider


class VolumeSurgeReversalSignalGenerator:
    """
    전략: VOLUME_SURGE_REVERSAL (극단적 거래량 반전)

    데이터 분석 기반 고도화 (v4.1):
    - 승리 거래: change_3 avg -3.1% (패배 -1.9%) → min_drop 2.5%로 상향
    - 승리 거래: close_position avg 0.83 (패배 0.90) → max 0.90 필터 추가
    - 09시(KST) 93.3% WR, 00시 12.5%, 02시 14.3% → 시간대 필터
    - 0-15분 보유 63.6% WR, 1-2시간 37.5% → 빠른 청산 유도

    진입: RVOL > 4.0 + 3봉내 -2.5% 하락 + 양봉 반전
          + close 위치 60-90% (과도한 반등 필터링)
          + 현재봉 거래량 > 직전봉 (거래량 증가)
    포지션: 20%, 쿨다운 20분
    """

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.min_rvol = self.params.get("min_rvol", 4.0)
        self.min_drop_pct = self.params.get("min_drop_pct", 0.025)
        self.min_close_position = self.params.get("min_close_position", 0.60)
        self.max_close_position = self.params.get("max_close_position", 0.90)
        self.position_pct = self.params.get("position_pct", 0.20)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 20)
        self._blocked_hours_kst = self.params.get("blocked_hours_kst", [])

    def __call__(self, timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            signal = self._evaluate(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate(self, timestamp, history, symbol) -> Optional[dict]:
        # 시간대 필터 (KST = UTC+9)
        if self._blocked_hours_kst:
            kst_hour = (timestamp.hour + 9) % 24
            if kst_hour in self._blocked_hours_kst:
                return None

        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 30)
        if len(candles) < 5:
            return None

        current = candles[-1]
        price = current.close
        if price <= 0:
            return None

        # 1. 양봉 필수
        if current.close <= current.open:
            return None

        # 2. RVOL > 4.0 (극단적 거래량)
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # 3. 3봉내 급락 (min_drop_pct 이상)
        price_3_ago = candles[-4].close if len(candles) >= 4 else None
        if price_3_ago is None or price_3_ago <= 0:
            return None
        change_3 = (price - price_3_ago) / price_3_ago
        if change_3 > -self.min_drop_pct:
            return None

        # 4. close 위치 범위 필터 (60-90%: 과도한 반등은 가짜 시그널)
        candle_range = current.high - current.low
        if candle_range <= 0:
            return None
        close_position = (current.close - current.low) / candle_range
        if close_position < self.min_close_position:
            return None
        if close_position > self.max_close_position:
            return None

        # 5. 현재봉 거래량 > 직전봉 거래량 (증가 확인)
        if current.quote_volume <= candles[-2].quote_volume:
            return None

        score = 65 + min(rvol, 8) * 3 + min(abs(change_3) / 0.03, 1.0) * 10 + close_position * 5

        self._last_entry[symbol] = timestamp
        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "VOLUME_SURGE_REVERSAL",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price, "rvol": rvol, "change_3": change_3,
                "close_position": close_position,
            },
        }
