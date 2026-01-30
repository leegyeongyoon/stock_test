"""Order Execution Policy

IOC/타임아웃/재시도/fallback 정책
슬리피지 관리
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import structlog

from src.risk.config import get_risk_config

logger = structlog.get_logger()


class OrderTimeInForce(str, Enum):
    """주문 유효 기간"""

    GTC = "GTC"  # Good Till Cancel
    IOC = "IOC"  # Immediate Or Cancel
    FOK = "FOK"  # Fill Or Kill


class FillStatus(str, Enum):
    """체결 상태"""

    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


@dataclass
class OrderPolicy:
    """주문 실행 정책"""

    # 타임아웃
    timeout_ms: int = 3000  # 3초

    # 재시도
    max_retries: int = 2
    retry_delay_ms: int = 500

    # Fallback
    fallback_to_market: bool = True

    # 슬리피지
    max_slippage_pct: float = 0.01  # 1%

    # IOC 사용 여부
    use_ioc: bool = True

    @classmethod
    def from_config(cls) -> "OrderPolicy":
        """설정에서 정책 생성"""
        cfg = get_risk_config()
        return cls(
            timeout_ms=int(cfg.exec_order_ack_timeout_ms),
            max_slippage_pct=cfg.max_slippage_pct,
        )


@dataclass
class ExecutionResult:
    """실행 결과"""

    success: bool
    status: FillStatus
    order_id: str = ""
    filled_qty: float = 0.0
    avg_price: float = 0.0
    slippage_pct: float = 0.0
    latency_ms: float = 0.0
    retries: int = 0
    error: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "status": self.status.value,
            "order_id": self.order_id,
            "filled_qty": self.filled_qty,
            "avg_price": self.avg_price,
            "slippage_pct": self.slippage_pct,
            "latency_ms": self.latency_ms,
            "retries": self.retries,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SlippageCalc:
    """슬리피지 계산"""

    expected_price: float
    actual_price: float
    slippage_abs: float = 0.0
    slippage_pct: float = 0.0
    is_acceptable: bool = True

    @classmethod
    def calculate(
        cls,
        expected_price: float,
        actual_price: float,
        side: str,
        max_slippage_pct: float = 0.01,
    ) -> "SlippageCalc":
        """
        슬리피지 계산

        Args:
            expected_price: 예상 체결가
            actual_price: 실제 체결가
            side: "BUY" or "SELL"
            max_slippage_pct: 최대 허용 슬리피지

        Returns:
            SlippageCalc
        """
        if expected_price <= 0:
            return cls(
                expected_price=expected_price,
                actual_price=actual_price,
                is_acceptable=True,
            )

        # 슬리피지 계산 (방향 고려)
        if side == "BUY":
            # 매수: 비싸게 사면 불리
            slippage_abs = actual_price - expected_price
        else:
            # 매도: 싸게 팔면 불리
            slippage_abs = expected_price - actual_price

        slippage_pct = slippage_abs / expected_price

        # 허용 범위 체크
        is_acceptable = slippage_pct <= max_slippage_pct

        return cls(
            expected_price=expected_price,
            actual_price=actual_price,
            slippage_abs=slippage_abs,
            slippage_pct=slippage_pct,
            is_acceptable=is_acceptable,
        )


class PositionSizer:
    """
    포지션 사이징 (슬리피지 버퍼 포함)

    실제 체결 손실 기준으로 리스크 계산
    """

    def __init__(self):
        self.config = get_risk_config()

    def calc_position_size(
        self,
        capital: float,
        risk_pct: float,
        entry_price: float,
        stop_price: float,
        slippage_buffer: float = None,
    ) -> float:
        """
        슬리피지 포함한 포지션 사이즈 계산

        Args:
            capital: 총 자본
            risk_pct: 리스크 비율 (예: 0.01 = 1%)
            entry_price: 진입가
            stop_price: 손절가
            slippage_buffer: 슬리피지 버퍼 (기본값: config에서)

        Returns:
            포지션 사이즈 (수량)
        """
        if slippage_buffer is None:
            slippage_buffer = self.config.slippage_buffer_pct

        # 기본 스탑 거리
        stop_distance_pct = abs(entry_price - stop_price) / entry_price

        # 슬리피지 포함한 실효 스탑 거리
        effective_stop_distance = stop_distance_pct + slippage_buffer

        if effective_stop_distance <= 0:
            return 0.0

        # 리스크 금액
        risk_amount = capital * risk_pct

        # 포지션 가치 = 리스크 금액 / 실효 스탑 거리
        position_value = risk_amount / effective_stop_distance

        # 수량 = 포지션 가치 / 진입가
        quantity = position_value / entry_price

        logger.debug(
            "Position size calculated",
            capital=capital,
            risk_pct=risk_pct,
            stop_distance_pct=stop_distance_pct,
            slippage_buffer=slippage_buffer,
            effective_stop=effective_stop_distance,
            position_value=position_value,
            quantity=quantity,
        )

        return quantity

    def calc_risk_adjusted_size(
        self,
        base_size: float,
        sizing_multiplier: float,
        max_exposure_pct: float,
        capital: float,
        entry_price: float,
    ) -> float:
        """
        Risk Overlay 적용된 사이즈 계산

        Args:
            base_size: 기본 사이즈
            sizing_multiplier: DD 기반 배수 (0~1)
            max_exposure_pct: 최대 노출 비율
            capital: 총 자본
            entry_price: 진입가

        Returns:
            조정된 사이즈
        """
        # 사이징 배수 적용
        adjusted_size = base_size * sizing_multiplier

        # 최대 노출 한도 체크
        max_position_value = capital * max_exposure_pct
        max_size = max_position_value / entry_price

        final_size = min(adjusted_size, max_size)

        if final_size < adjusted_size:
            logger.info(
                "Position size capped by exposure limit",
                requested=adjusted_size,
                capped=final_size,
                max_exposure_pct=max_exposure_pct,
            )

        return final_size


# Singleton
_sizer: PositionSizer = None


def get_position_sizer() -> PositionSizer:
    """PositionSizer 싱글톤"""
    global _sizer
    if _sizer is None:
        _sizer = PositionSizer()
    return _sizer
