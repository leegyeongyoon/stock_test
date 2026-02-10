"""
한국 주식 전략 기본 클래스

손실 최소화를 위한 다층 필터 시스템 통합
모든 전략은 이 클래스를 상속받아 구현
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Optional, Callable
import numpy as np
from numpy.typing import NDArray

from src.krstock.indicators import TechnicalIndicators, calculate_all_indicators
from src.krstock.filters import (
    MultiLayerFilterConfig,
    MultiLayerFilterResult,
    run_multi_layer_filters,
    BasicFilterConfig,
    TechnicalFilterConfig,
    SupplyDemandFilterConfig,
    MarketFilterConfig,
    RiskFilterConfig,
)
from src.krstock.market import (
    MarketType,
    is_market_open,
    calculate_trading_cost,
    get_tick_size,
    align_to_tick,
    PRICE_LIMIT_RATIO,
)


class SignalType(Enum):
    """신호 유형"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalStrength(Enum):
    """신호 강도"""
    STRONG = "STRONG"      # 확신 높음 (풀 사이즈)
    NORMAL = "NORMAL"      # 일반 (기본 사이즈)
    WEAK = "WEAK"          # 약함 (축소 사이즈)


class ExitReason(Enum):
    """청산 사유"""
    STOP_LOSS = "STOP_LOSS"              # 손절
    TAKE_PROFIT = "TAKE_PROFIT"          # 익절
    TRAILING_STOP = "TRAILING_STOP"      # 추적손절
    TIME_STOP = "TIME_STOP"              # 시간손절
    FILTER_EXIT = "FILTER_EXIT"          # 필터 조건 위반
    MARKET_CLOSE = "MARKET_CLOSE"        # 장 마감
    MANUAL = "MANUAL"                    # 수동 청산
    SIGNAL = "SIGNAL"                    # 반대 신호


@dataclass(frozen=True)
class Signal:
    """매매 신호"""
    signal_type: SignalType
    strength: SignalStrength
    stock_code: str
    price: float
    timestamp: datetime
    strategy_name: str

    # 신호 점수 (0-100)
    score: float = 50.0

    # 목표/손절 가격
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None

    # 포지션 사이즈 (자본 대비 %)
    position_size_pct: float = 0.05  # 기본 5%

    # 신호 생성 시 지표 스냅샷
    indicators: Optional[dict] = None

    # 필터 결과
    filter_result: Optional[MultiLayerFilterResult] = None

    # 메타데이터
    reason: str = ""


@dataclass
class Position:
    """포지션 정보"""
    stock_code: str
    entry_price: float
    quantity: int
    entry_time: datetime
    strategy_name: str

    # 현재 상태
    current_price: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0

    # 손익
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    max_profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    # 리스크 관리
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    trailing_stop_price: float = 0.0
    trailing_activated: bool = False

    def update(self, current_price: float) -> None:
        """포지션 업데이트 (immutable 패턴은 아니지만 성능상 mutable 사용)"""
        self.current_price = current_price
        self.highest_price = max(self.highest_price, current_price)
        self.lowest_price = min(self.lowest_price, current_price) if self.lowest_price > 0 else current_price

        # 손익 계산
        self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        self.unrealized_pnl_pct = (current_price / self.entry_price - 1) * 100

        # 최대 수익/손실 추적
        profit_pct = (self.highest_price / self.entry_price - 1) * 100
        self.max_profit_pct = max(self.max_profit_pct, profit_pct)

        drawdown_pct = (self.entry_price - self.lowest_price) / self.entry_price * 100
        self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown_pct)


@dataclass(frozen=True)
class StrategyConfig:
    """전략 설정"""
    # 기본 설정
    strategy_name: str
    strategy_type: str  # MOMENTUM, MEAN_REVERSION, VOLUME, etc.

    # 리스크 관리 (손실 방지 핵심!)
    stop_loss_pct: float = -2.0        # 손절 % (음수)
    take_profit_pct: float = 4.0       # 익절 %
    trailing_trigger_pct: float = 2.0  # 추적손절 활성화 수익률
    trailing_stop_pct: float = 1.0     # 추적손절 폭 (고점 대비)

    # 시간 관리
    max_holding_days: int = 5          # 최대 보유일
    max_holding_minutes: Optional[int] = None  # 장중 전략용

    # 포지션 사이징
    max_position_pct: float = 10.0     # 최대 포지션 크기 (%)
    default_position_pct: float = 5.0  # 기본 포지션 크기 (%)

    # 필터 설정
    filter_config: MultiLayerFilterConfig = field(
        default_factory=MultiLayerFilterConfig
    )

    # 진입 조건
    min_signal_score: float = 60.0     # 최소 신호 점수

    # 쿨다운
    cooldown_minutes: int = 30         # 같은 종목 재진입 대기 (분)


