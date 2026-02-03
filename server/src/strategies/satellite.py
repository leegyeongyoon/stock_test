"""Satellite 전략 - 5m 스캐너 (돌파 + RVOL + VWAP) + 1h MA/ATR 레짐 필터 (강화 버전)

v3 업데이트 (Upbit 호환):
- Long-only 모드 지원 (Upbit은 숏 불가)
- 과열 추격 금지 필터 (+12% 이상 급등 시 진입 금지)
- settings.is_upbit 기반 자동 모드 전환
"""

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

# Upbit 과열 추격 금지 임계값 (12%→8%: 더 보수적인 필터)
OVERHEAT_THRESHOLD = 0.08  # +8% 이상 급등 시 진입 금지


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
    Satellite 전략 - 5분봉 모멘텀/돌파 (v3: Long-only 모드 지원)

    v3 변경사항 (Upbit 호환):
    - Long-only 모드: is_upbit=True일 때 숏 진입 비활성화
    - 과열 추격 금지: 전일 대비 +12% 이상 급등 시 진입 금지
    - 슬리피지 가드 추가

    v2 변경사항:
    - BTC Regime 의존 제거 → 각 코인별 독립적 돌파 조건만 체크
    - VOLATILE일 때만 신규 진입 차단 (안전장치)

    진입 조건 (Phase 1 - 시그널 감지):
    - RVOL_5m >= 2.5 (거래량 2.5배 폭증)
    - ClosePos >= 0.75 (봉 상단 75% 위치 마감)
    - 롱: price > 최근 12개 5m 고점 AND price >= VWAP
    - 숏: price < 최근 12개 5m 저점 AND price <= VWAP (Long-only 모드 제외)

    진입 조건 (Phase 2 - 확인, 5분 후):
    - 돌파 레벨 위/아래 유지
    - RVOL >= 1.25 유지

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

        # Long-only 모드 (Upbit은 숏 불가)
        self._long_only_mode = settings.is_upbit

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

        # 과열 추격 금지 임계값
        self.overheat_threshold = OVERHEAT_THRESHOLD  # +12%

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

        v2: 각 코인별 독립 돌파 조건 체크 (BTC Regime 의존 제거)

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

        # 레짐 업데이트 (청산 조건용으로만 사용)
        self._check_regime_from_feature_engine()

        # VOLATILE일 때만 신규 진입 차단 (급변장 안전장치)
        if self._btc_regime == Regime.VOLATILE or self._btc_is_volatile:
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

    def _is_overheated(self, market_data: dict) -> bool:
        """과열 추격 금지 체크 - 전일 대비 급등 시 진입 금지"""
        change_rate = market_data.get("change_rate", 0)
        if change_rate >= self.overheat_threshold:
            logger.info(
                "Satellite overheat detected - skip entry",
                symbol=market_data.get("symbol"),
                change_rate=f"{change_rate:.1%}",
                threshold=f"{self.overheat_threshold:.1%}",
            )
            return True
        return False

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

        # 과열 추격 금지 (Long-only 모드에서만 적용)
        if self._long_only_mode and self._is_overheated(market_data):
            return False

        # RVOL 필터
        if rvol < self.rvol_threshold:
            return False

        # ClosePos 필터
        if close_pos < self.close_pos_threshold:
            return False

        # 디버그 로그 - 조건 통과 코인
        logger.info(
            "Satellite breakout candidate",
            symbol=symbol,
            price=price,
            rvol=f"{rvol:.2f}",
            close_pos=f"{close_pos:.2f}",
            highest_12=highest_12,
            lowest_12=lowest_12,
            vwap=f"{vwap:.2f}",
            at_high=price >= highest_12,
            at_low=price <= lowest_12,
            long_only_mode=self._long_only_mode,
        )

        # 돌파 체크 (롱) - 코인별 독립 판단
        # 24h 고점의 99% 이상이면 돌파 근접으로 인정
        breakout_threshold_high = highest_12 * 0.99 if highest_12 > 0 else 0
        if price >= breakout_threshold_high > 0 and price >= vwap:
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
                close_pos=close_pos,
            )
            return True

        # 돌파 체크 (숏) - Long-only 모드에서는 스킵
        if self._long_only_mode:
            return False

        # 24h 저점의 101% 이하면 돌파 근접으로 인정
        breakout_threshold_low = lowest_12 * 1.01 if lowest_12 > 0 else float("inf")
        if price <= breakout_threshold_low < float("inf") and price <= vwap:
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
                close_pos=1 - close_pos,
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

        # 과열 추격 금지 (Long-only 모드에서만 적용)
        if self._long_only_mode and self._is_overheated(market_data):
            return None

        # RVOL 필터
        if rvol < self.rvol_threshold:
            return None

        # ClosePos 필터
        if close_pos < self.close_pos_threshold:
            return None

        # 고점 돌파 (롱) - 코인별 독립 판단
        breakout_threshold_high = highest_12 * 0.99 if highest_12 > 0 else 0
        if price >= breakout_threshold_high > 0 and price >= vwap:
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

        # 저점 돌파 (숏) - Long-only 모드에서는 스킵
        if self._long_only_mode:
            return None

        breakout_threshold_low = lowest_12 * 1.01 if lowest_12 > 0 else float("inf")
        if price <= breakout_threshold_low < float("inf") and price <= vwap:
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

        # v2: BTC 레짐 기반 청산 제거 (개별 코인 독립 판단)
        # VOLATILE 청산만 유지 (위에서 처리됨)

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
            "long_only_mode": self._long_only_mode,
            "overheat_threshold": self.overheat_threshold,
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

    def sync_positions_from_balances(
        self,
        balances: list,
        market_data: dict,
    ) -> int:
        """
        Upbit 잔고에서 기존 포지션 동기화

        서버 재시작 시 호출하여 기존 보유 종목을 _active_positions에 등록

        Args:
            balances: BalanceInfo 리스트 (Upbit 잔고)
            market_data: 현재 시세 정보

        Returns:
            동기화된 포지션 수
        """
        synced_count = 0

        for balance in balances:
            if balance.asset == "KRW" or balance.total <= 0:
                continue

            symbol = f"KRW-{balance.asset}"

            # 이미 추적 중이면 스킵
            if symbol in self._active_positions:
                continue

            # 평균 매수가 (Upbit API에서 제공)
            entry_price = balance.avg_buy_price
            if entry_price <= 0:
                continue

            # 현재가 조회
            current_price = market_data.get(symbol, {}).get("price", entry_price)

            # 수익률 계산
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

            # 트레일링 활성화 여부 (이미 +1% 이상이면 활성화)
            trailing_active = pnl_pct >= self.trailing_trigger_pct

            # 포지션 등록
            self._active_positions[symbol] = SatellitePosition(
                symbol=symbol,
                side=OrderSide.BUY,
                entry_price=entry_price,
                quantity=balance.total,
                entry_time=datetime.utcnow(),  # 실제 진입 시간은 알 수 없음
                highest_price=max(entry_price, current_price),  # 현재가가 고점일 수 있음
                lowest_price=min(entry_price, current_price),
                trailing_active=trailing_active,
            )

            logger.info(
                "Synced position from Upbit balance",
                symbol=symbol,
                entry_price=entry_price,
                quantity=balance.total,
                current_price=current_price,
                pnl_pct=f"{pnl_pct:.2%}",
                trailing_active=trailing_active,
            )

            synced_count += 1

        if synced_count > 0:
            logger.info(
                "Position sync completed",
                synced_count=synced_count,
                total_positions=len(self._active_positions),
            )

        return synced_count

    def get_positions_for_liquidation(
        self,
        market_data: dict,
        required_amount: float,
        max_loss_pct: float = -0.02,
    ) -> list[tuple[str, float, float, float]]:
        """
        Surge/Ignition 진입을 위한 자본 확보용 청산 대상 포지션 선택

        청산 우선순위:
        1. 수익 포지션 (PnL% 내림차순 - 이익 큰 것부터)
        2. 소폭 손실 포지션 (max_loss_pct 이상)
        3. 큰 손실 포지션은 제외 (손실 확정 방지)

        Args:
            market_data: 현재 시세 정보 {symbol: {price: float, ...}}
            required_amount: 확보해야 할 금액 (KRW)
            max_loss_pct: 청산 허용 최대 손실률 (기본 -2%)

        Returns:
            청산 대상 리스트: [(symbol, quantity, current_price, pnl_pct), ...]
        """
        if not self._active_positions:
            return []

        # 포지션별 PnL 계산
        positions_with_pnl = []
        for symbol, pos in self._active_positions.items():
            current_price = market_data.get(symbol, {}).get("price", pos.entry_price)
            if current_price <= 0:
                continue

            pnl_pct = (current_price - pos.entry_price) / pos.entry_price
            notional = pos.quantity * current_price

            # 최대 손실 이상인 것만 청산 대상
            if pnl_pct >= max_loss_pct:
                positions_with_pnl.append({
                    "symbol": symbol,
                    "quantity": pos.quantity,
                    "current_price": current_price,
                    "entry_price": pos.entry_price,
                    "pnl_pct": pnl_pct,
                    "notional": notional,
                })

        # 정렬: 수익 포지션 우선, 수익률 내림차순
        positions_with_pnl.sort(key=lambda x: x["pnl_pct"], reverse=True)

        # 필요 금액만큼 선택
        selected = []
        accumulated = 0.0

        for pos_info in positions_with_pnl:
            if accumulated >= required_amount:
                break

            selected.append((
                pos_info["symbol"],
                pos_info["quantity"],
                pos_info["current_price"],
                pos_info["pnl_pct"],
            ))
            accumulated += pos_info["notional"]

            logger.info(
                "Selected Satellite for liquidation",
                symbol=pos_info["symbol"],
                pnl_pct=f"{pos_info['pnl_pct']:.2%}",
                notional=f"₩{pos_info['notional']:,.0f}",
                accumulated=f"₩{accumulated:,.0f}",
                required=f"₩{required_amount:,.0f}",
            )

        return selected

    def get_active_positions_summary(self, market_data: dict) -> dict:
        """
        활성 포지션 요약 (청산 의사결정용)

        Returns:
            {
                "total_count": int,
                "total_notional": float,
                "profitable_count": int,
                "profitable_notional": float,
                "losing_count": int,
                "positions": [...]
            }
        """
        if not self._active_positions:
            return {
                "total_count": 0,
                "total_notional": 0,
                "profitable_count": 0,
                "profitable_notional": 0,
                "losing_count": 0,
                "positions": [],
            }

        positions_info = []
        total_notional = 0
        profitable_count = 0
        profitable_notional = 0
        losing_count = 0

        for symbol, pos in self._active_positions.items():
            current_price = market_data.get(symbol, {}).get("price", pos.entry_price)
            if current_price <= 0:
                continue

            pnl_pct = (current_price - pos.entry_price) / pos.entry_price
            notional = pos.quantity * current_price

            total_notional += notional
            if pnl_pct >= 0:
                profitable_count += 1
                profitable_notional += notional
            else:
                losing_count += 1

            positions_info.append({
                "symbol": symbol,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "current_price": current_price,
                "pnl_pct": pnl_pct,
                "notional": notional,
            })

        # PnL 내림차순 정렬
        positions_info.sort(key=lambda x: x["pnl_pct"], reverse=True)

        return {
            "total_count": len(positions_info),
            "total_notional": total_notional,
            "profitable_count": profitable_count,
            "profitable_notional": profitable_notional,
            "losing_count": losing_count,
            "positions": positions_info,
        }
