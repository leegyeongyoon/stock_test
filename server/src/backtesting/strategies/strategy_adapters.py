"""백테스트용 전략 어댑터

실제 전략 로직을 백테스트에서 사용할 수 있도록 변환
"""

from datetime import datetime
from typing import Optional, Callable

import structlog

from src.backtesting.data.data_loader import HistoryProvider

logger = structlog.get_logger()


class PullbackSignalGenerator:
    """
    PULLBACK 전략 시그널 생성기

    눌림목 매수 전략:
    - 24시간 내 급등 이력 (5%+)
    - 고점 대비 3~8% 눌림
    - RVOL 안정적 (1.0 이하)
    - 반등 조짐 (5분 양봉)
    """

    def __init__(self, params: dict = None):
        self.params = params or {}

        # 설정값 (v4: 공격적 - 월 20% 목표)
        self.min_pullback_pct = self.params.get("min_pullback_pct", 0.03)  # 3% 눌림
        self.max_pullback_pct = self.params.get("max_pullback_pct", 0.15)  # 15% 눌림
        self.min_range_pct = self.params.get("min_range_pct", 0.05)  # 5% 변동폭
        self.max_rvol = self.params.get("max_rvol", 2.0)  # 2.0x RVOL
        self.min_score = self.params.get("min_score", 60)  # 최소 60점
        self.position_pct = self.params.get("position_pct", 0.15)  # 15% 포지션

        # 재진입 방지 (v4: 쿨다운 완화)
        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 30)  # 30분

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        """시그널 생성"""
        signals = []

        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)

        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        """심볼 평가"""
        # 재진입 쿨다운 체크
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        # 캔들 데이터 가져오기
        candles = history.get_candles(symbol, "5m", 50)
        if len(candles) < 30:
            return None

        # 현재가
        price = candles[-1].close

        # 24시간 고저점 (약 288개 5분봉이지만 50개로 대체)
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        highest = max(highs)
        lowest = min(lows)

        # 24시간 변동폭
        range_pct = (highest - lowest) / lowest if lowest > 0 else 0
        if range_pct < self.min_range_pct:
            return None  # 변동폭 부족

        # 눌림 깊이
        pullback_pct = (highest - price) / highest if highest > 0 else 0
        if pullback_pct < self.min_pullback_pct or pullback_pct > self.max_pullback_pct:
            return None  # 눌림 범위 밖

        # RVOL 체크
        rvol = history.calc_rvol(symbol, "5m", 20)
        if rvol and rvol > self.max_rvol:
            return None  # 거래량 과열

        # 🔑 반등 확인 게이팅 (REBOUND 핵심 성공 요인)
        # 현재 캔들이 양봉이어야 진입
        current_candle = candles[-1]
        if current_candle.close <= current_candle.open:
            return None  # 음봉이면 진입 안함 - 반등 확인 필요

        # 점수 계산
        score = self._calc_score(candles, price, highest, lowest, pullback_pct, rvol)

        if score < self.min_score:
            return None

        # 시그널 생성
        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "PULLBACK",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "pullback_pct": pullback_pct,
                "range_pct": range_pct,
                "rvol": rvol or 1.0,
            },
        }

    def _calc_score(
        self,
        candles,
        price: float,
        highest: float,
        lowest: float,
        pullback_pct: float,
        rvol: Optional[float],
    ) -> float:
        """점수 계산 (0~100)"""
        score = 0.0

        # 1. 눌림 깊이 점수 (0~30)
        if 0.03 <= pullback_pct <= 0.05:
            score += 30  # 이상적
        elif 0.05 < pullback_pct <= 0.08:
            score += 25
        elif 0.02 <= pullback_pct < 0.03:
            score += 20
        elif 0.08 < pullback_pct <= 0.12:
            score += 15
        else:
            score += 10

        # 2. 거래량 점수 (0~25)
        if rvol:
            if rvol <= 0.8:
                score += 25  # 거래량 감소 (좋음)
            elif rvol <= 1.2:
                score += 20
            elif rvol <= 1.5:
                score += 15
            else:
                score += 5

        # 3. 반등 조짐 (0~25)
        if len(candles) >= 3:
            # 최근 3봉 중 양봉 수
            recent = candles[-3:]
            bullish = sum(1 for c in recent if c.close > c.open)
            if bullish >= 2:
                score += 25
            elif bullish == 1:
                score += 15
            else:
                score += 5

        # 4. 저점 대비 위치 (0~20)
        if lowest > 0:
            low_dist = (price - lowest) / lowest
            if low_dist <= 0.02:
                score += 20  # 저점 근접
            elif low_dist <= 0.05:
                score += 15
            elif low_dist <= 0.08:
                score += 10
            else:
                score += 5

        return min(100, score)