@dataclass(frozen=True)
class CandleData:
    """캔들 데이터"""
    stock_code: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_value: float = 0.0

    # 수급 데이터
    foreign_net: int = 0
    inst_net: int = 0
    individual_net: int = 0


class BaseKRStockStrategy(ABC):
    """
    한국 주식 전략 기본 클래스

    모든 전략은 이 클래스를 상속받아 다음을 구현:
    1. calculate_signal_score(): 신호 점수 계산
    2. generate_entry_signal(): 진입 신호 생성
    3. check_exit_conditions(): 청산 조건 확인
    """

    def __init__(self, config: StrategyConfig):
        self.config = config
        self._cooldown_map: dict[str, datetime] = {}  # 종목별 쿨다운 추적

    @property
    def name(self) -> str:
        return self.config.strategy_name

    # ========================================================================
    # 추상 메서드 (각 전략에서 구현)
    # ========================================================================

    @abstractmethod
    def calculate_signal_score(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> float:
        """
        신호 점수 계산 (0-100)

        각 전략별 고유 로직으로 점수 산출
        점수가 높을수록 진입 확신도 높음
        """
        pass

    @abstractmethod
    def get_entry_conditions(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData]
    ) -> tuple[bool, str]:
        """
        진입 조건 확인

        Returns:
            (조건충족여부, 사유설명) 튜플
        """
        pass

    # ========================================================================
    # 공통 메서드
    # ========================================================================

    def is_in_cooldown(self, stock_code: str, current_time: datetime) -> bool:
        """쿨다운 중인지 확인"""
        if stock_code not in self._cooldown_map:
            return False

        cooldown_end = self._cooldown_map[stock_code]
        return current_time < cooldown_end

    def set_cooldown(self, stock_code: str, current_time: datetime) -> None:
        """쿨다운 설정"""
        cooldown_end = current_time + timedelta(minutes=self.config.cooldown_minutes)
        self._cooldown_map[stock_code] = cooldown_end

    def generate_signal(
        self,
        candle: CandleData,
        indicators: TechnicalIndicators,
        historical_candles: list[CandleData],
        # 필터용 추가 데이터
        market_cap: Optional[float] = None,
        trading_status: str = "NORMAL",
        is_tradable: bool = True,
        foreign_net_5d: int = 0,
        inst_net_5d: int = 0,
        market_change_pct: float = 0.0,
        market_trend: int = 0,
        sector_rs_rank: Optional[int] = None,
        vkospi: Optional[float] = None,
        advance_count: int = 500,
        decline_count: int = 500,
        short_ratio: Optional[float] = None,
        short_change: Optional[float] = None,
        credit_ratio: Optional[float] = None,
        lending_ratio: Optional[float] = None,
        week_52_high: Optional[float] = None,
        week_52_low: Optional[float] = None
    ) -> Optional[Signal]:
        """
        매매 신호 생성 (다층 필터 적용)

        1. 기본 조건 확인 (쿨다운, 장중 여부)
        2. 진입 조건 확인 (전략별 로직)
        3. 다층 필터 통과 확인
        4. 신호 점수 계산
        5. 신호 생성
        """
        # 1. 쿨다운 확인
        if self.is_in_cooldown(candle.stock_code, candle.timestamp):
            return None

        # 2. 진입 조건 확인
        entry_ok, entry_reason = self.get_entry_conditions(
            candle, indicators, historical_candles
        )
        if not entry_ok:
            return None

        # 3. 다층 필터 실행
        filter_result = run_multi_layer_filters(
            config=self.config.filter_config,
            market_cap=market_cap,
            daily_volume=candle.volume,
            daily_value=candle.trade_value,
            change_pct=((candle.close / candle.open) - 1) * 100 if candle.open > 0 else 0,
            trading_status=trading_status,
            is_tradable=is_tradable,
            indicators=indicators,
            current_price=candle.close,
            foreign_net=candle.foreign_net,
            inst_net=candle.inst_net,
            individual_net=candle.individual_net,
            foreign_net_5d=foreign_net_5d,
            inst_net_5d=inst_net_5d,
            total_volume=candle.volume,
            market_change_pct=market_change_pct,
            market_trend=market_trend,
            sector_rs_rank=sector_rs_rank,
            vkospi=vkospi,
            advance_count=advance_count,
            decline_count=decline_count,
            short_ratio=short_ratio,
            short_change=short_change,
            credit_ratio=credit_ratio,
            lending_ratio=lending_ratio,
            week_52_high=week_52_high,
            week_52_low=week_52_low
        )

        # 필터 통과 실패 시 진입 불가
        if not filter_result.can_enter:
            return None

        # 4. 신호 점수 계산
        signal_score = self.calculate_signal_score(
            candle, indicators, historical_candles
        )

        # 최소 점수 미달 시 진입 불가
        if signal_score < self.config.min_signal_score:
            return None

        # 5. 신호 강도 결정
        if signal_score >= 80:
            strength = SignalStrength.STRONG
            position_size = self.config.max_position_pct
        elif signal_score >= 60:
            strength = SignalStrength.NORMAL
            position_size = self.config.default_position_pct
        else:
            strength = SignalStrength.WEAK
            position_size = self.config.default_position_pct * 0.5

        # 6. 손절/익절 가격 계산
        stop_loss_price = candle.close * (1 + self.config.stop_loss_pct / 100)
        take_profit_price = candle.close * (1 + self.config.take_profit_pct / 100)

        # 호가단위 조정
        stop_loss_price = align_to_tick(stop_loss_price, up=False)
        take_profit_price = align_to_tick(take_profit_price, up=False)

        # 7. 지표 스냅샷 생성
        indicators_snapshot = {
            "rsi_14": indicators.rsi_14,
            "macd": indicators.macd,
            "macd_signal": indicators.macd_signal,
            "bb_pct": indicators.bb_pct,
            "adx": indicators.adx,
            "rvol": indicators.rvol,
        }

        # 8. 신호 생성
        return Signal(
            signal_type=SignalType.BUY,
            strength=strength,
            stock_code=candle.stock_code,
            price=candle.close,
            timestamp=candle.timestamp,
            strategy_name=self.name,
            score=signal_score,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            position_size_pct=position_size,
            indicators=indicators_snapshot,
            filter_result=filter_result,
            reason=entry_reason
        )

    def check_exit(
        self,
        position: Position,
        current_candle: CandleData,
        current_time: datetime
    ) -> Optional[tuple[ExitReason, float]]:
        """
        청산 조건 확인

        Returns:
            (청산사유, 청산가격) 또는 None (청산 불필요)
        """
        current_price = current_candle.close
        position.update(current_price)

        # 1. 손절 확인 (최우선)
        if current_price <= position.stop_loss_price:
            return ExitReason.STOP_LOSS, current_price

        # 2. 익절 확인
        if current_price >= position.take_profit_price:
            return ExitReason.TAKE_PROFIT, current_price

        # 3. 추적손절 확인
        profit_pct = position.unrealized_pnl_pct

        # 추적손절 활성화
        if not position.trailing_activated:
            if profit_pct >= self.config.trailing_trigger_pct:
                position.trailing_activated = True
                # 추적손절가 설정
                trailing_price = current_price * (1 - self.config.trailing_stop_pct / 100)
                position.trailing_stop_price = align_to_tick(trailing_price, up=True)

        # 추적손절 업데이트 및 체크
        if position.trailing_activated:
            # 고점 갱신 시 추적손절가 상향
            if current_price > position.highest_price:
                new_trailing = current_price * (1 - self.config.trailing_stop_pct / 100)
                position.trailing_stop_price = max(
                    position.trailing_stop_price,
                    align_to_tick(new_trailing, up=True)
                )

            # 추적손절 체크
            if current_price <= position.trailing_stop_price:
                return ExitReason.TRAILING_STOP, current_price

        # 4. 시간손절 확인
        holding_duration = current_time - position.entry_time

        if self.config.max_holding_minutes is not None:
            # 장중 전략: 분 단위
            if holding_duration.total_seconds() / 60 >= self.config.max_holding_minutes:
                return ExitReason.TIME_STOP, current_price
        else:
            # 일반 전략: 일 단위
            if holding_duration.days >= self.config.max_holding_days:
                return ExitReason.TIME_STOP, current_price

        return None

    def calculate_position_size(
        self,
        signal: Signal,
        capital: float,
        current_price: float
    ) -> int:
        """
        포지션 크기 계산

        Args:
            signal: 매매 신호
            capital: 현재 자본
            current_price: 현재가

        Returns:
            매수 수량
        """
        # 목표 포지션 금액
        target_value = capital * (signal.position_size_pct / 100)

        # 수량 계산
        quantity = int(target_value / current_price)

        # 최소 1주
        return max(1, quantity)


