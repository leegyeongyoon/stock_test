"""전략 베이스 클래스"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from src.models.schemas import OrderSide, StrategyType


class SignalType(str, Enum):
    """시그널 타입"""

    ENTRY = "ENTRY"  # 진입
    EXIT = "EXIT"  # 청산
    REBALANCE = "REBALANCE"  # 리밸런싱
    NONE = "NONE"  # 없음


@dataclass
class Signal:
    """트레이딩 시그널"""

    strategy: StrategyType
    signal_type: SignalType
    symbol: str
    side: OrderSide
    quantity: float
    price: Optional[float] = None
    reason: str = ""
    confidence: float = 1.0
    timestamp: datetime = None
    metadata: Optional[dict] = None  # 추가 메타데이터 (트랜치 정보 등)

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


class BaseStrategy(ABC):
    """전략 베이스 클래스"""

    def __init__(self, strategy_type: StrategyType) -> None:
        self.strategy_type = strategy_type
        self._enabled = True
        self._last_signal: Optional[Signal] = None

    @property
    def is_enabled(self) -> bool:
        """전략 활성화 여부"""
        return self._enabled

    def enable(self) -> None:
        """전략 활성화"""
        self._enabled = True

    def disable(self) -> None:
        """전략 비활성화"""
        self._enabled = False

    @abstractmethod
    async def generate_signal(self, market_data: dict) -> Optional[Signal]:
        """시그널 생성"""
        pass

    @abstractmethod
    async def should_exit(self, position: dict, market_data: dict) -> Optional[Signal]:
        """청산 조건 확인"""
        pass

    @abstractmethod
    def get_position_size(self, signal: Signal, available_capital: float) -> float:
        """포지션 사이즈 계산"""
        pass