class ReboundSignalGenerator:
    """
    REBOUND 전략 시그널 생성기

    급락 반등 전략:
    - 1분 내 -2% 이상 급락
    - RVOL 급증 (3x+)
    - 급락 후 반등 시작
    """

    def __init__(self, params: dict = None):
        self.params = params or {}

        # v4: 공격적 - 월 20% 목표
        self.min_drop_pct = self.params.get("min_drop_pct", 0.015)  # 1.5% 급락
        self.min_rvol = self.params.get("min_rvol", 2.0)  # RVOL 2.0x
        self.min_score = self.params.get("min_score", 60)
        self.position_pct = self.params.get("position_pct", 0.15)  # 15% 포지션

        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 10)  # 10분

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []

        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)

        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        # 쿨다운 체크
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 20)
        if len(candles) < 5:
            return None

        # 최근 급락 체크
        recent = candles[-3:]
        price = recent[-1].close

        # 3봉 전 대비 변화
        prev_price = recent[0].open
        change_pct = (price - prev_price) / prev_price if prev_price > 0 else 0

        # 급락 조건
        if change_pct > -self.min_drop_pct:
            return None  # 급락 아님

        # RVOL 체크
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # 반등 조짐 체크 (마지막 봉이 양봉)
        if recent[-1].close <= recent[-1].open:
            return None  # 아직 반등 시작 안함

        # 점수 계산
        score = self._calc_score(change_pct, rvol, recent)

        if score < self.min_score:
            return None

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "REBOUND",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "drop_pct": change_pct,
                "rvol": rvol,
            },
        }

    def _calc_score(self, drop_pct: float, rvol: float, recent) -> float:
        score = 0.0

        # 급락 강도 (0~40)
        drop_abs = abs(drop_pct)
        if drop_abs >= 0.05:
            score += 40
        elif drop_abs >= 0.03:
            score += 30
        else:
            score += 20

        # RVOL (0~30)
        if rvol >= 5.0:
            score += 30
        elif rvol >= 3.0:
            score += 25
        else:
            score += 15

        # 반등 강도 (0~30)
        if recent[-1].close > recent[-1].open:
            candle_range = recent[-1].high - recent[-1].low
            body = recent[-1].close - recent[-1].open
            if candle_range > 0:
                body_ratio = body / candle_range
                if body_ratio >= 0.6:
                    score += 30
                elif body_ratio >= 0.4:
                    score += 20
                else:
                    score += 10

        return min(100, score)


