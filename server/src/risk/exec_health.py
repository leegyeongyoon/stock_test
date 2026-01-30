"""Execution Health Monitor

실전에서 터지는 건 전략이 아니라 실행이다.
- WebSocket 지연
- 주문 ACK 지연
- 주문 실패율
- 리콘실 drift

임계값 초과 시 자동 SAFE 모드 진입
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

from src.risk.config import get_risk_config

logger = structlog.get_logger()


@dataclass
class ExecHealth:
    """실행 건강도 상태"""

    ws_latency_ms: float = 0.0
    order_ack_latency_ms: float = 0.0
    order_failure_rate: float = 0.0
    last_reconcile_drift: float = 0.0
    is_healthy: bool = True
    warning_reasons: list = field(default_factory=list)
    safe_reasons: list = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "ws_latency_ms": self.ws_latency_ms,
            "order_ack_latency_ms": self.order_ack_latency_ms,
            "order_failure_rate": self.order_failure_rate,
            "last_reconcile_drift": self.last_reconcile_drift,
            "is_healthy": self.is_healthy,
            "warning_reasons": self.warning_reasons,
            "safe_reasons": self.safe_reasons,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class OrderRecord:
    """주문 결과 기록"""

    success: bool
    latency_ms: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


class ExecHealthMonitor:
    """
    실행 건강도 모니터

    실시간으로 실행 품질을 추적하고
    임계값 초과 시 SAFE 모드 진입 신호를 발생
    """

    def __init__(self):
        self.config = get_risk_config()

        # WS 지연 추적 (롤링 윈도우)
        self._ws_latencies: deque = deque(maxlen=self.config.exec_health_window)

        # 주문 결과 추적
        self._order_records: deque = deque(maxlen=self.config.exec_health_window)

        # 리콘실 drift
        self._last_reconcile_drift: float = 0.0
        self._last_reconcile_time: Optional[datetime] = None

        # 마지막 상태
        self._last_health: Optional[ExecHealth] = None

    def record_ws_latency(self, latency_ms: float) -> None:
        """WebSocket 지연 기록"""
        self._ws_latencies.append(latency_ms)

        cfg = self.config
        if latency_ms >= cfg.exec_ws_latency_safe_ms:
            logger.warning(
                "WS latency critical",
                latency_ms=latency_ms,
                threshold=cfg.exec_ws_latency_safe_ms,
            )
        elif latency_ms >= cfg.exec_ws_latency_warn_ms:
            logger.info(
                "WS latency warning",
                latency_ms=latency_ms,
                threshold=cfg.exec_ws_latency_warn_ms,
            )

    def record_order_result(
        self,
        success: bool,
        latency_ms: float,
        order_id: str = None,
    ) -> None:
        """주문 결과 기록"""
        record = OrderRecord(success=success, latency_ms=latency_ms)
        self._order_records.append(record)

        if not success:
            logger.warning(
                "Order failed",
                order_id=order_id,
                latency_ms=latency_ms,
            )
        elif latency_ms >= self.config.exec_order_ack_timeout_ms:
            logger.warning(
                "Order ACK slow",
                order_id=order_id,
                latency_ms=latency_ms,
            )

    def record_reconcile_drift(self, drift: float) -> None:
        """리콘실 drift 기록"""
        self._last_reconcile_drift = drift
        self._last_reconcile_time = datetime.utcnow()

        if abs(drift) >= self.config.exec_reconcile_drift_max:
            logger.error(
                "Reconcile drift critical",
                drift=drift,
                threshold=self.config.exec_reconcile_drift_max,
            )

    def get_health(self) -> ExecHealth:
        """현재 실행 건강도 조회"""
        cfg = self.config

        # WS 지연 평균
        ws_latency = (
            sum(self._ws_latencies) / len(self._ws_latencies)
            if self._ws_latencies
            else 0.0
        )

        # 주문 ACK 지연 평균
        order_latencies = [r.latency_ms for r in self._order_records]
        order_ack_latency = (
            sum(order_latencies) / len(order_latencies) if order_latencies else 0.0
        )

        # 주문 실패율
        if self._order_records:
            failures = sum(1 for r in self._order_records if not r.success)
            failure_rate = failures / len(self._order_records)
        else:
            failure_rate = 0.0

        # 경고/위험 판단
        warning_reasons = []
        safe_reasons = []

        # WS 지연 체크
        if ws_latency >= cfg.exec_ws_latency_safe_ms:
            safe_reasons.append(f"WS latency {ws_latency:.0f}ms >= {cfg.exec_ws_latency_safe_ms}ms")
        elif ws_latency >= cfg.exec_ws_latency_warn_ms:
            warning_reasons.append(f"WS latency {ws_latency:.0f}ms >= {cfg.exec_ws_latency_warn_ms}ms")

        # 주문 실패율 체크
        if failure_rate >= cfg.exec_order_failure_rate_max:
            safe_reasons.append(f"Order failure rate {failure_rate:.1%} >= {cfg.exec_order_failure_rate_max:.1%}")

        # 리콘실 drift 체크
        if abs(self._last_reconcile_drift) >= cfg.exec_reconcile_drift_max:
            safe_reasons.append(
                f"Reconcile drift {self._last_reconcile_drift:.2%} >= {cfg.exec_reconcile_drift_max:.2%}"
            )

        # 종합 판단
        is_healthy = len(safe_reasons) == 0

        health = ExecHealth(
            ws_latency_ms=ws_latency,
            order_ack_latency_ms=order_ack_latency,
            order_failure_rate=failure_rate,
            last_reconcile_drift=self._last_reconcile_drift,
            is_healthy=is_healthy,
            warning_reasons=warning_reasons,
            safe_reasons=safe_reasons,
        )

        self._last_health = health
        return health

    def should_enter_safe(self) -> tuple[bool, str]:
        """
        SAFE 모드 진입 필요 여부

        Returns:
            (should_enter, reason)
        """
        health = self.get_health()

        if not health.is_healthy and health.safe_reasons:
            reason = "; ".join(health.safe_reasons)
            return True, f"Exec health critical: {reason}"

        return False, ""

    def get_summary(self) -> dict:
        """요약 정보"""
        health = self.get_health()
        return {
            "is_healthy": health.is_healthy,
            "ws_latency_ms": health.ws_latency_ms,
            "order_failure_rate": health.order_failure_rate,
            "reconcile_drift": health.last_reconcile_drift,
            "warning_count": len(health.warning_reasons),
            "safe_trigger_count": len(health.safe_reasons),
        }


# Singleton
_monitor: ExecHealthMonitor = None


def get_exec_health_monitor() -> ExecHealthMonitor:
    """ExecHealthMonitor 싱글톤"""
    global _monitor
    if _monitor is None:
        _monitor = ExecHealthMonitor()
    return _monitor
