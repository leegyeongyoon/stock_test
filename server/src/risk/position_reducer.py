"""Position Reducer

위험 시장에서 포지션 강제 축소:
- RISK_OFF: 40% 축소
- Correlation Shock: 30~60% 축소 (심각도에 따라)
- "Reduce & Stay" 정책: 축소 후 일정 시간 유지
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import structlog

from src.risk.config import get_risk_config

logger = structlog.get_logger()


class ReduceTrigger(str, Enum):
    """축소 트리거"""

    RISK_OFF = "RISK_OFF"  # BTC RISK_OFF 시장
    CORR_SHOCK = "CORR_SHOCK"  # 상관 충격
    DD_SOFT = "DD_SOFT"  # Soft DD (3.5%)
    DD_HARD = "DD_HARD"  # Hard DD (5%) - 분할 청산 1단계
    DD_HARD_PHASE2 = "DD_HARD_PHASE2"  # Hard DD 2단계 (헤지)
    DD_HARD_PHASE3 = "DD_HARD_PHASE3"  # Hard DD 3단계 (조건부 전량 청산)
    MANUAL = "MANUAL"  # 수동


@dataclass
class ReduceOrder:
    """축소 주문"""

    symbol: str
    reduce_qty: float
    reduce_pct: float
    trigger: ReduceTrigger
    created_at: datetime = field(default_factory=datetime.utcnow)
    executed: bool = False
    executed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "reduce_qty": self.reduce_qty,
            "reduce_pct": round(self.reduce_pct * 100, 1),
            "trigger": self.trigger.value,
            "created_at": self.created_at.isoformat(),
            "executed": self.executed,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }


@dataclass
class ReduceState:
    """축소 상태"""

    is_reducing: bool = False
    trigger: Optional[ReduceTrigger] = None
    reduce_pct: float = 0.0
    started_at: Optional[datetime] = None
    stay_until: Optional[datetime] = None
    pending_orders: list[ReduceOrder] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_reducing": self.is_reducing,
            "trigger": self.trigger.value if self.trigger else None,
            "reduce_pct": round(self.reduce_pct * 100, 1),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stay_until": self.stay_until.isoformat() if self.stay_until else None,
            "pending_orders": [o.to_dict() for o in self.pending_orders],
        }


class PositionReducer:
    """
    포지션 축소 관리자

    책임:
    1. 위험 시장 감지 시 축소 명령 생성
    2. Reduce & Stay 정책 적용
    3. 축소 실행 추적
    """

    def __init__(self):
        self.config = get_risk_config()

        # 현재 축소 상태
        self._state = ReduceState()

        # 축소 이력
        self._history: list[ReduceOrder] = []

    def trigger_reduce(
        self,
        trigger: ReduceTrigger,
        positions: dict[str, float],  # symbol -> quantity
        severity: float = 1.0,  # 심각도 (0~1)
    ) -> list[ReduceOrder]:
        """
        축소 트리거

        Args:
            trigger: 축소 트리거
            positions: 현재 포지션 (symbol -> quantity)
            severity: 심각도 (corr_shock 시 사용)

        Returns:
            축소 주문 목록
        """
        # 축소 비율 결정
        reduce_pct = self._get_reduce_pct(trigger, severity)

        if reduce_pct <= 0:
            return []

        now = datetime.utcnow()

        # 이미 축소 중이고 더 심각한 트리거가 아니면 무시
        if self._state.is_reducing:
            if self._state.reduce_pct >= reduce_pct:
                logger.info(
                    "Position reducer: Already reducing at higher rate",
                    current=f"{self._state.reduce_pct*100:.1f}%",
                    requested=f"{reduce_pct*100:.1f}%",
                )
                return []

        # 축소 주문 생성
        orders = []
        for symbol, qty in positions.items():
            if qty <= 0:
                continue

            reduce_qty = qty * reduce_pct
            order = ReduceOrder(
                symbol=symbol,
                reduce_qty=reduce_qty,
                reduce_pct=reduce_pct,
                trigger=trigger,
            )
            orders.append(order)

        # 상태 업데이트
        stay_duration = timedelta(minutes=self.config.reduce_stay_duration_minutes)

        self._state = ReduceState(
            is_reducing=True,
            trigger=trigger,
            reduce_pct=reduce_pct,
            started_at=now,
            stay_until=now + stay_duration,
            pending_orders=orders,
        )

        logger.warning(
            "Position reducer: Triggered",
            trigger=trigger.value,
            reduce_pct=f"{reduce_pct*100:.1f}%",
            positions=len(positions),
            orders=len(orders),
            stay_until=self._state.stay_until.isoformat(),
        )

        return orders

    def _get_reduce_pct(self, trigger: ReduceTrigger, severity: float) -> float:
        """트리거별 축소 비율 (v3.1: Hard DD 분할 청산)"""
        if trigger == ReduceTrigger.RISK_OFF:
            return self.config.reduce_rate_risk_off

        if trigger == ReduceTrigger.CORR_SHOCK:
            # severity에 따라 30~60%
            min_rate = self.config.reduce_rate_corr_shock_min
            max_rate = self.config.reduce_rate_corr_shock_max
            return min_rate + (max_rate - min_rate) * severity

        if trigger == ReduceTrigger.DD_SOFT:
            return 0.50  # Satellite OFF + 50% 축소

        if trigger == ReduceTrigger.DD_HARD:
            # v3.1: 분할 청산 1단계 (손실 포지션 50% 축소)
            if self.config.hard_dd_gradual_close_enabled:
                return self.config.hard_dd_phase1_reduce_pct  # 50%
            return 1.0  # 분할 비활성화 시 전량 청산

        if trigger == ReduceTrigger.DD_HARD_PHASE2:
            # v3.1: 2단계 - 추가 축소 (헤지 후)
            return 0.30  # 남은 포지션 30% 추가 축소

        if trigger == ReduceTrigger.DD_HARD_PHASE3:
            # v3.1: 3단계 - 조건부 전량 청산
            return 1.0  # 전량 청산

        return 0.0

    def mark_executed(self, symbol: str) -> None:
        """축소 주문 실행 완료 표시"""
        for order in self._state.pending_orders:
            if order.symbol == symbol and not order.executed:
                order.executed = True
                order.executed_at = datetime.utcnow()
                self._history.append(order)
                break

        # 모든 주문 실행 완료 체크
        all_executed = all(o.executed for o in self._state.pending_orders)
        if all_executed and self._state.pending_orders:
            logger.info(
                "Position reducer: All orders executed",
                count=len(self._state.pending_orders),
            )

    def check_stay_period(self) -> bool:
        """
        Stay 기간 체크

        Returns:
            True if still in stay period
        """
        if not self._state.is_reducing:
            return False

        if self._state.stay_until is None:
            return False

        now = datetime.utcnow()
        if now >= self._state.stay_until:
            logger.info(
                "Position reducer: Stay period ended",
                trigger=self._state.trigger.value if self._state.trigger else "NONE",
            )
            self._state.is_reducing = False
            return False

        return True

    def can_expand_position(self) -> tuple[bool, str]:
        """
        포지션 확대 가능 여부 (Stay 기간 중 불가)

        Returns:
            tuple[bool, str]: (가능 여부, 사유)
        """
        if self.check_stay_period():
            remaining = self._state.stay_until - datetime.utcnow()
            return (
                False,
                f"In reduce & stay period ({remaining.seconds // 60}m remaining)",
            )
        return (True, "OK")

    def get_state(self) -> ReduceState:
        """현재 축소 상태"""
        # Stay 기간 체크로 상태 업데이트
        self.check_stay_period()
        return self._state

    def get_pending_orders(self) -> list[ReduceOrder]:
        """미실행 축소 주문"""
        return [o for o in self._state.pending_orders if not o.executed]

    def gradual_hard_dd_close(
        self,
        positions: dict[str, float],
        position_pnls: dict[str, float],  # symbol -> unrealized PnL %
        phase: int = 1,
    ) -> list[ReduceOrder]:
        """
        v3.1: Hard DD 분할 청산

        Phase 1: 손실 포지션 50% 축소 (손실 큰 순)
        Phase 2: 헤지 추가 후 남은 포지션 30% 축소
        Phase 3: 조건 충족 시 전량 청산

        Args:
            positions: 현재 포지션 (symbol -> quantity)
            position_pnls: 포지션별 미실현 손익률
            phase: 청산 단계 (1, 2, 3)

        Returns:
            축소 주문 목록
        """
        if not self.config.hard_dd_gradual_close_enabled:
            return self.force_close_all(positions, "DD_HARD")

        orders = []
        now = datetime.utcnow()

        if phase == 1:
            # Phase 1: 손실 포지션 50% 축소 (손실 큰 순)
            losing_positions = [
                (sym, qty, position_pnls.get(sym, 0))
                for sym, qty in positions.items()
                if position_pnls.get(sym, 0) < 0 and qty > 0
            ]
            # 손실 큰 순 정렬
            losing_positions.sort(key=lambda x: x[2])

            reduce_pct = self.config.hard_dd_phase1_reduce_pct
            for symbol, qty, pnl in losing_positions:
                reduce_qty = qty * reduce_pct
                order = ReduceOrder(
                    symbol=symbol,
                    reduce_qty=reduce_qty,
                    reduce_pct=reduce_pct,
                    trigger=ReduceTrigger.DD_HARD,
                )
                orders.append(order)

            logger.warning(
                "Position reducer: Hard DD Phase 1 (reduce losing positions)",
                losing_count=len(losing_positions),
                reduce_pct=f"{reduce_pct*100:.0f}%",
            )

        elif phase == 2:
            # Phase 2: 남은 포지션 30% 추가 축소
            reduce_pct = 0.30
            for symbol, qty in positions.items():
                if qty <= 0:
                    continue
                reduce_qty = qty * reduce_pct
                order = ReduceOrder(
                    symbol=symbol,
                    reduce_qty=reduce_qty,
                    reduce_pct=reduce_pct,
                    trigger=ReduceTrigger.DD_HARD_PHASE2,
                )
                orders.append(order)

            logger.warning(
                "Position reducer: Hard DD Phase 2 (additional reduce)",
                positions=len(positions),
                reduce_pct=f"{reduce_pct*100:.0f}%",
            )

        elif phase == 3:
            # Phase 3: 전량 청산
            return self.force_close_all(positions, "DD_HARD_PHASE3")

        # 상태 업데이트
        trigger = (
            ReduceTrigger.DD_HARD
            if phase == 1
            else ReduceTrigger.DD_HARD_PHASE2
            if phase == 2
            else ReduceTrigger.DD_HARD_PHASE3
        )

        stay_duration = timedelta(minutes=30)  # 분할 청산 시 30분 유지

        self._state = ReduceState(
            is_reducing=True,
            trigger=trigger,
            reduce_pct=self._get_reduce_pct(trigger, 1.0),
            started_at=now,
            stay_until=now + stay_duration,
            pending_orders=orders,
        )

        return orders

    def force_close_all(
        self, positions: dict[str, float], reason: str = "DD_HARD"
    ) -> list[ReduceOrder]:
        """
        전량 강제 청산

        Args:
            positions: 현재 포지션 (symbol -> quantity)
            reason: 청산 사유

        Returns:
            청산 주문 목록
        """
        trigger_map = {
            "DD_HARD": ReduceTrigger.DD_HARD,
            "DD_HARD_PHASE3": ReduceTrigger.DD_HARD_PHASE3,
        }
        trigger = trigger_map.get(reason, ReduceTrigger.MANUAL)

        orders = []
        for symbol, qty in positions.items():
            if qty <= 0:
                continue

            order = ReduceOrder(
                symbol=symbol,
                reduce_qty=qty,  # 전량
                reduce_pct=1.0,
                trigger=trigger,
            )
            orders.append(order)

        # 상태 업데이트
        self._state = ReduceState(
            is_reducing=True,
            trigger=trigger,
            reduce_pct=1.0,
            started_at=datetime.utcnow(),
            stay_until=None,  # 전량 청산은 stay 없음
            pending_orders=orders,
        )

        logger.critical(
            "Position reducer: FORCE CLOSE ALL",
            reason=reason,
            positions=len(positions),
        )

        return orders

    def reset(self) -> None:
        """상태 초기화"""
        self._state = ReduceState()
        logger.info("Position reducer: Reset")


# Singleton
_reducer: PositionReducer = None


def get_position_reducer() -> PositionReducer:
    """PositionReducer 싱글톤"""
    global _reducer
    if _reducer is None:
        _reducer = PositionReducer()
    return _reducer