class DipScalperSignalGenerator:
    """
    DIP_SCALPER 전략 시그널 생성기

    급락 스캘핑:
    - 1분 내 -1.5% 급락
    - RVOL 급증
    - 빠른 반등 포착
    """

    def __init__(self, params: dict = None):
        self.params = params or {}

        # v4: 공격적 - 월 20% 목표
        self.min_dip_pct = self.params.get("min_dip_pct", 0.015)  # 1.5% 급락
        self.min_rvol = self.params.get("min_rvol", 1.5)  # 1.5x RVOL
        self.min_score = self.params.get("min_score", 50)  # 50점
        self.position_pct = self.params.get("position_pct", 0.12)  # 12% 포지션

        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 10)  # 10분

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []

        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)

        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 10)
        if len(candles) < 3:
            return None

        price = candles[-1].close
        prev_price = candles[-2].close

        # 급락 체크
        change_pct = (price - prev_price) / prev_price if prev_price > 0 else 0

        if change_pct > -self.min_dip_pct:
            return None

        # RVOL
        rvol = history.calc_rvol(symbol, "5m", 10)
        if not rvol or rvol < self.min_rvol:
            return None

        # 🔑 반등 확인 게이팅 (v4: 완화)
        current_candle = candles[-1]
        # 저가 대비 반등 체크
        bounce_from_low = (current_candle.close - current_candle.low) / current_candle.low if current_candle.low > 0 else 0
        if bounce_from_low < 0.005:  # 저가 대비 0.5% 이상 반등해야 함
            return None

        # 점수
        score = 50 + abs(change_pct) * 500 + min(rvol, 5) * 5

        if score < self.min_score:
            return None

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "DIP_SCALPER",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "dip_pct": change_pct,
                "rvol": rvol,
            },
        }


class AttackSignalGenerator:
    """
    ATTACK 전략 시그널 생성기

    급등주 추격 전략:
    - 5분봉 고점 돌파
    - RVOL 급증 (2x+)
    - 거래대금 증가
    """

    def __init__(self, params: dict = None):
        self.params = params or {}

        # v4: 공격적 - 월 20% 목표
        self.min_breakout_pct = self.params.get("min_breakout_pct", 0.02)  # 2% 돌파
        self.min_rvol = self.params.get("min_rvol", 2.0)  # 2.0x RVOL
        self.min_score = self.params.get("min_score", 60)  # 60점
        self.position_pct = self.params.get("position_pct", 0.15)  # 15% 포지션

        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 15)  # 15분

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []

        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)

        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        # 쿨다운 체크
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 20)
        if len(candles) < 12:
            return None

        price = candles[-1].close

        # 최근 12봉 고점 (1시간)
        recent_12 = candles[-12:-1]  # 현재봉 제외
        highest_12 = max(c.high for c in recent_12)

        # 고점 돌파 체크
        breakout_pct = (price - highest_12) / highest_12 if highest_12 > 0 else 0
        if breakout_pct < self.min_breakout_pct:
            return None  # 돌파 아님

        # RVOL 체크
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # 🔑 추격 방지 게이트 (불트랩 회피)
        # 5분봉 변화율 계산
        change_5m = (price - candles[-2].close) / candles[-2].close if candles[-2].close > 0 else 0

        # 현재 봉 내 변화율 (시가 대비)
        current_candle = candles[-1]
        change_intra = (price - current_candle.open) / current_candle.open if current_candle.open > 0 else 0

        # 5분봉 +5% 이상 + 현재 봉 +2% 이상이면 추격 진입 → 패스
        if change_5m > 0.05 and change_intra > 0.02:
            return None  # 과열 추격 방지

        # 🔑 반등 확인: 현재 캔들이 양봉이어야 함 (REBOUND 패턴)
        if current_candle.close <= current_candle.open:
            return None  # 음봉이면 진입 안함

        # 점수 계산
        score = self._calc_score(candles, breakout_pct, rvol)

        if score < self.min_score:
            return None

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "buy",
            "strategy": "ATTACK",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "breakout_pct": breakout_pct,
                "rvol": rvol,
            },
        }

    def _calc_score(self, candles, breakout_pct: float, rvol: float) -> float:
        score = 0.0

        # 돌파 강도 (0~30)
        if breakout_pct >= 0.05:
            score += 30
        elif breakout_pct >= 0.03:
            score += 25
        elif breakout_pct >= 0.02:
            score += 20
        else:
            score += 10

        # RVOL (0~30)
        if rvol >= 5.0:
            score += 30
        elif rvol >= 3.0:
            score += 25
        elif rvol >= 2.0:
            score += 20
        else:
            score += 10

        # 캔들 양봉 (0~20)
        recent = candles[-3:]
        bullish = sum(1 for c in recent if c.close > c.open)
        if bullish >= 3:
            score += 20
        elif bullish >= 2:
            score += 15
        else:
            score += 5

        # 거래량 트렌드 (0~20)
        volumes = [c.quote_volume for c in candles[-5:]]
        if len(volumes) >= 2 and volumes[-1] > volumes[-2]:
            score += 20
        else:
            score += 10

        return min(100, score)


