"""
Ignition Strategy - 통합 전략 클래스

역할:
- Setup Engine + Ignition Engine + Anti-Chase Gate + Position Policy 통합
- 포지션 관리 (진입, 증액, 청산)
- Exit 관리:
  1. 시간 청산 (30분)
  2. 이익 보호 (+1R에서 50% 청산)
  3. 트레일링 스톱 (+2R 이상에서 ATR 1배 트레일)
  4. 손절
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.strategies.ignition.anti_chase_gate import (
    AntiChaseGate,
    AntiChaseReason,
    get_anti_chase_gate,
)
from src.strategies.ignition.ignition_engine import (
    IgnitionEngine,
    IgnitionSignal,
    get_ignition_engine,
)
from src.strategies.ignition.ignition_position_policy import (
    IgnitionPositionPolicy,
    IgnitionPositionSizing,
    get_ignition_position_policy,
)
from src.strategies.ignition.setup_engine import (
    SetupCandidate,
    SetupEngine,
    get_setup_engine,
)

logger = structlog.get_logger()


class PositionStatus(str, Enum):
    """포지션 상태"""

    PENDING = "PENDING"  # 진입 대기
    ACTIVE = "ACTIVE"  # 활성
    PARTIAL_EXIT = "PARTIAL_EXIT"  # 부분 청산
    CLOSED = "CLOSED"  # 청산 완료


class ExitReason(str, Enum):
    """청산 사유"""

    STOP_LOSS = "STOP_LOSS"  # 손절
    TIME_STOP = "TIME_STOP"  # 시간 청산
    PROFIT_PROTECTION = "PROFIT_PROTECTION"  # 이익 보호
    TRAILING_STOP = "TRAILING_STOP"  # 트레일링 스톱
    MANUAL = "MANUAL"  # 수동 청산
    SIGNAL_INVALID = "SIGNAL_INVALID"  # 신호 무효화


@dataclass
class IgnitionPosition:
    """Ignition 포지션"""

    symbol: str
    signal: IgnitionSignal
    sizings: list[IgnitionPositionSizing]
    status: PositionStatus
    opened_at: datetime
    closed_at: Optional[datetime] = None
    exit_reason: Optional[ExitReason] = None
    total_quantity: float = 0
    avg_entry_price: float = 0
    stop_loss: float = 0
    highest_price: float = 0
    trailing_stop: Optional[float] = None
    partial_exit_quantity: float = 0
    realized_pnl: float = 0
    unrealized_pnl: float = 0
    current_r: float = 0

    def update_from_sizings(self) -> None:
        """사이징 정보로 포지션 업데이트"""
        if not self.sizings:
            return

        total_qty = sum(s.quantity for s in self.sizings)
        if total_qty > 0:
            self.avg_entry_price = (
                sum(s.quantity * s.entry_price for s in self.sizings) / total_qty
            )
        self.total_quantity = total_qty - self.partial_exit_quantity
        self.stop_loss = self.sizings[0].stop_loss

    def update_pnl(self, current_price: float) -> None:
        """손익 업데이트"""
        if self.total_quantity <= 0:
            return

        self.unrealized_pnl = (current_price - self.avg_entry_price) * self.total_quantity

        # 최고가 업데이트
        if current_price > self.highest_price:
            self.highest_price = current_price

        # 현재 R 계산
        risk_per_share = abs(self.avg_entry_price - self.stop_loss)
        if risk_per_share > 0:
            self.current_r = (current_price - self.avg_entry_price) / risk_per_share

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "status": self.status.value,
            "total_quantity": round(self.total_quantity, 8),
            "avg_entry_price": round(self.avg_entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "highest_price": round(self.highest_price, 2),
            "trailing_stop": round(self.trailing_stop, 2) if self.trailing_stop else None,
            "current_r": round(self.current_r, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 0),
            "realized_pnl": round(self.realized_pnl, 0),
            "add_count": len(self.sizings) - 1,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "exit_reason": self.exit_reason.value if self.exit_reason else None,
        }


class IgnitionStrategy:
    """
    Ignition Strategy

    통합 전략 클래스
    - Setup → Ignition → Entry → Exit 전체 흐름 관리
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._setup_engine = get_setup_engine()
        self._ignition_engine = get_ignition_engine()
        self._anti_chase_gate = get_anti_chase_gate()
        self._position_policy = get_ignition_position_policy()

        self._positions: dict[str, IgnitionPosition] = {}
        self._closed_positions: list[IgnitionPosition] = []
        self._mode: str = "NORMAL"
        self._account_balance: float = 0

    def set_mode(self, mode: str) -> None:
        """운영 모드 설정 (NORMAL/ATTACK)"""
        if mode in ["NORMAL", "ATTACK"]:
            self._mode = mode
            logger.info("Ignition strategy mode changed", mode=mode)

    def set_account_balance(self, balance: float) -> None:
        """계좌 잔고 설정"""
        self._account_balance = balance

    def set_btc_change(self, change_24h: float) -> None:
        """BTC 24시간 변화율 설정"""
        self._setup_engine.set_btc_change(change_24h)

    async def scan_setups(
        self,
        symbols: list[str],
        market_data_map: dict[str, dict],
    ) -> list[SetupCandidate]:
        """
        Setup 스캔 (15분 주기)

        Args:
            symbols: 스캔할 심볼 리스트
            market_data_map: 심볼별 시장 데이터

        Returns:
            새로 추가된 후보 리스트
        """
        return await self._setup_engine.scan_setups(symbols, market_data_map)

    def check_ignition(
        self,
        symbol: str,
        market_data: dict,
    ) -> Optional[IgnitionSignal]:
        """
        점화 체크 (실시간)

        Args:
            symbol: 심볼
            market_data: 시장 데이터

        Returns:
            점화 신호 (없으면 None)
        """
        candidate = self._setup_engine.get_candidate(symbol)
        if not candidate:
            return None

        return self._ignition_engine.check_ignition(candidate, market_data)

    def try_entry(
        self,
        signal: IgnitionSignal,
        current_price: float,
        bid_price: float,
        ask_price: float,
        candle_data: Optional[dict] = None,
    ) -> Optional[IgnitionPosition]:
        """
        진입 시도

        Args:
            signal: 점화 신호
            current_price: 현재가
            bid_price: 매수 호가
            ask_price: 매도 호가
            candle_data: 현재 캔들 데이터

        Returns:
            생성된 포지션 (진입 실패시 None)
        """
        symbol = signal.symbol

        # 이미 포지션이 있으면 스킵
        if symbol in self._positions:
            return None

        # Anti-Chase Gate 체크
        chase_result = self._anti_chase_gate.check(
            signal=signal,
            current_price=current_price,
            bid_price=bid_price,
            ask_price=ask_price,
            candle_data=candle_data,
        )

        if not chase_result.passed:
            logger.info(
                "Entry blocked by anti-chase gate",
                symbol=symbol,
                reason=chase_result.reason.value,
            )
            return None

        # 사이징 계산
        sizing = self._position_policy.calculate_initial_sizing(
            signal=signal,
            account_balance=self._account_balance,
            mode=self._mode,
        )

        # 포지션 생성
        position = IgnitionPosition(
            symbol=symbol,
            signal=signal,
            sizings=[sizing],
            status=PositionStatus.ACTIVE,
            opened_at=datetime.utcnow(),
            highest_price=current_price,
        )
        position.update_from_sizings()

        self._positions[symbol] = position

        # Watchlist에서 제거
        self._setup_engine.remove_from_watchlist(symbol)

        logger.info(
            "Ignition position opened",
            symbol=symbol,
            mode=self._mode,
            quantity=sizing.quantity,
            entry_price=sizing.entry_price,
            stop_loss=sizing.stop_loss,
        )

        return position

    def try_add_position(
        self,
        symbol: str,
        current_price: float,
    ) -> Optional[IgnitionPositionSizing]:
        """
        증액 시도 (Profit-add only)

        Args:
            symbol: 심볼
            current_price: 현재가

        Returns:
            새 사이징 (증액 불가시 None)
        """
        position = self._positions.get(symbol)
        if not position or position.status != PositionStatus.ACTIVE:
            return None

        current_sizing = position.sizings[-1]

        # 증액 가능 여부 확인
        can_add, trigger_r = self._position_policy.can_add_position(
            current_sizing, current_price
        )

        if not can_add:
            return None

        # 증액 사이징 계산
        new_sizing = self._position_policy.calculate_add_sizing(
            signal=position.signal,
            current_sizing=current_sizing,
            current_price=current_price,
            account_balance=self._account_balance,
        )

        if not new_sizing:
            return None

        # 포지션에 추가
        position.sizings.append(new_sizing)
        position.update_from_sizings()

        logger.info(
            "Position added",
            symbol=symbol,
            phase=new_sizing.phase.value,
            add_quantity=new_sizing.quantity,
            trigger_r=trigger_r,
        )

        return new_sizing

    def check_exits(
        self,
        symbol: str,
        current_price: float,
    ) -> Optional[tuple[ExitReason, float]]:
        """
        청산 조건 체크

        Args:
            symbol: 심볼
            current_price: 현재가

        Returns:
            (청산 사유, 청산 비율) 또는 None
        """
        settings = self._settings
        position = self._positions.get(symbol)

        if not position or position.status == PositionStatus.CLOSED:
            return None

        position.update_pnl(current_price)

        # === 1. 손절 체크 ===
        if current_price <= position.stop_loss:
            return ExitReason.STOP_LOSS, 1.0

        # === 2. 시간 청산 체크 ===
        time_limit = timedelta(minutes=settings.ignition_time_stop_min)
        if datetime.utcnow() - position.opened_at > time_limit:
            if position.current_r < 0:  # 손실 중이면 전량 청산
                return ExitReason.TIME_STOP, 1.0

        # === 3. 이익 보호 청산 (+1R에서 50%) ===
        protection_r = settings.ignition_profit_protection_r
        protection_pct = settings.ignition_profit_protection_pct

        if (
            position.current_r >= protection_r
            and position.status == PositionStatus.ACTIVE
        ):
            position.status = PositionStatus.PARTIAL_EXIT
            return ExitReason.PROFIT_PROTECTION, protection_pct

        # === 4. 트레일링 스톱 체크 ===
        trailing_trigger_r = settings.ignition_trailing_trigger_r
        trailing_atr_mult = settings.ignition_trailing_stop_atr_mult
        atr = position.signal.atr_1h

        if position.current_r >= trailing_trigger_r:
            # 트레일링 스톱 설정/업데이트
            new_trailing = position.highest_price - (atr * trailing_atr_mult)

            if position.trailing_stop is None or new_trailing > position.trailing_stop:
                position.trailing_stop = new_trailing

            # 트레일링 스톱 이탈 체크
            if current_price <= position.trailing_stop:
                return ExitReason.TRAILING_STOP, 1.0

        return None

    def close_position(
        self,
        symbol: str,
        reason: ExitReason,
        exit_price: float,
        exit_quantity: Optional[float] = None,
    ) -> Optional[IgnitionPosition]:
        """
        포지션 청산

        Args:
            symbol: 심볼
            reason: 청산 사유
            exit_price: 청산가
            exit_quantity: 청산 수량 (None이면 전량)

        Returns:
            청산된 포지션
        """
        position = self._positions.get(symbol)
        if not position:
            return None

        # 청산 수량 결정
        if exit_quantity is None:
            exit_quantity = position.total_quantity

        # 실현 손익 계산
        realized = (exit_price - position.avg_entry_price) * exit_quantity
        position.realized_pnl += realized

        if exit_quantity >= position.total_quantity:
            # 전량 청산
            position.status = PositionStatus.CLOSED
            position.closed_at = datetime.utcnow()
            position.exit_reason = reason
            position.total_quantity = 0

            # 활성 포지션에서 제거
            del self._positions[symbol]
            self._closed_positions.append(position)

            # 점화 신호 무효화
            self._ignition_engine.invalidate_signal(symbol, f"Position closed: {reason.value}")
        else:
            # 부분 청산
            position.partial_exit_quantity += exit_quantity
            position.total_quantity -= exit_quantity

        logger.info(
            "Position closed",
            symbol=symbol,
            reason=reason.value,
            exit_price=exit_price,
            exit_quantity=exit_quantity,
            realized_pnl=realized,
        )

        return position

    def get_position(self, symbol: str) -> Optional[IgnitionPosition]:
        """특정 심볼의 포지션 반환"""
        return self._positions.get(symbol)

    def get_all_positions(self) -> list[IgnitionPosition]:
        """모든 활성 포지션 반환"""
        return list(self._positions.values())

    def get_watchlist(self) -> list[SetupCandidate]:
        """현재 Watchlist 반환"""
        return self._setup_engine.get_watchlist()

    def get_active_signals(self) -> list[IgnitionSignal]:
        """활성 점화 신호 반환"""
        return self._ignition_engine.get_all_active_signals()

    def get_status(self) -> dict:
        """전체 상태 반환"""
        positions = self.get_all_positions()
        total_unrealized = sum(p.unrealized_pnl for p in positions)
        total_realized = sum(p.realized_pnl for p in self._closed_positions)

        return {
            "mode": self._mode,
            "account_balance": self._account_balance,
            "active_positions": len(positions),
            "total_unrealized_pnl": round(total_unrealized, 0),
            "total_realized_pnl": round(total_realized, 0),
            "positions": [p.to_dict() for p in positions],
            "setup_engine": self._setup_engine.get_status(),
            "ignition_engine": self._ignition_engine.get_status(),
            "anti_chase_gate": self._anti_chase_gate.get_status(),
            "position_policy": self._position_policy.get_status(),
        }

    def cleanup(self) -> None:
        """정리 작업"""
        self._setup_engine.cleanup_stale()
        self._ignition_engine.cleanup_expired()


# 싱글톤 인스턴스
_ignition_strategy: Optional[IgnitionStrategy] = None


def get_ignition_strategy() -> IgnitionStrategy:
    """Ignition Strategy 싱글톤"""
    global _ignition_strategy
    if _ignition_strategy is None:
        _ignition_strategy = IgnitionStrategy()
    return _ignition_strategy
