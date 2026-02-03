"""
Ignition Strategy V2 - 2-Engine 통합 전략 (v4.0)

2-Engine 아키텍처:
- Ignition Engine: 초반 1파 (돌파 직후, 공격적)
- Retest Engine: 리테스트 2파 (되돌림 후 재진입, 보수적)

리스크 기반 사이징:
- position_value = (equity × risk_per_trade) / stop_distance
- ATTACK_LEVEL 1/2/3: 0.20% / 0.35% / 0.50% risk per trade

통합 컴포넌트:
- DD Tier Gate: Tier 4+ → Ignition OFF
- Liquidity Time Filter: 시간대별 유동성 조정
- Pre-Ignition Patterns: 압축/건조 감지
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.strategies.ignition.engines import (
    IgnitionEngineV2,
    RetestEngine,
    get_ignition_engine_v2,
    get_retest_engine,
    EngineSignal,
)
from src.strategies.ignition.sizing import (
    RiskSizer,
    AttackLevel,
    get_risk_sizer,
)
from src.strategies.ignition.filters import (
    IgnitionDDGate,
    DDGateResult,
    get_ignition_dd_gate,
    LiquidityTimeFilter,
    get_liquidity_time_filter,
)
from src.strategies.ignition.patterns import (
    VolatilitySqueezeDetector,
    VolumeDryupDetector,
    get_volatility_squeeze_detector,
    get_volume_dryup_detector,
)
from src.strategies.ignition.setup_engine import (
    SetupCandidate,
    get_setup_engine,
)

logger = structlog.get_logger()


class PositionStatusV2(str, Enum):
    """포지션 상태"""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    CLOSED = "CLOSED"


class ExitReasonV2(str, Enum):
    """청산 사유"""

    STOP_LOSS = "STOP_LOSS"
    TIME_STOP = "TIME_STOP"
    PROFIT_PROTECTION = "PROFIT_PROTECTION"
    TRAILING_STOP = "TRAILING_STOP"
    DD_HALT = "DD_HALT"
    MANUAL = "MANUAL"
    SIGNAL_INVALID = "SIGNAL_INVALID"


@dataclass
class PositionV2:
    """V2 포지션"""

    symbol: str
    engine_type: str  # "IGNITION" or "RETEST"
    signal: EngineSignal
    attack_level: int
    status: PositionStatusV2
    opened_at: datetime

    # 진입 정보
    entry_price: float = 0
    quantity: float = 0
    position_value: float = 0
    stop_loss: float = 0
    risk_amount: float = 0

    # 현재 상태
    highest_price: float = 0
    trailing_stop: Optional[float] = None
    partial_exit_quantity: float = 0
    current_r: float = 0

    # 손익
    realized_pnl: float = 0
    unrealized_pnl: float = 0

    # 청산 정보
    closed_at: Optional[datetime] = None
    exit_reason: Optional[ExitReasonV2] = None
    exit_price: float = 0

    def update_pnl(self, current_price: float) -> None:
        """손익 업데이트"""
        remaining_qty = self.quantity - self.partial_exit_quantity
        if remaining_qty <= 0:
            return

        self.unrealized_pnl = (current_price - self.entry_price) * remaining_qty

        if current_price > self.highest_price:
            self.highest_price = current_price

        risk_per_share = abs(self.entry_price - self.stop_loss)
        if risk_per_share > 0:
            self.current_r = (current_price - self.entry_price) / risk_per_share

    def to_dict(self) -> dict:
        remaining_qty = self.quantity - self.partial_exit_quantity
        return {
            "symbol": self.symbol,
            "engine_type": self.engine_type,
            "attack_level": self.attack_level,
            "status": self.status.value,
            "entry_price": round(self.entry_price, 2),
            "quantity": round(remaining_qty, 8),
            "position_value": round(self.position_value, 0),
            "stop_loss": round(self.stop_loss, 2),
            "risk_amount": round(self.risk_amount, 0),
            "highest_price": round(self.highest_price, 2),
            "trailing_stop": round(self.trailing_stop, 2) if self.trailing_stop else None,
            "current_r": round(self.current_r, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 0),
            "realized_pnl": round(self.realized_pnl, 0),
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "exit_reason": self.exit_reason.value if self.exit_reason else None,
        }


class IgnitionStrategyV2:
    """
    Ignition Strategy V2

    2-Engine 아키텍처 + 리스크 기반 사이징 통합
    """

    def __init__(self) -> None:
        self._settings = get_settings()

        # Engines
        self._ignition_engine = get_ignition_engine_v2()
        self._retest_engine = get_retest_engine()
        self._setup_engine = get_setup_engine()

        # Sizing
        self._risk_sizer = get_risk_sizer()

        # Filters & Gates
        self._dd_gate = get_ignition_dd_gate()
        self._liquidity_filter = get_liquidity_time_filter()

        # Patterns
        self._squeeze_detector = get_volatility_squeeze_detector()
        self._dryup_detector = get_volume_dryup_detector()

        # State
        self._positions: dict[str, PositionV2] = {}
        self._closed_positions: list[PositionV2] = []
        self._equity: float = 0
        self._dd_tier: int = 1
        self._daily_loss_pct: float = 0
        self._attack_level: AttackLevel = AttackLevel.LEVEL_2

    # === 설정 메서드 ===

    def set_equity(self, equity: float) -> None:
        """계좌 자산 설정"""
        self._equity = equity

    def set_dd_state(self, dd_tier: int, daily_loss_pct: float) -> None:
        """DD 상태 설정"""
        self._dd_tier = dd_tier
        self._daily_loss_pct = daily_loss_pct

    def set_attack_level(self, level: int) -> None:
        """ATTACK_LEVEL 설정 (1/2/3)"""
        if level in [1, 2, 3]:
            self._attack_level = AttackLevel(level)
            logger.info("Attack level changed", level=level)

    # === 메인 스캔 로직 ===

    async def scan_and_detect(
        self,
        symbols: list[str],
        market_data_map: dict[str, dict],
    ) -> list[EngineSignal]:
        """
        Setup 스캔 + 엔진 신호 감지

        Returns:
            감지된 엔진 신호 리스트
        """
        signals: list[EngineSignal] = []

        # DD Gate 체크
        dd_result = self._dd_gate.check(
            dd_tier=self._dd_tier,
            daily_loss_pct=self._daily_loss_pct,
        )

        if dd_result.mode == "HALT":
            logger.warning("Trading halted by DD gate", reason=dd_result.reason)
            return signals

        # 시간대 조정값
        time_adj = self._liquidity_filter.get_adjustment()

        # Setup 스캔
        candidates = await self._setup_engine.scan_setups(symbols, market_data_map)

        for candidate in candidates:
            symbol = candidate.symbol
            if symbol in self._positions:
                continue

            market_data = market_data_map.get(symbol, {})

            # Pre-Ignition 패턴 체크
            squeeze = self._squeeze_detector.detect(symbol)
            dryup = self._dryup_detector.detect(symbol)

            pre_ignition_score = (squeeze.squeeze_score + dryup.dryup_score) / 2

            # Ignition Engine 체크
            if dd_result.ignition_allowed:
                ignition_signal = self._ignition_engine.detect_signal(
                    candidate=candidate,
                    market_data=market_data,
                    dd_tier=self._dd_tier,
                    daily_loss_pct=self._daily_loss_pct,
                )

                if ignition_signal and ignition_signal.passed:
                    ignition_signal.pre_ignition_score = pre_ignition_score
                    signals.append(ignition_signal)
                    continue

            # Retest Engine 체크
            if dd_result.retest_allowed:
                retest_signal = self._retest_engine.detect_signal(
                    candidate=candidate,
                    market_data=market_data,
                    dd_tier=self._dd_tier,
                    daily_loss_pct=self._daily_loss_pct,
                )

                if retest_signal and retest_signal.passed:
                    retest_signal.pre_ignition_score = pre_ignition_score
                    signals.append(retest_signal)

        logger.info(
            "Scan completed",
            candidates=len(candidates),
            signals=len(signals),
            dd_mode=dd_result.mode,
            time_zone=time_adj.time_zone,
        )

        return signals

    # === 진입 로직 ===

    def try_entry(
        self,
        signal: EngineSignal,
        current_price: float,
    ) -> Optional[PositionV2]:
        """
        진입 시도

        Args:
            signal: 엔진 신호
            current_price: 현재가

        Returns:
            생성된 포지션 (실패시 None)
        """
        symbol = signal.symbol

        if symbol in self._positions:
            return None

        # DD Gate 재확인
        dd_result = self._dd_gate.check(
            dd_tier=self._dd_tier,
            daily_loss_pct=self._daily_loss_pct,
        )

        if dd_result.mode == "HALT":
            return None

        engine_type = signal.engine_type
        if engine_type == "IGNITION" and not dd_result.ignition_allowed:
            logger.debug("Ignition blocked by DD gate", symbol=symbol)
            return None

        if engine_type == "RETEST" and not dd_result.retest_allowed:
            logger.debug("Retest blocked by DD gate", symbol=symbol)
            return None

        # 시간대 조정
        time_adj = self._liquidity_filter.get_adjustment()
        if not time_adj.allow_market_order:
            logger.debug(
                "Market order blocked by time filter",
                symbol=symbol,
                reason=time_adj.reason,
            )

        # 손절가 계산
        stop_loss = signal.stop_loss

        # 리스크 기반 사이징
        sizing = self._risk_sizer.calculate(
            equity=self._equity,
            attack_level=self._attack_level,
            entry_price=current_price,
            stop_loss_price=stop_loss,
            dd_tier=self._dd_tier,
            daily_loss_pct=self._daily_loss_pct,
            engine_type=engine_type,
        )

        if sizing.position_value <= 0:
            logger.debug("Sizing rejected", symbol=symbol)
            return None

        # DD Gate 사이징 조정
        final_position_value = sizing.position_value * dd_result.sizing_multiplier

        # 포지션 생성
        quantity = final_position_value / current_price

        position = PositionV2(
            symbol=symbol,
            engine_type=engine_type,
            signal=signal,
            attack_level=self._attack_level.value,
            status=PositionStatusV2.ACTIVE,
            opened_at=datetime.now(timezone.utc),
            entry_price=current_price,
            quantity=quantity,
            position_value=final_position_value,
            stop_loss=stop_loss,
            risk_amount=sizing.risk_amount,
            highest_price=current_price,
        )

        self._positions[symbol] = position

        # Watchlist에서 제거
        self._setup_engine.remove_from_watchlist(symbol)

        logger.info(
            "Position opened (V2)",
            symbol=symbol,
            engine_type=engine_type,
            attack_level=self._attack_level.value,
            entry_price=current_price,
            position_value=round(final_position_value, 0),
            stop_loss=round(stop_loss, 2),
            risk_amount=round(sizing.risk_amount, 0),
            dd_sizing_mult=dd_result.sizing_multiplier,
        )

        return position

    # === 청산 로직 ===

    def check_exits(
        self,
        symbol: str,
        current_price: float,
    ) -> Optional[tuple[ExitReasonV2, float]]:
        """
        청산 조건 체크

        Returns:
            (청산 사유, 청산 비율) 또는 None
        """
        settings = self._settings
        position = self._positions.get(symbol)

        if not position or position.status == PositionStatusV2.CLOSED:
            return None

        position.update_pnl(current_price)

        # DD HALT 체크
        dd_result = self._dd_gate.check(
            dd_tier=self._dd_tier,
            daily_loss_pct=self._daily_loss_pct,
        )
        if dd_result.mode == "HALT":
            return ExitReasonV2.DD_HALT, 1.0

        # 손절
        if current_price <= position.stop_loss:
            return ExitReasonV2.STOP_LOSS, 1.0

        # 시간 청산
        time_limit = timedelta(minutes=getattr(settings, "ignition_time_stop_min", 30))
        if datetime.now(timezone.utc) - position.opened_at > time_limit:
            if position.current_r < 0:
                return ExitReasonV2.TIME_STOP, 1.0

        # 이익 보호 (+1R에서 50%)
        protection_r = getattr(settings, "ignition_profit_protection_r", 1.0)
        protection_pct = getattr(settings, "ignition_profit_protection_pct", 0.5)

        if (
            position.current_r >= protection_r
            and position.status == PositionStatusV2.ACTIVE
        ):
            position.status = PositionStatusV2.PARTIAL_EXIT
            return ExitReasonV2.PROFIT_PROTECTION, protection_pct

        # 트레일링 스톱 (+2R 이상)
        trailing_trigger_r = getattr(settings, "ignition_trailing_trigger_r", 2.0)
        trailing_atr_mult = getattr(settings, "ignition_trailing_stop_atr_mult", 1.0)
        atr = getattr(position.signal, "atr", 0) or (position.entry_price * 0.02)

        if position.current_r >= trailing_trigger_r:
            new_trailing = position.highest_price - (atr * trailing_atr_mult)

            if position.trailing_stop is None or new_trailing > position.trailing_stop:
                position.trailing_stop = new_trailing

            if current_price <= position.trailing_stop:
                return ExitReasonV2.TRAILING_STOP, 1.0

        return None

    def close_position(
        self,
        symbol: str,
        reason: ExitReasonV2,
        exit_price: float,
        exit_ratio: float = 1.0,
    ) -> Optional[PositionV2]:
        """
        포지션 청산

        Args:
            symbol: 심볼
            reason: 청산 사유
            exit_price: 청산가
            exit_ratio: 청산 비율 (1.0 = 전량)

        Returns:
            청산된 포지션
        """
        position = self._positions.get(symbol)
        if not position:
            return None

        remaining_qty = position.quantity - position.partial_exit_quantity
        exit_quantity = remaining_qty * exit_ratio

        # 실현 손익
        realized = (exit_price - position.entry_price) * exit_quantity
        position.realized_pnl += realized

        if exit_ratio >= 1.0:
            # 전량 청산
            position.status = PositionStatusV2.CLOSED
            position.closed_at = datetime.now(timezone.utc)
            position.exit_reason = reason
            position.exit_price = exit_price

            del self._positions[symbol]
            self._closed_positions.append(position)
        else:
            # 부분 청산
            position.partial_exit_quantity += exit_quantity

        logger.info(
            "Position closed (V2)",
            symbol=symbol,
            engine_type=position.engine_type,
            reason=reason.value,
            exit_price=exit_price,
            exit_quantity=round(exit_quantity, 8),
            realized_pnl=round(realized, 0),
        )

        return position

    # === 상태 조회 ===

    def get_position(self, symbol: str) -> Optional[PositionV2]:
        return self._positions.get(symbol)

    def get_all_positions(self) -> list[PositionV2]:
        return list(self._positions.values())

    def get_closed_positions(self) -> list[PositionV2]:
        return self._closed_positions

    def get_status(self) -> dict:
        """전체 상태 반환"""
        positions = self.get_all_positions()
        total_unrealized = sum(p.unrealized_pnl for p in positions)
        total_realized = sum(p.realized_pnl for p in self._closed_positions)

        dd_result = self._dd_gate.check(
            dd_tier=self._dd_tier,
            daily_loss_pct=self._daily_loss_pct,
        )

        time_adj = self._liquidity_filter.get_adjustment()

        return {
            "version": "v4.0",
            "attack_level": self._attack_level.value,
            "equity": self._equity,
            "dd_tier": self._dd_tier,
            "daily_loss_pct": round(self._daily_loss_pct * 100, 2),
            "dd_mode": dd_result.mode,
            "dd_sizing_mult": dd_result.sizing_multiplier,
            "ignition_allowed": dd_result.ignition_allowed,
            "retest_allowed": dd_result.retest_allowed,
            "time_zone": time_adj.time_zone,
            "time_reason": time_adj.reason,
            "active_positions": len(positions),
            "closed_positions": len(self._closed_positions),
            "total_unrealized_pnl": round(total_unrealized, 0),
            "total_realized_pnl": round(total_realized, 0),
            "positions": [p.to_dict() for p in positions],
        }

    def cleanup(self) -> None:
        """정리 작업"""
        self._setup_engine.cleanup_stale()


# 싱글톤
_ignition_strategy_v2: Optional[IgnitionStrategyV2] = None


def get_ignition_strategy_v2() -> IgnitionStrategyV2:
    """IgnitionStrategyV2 싱글톤"""
    global _ignition_strategy_v2
    if _ignition_strategy_v2 is None:
        _ignition_strategy_v2 = IgnitionStrategyV2()
    return _ignition_strategy_v2