class ShortBreakdownSignalGenerator:
    """
    SHORT_BREAKDOWN 전략 시그널 생성기

    하락 돌파 숏 전략:
    - 지지선 이탈
    - 거래량 급증
    - 하락 모멘텀 확인

    백테스트 결과: 56.2% 승률, +0.78%, PF 1.57
    """

    def __init__(self, params: dict = None):
        self.params = params or {}

        # v2: 공격적 (더 많은 기회)
        self.min_breakdown_pct = self.params.get("min_breakdown_pct", 0.015)  # 1.5% 돌파
        self.min_rvol = self.params.get("min_rvol", 1.5)  # 1.5x RVOL
        self.min_score = self.params.get("min_score", 55)
        self.position_pct = self.params.get("position_pct", 0.15)

        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 10)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 20)
        if len(candles) < 12:
            return None

        price = candles[-1].close

        # 최근 12봉 저점
        recent_12 = candles[-12:-1]
        lowest_12 = min(c.low for c in recent_12)

        # 저점 이탈 체크
        breakdown_pct = (lowest_12 - price) / lowest_12 if lowest_12 > 0 else 0
        if breakdown_pct < self.min_breakdown_pct:
            return None

        # RVOL 체크
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        # 하락 확인: 현재 캔들이 음봉
        current_candle = candles[-1]
        if current_candle.close >= current_candle.open:
            return None  # 양봉이면 진입 안함

        score = 50 + breakdown_pct * 500 + min(rvol, 5) * 5

        if score < self.min_score:
            return None

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "sell",  # SHORT
            "strategy": "SHORT_BREAKDOWN",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "breakdown_pct": breakdown_pct,
                "rvol": rvol,
            },
        }


class ShortRallyFadeSignalGenerator:
    """
    SHORT_RALLY_FADE 전략 시그널 생성기

    급등 후 하락 숏:
    - 급등 후 고점 형성
    - 음봉 전환 확인
    - 거래량 감소

    백테스트 결과: 38.8% 승률, +1.65%, PF 1.11
    """

    def __init__(self, params: dict = None):
        self.params = params or {}

        # v2: 최적화된 파라미터
        self.min_rally_pct = self.params.get("min_rally_pct", 0.05)  # 5% 급등 후
        self.min_fade_pct = self.params.get("min_fade_pct", 0.02)    # 2% 이상 하락
        self.min_score = self.params.get("min_score", 75)
        self.position_pct = self.params.get("position_pct", 0.15)

        self._last_entry: dict[str, datetime] = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 30)

    def __call__(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbols: list[str],
    ) -> list[dict]:
        signals = []
        for symbol in symbols:
            signal = self._evaluate_symbol(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate_symbol(
        self,
        timestamp: datetime,
        history: HistoryProvider,
        symbol: str,
    ) -> Optional[dict]:
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 20)
        if len(candles) < 10:
            return None

        price = candles[-1].close

        # 최근 10봉 고점
        highest = max(c.high for c in candles[-10:])
        lowest_before = min(c.low for c in candles[-10:-3])

        # 급등폭 체크
        rally_pct = (highest - lowest_before) / lowest_before if lowest_before > 0 else 0
        if rally_pct < self.min_rally_pct:
            return None

        # 고점 대비 하락폭
        fade_pct = (highest - price) / highest if highest > 0 else 0
        if fade_pct < self.min_fade_pct:
            return None

        # 하락 확인: 최근 2봉 연속 음봉
        recent_2 = candles[-2:]
        bearish = sum(1 for c in recent_2 if c.close < c.open)
        if bearish < 2:
            return None

        score = 50 + fade_pct * 300 + rally_pct * 200

        if score < self.min_score:
            return None

        self._last_entry[symbol] = timestamp

        return {
            "symbol": symbol,
            "action": "sell",  # SHORT
            "strategy": "SHORT_RALLY_FADE",
            "score": score,
            "position_pct": self.position_pct,
            "indicators": {
                "price": price,
                "rally_pct": rally_pct,
                "fade_pct": fade_pct,
            },
        }


