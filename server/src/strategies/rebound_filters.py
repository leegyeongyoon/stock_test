"""
Rebound Scalper Filters v4.2

3가지 안전 메커니즘:
1. VolatilityFilter - 휩쏘 장세 감지 (ATR 기반)
2. TrendFilter - 하락 추세 감지 (EMA 기반)
3. ConsecutiveLossBreaker - 연속 손실 브레이크

철학: 나쁜 시장 환경에서는 진입하지 않는다
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


# ============================================================
# 1. Volatility Filter (휩쏘 감지)
# ============================================================

@dataclass
class VolatilityState:
    """변동성 상태"""
    atr_14: float = 0.0
    atr_ratio: float = 0.0  # current ATR / avg ATR
    is_whipsaw: bool = False
    whipsaw_reason: str = ""
    last_updated: datetime = field(default_factory=datetime.now)


class VolatilityFilter:
    """
    휩쏘 장세 감지 필터

    휩쏘 조건:
    1. ATR이 평균 ATR의 N배 이상 (급격한 변동성 증가)
    2. 최근 N개 캔들에서 방향 전환이 M회 이상
    3. 스프레드가 비정상적으로 넓은 경우
    """

    def __init__(
        self,
        atr_multiplier: float = 2.0,
        direction_change_threshold: int = 4,
        spread_threshold: float = 0.003,  # 0.3%
        lookback_candles: int = 10
    ):
        self.atr_multiplier = atr_multiplier
        self.direction_change_threshold = direction_change_threshold
        self.spread_threshold = spread_threshold
        self.lookback_candles = lookback_candles
        self._state_cache: dict[str, VolatilityState] = {}

    def check(self, symbol: str, market_data: dict) -> VolatilityState:
        """
        휩쏘 상태 체크

        Args:
            symbol: 코인 심볼
            market_data: 시장 데이터 (candles, orderbook 등 포함)

        Returns:
            VolatilityState: 변동성 상태
        """
        state = VolatilityState(last_updated=datetime.now())

        candles = market_data.get("candles_1m", [])
        if len(candles) < 20:
            state.whipsaw_reason = "데이터 부족"
            return state

        # 1. ATR 계산
        atr_14 = self._calculate_atr(candles[-14:])
        avg_atr = self._calculate_avg_atr(candles[-50:]) if len(candles) >= 50 else atr_14

        state.atr_14 = atr_14
        state.atr_ratio = atr_14 / avg_atr if avg_atr > 0 else 1.0

        # ATR 급증 체크
        if state.atr_ratio > self.atr_multiplier:
            state.is_whipsaw = True
            state.whipsaw_reason = f"ATR 급증 ({state.atr_ratio:.1f}x)"
            logger.debug(f"[{symbol}] 휩쏘 감지: {state.whipsaw_reason}")
            return state

        # 2. 방향 전환 횟수 체크
        direction_changes = self._count_direction_changes(candles[-self.lookback_candles:])
        if direction_changes >= self.direction_change_threshold:
            state.is_whipsaw = True
            state.whipsaw_reason = f"방향 전환 과다 ({direction_changes}회/{self.lookback_candles}봉)"
            logger.debug(f"[{symbol}] 휩쏘 감지: {state.whipsaw_reason}")
            return state

        # 3. 스프레드 체크 (orderbook 있을 때)
        orderbook = market_data.get("orderbook", {})
        if orderbook:
            spread = self._calculate_spread(orderbook)
            if spread > self.spread_threshold:
                state.is_whipsaw = True
                state.whipsaw_reason = f"스프레드 과대 ({spread*100:.2f}%)"
                logger.debug(f"[{symbol}] 휩쏘 감지: {state.whipsaw_reason}")
                return state

        self._state_cache[symbol] = state
        return state

    def _calculate_atr(self, candles: list) -> float:
        """ATR 계산 (Average True Range)"""
        if len(candles) < 2:
            return 0.0

        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i].get("high", 0)
            low = candles[i].get("low", 0)
            prev_close = candles[i-1].get("close", 0)

            if prev_close == 0:
                continue

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr / prev_close)  # 비율로 정규화

        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    def _calculate_avg_atr(self, candles: list) -> float:
        """장기 평균 ATR 계산"""
        if len(candles) < 30:
            return self._calculate_atr(candles)

        # 14봉 ATR을 슬라이딩 윈도우로 계산하여 평균
        atr_values = []
        for i in range(14, len(candles)):
            atr = self._calculate_atr(candles[i-14:i])
            atr_values.append(atr)

        return sum(atr_values) / len(atr_values) if atr_values else 0.0

    def _count_direction_changes(self, candles: list) -> int:
        """방향 전환 횟수 계산"""
        if len(candles) < 2:
            return 0

        changes = 0
        prev_direction = None

        for candle in candles:
            open_price = candle.get("open", 0)
            close_price = candle.get("close", 0)

            if open_price == 0:
                continue

            current_direction = 1 if close_price >= open_price else -1

            if prev_direction is not None and current_direction != prev_direction:
                changes += 1

            prev_direction = current_direction

        return changes

    def _calculate_spread(self, orderbook: dict) -> float:
        """스프레드 비율 계산"""
        asks = orderbook.get("orderbook_units", [])
        bids = orderbook.get("orderbook_units", [])

        if not asks or not bids:
            return 0.0

        best_ask = asks[0].get("ask_price", 0) if asks else 0
        best_bid = bids[0].get("bid_price", 0) if bids else 0

        if best_bid == 0:
            return 0.0

        return (best_ask - best_bid) / best_bid


# ============================================================
# 2. Trend Filter (하락 추세 감지)
# ============================================================

@dataclass
class TrendState:
    """추세 상태"""
    ema_short: float = 0.0  # EMA 10
    ema_long: float = 0.0   # EMA 30
    trend_direction: int = 0  # 1=상승, -1=하락, 0=횡보
    is_downtrend: bool = False
    downtrend_reason: str = ""
    trend_strength: float = 0.0  # -1 ~ 1
    last_updated: datetime = field(default_factory=datetime.now)


class TrendFilter:
    """
    하락 추세 감지 필터

    하락 추세 조건:
    1. EMA10 < EMA30 (단기 < 장기)
    2. 현재가가 EMA30 아래
    3. 최근 고점이 점점 낮아지는 패턴 (Lower Highs)
    """

    def __init__(
        self,
        ema_short_period: int = 10,
        ema_long_period: int = 30,
        price_below_ema_threshold: float = 0.02,  # 2% 아래
        lower_highs_count: int = 3
    ):
        self.ema_short_period = ema_short_period
        self.ema_long_period = ema_long_period
        self.price_below_ema_threshold = price_below_ema_threshold
        self.lower_highs_count = lower_highs_count
        self._state_cache: dict[str, TrendState] = {}

    def check(self, symbol: str, market_data: dict) -> TrendState:
        """
        추세 상태 체크

        Args:
            symbol: 코인 심볼
            market_data: 시장 데이터

        Returns:
            TrendState: 추세 상태
        """
        state = TrendState(last_updated=datetime.now())

        # 5분봉 사용 (더 안정적인 추세 판단)
        candles = market_data.get("candles_5m", market_data.get("candles_1m", []))
        if len(candles) < self.ema_long_period + 5:
            state.downtrend_reason = "데이터 부족"
            return state

        closes = [c.get("close", 0) for c in candles if c.get("close", 0) > 0]
        if len(closes) < self.ema_long_period:
            return state

        # 1. EMA 계산
        state.ema_short = self._calculate_ema(closes, self.ema_short_period)
        state.ema_long = self._calculate_ema(closes, self.ema_long_period)

        current_price = closes[-1]

        # 추세 방향 결정
        if state.ema_short > state.ema_long * 1.005:  # 0.5% 이상 위
            state.trend_direction = 1  # 상승
        elif state.ema_short < state.ema_long * 0.995:  # 0.5% 이상 아래
            state.trend_direction = -1  # 하락
        else:
            state.trend_direction = 0  # 횡보

        # 추세 강도 계산 (-1 ~ 1)
        if state.ema_long > 0:
            state.trend_strength = (state.ema_short - state.ema_long) / state.ema_long
            state.trend_strength = max(-1.0, min(1.0, state.trend_strength * 20))  # 스케일 조정

        # 2. 하락 추세 체크
        downtrend_signals = 0
        reasons = []

        # 조건 1: EMA 배열 (단기 < 장기)
        if state.ema_short < state.ema_long:
            downtrend_signals += 1
            reasons.append("EMA 역배열")

        # 조건 2: 현재가가 EMA30 아래
        if current_price < state.ema_long * (1 - self.price_below_ema_threshold):
            downtrend_signals += 1
            reasons.append(f"가격 EMA30 {self.price_below_ema_threshold*100:.0f}%↓")

        # 조건 3: Lower Highs 패턴
        if self._has_lower_highs(candles[-20:]):
            downtrend_signals += 1
            reasons.append("Lower Highs")

        # 2개 이상 조건 충족 시 하락 추세
        if downtrend_signals >= 2:
            state.is_downtrend = True
            state.downtrend_reason = " + ".join(reasons)
            logger.debug(f"[{symbol}] 하락 추세 감지: {state.downtrend_reason}")

        self._state_cache[symbol] = state
        return state

    def _calculate_ema(self, prices: list, period: int) -> float:
        """EMA 계산"""
        if len(prices) < period:
            return prices[-1] if prices else 0.0

        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period  # SMA로 시작

        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        return ema

    def _has_lower_highs(self, candles: list) -> bool:
        """Lower Highs 패턴 감지"""
        if len(candles) < self.lower_highs_count * 3:
            return False

        # 최근 캔들을 그룹으로 나눠서 각 그룹의 고점 비교
        group_size = len(candles) // self.lower_highs_count
        highs = []

        for i in range(self.lower_highs_count):
            start = i * group_size
            end = start + group_size
            group = candles[start:end]
            group_high = max(c.get("high", 0) for c in group)
            highs.append(group_high)

        # 모든 고점이 점점 낮아지는지 체크
        for i in range(1, len(highs)):
            if highs[i] >= highs[i-1]:
                return False

        return True


# ============================================================
# 3. Consecutive Loss Breaker (연속 손실 브레이크)
# ============================================================

@dataclass
class LossRecord:
    """손실 기록"""
    symbol: str
    loss_pct: float
    timestamp: datetime
    reason: str = ""


@dataclass
class CooldownState:
    """쿨다운 상태"""
    is_in_cooldown: bool = False
    cooldown_until: Optional[datetime] = None
    consecutive_losses: int = 0
    recent_losses: list[LossRecord] = field(default_factory=list)
    total_loss_in_window: float = 0.0


class ConsecutiveLossBreaker:
    """
    연속 손실 브레이크

    목적: 연속 손실 발생 시 일시 정지하여 추가 손실 방지

    작동 방식:
    1. 손실 거래 기록
    2. 연속 N회 손실 시 쿨다운 모드 진입
    3. 쿨다운 기간 동안 해당 심볼 진입 금지
    4. 전략 전체 쿨다운 옵션 (global mode)
    """

    def __init__(
        self,
        max_consecutive_losses: int = 3,
        cooldown_minutes: int = 120,  # 2시간
        loss_window_hours: int = 24,
        global_mode: bool = False  # True면 전략 전체 정지
    ):
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_minutes = cooldown_minutes
        self.loss_window_hours = loss_window_hours
        self.global_mode = global_mode

        # symbol -> list of LossRecord
        self._loss_history: dict[str, list[LossRecord]] = defaultdict(list)
        # symbol -> CooldownState
        self._cooldown_states: dict[str, CooldownState] = {}
        # 전역 쿨다운 (global_mode용)
        self._global_cooldown_until: Optional[datetime] = None

    def record_loss(self, symbol: str, loss_pct: float, reason: str = "") -> CooldownState:
        """
        손실 기록

        Args:
            symbol: 코인 심볼
            loss_pct: 손실률 (음수)
            reason: 손실 사유

        Returns:
            CooldownState: 쿨다운 상태
        """
        record = LossRecord(
            symbol=symbol,
            loss_pct=loss_pct,
            timestamp=datetime.now(),
            reason=reason
        )

        self._loss_history[symbol].append(record)
        self._cleanup_old_records(symbol)

        # 연속 손실 체크
        consecutive = self._count_consecutive_losses(symbol)

        state = CooldownState(
            consecutive_losses=consecutive,
            recent_losses=self._loss_history[symbol][-10:],  # 최근 10개
            total_loss_in_window=self._calculate_total_loss(symbol)
        )

        if consecutive >= self.max_consecutive_losses:
            cooldown_until = datetime.now() + timedelta(minutes=self.cooldown_minutes)
            state.is_in_cooldown = True
            state.cooldown_until = cooldown_until

            logger.warning(
                f"[{symbol}] 연속 {consecutive}회 손실 → "
                f"쿨다운 진입 (until {cooldown_until.strftime('%H:%M')})"
            )

            if self.global_mode:
                self._global_cooldown_until = cooldown_until
                logger.warning("전략 전체 쿨다운 진입")

        self._cooldown_states[symbol] = state
        return state

    def record_win(self, symbol: str, profit_pct: float) -> None:
        """
        수익 거래 기록 (연속 손실 카운터 리셋)

        Args:
            symbol: 코인 심볼
            profit_pct: 수익률 (양수)
        """
        # 수익 거래는 연속 손실 카운터를 리셋
        # 하지만 기록 자체는 유지 (통계용)
        if symbol in self._cooldown_states:
            self._cooldown_states[symbol].consecutive_losses = 0

        logger.debug(f"[{symbol}] 수익 기록: +{profit_pct*100:.2f}% → 연속 손실 리셋")

    def can_enter(self, symbol: str) -> tuple[bool, str]:
        """
        진입 가능 여부 체크

        Args:
            symbol: 코인 심볼

        Returns:
            tuple[bool, str]: (진입 가능 여부, 사유)
        """
        now = datetime.now()

        # 전역 쿨다운 체크
        if self.global_mode and self._global_cooldown_until:
            if now < self._global_cooldown_until:
                remaining = (self._global_cooldown_until - now).total_seconds() / 60
                return False, f"전역 쿨다운 중 ({remaining:.0f}분 남음)"
            else:
                self._global_cooldown_until = None

        # 심볼별 쿨다운 체크
        state = self._cooldown_states.get(symbol)
        if state and state.is_in_cooldown and state.cooldown_until:
            if now < state.cooldown_until:
                remaining = (state.cooldown_until - now).total_seconds() / 60
                return False, f"연속 손실 쿨다운 중 ({remaining:.0f}분 남음)"
            else:
                # 쿨다운 종료
                state.is_in_cooldown = False
                state.cooldown_until = None

        return True, ""

    def get_state(self, symbol: str) -> CooldownState:
        """
        현재 쿨다운 상태 조회

        Args:
            symbol: 코인 심볼

        Returns:
            CooldownState: 쿨다운 상태
        """
        if symbol not in self._cooldown_states:
            return CooldownState()

        state = self._cooldown_states[symbol]

        # 쿨다운 만료 체크
        if state.is_in_cooldown and state.cooldown_until:
            if datetime.now() >= state.cooldown_until:
                state.is_in_cooldown = False
                state.cooldown_until = None

        return state

    def get_all_states(self) -> dict[str, CooldownState]:
        """모든 심볼의 쿨다운 상태 조회"""
        return {
            symbol: self.get_state(symbol)
            for symbol in self._cooldown_states
        }

    def _cleanup_old_records(self, symbol: str) -> None:
        """오래된 기록 정리"""
        cutoff = datetime.now() - timedelta(hours=self.loss_window_hours)
        self._loss_history[symbol] = [
            record for record in self._loss_history[symbol]
            if record.timestamp > cutoff
        ]

    def _count_consecutive_losses(self, symbol: str) -> int:
        """연속 손실 횟수 계산 (최근부터)"""
        records = self._loss_history.get(symbol, [])
        if not records:
            return 0

        # 최근 기록부터 연속 손실 카운트
        count = 0
        for record in reversed(records):
            if record.loss_pct < 0:
                count += 1
            else:
                break

        return count

    def _calculate_total_loss(self, symbol: str) -> float:
        """윈도우 내 총 손실 계산"""
        records = self._loss_history.get(symbol, [])
        return sum(r.loss_pct for r in records if r.loss_pct < 0)


# ============================================================
# 통합 필터 매니저
# ============================================================

@dataclass
class FilterResult:
    """필터 결과"""
    can_enter: bool = True
    blocked_by: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    volatility_state: Optional[VolatilityState] = None
    trend_state: Optional[TrendState] = None
    cooldown_state: Optional[CooldownState] = None


class ReboundFilterManager:
    """
    Rebound 전략 필터 통합 관리자

    3가지 필터를 통합하여 진입 가능 여부 판단
    """

    def __init__(
        self,
        volatility_filter: Optional[VolatilityFilter] = None,
        trend_filter: Optional[TrendFilter] = None,
        loss_breaker: Optional[ConsecutiveLossBreaker] = None,
        disable_on_volatile: bool = True,
        disable_in_downtrend: bool = True
    ):
        self.volatility_filter = volatility_filter or VolatilityFilter()
        self.trend_filter = trend_filter or TrendFilter()
        self.loss_breaker = loss_breaker or ConsecutiveLossBreaker()
        self.disable_on_volatile = disable_on_volatile
        self.disable_in_downtrend = disable_in_downtrend

    def check_all(self, symbol: str, market_data: dict) -> FilterResult:
        """
        모든 필터 체크

        Args:
            symbol: 코인 심볼
            market_data: 시장 데이터

        Returns:
            FilterResult: 필터 결과
        """
        result = FilterResult()

        # 1. 연속 손실 체크 (가장 먼저)
        can_enter, reason = self.loss_breaker.can_enter(symbol)
        result.cooldown_state = self.loss_breaker.get_state(symbol)

        if not can_enter:
            result.can_enter = False
            result.blocked_by.append("ConsecutiveLossBreaker")
            result.reasons["consecutive_loss"] = reason

        # 2. 변동성 체크
        if self.disable_on_volatile:
            vol_state = self.volatility_filter.check(symbol, market_data)
            result.volatility_state = vol_state

            if vol_state.is_whipsaw:
                result.can_enter = False
                result.blocked_by.append("VolatilityFilter")
                result.reasons["volatility"] = vol_state.whipsaw_reason

        # 3. 추세 체크
        if self.disable_in_downtrend:
            trend_state = self.trend_filter.check(symbol, market_data)
            result.trend_state = trend_state

            if trend_state.is_downtrend:
                result.can_enter = False
                result.blocked_by.append("TrendFilter")
                result.reasons["trend"] = trend_state.downtrend_reason

        if not result.can_enter:
            logger.info(
                f"[{symbol}] 필터 차단: {', '.join(result.blocked_by)} | "
                f"사유: {result.reasons}"
            )

        return result

    def record_trade_result(
        self,
        symbol: str,
        profit_pct: float,
        reason: str = ""
    ) -> None:
        """
        거래 결과 기록 (손익에 따라 적절한 메서드 호출)

        Args:
            symbol: 코인 심볼
            profit_pct: 손익률
            reason: 거래 종료 사유
        """
        if profit_pct < 0:
            self.loss_breaker.record_loss(symbol, profit_pct, reason)
        else:
            self.loss_breaker.record_win(symbol, profit_pct)

    def get_dashboard_data(self) -> dict:
        """
        대시보드용 데이터 조회

        Returns:
            dict: 대시보드 표시용 데이터
        """
        cooldown_states = self.loss_breaker.get_all_states()

        return {
            "cooldowns": {
                symbol: {
                    "is_in_cooldown": state.is_in_cooldown,
                    "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else None,
                    "consecutive_losses": state.consecutive_losses,
                    "total_loss_in_window": state.total_loss_in_window
                }
                for symbol, state in cooldown_states.items()
            },
            "filter_settings": {
                "max_consecutive_losses": self.loss_breaker.max_consecutive_losses,
                "cooldown_minutes": self.loss_breaker.cooldown_minutes,
                "disable_on_volatile": self.disable_on_volatile,
                "disable_in_downtrend": self.disable_in_downtrend
            }
        }
