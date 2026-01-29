"""Satellite 전략 - 5m 스캐너 (돌파 + RVOL + VWAP) + 1h MA/ATR 레짐 필터 (강화 버전)"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Optional

import structlog

from src.config import get_settings
from src.models.schemas import OrderSide, StrategyType
from src.strategies.base import BaseStrategy, Signal, SignalType

if TYPE_CHECKING:
    from src.features.feature_engine import FeatureEngine

logger = structlog.get_logger()
settings = get_settings()


class Regime(str, Enum):
    """BTC 레짐"""

    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    VOLATILE = "VOLATILE"  # 급변장 (신규 진입 금지)


class ConfirmationStatus(str, Enum):
    """확인 진입 상태"""

    NONE = "NONE"  # 시그널 없음
    SIGNAL_DETECTED = "SIGNAL_DETECTED"  # Phase 1 완료 (시그널 감지)
    CONFIRMED = "CONFIRMED"  # Phase 2 완료 (확인됨)
    REJECTED = "REJECTED"  # Phase 2 실패 (거부됨)


@dataclass
class PendingSignal:
    """확인 대기 중인 시그널"""

    symbol: str
    side: OrderSide
    detected_at: datetime
    breakout_level: float  # 돌파 레벨
    rvol: float
    close_pos: float
    status: ConfirmationStatus = ConfirmationStatus.SIGNAL_DETECTED


@dataclass
class SatellitePosition:
    """Satellite 포지션 추적"""

    symbol: str
    side: OrderSide
    entry_price: float
    quantity: float
    entry_time: datetime
    highest_price: float  # 트레일링용
    lowest_price: float
    trailing_active: bool = False  # 트레일링 활성화 여부


class SatelliteStrategy(BaseStrategy):
    """
    Satellite 전략 - 5분봉 모멘텀/돌파 (강화 버전)

    개선사항:
    1. 레짐 필터: 1h MA50 + ATR 기반 (VOLATILE 레짐 추가)
    2. 확인 진입: 2단계 확인 (시그널 → 다음 5m 봉 확인)
    3. 청산 조건 강화: -0.8% 하드손절, +1% 후 0.6% 트레일링, 30분 타임스톱

    진입 조건 (Phase 1 - 시그널 감지):
    - RVOL_5m >= 2.5
    - close > highest(high, 12개 5m)
    - close >= VWAP_5m
    - ClosePos >= 0.75

    진입 조건 (Phase 2 - 확인):
    - 다음 5m 봉에서 돌파 레벨 위 유지
    - 거래량 유지

    레짐 필터:
    - BTC 1h 레짐이 BULLISH일 때만 롱
    - BTC 1h 레짐이 BEARISH일 때만 숏
    - NEUTRAL/VOLATILE이면 진입 안 함

    청산 조건:
    - 하드 손절: -0.8%
    - 트레일링 스탑: +1.0% 도달 후 0.6% 하락
    - 타임스톱: 30분
    - VOLATILE 감지 시 즉시 청산
    """

    def __init__(self, feature_engine: Optional["FeatureEngine"] = None) -> None:
        super().__init__(StrategyType.SATELLITE)

        self._enabled = settings.satellite_enabled
        self.feature_engine = feature_engine

        # 설정
        self.max_position_usd = settings.satellite_max_position_usd
        self.hard_stop_pct = settings.satellite_hard_stop_pct  # -0.8%
        self.trailing_trigger_pct = settings.satellite_trailing_trigger_pct  # +1.0%
        self.trailing_stop_pct = settings.satellite_trailing_stop_pct  # 0.6%
        self.time_stop_minutes = settings.satellite_time_stop_minutes  # 30분

        # 스캐너 조건
        self.rvol_threshold = settings.satellite_rvol_threshold  # 2.5
        self.close_pos_threshold = settings.satellite_close_pos_threshold  # 0.75
        self.confirmation_enabled = settings.satellite_confirmation_entry

        # 상태
        self._btc_regime: Regime = Regime.NEUTRAL
        self._btc_is_volatile: bool = False
        self._active_positions: dict[str, SatellitePosition] = {}
        self._pending_signals: dict[str, PendingSignal] = {}

    def set_feature_engine(self, feature_engine: "FeatureEngine") -> None:
        """Feature Engine 설정"""
        self.feature_engine = feature_engine

    def update_btc_regime(self, regime: str, is_volatile: bool = False) -> None:
        """BTC 레짐 업데이트 (FeatureEngine에서 호출)"""
        try:
            new_regime = Regime(regime)
        except ValueError:
            new_regime = Regime.NEUTRAL

        old_regime = self._btc_regime
        self._btc_regime = new_regime
        self._btc_is_volatile = is_volatile

        if old_regime != new_regime:
            logger.info(
                "BTC regime changed",
                old=old_regime.value,
                new=new_regime.value,
                is_volatile=is_volatile,
            )

    def _check_regime_from_feature_engine(self) -> None:
        """FeatureEngine에서 레짐 업데이트"""
        if self.feature_engine:
            btc_regime_info = self.feature_engine.check_btc_regime()
            self.update_btc_regime(
                regime=btc_regime_info.get("regime", "NEUTRAL"),
                is_volatile=btc_regime_info.get("is_volatile", False),
            )

    async def generate_signal(self, market_data: dict) -> Optional[Signal]:
        """
        5분봉 스캐너 시그널 생성 (확인 진입 포함)

        market_data:
            symbol: str
            price: float
            rvol: float (RVOL_5m)
            close_pos: float (ClosePos)
            vwap: float (VWAP_5m)
            highest_12_5m: float (최근 12개 5m 고점)
            lowest_12_5m: float (최근 12개 5m 저점)
        """
        if not self._enabled:
            return None

        # 레짐 업데이트
        self._check_regime_from_feature_engine()

        # 레짐 필터 - NEUTRAL/VOLATILE이면 진입 안 함
        if self._btc_regime in [Regime.NEUTRAL, Regime.VOLATILE]:
            # 대기 중인 시그널 취소
            symbol = market_data.get("symbol")
            if symbol in self._pending_signals:
                self._pending_signals.pop(symbol)
            return None

        symbol = market_data.get("symbol")

        # 확인 진입이 활성화된 경우
        if self.confirmation_enabled:
            # Phase 2: 대기 중인 시그널 확인
            confirmed_signal = await self._check_confirmation(symbol, market_data)
            if confirmed_signal:
                return confirmed_signal

            # Phase 1: 새 시그널 감지
            detected = await self._detect_signal(market_data)
            if detected:
                return None  # 확인 대기

        else:
            # 확인 진입 비활성화 - 즉시 시그널
            return await self._generate_immediate_signal(market_data)

        return None

    async def _detect_signal(self, market_data: dict) -> bool:
        """Phase 1: 시그널 감지"""
        symbol = market_data.get("symbol")
        price = market_data.get("price", 0)
        rvol = market_data.get("rvol", 0)
        close_pos = market_data.get("close_pos", 0.5)
        vwap = market_data.get("vwap", 0)
        highest_12 = market_data.get("highest_12_5m", 0)
        lowest_12 = market_data.get("lowest_12_5m", float("inf"))

        if price <= 0:
            return False

        # 이미 대기 중인 시그널이 있으면 스킵
        if symbol in self._pending_signals:
            return False

        # RVOL 필터
        if rvol < self.rvol_threshold:
            return False

        # ClosePos 필터
        if close_pos < self.close_pos_threshold:
            return False

        # 돌파 체크 (롱)
        if (
            self._btc_regime == Regime.BULLISH
            and price > highest_12
            and price >= vwap
        ):
            self._pending_signals[symbol] = PendingSignal(
                symbol=symbol,
                side=OrderSide.BUY,
                detected_at=datetime.utcnow(),
                breakout_level=highest_12,
                rvol=rvol,
                close_pos=close_pos,
            )
            logger.info(
                "Satellite signal detected (Phase 1)",
                symbol=symbol,
                side="BUY",
                breakout_level=highest_12,
                rvol=rvol,
            )
            return True

        # 돌파 체크 (숏)
        if (
            self._btc_regime == Regime.BEARISH
            and price < lowest_12
            and price <= vwap
        ):
            self._pending_signals[symbol] = PendingSignal(
                symbol=symbol,
                side=OrderSide.SELL,
                detected_at=datetime.utcnow(),
                breakout_level=lowest_12,
                rvol=rvol,
                close_pos=1 - close_pos,  # 숏은 반전
            )
            logger.info(
                "Satellite signal detected (Phase 1)",
                symbol=symbol,
                side="SELL",
                breakout_level=lowest_12,
                rvol=rvol,
            )
            return True

        return False

    async def _check_confirmation(
        self, symbol: str, market_data: dict
    ) -> Optional[Signal]:
        """Phase 2: 시그널 확인"""
        pending = self._pending_signals.get(symbol)
        if not pending:
            return None

        # 5분 경과 체크 (다음 봉)
        elapsed = (datetime.utcnow() - pending.detected_at).total_seconds()
        if elapsed < 300:  # 5분 미만
            return None

        price = market_data.get("price", 0)
        rvol = market_data.get("rvol", 0)

        # 확인 조건 체크
        confirmed = False

        if pending.side == OrderSide.BUY:
            # 롱: 돌파 레벨 위 유지 + 거래량 유지
            if price > pending.breakout_level and rvol >= self.rvol_threshold * 0.5:
                confirmed = True

        else:  # SELL
            # 숏: 돌파 레벨 아래 유지 + 거래량 유지
            if price < pending.breakout_level and rvol >= self.rvol_threshold * 0.5:
                confirmed = True

        # 확인 실패
        if not confirmed:
            pending.status = ConfirmationStatus.REJECTED
            self._pending_signals.pop(symbol)
            logger.warning(
                "Satellite signal rejected (Phase 2)",
                symbol=symbol,
                reason="conditions not maintained",
            )
            return None

        # 확인 성공
        pending.status = ConfirmationStatus.CONFIRMED
        self._pending_signals.pop(symbol)

        logger.info(
            "Satellite signal confirmed (Phase 2)",
            symbol=symbol,
            side=pending.side.value,
        )

        return Signal(
            strategy=StrategyType.SATELLITE,
            signal_type=SignalType.ENTRY,
            symbol=symbol,
            side=pending.side,
            quantity=0,
            price=price,
            reason=f"Confirmed breakout, RVOL={pending.rvol:.2f}, ClosePos={pending.close_pos:.2f}",
            confidence=min(1.0, pending.rvol / self.rvol_threshold),
            metadata={
                "breakout_level": pending.breakout_level,
                "detection_time": pending.detected_at.isoformat(),
                "confirmation_time": datetime.utcnow().isoformat(),
            },
        )

    async def _generate_immediate_signal(self, market_data: dict) -> Optional[Signal]:
        """확인 진입 비활성화 시 즉시 시그널"""
        symbol = market_data.get("symbol")
        price = market_data.get("price", 0)
        rvol = market_data.get("rvol", 0)
        close_pos = market_data.get("close_pos", 0.5)
        vwap = market_data.get("vwap", 0)
        highest_12 = market_data.get("highest_12_5m", 0)
        lowest_12 = market_data.get("lowest_12_5m", float("inf"))

        if price <= 0:
            return None

        # RVOL 필터
        if rvol < self.rvol_threshold:
            return None

        # ClosePos 필터
        if close_pos < self.close_pos_threshold:
            return None

        # 고점 돌파 (롱)
        if (
            self._btc_regime == Regime.BULLISH
            and price > highest_12
            and price >= vwap
        ):
            return Signal(
                strategy=StrategyType.SATELLITE,
                signal_type=SignalType.ENTRY,
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=0,
                price=price,
                reason=f"Breakout HIGH, RVOL={rvol:.2f}, ClosePos={close_pos:.2f}",
                confidence=min(1.0, rvol / self.rvol_threshold),
            )

        # 저점 돌파 (숏)
        if (
            self._btc_regime == Regime.BEARISH
            and price < lowest_12
            and price <= vwap
        ):
            return Signal(
                strategy=StrategyType.SATELLITE,
                signal_type=SignalType.ENTRY,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=0,
                price=price,
                reason=f"Breakout LOW, RVOL={rvol:.2f}, ClosePos={1-close_pos:.2f}",
                confidence=min(1.0, rvol / self.rvol_threshold),
            )

        return None

    async def should_exit(self, position: dict, market_data: dict) -> Optional[Signal]:
        """
        청산 조건 확인 (강화)

        조건:
        1. 하드 손절: -0.8%
        2. 트레일링 스탑: +1.0% 도달 후 0.6% 하락
        3. 타임스톱: 30분
        4. VOLATILE 감지 시 즉시 청산
        5. BTC 레짐 악화
        """
        # 레짐 업데이트
        self._check_regime_from_feature_engine()

        symbol = position.get("symbol")
        entry_price = position.get("avg_price", 0)
        side = position.get("side")
        quantity = position.get("quantity", 0)
        entry_time = position.get("opened_at")

        current_price = market_data.get("price", 0)

        if entry_price <= 0 or current_price <= 0:
            return None

        # 포지션 추적
        sat_pos = self._active_positions.get(symbol)
        if sat_pos:
            # 고점/저점 업데이트
            if current_price > sat_pos.highest_price:
                sat_pos.highest_price = current_price
            if current_price < sat_pos.lowest_price:
                sat_pos.lowest_price = current_price

        # PnL 계산
        is_long = side in ["LONG", OrderSide.BUY, "BUY"]
        if is_long:
            pnl_pct = (current_price - entry_price) / entry_price
            highest = sat_pos.highest_price if sat_pos else entry_price
            from_high_drop = (current_price - highest) / highest
        else:
            pnl_pct = (entry_price - current_price) / entry_price
            lowest = sat_pos.lowest_price if sat_pos else entry_price
            from_low_rise = (current_price - lowest) / lowest if lowest > 0 else 0
            from_high_drop = -from_low_rise  # 숏은 반전

        # 0. VOLATILE 레짐 감지 시 즉시 청산
        if self._btc_is_volatile or self._btc_regime == Regime.VOLATILE:
            logger.warning(
                "Satellite VOLATILE exit",
                symbol=symbol,
                regime=self._btc_regime.value,
            )
            return Signal(
                strategy=StrategyType.SATELLITE,
                signal_type=SignalType.EXIT,
                symbol=symbol,
                side=OrderSide.SELL if is_long else OrderSide.BUY,
                quantity=quantity,
                reason="VOLATILE regime detected",
            )

        # 1. 하드 손절
        if pnl_pct <= self.hard_stop_pct:
            logger.warning(
                "Satellite hard stop triggered",
                symbol=symbol,
                pnl_pct=f"{pnl_pct:.2%}",
            )
            return Signal(
                strategy=StrategyType.SATELLITE,
                signal_type=SignalType.EXIT,
                symbol=symbol,
                side=OrderSide.SELL if is_long else OrderSide.BUY,
                quantity=quantity,
                reason=f"Hard stop: {pnl_pct:.2%}",
            )

        # 2. 트레일링 스탑 (트리거 후)
        if sat_pos:
            # 트레일링 활성화 체크
            if not sat_pos.trailing_active and pnl_pct >= self.trailing_trigger_pct:
                sat_pos.trailing_active = True
                logger.info(
                    "Satellite trailing activated",
                    symbol=symbol,
                    pnl_pct=f"{pnl_pct:.2%}",
                )

            # 트레일링 체크
            if sat_pos.trailing_active and from_high_drop <= -self.trailing_stop_pct:
                logger.info(
                    "Satellite trailing stop triggered",
                    symbol=symbol,
                    from_high_drop=f"{from_high_drop:.2%}",
                )
                return Signal(
                    strategy=StrategyType.SATELLITE,
                    signal_type=SignalType.EXIT,
                    symbol=symbol,
                    side=OrderSide.SELL if is_long else OrderSide.BUY,
                    quantity=quantity,
                    reason=f"Trailing stop: {from_high_drop:.2%}",
                )

        # 3. 타임스톱
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)

            elapsed = datetime.utcnow() - entry_time
            if elapsed > timedelta(minutes=self.time_stop_minutes):
                logger.info(
                    "Satellite time stop triggered",
                    symbol=symbol,
                    elapsed_minutes=elapsed.total_seconds() / 60,
                )
                return Signal(
                    strategy=StrategyType.SATELLITE,
                    signal_type=SignalType.EXIT,
                    symbol=symbol,
                    side=OrderSide.SELL if is_long else OrderSide.BUY,
                    quantity=quantity,
                    reason=f"Time stop: {elapsed.total_seconds()/60:.0f}min",
                )

        # 4. BTC 레짐 악화
        if is_long and self._btc_regime == Regime.BEARISH:
            logger.warning(
                "Satellite regime exit - long in bearish",
                symbol=symbol,
            )
            return Signal(
                strategy=StrategyType.SATELLITE,
                signal_type=SignalType.EXIT,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=quantity,
                reason="Regime changed to BEARISH",
            )

        if not is_long and self._btc_regime == Regime.BULLISH:
            logger.warning(
                "Satellite regime exit - short in bullish",
                symbol=symbol,
            )
            return Signal(
                strategy=StrategyType.SATELLITE,
                signal_type=SignalType.EXIT,
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=quantity,
                reason="Regime changed to BULLISH",
            )

        return None

    def get_position_size(self, signal: Signal, available_capital: float) -> float:
        """포지션 사이즈 계산"""
        # 최대 포지션의 25% 사용
        max_position = min(self.max_position_usd, available_capital * 0.25)

        # Confidence 기반 조절
        position_usd = max_position * signal.confidence

        # 가격 정보가 있으면 수량 계산
        if signal.price and signal.price > 0:
            return position_usd / signal.price

        return 0

    def track_position(
        self,
        symbol: str,
        side: OrderSide,
        entry_price: float,
        quantity: float,
    ) -> None:
        """포지션 추적 시작"""
        self._active_positions[symbol] = SatellitePosition(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.utcnow(),
            highest_price=entry_price,
            lowest_price=entry_price,
            trailing_active=False,
        )

    def close_position(self, symbol: str) -> None:
        """포지션 추적 종료"""
        self._active_positions.pop(symbol, None)
        self._pending_signals.pop(symbol, None)

    def get_status(self) -> dict:
        """전략 상태 조회"""
        return {
            "enabled": self._enabled,
            "btc_regime": self._btc_regime.value,
            "btc_is_volatile": self._btc_is_volatile,
            "confirmation_enabled": self.confirmation_enabled,
            "pending_signals": {
                sym: {
                    "side": sig.side.value,
                    "status": sig.status.value,
                    "detected_at": sig.detected_at.isoformat(),
                }
                for sym, sig in self._pending_signals.items()
            },
            "active_positions": {
                sym: {
                    "side": pos.side.value,
                    "entry_price": pos.entry_price,
                    "trailing_active": pos.trailing_active,
                }
                for sym, pos in self._active_positions.items()
            },
        }
