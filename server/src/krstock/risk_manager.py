"""
한국 주식 리스크 관리 엔진

손실 최소화를 위한 다층 리스크 관리 시스템:
1. 일일 손실 한도 (NORMAL → SAFE → HALT)
2. 포지션 집중도 관리
3. 섹터 분산
4. 동적 손절/익절
5. 시장 상황별 포지션 조절
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional
import numpy as np


class TradingMode(Enum):
    """거래 모드"""
    NORMAL = "NORMAL"  # 정상 거래
    SAFE = "SAFE"      # 보수적 모드 (신규 진입 제한)
    HALT = "HALT"      # 거래 중단 (청산만)


class MarketCondition(Enum):
    """시장 상황"""
    BULLISH = "BULLISH"    # 강세장
    NEUTRAL = "NEUTRAL"    # 중립
    BEARISH = "BEARISH"    # 약세장
    VOLATILE = "VOLATILE"  # 변동성 확대


@dataclass(frozen=True)
class RiskConfig:
    """리스크 설정"""
    # 일일 손실 한도
    daily_loss_safe_pct: float = -2.0    # -2% → SAFE 모드
    daily_loss_halt_pct: float = -4.0    # -4% → HALT 모드

    # 주간 손실 한도
    weekly_loss_limit_pct: float = -6.0  # -6% → 주말까지 거래 중단

    # 포지션 한도
    max_positions: int = 10
    max_single_position_pct: float = 10.0    # 단일 종목 최대 10%
    max_sector_exposure_pct: float = 30.0    # 단일 섹터 최대 30%
    max_total_exposure_pct: float = 80.0     # 총 투자 비율 최대 80%
    min_cash_reserve_pct: float = 20.0       # 최소 현금 보유 20%

    # 연속 손실 관리
    max_consecutive_losses: int = 3          # 연속 3회 손실 시 휴식
    cooldown_after_losses_hours: int = 4     # 4시간 쿨다운

    # 시장 상황별 조절
    position_size_bullish: float = 1.0       # 강세장 100%
    position_size_neutral: float = 0.8       # 중립 80%
    position_size_bearish: float = 0.5       # 약세장 50%
    position_size_volatile: float = 0.3      # 변동성 확대 30%


@dataclass
class PositionInfo:
    """포지션 정보"""
    stock_code: str
    sector: str
    current_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


@dataclass
class RiskState:
    """리스크 상태"""
    mode: TradingMode = TradingMode.NORMAL
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    weekly_pnl: float = 0.0
    weekly_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    last_loss_time: Optional[datetime] = None
    positions_count: int = 0
    total_exposure_pct: float = 0.0
    sector_exposures: dict[str, float] = field(default_factory=dict)
    market_condition: MarketCondition = MarketCondition.NEUTRAL


class KRStockRiskManager:
    """
    한국 주식 리스크 관리자

    손실 최소화를 위한 종합 리스크 관리:
    1. 일일/주간 손실 한도 모니터링
    2. 자동 모드 전환 (NORMAL → SAFE → HALT)
    3. 포지션 집중도 관리
    4. 시장 상황별 대응
    """

    def __init__(self, config: RiskConfig, initial_capital: float):
        self.config = config
        self.initial_capital = initial_capital
        self.state = RiskState()

        # 일일 추적
        self._day_start_equity: float = initial_capital
        self._week_start_equity: float = initial_capital
        self._current_day: Optional[date] = None
        self._current_week_start: Optional[date] = None

        # 거래 기록
        self._trade_results: list[float] = []  # 최근 거래 손익

    def update(
        self,
        current_equity: float,
        current_time: datetime,
        positions: list[PositionInfo],
        market_change_pct: float,
        vkospi: Optional[float] = None
    ) -> RiskState:
        """
        리스크 상태 업데이트

        Args:
            current_equity: 현재 총 자산
            current_time: 현재 시간
            positions: 현재 포지션 목록
            market_change_pct: 시장 등락률
            vkospi: VKOSPI (변동성 지수)

        Returns:
            업데이트된 리스크 상태
        """
        current_date = current_time.date()

        # 일자 변경 체크
        if self._current_day != current_date:
            self._on_new_day(current_date, current_equity)

        # 주간 변경 체크
        week_start = current_date - timedelta(days=current_date.weekday())
        if self._current_week_start != week_start:
            self._on_new_week(week_start, current_equity)

        # 1. 손익 계산
        self._update_pnl(current_equity)

        # 2. 포지션 분석
        self._update_positions(positions, current_equity)

        # 3. 시장 상황 판단
        self._update_market_condition(market_change_pct, vkospi)

        # 4. 모드 결정
        self._update_mode(current_time)

        return self.state

    def _on_new_day(self, new_date: date, current_equity: float) -> None:
        """일자 변경 처리"""
        self._current_day = new_date
        self._day_start_equity = current_equity

        # 일일 상태 리셋 (모드는 유지)
        self.state.daily_pnl = 0.0
        self.state.daily_pnl_pct = 0.0

        # HALT 모드가 아니면 SAFE에서 NORMAL로 복귀 가능
        if self.state.mode == TradingMode.SAFE:
            self.state.mode = TradingMode.NORMAL

    def _on_new_week(self, week_start: date, current_equity: float) -> None:
        """주간 변경 처리"""
        self._current_week_start = week_start
        self._week_start_equity = current_equity
        self.state.weekly_pnl = 0.0
        self.state.weekly_pnl_pct = 0.0

        # 주간 리셋 시 HALT 해제
        if self.state.mode == TradingMode.HALT:
            self.state.mode = TradingMode.NORMAL

    def _update_pnl(self, current_equity: float) -> None:
        """손익 업데이트"""
        # 일일 손익
        self.state.daily_pnl = current_equity - self._day_start_equity
        self.state.daily_pnl_pct = (
            (current_equity / self._day_start_equity - 1) * 100
            if self._day_start_equity > 0 else 0.0
        )

        # 주간 손익
        self.state.weekly_pnl = current_equity - self._week_start_equity
        self.state.weekly_pnl_pct = (
            (current_equity / self._week_start_equity - 1) * 100
            if self._week_start_equity > 0 else 0.0
        )

    def _update_positions(
        self, positions: list[PositionInfo], current_equity: float
    ) -> None:
        """포지션 분석 업데이트"""
        self.state.positions_count = len(positions)

        # 총 투자 비율
        total_value = sum(p.current_value for p in positions)
        self.state.total_exposure_pct = (
            (total_value / current_equity * 100) if current_equity > 0 else 0.0
        )

        # 섹터별 노출
        sector_values: dict[str, float] = {}
        for p in positions:
            sector_values[p.sector] = sector_values.get(p.sector, 0) + p.current_value

        self.state.sector_exposures = {
            sector: (value / current_equity * 100) if current_equity > 0 else 0.0
            for sector, value in sector_values.items()
        }

    def _update_market_condition(
        self, market_change_pct: float, vkospi: Optional[float]
    ) -> None:
        """시장 상황 판단"""
        # VKOSPI 기반 변동성 판단
        if vkospi is not None and vkospi > 25:
            self.state.market_condition = MarketCondition.VOLATILE
            return

        # 시장 등락률 기반
        if market_change_pct >= 1.0:
            self.state.market_condition = MarketCondition.BULLISH
        elif market_change_pct <= -1.0:
            self.state.market_condition = MarketCondition.BEARISH
        else:
            self.state.market_condition = MarketCondition.NEUTRAL

    def _update_mode(self, current_time: datetime) -> None:
        """거래 모드 업데이트"""
        # 1. 주간 손실 체크
        if self.state.weekly_pnl_pct <= self.config.weekly_loss_limit_pct:
            self.state.mode = TradingMode.HALT
            return

        # 2. 일일 손실 체크
        if self.state.daily_pnl_pct <= self.config.daily_loss_halt_pct:
            self.state.mode = TradingMode.HALT
            return

        if self.state.daily_pnl_pct <= self.config.daily_loss_safe_pct:
            self.state.mode = TradingMode.SAFE
            return

        # 3. 연속 손실 체크
        if self.state.consecutive_losses >= self.config.max_consecutive_losses:
            if self.state.last_loss_time:
                cooldown_end = self.state.last_loss_time + timedelta(
                    hours=self.config.cooldown_after_losses_hours
                )
                if current_time < cooldown_end:
                    self.state.mode = TradingMode.SAFE
                    return

        # 정상 모드
        if self.state.mode != TradingMode.HALT:
            self.state.mode = TradingMode.NORMAL

    def record_trade_result(self, pnl: float, current_time: datetime) -> None:
        """거래 결과 기록"""
        self._trade_results.append(pnl)

        # 최근 10개만 유지
        if len(self._trade_results) > 10:
            self._trade_results.pop(0)

        # 연속 손실 카운트
        if pnl < 0:
            self.state.consecutive_losses += 1
            self.state.last_loss_time = current_time
        else:
            self.state.consecutive_losses = 0
            self.state.last_loss_time = None

    def can_open_position(
        self,
        stock_code: str,
        sector: str,
        position_value: float,
        current_equity: float,
        positions: list[PositionInfo]
    ) -> tuple[bool, str]:
        """
        신규 포지션 오픈 가능 여부 확인

        Returns:
            (가능여부, 사유)
        """
        # 1. 모드 체크
        if self.state.mode == TradingMode.HALT:
            return False, "HALT 모드 - 신규 진입 금지"

        if self.state.mode == TradingMode.SAFE:
            return False, "SAFE 모드 - 신규 진입 제한"

        # 2. 포지션 수 체크
        if len(positions) >= self.config.max_positions:
            return False, f"최대 포지션 수 초과 ({len(positions)}/{self.config.max_positions})"

        # 3. 단일 포지션 크기 체크
        position_pct = (position_value / current_equity * 100) if current_equity > 0 else 0
        if position_pct > self.config.max_single_position_pct:
            return False, f"단일 포지션 한도 초과 ({position_pct:.1f}% > {self.config.max_single_position_pct}%)"

        # 4. 총 투자 비율 체크
        current_total = sum(p.current_value for p in positions)
        new_total_pct = ((current_total + position_value) / current_equity * 100) if current_equity > 0 else 0
        if new_total_pct > self.config.max_total_exposure_pct:
            return False, f"총 투자 한도 초과 ({new_total_pct:.1f}% > {self.config.max_total_exposure_pct}%)"

        # 5. 현금 보유 체크
        cash_after = current_equity - current_total - position_value
        cash_pct = (cash_after / current_equity * 100) if current_equity > 0 else 0
        if cash_pct < self.config.min_cash_reserve_pct:
            return False, f"최소 현금 보유 미달 ({cash_pct:.1f}% < {self.config.min_cash_reserve_pct}%)"

        # 6. 섹터 집중도 체크
        sector_value = sum(p.current_value for p in positions if p.sector == sector)
        new_sector_pct = ((sector_value + position_value) / current_equity * 100) if current_equity > 0 else 0
        if new_sector_pct > self.config.max_sector_exposure_pct:
            return False, f"섹터 집중도 한도 초과 ({sector}): {new_sector_pct:.1f}% > {self.config.max_sector_exposure_pct}%"

        # 7. 중복 종목 체크
        existing_codes = [p.stock_code for p in positions]
        if stock_code in existing_codes:
            return False, f"이미 보유 중인 종목: {stock_code}"

        return True, "진입 가능"

    def get_adjusted_position_size(self, base_size_pct: float) -> float:
        """
        시장 상황에 따른 포지션 크기 조절

        Args:
            base_size_pct: 기본 포지션 크기 (%)

        Returns:
            조절된 포지션 크기 (%)
        """
        multiplier = {
            MarketCondition.BULLISH: self.config.position_size_bullish,
            MarketCondition.NEUTRAL: self.config.position_size_neutral,
            MarketCondition.BEARISH: self.config.position_size_bearish,
            MarketCondition.VOLATILE: self.config.position_size_volatile,
        }.get(self.state.market_condition, 1.0)

        return base_size_pct * multiplier

    def should_force_exit(self, position: PositionInfo) -> tuple[bool, str]:
        """
        강제 청산 필요 여부 확인

        Returns:
            (청산필요여부, 사유)
        """
        # HALT 모드에서는 모든 포지션 청산
        if self.state.mode == TradingMode.HALT:
            return True, "HALT 모드 - 전량 청산"

        # 큰 손실 포지션 청산 (-5% 이상)
        if position.unrealized_pnl_pct <= -5.0:
            return True, f"과도한 손실 ({position.unrealized_pnl_pct:.1f}%)"

        return False, ""

    def get_risk_summary(self) -> dict:
        """리스크 상태 요약"""
        return {
            "mode": self.state.mode.value,
            "daily_pnl_pct": round(self.state.daily_pnl_pct, 2),
            "weekly_pnl_pct": round(self.state.weekly_pnl_pct, 2),
            "positions_count": self.state.positions_count,
            "total_exposure_pct": round(self.state.total_exposure_pct, 1),
            "consecutive_losses": self.state.consecutive_losses,
            "market_condition": self.state.market_condition.value,
            "sector_exposures": {
                k: round(v, 1) for k, v in self.state.sector_exposures.items()
            },
        }


# ============================================================================
# 동적 손절/익절 계산
# ============================================================================

@dataclass(frozen=True)
class DynamicStopConfig:
    """동적 손절/익절 설정"""
    # ATR 기반 손절
    atr_stop_multiplier: float = 2.0  # ATR * 2 손절

    # 변동성 기반 조절
    high_vol_stop_multiplier: float = 1.5  # 고변동성 시 손절폭 확대
    low_vol_profit_multiplier: float = 0.8  # 저변동성 시 익절폭 축소

    # 시간 기반 조절
    time_decay_days: int = 5  # 5일 후부터 손절폭 타이트하게


def calculate_dynamic_stop_loss(
    entry_price: float,
    atr: float,
    holding_days: int,
    market_condition: MarketCondition,
    config: DynamicStopConfig = DynamicStopConfig()
) -> float:
    """
    동적 손절가 계산

    - ATR 기반
    - 시장 상황 반영
    - 보유 기간 반영
    """
    # 기본 ATR 손절
    base_stop = entry_price - (atr * config.atr_stop_multiplier)

    # 시장 상황 조절
    if market_condition == MarketCondition.VOLATILE:
        base_stop = entry_price - (atr * config.atr_stop_multiplier * config.high_vol_stop_multiplier)
    elif market_condition == MarketCondition.BEARISH:
        # 약세장에서는 더 타이트하게
        base_stop = entry_price - (atr * config.atr_stop_multiplier * 0.8)

    # 시간 경과에 따라 타이트하게
    if holding_days >= config.time_decay_days:
        decay_factor = 1 - (holding_days - config.time_decay_days) * 0.05
        decay_factor = max(0.7, decay_factor)  # 최소 70%
        distance = entry_price - base_stop
        base_stop = entry_price - (distance * decay_factor)

    return base_stop


def calculate_dynamic_take_profit(
    entry_price: float,
    atr: float,
    holding_days: int,
    market_condition: MarketCondition,
    config: DynamicStopConfig = DynamicStopConfig()
) -> float:
    """
    동적 익절가 계산
    """
    # 기본 목표: ATR * 3
    base_profit = entry_price + (atr * 3.0)

    # 시장 상황 조절
    if market_condition == MarketCondition.BULLISH:
        # 강세장에서는 더 여유롭게
        base_profit = entry_price + (atr * 4.0)
    elif market_condition in [MarketCondition.BEARISH, MarketCondition.VOLATILE]:
        # 약세장/변동성 확대 시 빠른 익절
        base_profit = entry_price + (atr * 2.0 * config.low_vol_profit_multiplier)

    # 시간 경과에 따라 낮추기 (익절 기회 확보)
    if holding_days >= config.time_decay_days:
        decay_factor = 1 - (holding_days - config.time_decay_days) * 0.03
        decay_factor = max(0.6, decay_factor)
        distance = base_profit - entry_price
        base_profit = entry_price + (distance * decay_factor)

    return base_profit
