"""Core Strategy Safety Guards

Core 전략 안전장치:
- 허용 심볼 제한 (BTC/ETH 초유동성만)
- 펀딩 역전 감지 (Binance Futures 전용)
- Edge 축소 감지
- 단계형 청산 트리거

v2 업데이트 (Upbit 호환):
- Upbit 모드에서 펀딩비 체크 비활성화 (현물 전용)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.risk.config import get_risk_config

logger = structlog.get_logger()
settings = get_settings()


class CoreSafetyAction(str, Enum):
    """Core 안전장치 액션"""

    NORMAL = "NORMAL"  # 정상 운영
    WARN = "WARN"  # 경고 (신규 진입 주의)
    REDUCE = "REDUCE"  # 노출 축소
    EXIT = "EXIT"  # 전량 청산


@dataclass
class FundingState:
    """펀딩 상태"""

    symbol: str
    current_rate: float = 0.0
    negative_count: int = 0  # 연속 음수 횟수
    is_reversed: bool = False
    should_exit: bool = False
    last_update: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "current_rate": self.current_rate,
            "negative_count": self.negative_count,
            "is_reversed": self.is_reversed,
            "should_exit": self.should_exit,
            "last_update": self.last_update.isoformat(),
        }


@dataclass
class EdgeState:
    """Edge 상태"""

    symbol: str
    current_edge_bps: float = 0.0
    is_sufficient: bool = True
    trend: str = "STABLE"  # IMPROVING, STABLE, DETERIORATING
    last_update: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "current_edge_bps": self.current_edge_bps,
            "is_sufficient": self.is_sufficient,
            "trend": self.trend,
            "last_update": self.last_update.isoformat(),
        }


@dataclass
class CoreSafetyState:
    """Core 안전 상태 종합"""

    action: CoreSafetyAction
    can_enter: bool
    should_reduce: bool
    should_exit: bool
    funding_state: Optional[FundingState] = None
    edge_state: Optional[EdgeState] = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "action": self.action.value,
            "can_enter": self.can_enter,
            "should_reduce": self.should_reduce,
            "should_exit": self.should_exit,
            "funding": self.funding_state.to_dict() if self.funding_state else None,
            "edge": self.edge_state.to_dict() if self.edge_state else None,
            "reasons": self.reasons,
        }


class CoreSafetyGuard:
    """
    Core 전략 안전장치

    책임:
    1. 허용 심볼 검증 (BTC/ETH만)
    2. 펀딩 역전 감지 및 청산 트리거 (Binance Futures 전용)
    3. Edge 축소 감지 및 진입 차단
    4. 종합 안전 판단

    v2 업데이트:
    - Upbit 모드에서 펀딩비 관련 체크 비활성화
    """

    def __init__(self):
        self.config = get_risk_config()

        # Upbit 모드 여부 (펀딩비 체크 비활성화)
        self._is_upbit = settings.is_upbit

        # 펀딩 상태 추적 (symbol -> FundingState) - Binance 전용
        self._funding_states: dict[str, FundingState] = {}

        # Edge 상태 추적 (symbol -> EdgeState)
        self._edge_states: dict[str, EdgeState] = {}

        # Edge 이력 (symbol -> list of edge_bps)
        self._edge_history: dict[str, list[float]] = {}

    def is_allowed_symbol(self, symbol: str) -> bool:
        """허용된 심볼인지 확인 (ALL이면 유동성 통과 심볼 모두 허용)"""
        if self.config.core_allow_all:
            return True  # 유동성 필터는 SymbolManager에서 이미 적용됨
        return symbol in self.config.core_symbols_list

    def get_allowed_symbols(self) -> list[str]:
        """허용 심볼 목록 반환 (ALL이면 빈 리스트)"""
        return self.config.core_symbols_list

    def update_funding_rate(self, symbol: str, funding_rate: float) -> FundingState:
        """
        펀딩 레이트 업데이트 (Binance Futures 전용)

        Args:
            symbol: 심볼
            funding_rate: 현재 펀딩 레이트

        Returns:
            FundingState: 업데이트된 펀딩 상태
        """
        # Upbit 모드에서는 펀딩비 없음
        if self._is_upbit:
            return FundingState(symbol=symbol)

        if symbol not in self._funding_states:
            self._funding_states[symbol] = FundingState(symbol=symbol)

        state = self._funding_states[symbol]
        prev_rate = state.current_rate
        state.current_rate = funding_rate
        state.last_update = datetime.utcnow()

        # 음수 펀딩 체크
        min_rate = self.config.core_min_funding_rate
        exit_rate = self.config.core_negative_funding_exit

        if funding_rate < min_rate:
            # 연속 음수 카운트
            if prev_rate < min_rate:
                state.negative_count += 1
            else:
                state.negative_count = 1

            state.is_reversed = True

            # 3연속 이상 음수 또는 임계값 초과 시 청산
            if state.negative_count >= 3 or funding_rate < exit_rate:
                state.should_exit = True
                logger.warning(
                    "Core safety: Funding exit triggered",
                    symbol=symbol,
                    funding_rate=funding_rate,
                    negative_count=state.negative_count,
                )
        else:
            state.negative_count = 0
            state.is_reversed = False
            state.should_exit = False

        return state

    def update_edge(self, symbol: str, edge_bps: float) -> EdgeState:
        """
        Edge 업데이트

        Args:
            symbol: 심볼
            edge_bps: 현재 Edge (basis points)

        Returns:
            EdgeState: 업데이트된 Edge 상태
        """
        if symbol not in self._edge_states:
            self._edge_states[symbol] = EdgeState(symbol=symbol)

        state = self._edge_states[symbol]
        state.current_edge_bps = edge_bps
        state.last_update = datetime.utcnow()

        # Edge 이력 기록
        if symbol not in self._edge_history:
            self._edge_history[symbol] = []
        self._edge_history[symbol].append(edge_bps)

        # 최근 20개만 유지
        if len(self._edge_history[symbol]) > 20:
            self._edge_history[symbol] = self._edge_history[symbol][-20:]

        # 충분성 체크
        min_edge = self.config.core_min_edge_bps
        state.is_sufficient = edge_bps >= min_edge

        # 추세 판단
        state.trend = self._calculate_edge_trend(symbol)

        if not state.is_sufficient:
            logger.warning(
                "Core safety: Edge insufficient",
                symbol=symbol,
                edge_bps=edge_bps,
                min_required=min_edge,
            )

        return state

    def _calculate_edge_trend(self, symbol: str) -> str:
        """Edge 추세 계산"""
        history = self._edge_history.get(symbol, [])
        if len(history) < 5:
            return "STABLE"

        recent = history[-5:]
        older = history[-10:-5] if len(history) >= 10 else history[:len(history) - 5]

        if not older:
            return "STABLE"

        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)

        diff = recent_avg - older_avg
        if diff > 1.0:  # 1bp 이상 개선
            return "IMPROVING"
        elif diff < -1.0:  # 1bp 이상 악화
            return "DETERIORATING"
        return "STABLE"

    def check_funding_safety(self, symbol: str) -> tuple[bool, str]:
        """
        펀딩 안전성 체크 (Binance Futures 전용)

        Returns:
            tuple[bool, str]: (안전 여부, 사유)
        """
        if not self.is_allowed_symbol(symbol):
            return (False, f"Symbol {symbol} not allowed for Core strategy")

        # Upbit 모드에서는 펀딩비 체크 스킵
        if self._is_upbit:
            return (True, "Upbit mode - no funding rate")

        state = self._funding_states.get(symbol)
        if not state:
            return (True, "No funding data yet")

        if state.should_exit:
            return (False, f"Funding exit triggered: rate={state.current_rate}, consecutive={state.negative_count}")

        if state.is_reversed:
            return (False, f"Funding reversed: rate={state.current_rate}")

        return (True, "OK")

    def check_edge_sufficient(self, symbol: str) -> tuple[bool, str]:
        """
        Edge 충분성 체크

        Returns:
            tuple[bool, str]: (충분 여부, 사유)
        """
        state = self._edge_states.get(symbol)
        if not state:
            return (True, "No edge data yet")

        if not state.is_sufficient:
            return (False, f"Edge insufficient: {state.current_edge_bps}bps < {self.config.core_min_edge_bps}bps")

        if state.trend == "DETERIORATING":
            return (True, f"Warning: Edge deteriorating ({state.current_edge_bps}bps)")

        return (True, "OK")

    def can_open_core(self, symbol: str) -> tuple[bool, str]:
        """
        Core 포지션 오픈 가능 여부

        Returns:
            tuple[bool, str]: (가능 여부, 사유)
        """
        # 1. 허용 심볼 체크
        if not self.is_allowed_symbol(symbol):
            return (False, f"Symbol {symbol} not in allowed list")

        # 2. 펀딩 체크
        funding_ok, funding_reason = self.check_funding_safety(symbol)
        if not funding_ok:
            return (False, funding_reason)

        # 3. Edge 체크
        edge_ok, edge_reason = self.check_edge_sufficient(symbol)
        if not edge_ok:
            return (False, edge_reason)

        return (True, "OK")

    def should_unwind(self, symbol: str) -> tuple[bool, str]:
        """
        포지션 청산 필요 여부

        Returns:
            tuple[bool, str]: (청산 필요, 사유)
        """
        # Upbit 모드에서는 펀딩비 기반 청산 없음
        if self._is_upbit:
            return (False, "Upbit mode - no funding-based unwind")

        state = self._funding_states.get(symbol)
        if state and state.should_exit:
            return (True, f"Funding exit: rate={state.current_rate}, consecutive={state.negative_count}")

        return (False, "OK")

    def get_safety_state(self, symbol: str) -> CoreSafetyState:
        """
        심볼별 안전 상태 종합

        Returns:
            CoreSafetyState: 종합 안전 상태
        """
        reasons = []

        # 펀딩 상태 (Upbit 모드에서는 무시)
        funding_state = None
        if not self._is_upbit:
            funding_state = self._funding_states.get(symbol)
            if funding_state and funding_state.should_exit:
                reasons.append("Funding exit triggered")

            if funding_state and funding_state.is_reversed:
                reasons.append(f"Funding reversed: {funding_state.current_rate}")

        # Edge 상태
        edge_state = self._edge_states.get(symbol)
        if edge_state and not edge_state.is_sufficient:
            reasons.append(f"Edge insufficient: {edge_state.current_edge_bps}bps")

        if edge_state and edge_state.trend == "DETERIORATING":
            reasons.append("Edge deteriorating")

        # 액션 결정
        should_exit = (funding_state.should_exit if funding_state else False) if not self._is_upbit else False
        should_reduce = (
            (funding_state and funding_state.is_reversed if not self._is_upbit else False)
            or (edge_state and edge_state.trend == "DETERIORATING")
        )
        can_enter = not should_exit and not should_reduce and self.is_allowed_symbol(symbol)

        if should_exit:
            action = CoreSafetyAction.EXIT
        elif should_reduce:
            action = CoreSafetyAction.REDUCE
        elif reasons:
            action = CoreSafetyAction.WARN
        else:
            action = CoreSafetyAction.NORMAL

        return CoreSafetyState(
            action=action,
            can_enter=can_enter,
            should_reduce=should_reduce,
            should_exit=should_exit,
            funding_state=funding_state,
            edge_state=edge_state,
            reasons=reasons,
        )

    def get_all_states(self) -> dict[str, CoreSafetyState]:
        """모든 심볼의 안전 상태 반환"""
        symbols = set(self._funding_states.keys()) | set(self._edge_states.keys())
        return {symbol: self.get_safety_state(symbol) for symbol in symbols}


# Singleton
_core_safety: CoreSafetyGuard = None


def get_core_safety_guard() -> CoreSafetyGuard:
    """CoreSafetyGuard 싱글톤"""
    global _core_safety
    if _core_safety is None:
        _core_safety = CoreSafetyGuard()
    return _core_safety
