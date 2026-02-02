"""
Ignition Position Policy - Profit-add Only 사이징

역할:
- 진입 포지션 사이징 계산
- Profit-add only 정책 (이익일 때만 증액)
- NORMAL 모드: 0.4% 리스크, 2단계 증액
- ATTACK 모드: 1.0% 리스크, 3단계 증액
- 증액 트리거: +0.5R, +1R
- 증액 비율: 초기 50%, 추가1 30%, 추가2 20%
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.strategies.ignition.ignition_engine import IgnitionSignal

logger = structlog.get_logger()


class PositionPhase(str, Enum):
    """포지션 단계"""

    INITIAL = "INITIAL"  # 초기 진입
    ADD_1 = "ADD_1"  # 1차 증액
    ADD_2 = "ADD_2"  # 2차 증액
    ADD_3 = "ADD_3"  # 3차 증액 (ATTACK 모드만)
    FULL = "FULL"  # 완전 진입


@dataclass
class IgnitionPositionSizing:
    """포지션 사이징 결과"""

    symbol: str
    mode: str  # NORMAL or ATTACK
    phase: PositionPhase
    risk_pct: float  # 계좌 리스크 %
    position_pct: float  # 이번 진입 비율
    position_amount: float  # 진입 금액 (KRW)
    quantity: float  # 진입 수량
    entry_price: float
    stop_loss: float
    risk_amount: float  # 리스크 금액 (KRW)
    max_add_steps: int  # 최대 증액 횟수
    current_add_count: int  # 현재 증액 횟수
    next_add_trigger_r: Optional[float]  # 다음 증액 트리거 (R 배수)
    calculated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "mode": self.mode,
            "phase": self.phase.value,
            "risk_pct": round(self.risk_pct * 100, 2),
            "position_pct": round(self.position_pct * 100, 1),
            "position_amount": round(self.position_amount, 0),
            "quantity": round(self.quantity, 8),
            "entry_price": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "risk_amount": round(self.risk_amount, 0),
            "max_add_steps": self.max_add_steps,
            "current_add_count": self.current_add_count,
            "next_add_trigger_r": self.next_add_trigger_r,
            "calculated_at": self.calculated_at.isoformat(),
        }


class IgnitionPositionPolicy:
    """
    Ignition Position Policy

    역할:
    - 리스크 기반 포지션 사이징
    - Profit-add only 정책 적용
    - 단계별 증액 관리
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._add_triggers: list[float] = []
        self._add_pcts: list[float] = []
        self._parse_add_config()

    def _parse_add_config(self) -> None:
        """증액 설정 파싱"""
        settings = self._settings

        # 증액 트리거 파싱 (예: "0.5,1.0")
        trigger_str = settings.ignition_add_trigger_r
        self._add_triggers = [float(x.strip()) for x in trigger_str.split(",")]

        # 증액 비율 파싱 (예: "0.30,0.20")
        pct_str = settings.ignition_add_position_pct
        self._add_pcts = [float(x.strip()) for x in pct_str.split(",")]

    def calculate_initial_sizing(
        self,
        signal: IgnitionSignal,
        account_balance: float,
        mode: str = "NORMAL",
    ) -> IgnitionPositionSizing:
        """
        초기 진입 사이징 계산

        Args:
            signal: 점화 신호
            account_balance: 계좌 잔고 (KRW)
            mode: 운영 모드 (NORMAL/ATTACK)

        Returns:
            IgnitionPositionSizing
        """
        settings = self._settings

        # 모드별 리스크 설정
        if mode == "ATTACK":
            risk_pct = settings.ignition_risk_attack
            max_add_steps = settings.ignition_max_add_steps_attack
        else:
            risk_pct = settings.ignition_risk_normal
            max_add_steps = settings.ignition_max_add_steps_normal

        # 초기 포지션 비율
        initial_pct = settings.ignition_initial_position_pct

        # 리스크 금액 = 계좌 * 리스크% * 초기비율
        risk_amount = account_balance * risk_pct * initial_pct

        # 주당 리스크
        risk_per_share = signal.risk_per_share
        if risk_per_share <= 0:
            risk_per_share = signal.atr_1h  # fallback

        # 수량 = 리스크금액 / 주당리스크
        quantity = risk_amount / risk_per_share if risk_per_share > 0 else 0

        # 포지션 금액
        position_amount = quantity * signal.entry_price

        # 다음 증액 트리거
        next_trigger = self._add_triggers[0] if self._add_triggers else None

        sizing = IgnitionPositionSizing(
            symbol=signal.symbol,
            mode=mode,
            phase=PositionPhase.INITIAL,
            risk_pct=risk_pct,
            position_pct=initial_pct,
            position_amount=position_amount,
            quantity=quantity,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            risk_amount=risk_amount,
            max_add_steps=max_add_steps,
            current_add_count=0,
            next_add_trigger_r=next_trigger,
        )

        logger.info(
            "Initial sizing calculated",
            symbol=signal.symbol,
            mode=mode,
            quantity=quantity,
            position_amount=position_amount,
            risk_amount=risk_amount,
        )

        return sizing

    def calculate_add_sizing(
        self,
        signal: IgnitionSignal,
        current_sizing: IgnitionPositionSizing,
        current_price: float,
        account_balance: float,
    ) -> Optional[IgnitionPositionSizing]:
        """
        증액 사이징 계산 (Profit-add only)

        Args:
            signal: 점화 신호
            current_sizing: 현재 사이징
            current_price: 현재가
            account_balance: 계좌 잔고

        Returns:
            새 사이징 (증액 조건 미충족시 None)
        """
        settings = self._settings

        # 최대 증액 횟수 체크
        if current_sizing.current_add_count >= current_sizing.max_add_steps:
            return None

        # Profit-add 조건 체크: 현재 수익 R 계산
        entry_price = current_sizing.entry_price
        risk_per_share = abs(entry_price - current_sizing.stop_loss)
        if risk_per_share <= 0:
            return None

        current_r = (current_price - entry_price) / risk_per_share

        # 다음 트리거 확인
        add_index = current_sizing.current_add_count
        if add_index >= len(self._add_triggers):
            return None

        trigger_r = self._add_triggers[add_index]

        # 트리거 미달
        if current_r < trigger_r:
            return None

        # 증액 비율 확인
        if add_index >= len(self._add_pcts):
            return None

        add_pct = self._add_pcts[add_index]

        # 리스크 금액 계산
        risk_pct = current_sizing.risk_pct
        risk_amount = account_balance * risk_pct * add_pct

        # 수량 계산 (현재가 기준)
        new_risk_per_share = abs(current_price - current_sizing.stop_loss)
        if new_risk_per_share <= 0:
            new_risk_per_share = signal.atr_1h

        quantity = risk_amount / new_risk_per_share if new_risk_per_share > 0 else 0
        position_amount = quantity * current_price

        # 새 단계 결정
        new_add_count = current_sizing.current_add_count + 1
        if new_add_count == 1:
            new_phase = PositionPhase.ADD_1
        elif new_add_count == 2:
            new_phase = PositionPhase.ADD_2
        elif new_add_count == 3:
            new_phase = PositionPhase.ADD_3
        else:
            new_phase = PositionPhase.FULL

        # 다음 트리거
        if new_add_count < len(self._add_triggers):
            next_trigger = self._add_triggers[new_add_count]
        else:
            next_trigger = None

        sizing = IgnitionPositionSizing(
            symbol=signal.symbol,
            mode=current_sizing.mode,
            phase=new_phase,
            risk_pct=risk_pct,
            position_pct=add_pct,
            position_amount=position_amount,
            quantity=quantity,
            entry_price=current_price,
            stop_loss=current_sizing.stop_loss,  # 손절가 유지
            risk_amount=risk_amount,
            max_add_steps=current_sizing.max_add_steps,
            current_add_count=new_add_count,
            next_add_trigger_r=next_trigger,
        )

        logger.info(
            "Add sizing calculated",
            symbol=signal.symbol,
            phase=new_phase.value,
            trigger_r=trigger_r,
            current_r=current_r,
            add_quantity=quantity,
            add_amount=position_amount,
        )

        return sizing

    def can_add_position(
        self,
        current_sizing: IgnitionPositionSizing,
        current_price: float,
    ) -> tuple[bool, Optional[float]]:
        """
        증액 가능 여부 확인

        Args:
            current_sizing: 현재 사이징
            current_price: 현재가

        Returns:
            (가능 여부, 다음 트리거 R)
        """
        # 최대 증액 횟수 체크
        if current_sizing.current_add_count >= current_sizing.max_add_steps:
            return False, None

        # 현재 R 계산
        entry_price = current_sizing.entry_price
        risk_per_share = abs(entry_price - current_sizing.stop_loss)
        if risk_per_share <= 0:
            return False, None

        current_r = (current_price - entry_price) / risk_per_share

        # 다음 트리거 확인
        add_index = current_sizing.current_add_count
        if add_index >= len(self._add_triggers):
            return False, None

        trigger_r = self._add_triggers[add_index]

        return current_r >= trigger_r, trigger_r

    def get_total_position(
        self,
        sizing_history: list[IgnitionPositionSizing],
    ) -> dict:
        """
        누적 포지션 정보 계산

        Args:
            sizing_history: 사이징 이력

        Returns:
            누적 포지션 정보
        """
        if not sizing_history:
            return {
                "total_quantity": 0,
                "total_amount": 0,
                "total_risk": 0,
                "avg_entry_price": 0,
                "add_count": 0,
            }

        total_qty = sum(s.quantity for s in sizing_history)
        total_amount = sum(s.position_amount for s in sizing_history)
        total_risk = sum(s.risk_amount for s in sizing_history)

        # 가중 평균 진입가
        if total_qty > 0:
            avg_entry = sum(s.quantity * s.entry_price for s in sizing_history) / total_qty
        else:
            avg_entry = 0

        return {
            "total_quantity": round(total_qty, 8),
            "total_amount": round(total_amount, 0),
            "total_risk": round(total_risk, 0),
            "avg_entry_price": round(avg_entry, 2),
            "add_count": len(sizing_history) - 1,  # 초기 제외
        }

    def get_status(self) -> dict:
        """현재 설정 상태"""
        settings = self._settings

        return {
            "normal_risk_pct": settings.ignition_risk_normal * 100,
            "attack_risk_pct": settings.ignition_risk_attack * 100,
            "initial_position_pct": settings.ignition_initial_position_pct * 100,
            "add_triggers_r": self._add_triggers,
            "add_pcts": [p * 100 for p in self._add_pcts],
            "max_add_normal": settings.ignition_max_add_steps_normal,
            "max_add_attack": settings.ignition_max_add_steps_attack,
        }


# 싱글톤 인스턴스
_ignition_position_policy: Optional[IgnitionPositionPolicy] = None


def get_ignition_position_policy() -> IgnitionPositionPolicy:
    """Ignition Position Policy 싱글톤"""
    global _ignition_position_policy
    if _ignition_position_policy is None:
        _ignition_position_policy = IgnitionPositionPolicy()
    return _ignition_position_policy
