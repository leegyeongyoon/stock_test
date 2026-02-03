"""
Base Engine - 2-Engine Architecture 공통 베이스

모든 엔진이 구현해야 하는 인터페이스 정의
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger()


class EngineType(str, Enum):
    """엔진 타입"""

    IGNITION = "IGNITION"  # 초반 1파
    RETEST = "RETEST"  # 리테스트 2파


@dataclass
class EngineSignal:
    """엔진 신호 베이스"""

    symbol: str
    engine_type: EngineType
    current_price: float
    signal_time: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(
        default_factory=lambda: datetime.utcnow() + timedelta(seconds=60)
    )

    # 진입/청산 레벨
    entry_price: float = 0
    stop_loss: float = 0
    take_profit_1: float = 0
    take_profit_2: float = 0

    # 지표
    breakout_level: float = 0
    vwap_5m: float = 0
    atr_5m: float = 0

    # Anti-Chase 거리
    dist_breakout: float = 0
    dist_vwap: float = 0
    dist_atr: float = 0

    # 메타
    confidence: float = 0  # 0-1
    reason: str = ""

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "engine_type": self.engine_type.value,
            "current_price": round(self.current_price, 2),
            "entry_price": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "take_profit_1": round(self.take_profit_1, 2),
            "take_profit_2": round(self.take_profit_2, 2),
            "breakout_level": round(self.breakout_level, 2),
            "vwap_5m": round(self.vwap_5m, 2),
            "atr_5m": round(self.atr_5m, 4),
            "dist_breakout": round(self.dist_breakout, 4),
            "dist_vwap": round(self.dist_vwap, 4),
            "dist_atr": round(self.dist_atr, 2),
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "signal_time": self.signal_time.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


class BaseEngine(ABC):
    """
    엔진 베이스 클래스

    모든 엔진은 이 인터페이스를 구현해야 함
    """

    @abstractmethod
    def get_engine_type(self) -> EngineType:
        """엔진 타입 반환"""
        pass

    @abstractmethod
    def detect_signal(
        self,
        symbol: str,
        market_data: dict,
        candles_1m: list,
    ) -> Optional[EngineSignal]:
        """
        신호 감지

        Args:
            symbol: 심볼
            market_data: 시장 데이터
            candles_1m: 1분봉 캔들 리스트

        Returns:
            신호 또는 None
        """
        pass

    @abstractmethod
    def can_enter(
        self,
        dd_tier: int,
        daily_loss_pct: float,
        is_aggressive_mode: bool,
    ) -> tuple[bool, str]:
        """
        진입 가능 여부 (DD/일손실 체크)

        Args:
            dd_tier: 현재 DD Tier (1-6)
            daily_loss_pct: 일손실률 (음수)
            is_aggressive_mode: AGGRESSIVE 모드 여부

        Returns:
            (허용 여부, 사유)
        """
        pass

    @abstractmethod
    def calculate_stop_loss(
        self,
        entry_price: float,
        breakout_level: float,
        vwap_5m: float,
        atr_5m: float,
    ) -> float:
        """
        손절가 계산

        엔진마다 다른 손절 로직 사용 가능
        """
        pass

    def calculate_take_profits(
        self,
        entry_price: float,
        stop_loss: float,
    ) -> tuple[float, float]:
        """
        목표가 계산 (공통)

        Returns:
            (TP1: 1.5R, TP2: 3.0R)
        """
        risk = entry_price - stop_loss
        if risk <= 0:
            return entry_price * 1.02, entry_price * 1.05

        tp1 = entry_price + risk * 1.5  # 1.5R
        tp2 = entry_price + risk * 3.0  # 3.0R

        return tp1, tp2

    def calculate_distances(
        self,
        current_price: float,
        breakout_level: float,
        vwap_5m: float,
        atr_5m: float,
    ) -> tuple[float, float, float]:
        """
        거리 지표 계산 (공통)

        Returns:
            (dist_breakout, dist_vwap, dist_atr)
        """
        if current_price <= 0 or breakout_level <= 0:
            return 0, 0, 0

        dist_breakout = (current_price - breakout_level) / breakout_level
        dist_vwap = (current_price - vwap_5m) / vwap_5m if vwap_5m > 0 else 0

        atr_pct = atr_5m / current_price if atr_5m > 0 else 0.01
        dist_atr = dist_vwap / atr_pct if atr_pct > 0 else 0

        return dist_breakout, dist_vwap, dist_atr

    def get_stats(self) -> dict:
        """엔진 통계 반환 (서브클래스에서 오버라이드)"""
        return {
            "engine_type": self.get_engine_type().value,
            "signals_generated": 0,
            "signals_passed": 0,
        }