def get_signal_generator(strategy: str, params: dict = None) -> Callable:
    """전략별 시그널 생성기 반환"""
    generators = {
        "PULLBACK": PullbackSignalGenerator,
        "REBOUND": ReboundSignalGenerator,
        "DIP_SCALPER": DipScalperSignalGenerator,
        "ATTACK": AttackSignalGenerator,
        "SHORT_BREAKDOWN": ShortBreakdownSignalGenerator,
        "SHORT_RALLY_FADE": ShortRallyFadeSignalGenerator,
    }

    if strategy not in generators:
        raise ValueError(f"Unknown strategy: {strategy}")

    return generators[strategy](params)


def get_exit_checker(strategy: str, params: dict = None) -> Callable:
    """전략별 청산 체커 반환"""
    from src.backtesting.engine.backtest_engine import create_pullback_exit_checker

    params = params or {}

    # 기본값 설정 (v4: 공격적 - 월 20% 목표)
    default_params = {
        "PULLBACK": {
            "stop_loss_pct": -0.01,       # -1.0%
            "take_profit_pct": 0.015,     # +1.5%
            "trailing_trigger_pct": 0.01,
            "trailing_stop_pct": 0.005,
        },
        "REBOUND": {
            "stop_loss_pct": -0.01,       # -1.0%
            "take_profit_pct": 0.015,     # +1.5%
            "trailing_trigger_pct": 0.008,
            "trailing_stop_pct": 0.004,
        },
        "DIP_SCALPER": {
            "stop_loss_pct": -0.008,      # -0.8%
            "take_profit_pct": 0.012,     # +1.2%
            "trailing_trigger_pct": 0.008,
            "trailing_stop_pct": 0.004,
        },
        "ATTACK": {
            "stop_loss_pct": -0.012,      # -1.2%
            "take_profit_pct": 0.025,     # +2.5%
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.008,
        },
        "SHORT_BREAKDOWN": {
            "stop_loss_pct": -0.01,       # -1.0% (숏이므로 가격 상승 시 손절)
            "take_profit_pct": 0.015,     # +1.5% (숏이므로 가격 하락 시 익절)
            "trailing_trigger_pct": 0.01,
            "trailing_stop_pct": 0.005,
        },
        "SHORT_RALLY_FADE": {
            "stop_loss_pct": -0.012,      # -1.2%
            "take_profit_pct": 0.02,      # +2.0%
            "trailing_trigger_pct": 0.012,
            "trailing_stop_pct": 0.006,
        },
    }

    strategy_defaults = default_params.get(strategy, default_params["PULLBACK"])

    return create_pullback_exit_checker(
        stop_loss_pct=params.get("stop_loss_pct", strategy_defaults["stop_loss_pct"]),
        take_profit_pct=params.get("take_profit_pct", strategy_defaults["take_profit_pct"]),
        trailing_trigger_pct=params.get("trailing_trigger_pct", strategy_defaults["trailing_trigger_pct"]),
        trailing_stop_pct=params.get("trailing_stop_pct", strategy_defaults["trailing_stop_pct"]),
    )