# ============================================================================
# 전략별 점수 계산 헬퍼
# ============================================================================

def calculate_trend_score(indicators: TechnicalIndicators, current_price: float) -> float:
    """추세 점수 계산 (0-100)"""
    score = 50.0  # 기본 중립

    # 이동평균선 배열
    if indicators.sma_20 and indicators.sma_60 and indicators.sma_200:
        # 정배열 (강한 상승)
        if current_price > indicators.sma_20 > indicators.sma_60 > indicators.sma_200:
            score += 30
        # 역배열 (하락)
        elif current_price < indicators.sma_20 < indicators.sma_60 < indicators.sma_200:
            score -= 30
        # 20일선 위
        elif current_price > indicators.sma_20:
            score += 10
        # 20일선 아래
        else:
            score -= 10

    # MACD
    if indicators.macd is not None and indicators.macd_signal is not None:
        if indicators.macd > indicators.macd_signal:
            score += 10
        else:
            score -= 10

    # ADX (추세 강도)
    if indicators.adx is not None:
        if indicators.adx >= 25:
            # 강한 추세
            if indicators.plus_di and indicators.minus_di:
                if indicators.plus_di > indicators.minus_di:
                    score += 10  # 상승 추세
                else:
                    score -= 10  # 하락 추세

    return max(0, min(100, score))


def calculate_momentum_score(indicators: TechnicalIndicators) -> float:
    """모멘텀 점수 계산 (0-100)"""
    score = 50.0

    # RSI
    if indicators.rsi_14 is not None:
        if 40 <= indicators.rsi_14 <= 60:
            score += 10  # 중립 (상승 여지)
        elif indicators.rsi_14 > 70:
            score -= 20  # 과매수
        elif indicators.rsi_14 < 30:
            score += 15  # 과매도 (반등 기대)

    # Stochastic
    if indicators.stoch_slow_k is not None and indicators.stoch_slow_d is not None:
        if indicators.stoch_slow_k > indicators.stoch_slow_d:
            score += 10  # 상승 모멘텀
        else:
            score -= 5

    # MACD 히스토그램
    if indicators.macd_histogram is not None:
        if indicators.macd_histogram > 0:
            score += 10
        else:
            score -= 5

    return max(0, min(100, score))


def calculate_volume_score(indicators: TechnicalIndicators) -> float:
    """거래량 점수 계산 (0-100)"""
    score = 50.0

    if indicators.rvol is not None:
        if indicators.rvol >= 2.0:
            score += 25  # 거래량 폭발
        elif indicators.rvol >= 1.5:
            score += 15  # 거래량 증가
        elif indicators.rvol >= 1.0:
            score += 5   # 평균 이상
        elif indicators.rvol < 0.5:
            score -= 20  # 거래량 급감

    return max(0, min(100, score))


def calculate_supply_demand_score(
    foreign_net: int,
    inst_net: int,
    individual_net: int,
    total_volume: int
) -> float:
    """수급 점수 계산 (0-100)"""
    score = 50.0

    if total_volume == 0:
        return score

    # 외국인 순매수
    foreign_ratio = foreign_net / total_volume if total_volume > 0 else 0
    if foreign_ratio > 0.1:
        score += 20
    elif foreign_ratio > 0.05:
        score += 10
    elif foreign_ratio < -0.1:
        score -= 20
    elif foreign_ratio < -0.05:
        score -= 10

    # 기관 순매수
    inst_ratio = inst_net / total_volume if total_volume > 0 else 0
    if inst_ratio > 0.1:
        score += 15
    elif inst_ratio > 0.05:
        score += 8
    elif inst_ratio < -0.1:
        score -= 15
    elif inst_ratio < -0.05:
        score -= 8

    return max(0, min(100, score))
