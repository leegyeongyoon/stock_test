"""Reconcile - 거래소 스냅샷과 장부 동기화"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

import structlog

from src.config import get_settings

if TYPE_CHECKING:
    from src.portfolio.ledger import Ledger
    from src.risk.risk_engine import RiskEngine

logger = structlog.get_logger()
settings = get_settings()


class ReconcileCheckType(str, Enum):
    """Reconcile 체크 유형"""

    BALANCE = "BALANCE"
    POSITION = "POSITION"
    ORDER = "ORDER"


@dataclass
class PositionMismatch:
    """포지션 불일치 정보"""

    symbol: str
    internal_qty: float
    exchange_qty: float
    internal_side: str
    exchange_side: str
    drift_pct: float


@dataclass
class OrderMismatch:
    """주문 불일치 정보"""

    order_id: str
    symbol: str
    issue: str  # "MISSING_FILL", "UNKNOWN_ORDER", "STATUS_MISMATCH"
    internal_status: str
    exchange_status: str


@dataclass
class ReconcileResult:
    """Reconcile 결과"""

    success: bool
    timestamp: datetime
    spot_balance_expected: float
    spot_balance_actual: float
    perp_balance_expected: float
    perp_balance_actual: float
    spot_drift: float
    perp_drift: float
    total_drift_pct: float
    message: str
    # 확장 필드
    position_mismatches: list[PositionMismatch] = field(default_factory=list)
    order_mismatches: list[OrderMismatch] = field(default_factory=list)
    check_type: ReconcileCheckType = ReconcileCheckType.BALANCE


class Reconciler:
    """
    Reconciler - 장부와 거래소 동기화

    책임:
    1. 주기적으로 거래소 스냅샷 조회 (10초마다)
    2. 잔고/포지션/주문 비교
    3. Drift 감지 시 RiskEngine에 SAFE 모드 전환 요청
    """

    def __init__(
        self,
        spot_exchange: "BinanceSpotExchange",
        perp_exchange: "BinancePerpExchange",
        ledger: "Ledger",
        risk_engine: "RiskEngine",
    ) -> None:
        self.spot = spot_exchange
        self.perp = perp_exchange
        self.ledger = ledger
        self.risk = risk_engine

        self._interval = settings.reconcile_interval_sec
        self._drift_threshold = 0.01  # 1% drift threshold - SAFE 모드 트리거

        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 예상 잔고 (장부 기준)
        self._expected_spot_balance: float = 0.0
        self._expected_perp_balance: float = 0.0

        # 내부 포지션 추적 (symbol -> {side, qty, entry_price})
        self._expected_positions: dict[str, dict] = {}

        # 활성 주문 추적 (order_id -> {symbol, status, qty})
        self._active_orders: dict[str, dict] = {}

        self._last_result: Optional[ReconcileResult] = None
        self._reconcile_history: list[ReconcileResult] = []

        # 연속 실패 카운터
        self._consecutive_failures: int = 0
        self._max_consecutive_failures: int = 3

    async def start(self) -> None:
        """Reconcile 시작"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._reconcile_loop())
        logger.info("Reconciler started", interval=self._interval)

    async def stop(self) -> None:
        """Reconcile 중지"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Reconciler stopped")

    async def _reconcile_loop(self) -> None:
        """주기적 Reconcile 루프 (10초마다 전체 검증)"""
        check_cycle = 0

        while self._running:
            try:
                # 매 사이클: 잔고 체크
                balance_result = await self._reconcile_balance()

                # 매 3 사이클 (30초마다): 포지션 체크
                position_result = None
                if check_cycle % 3 == 0:
                    position_result = await self._reconcile_positions()

                # 매 6 사이클 (60초마다): 주문 체크
                order_result = None
                if check_cycle % 6 == 0:
                    order_result = await self._reconcile_orders()

                # 종합 결과 판단
                all_success = balance_result.success
                if position_result:
                    all_success = all_success and position_result.success
                if order_result:
                    all_success = all_success and order_result.success

                if all_success:
                    self._consecutive_failures = 0
                    self.risk.on_reconcile_success()
                else:
                    self._consecutive_failures += 1
                    # 드리프트 또는 불일치 감지
                    drift = balance_result.total_drift_pct
                    self.risk.on_reconcile_failure(drift)

                    # 연속 3회 실패 시 SAFE 모드 요청
                    if self._consecutive_failures >= self._max_consecutive_failures:
                        logger.critical(
                            "Reconcile consecutive failures - requesting SAFE mode",
                            consecutive_failures=self._consecutive_failures,
                        )

                # 이력 저장 (최근 100개)
                self._reconcile_history.append(balance_result)
                if len(self._reconcile_history) > 100:
                    self._reconcile_history = self._reconcile_history[-100:]

                check_cycle += 1
                await asyncio.sleep(self._interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Reconcile loop error", error=str(e))
                self._consecutive_failures += 1
                await asyncio.sleep(self._interval)

    async def reconcile(self) -> ReconcileResult:
        """전체 Reconcile 실행 (하위 호환용)"""
        return await self._reconcile_balance()

    async def _reconcile_balance(self) -> ReconcileResult:
        """잔고 Reconcile"""
        timestamp = datetime.utcnow()

        try:
            # 거래소 잔고 조회
            spot_balance = await self.spot.get_balance("USDT")
            perp_balance = await self.perp.get_balance("USDT")

            spot_actual = spot_balance.total if spot_balance else 0
            perp_actual = perp_balance.total if perp_balance else 0

            # 예상 잔고 (장부 기준)
            spot_expected = self._expected_spot_balance
            perp_expected = self._expected_perp_balance

            # Drift 계산
            spot_drift = abs(spot_actual - spot_expected) if spot_expected > 0 else 0
            perp_drift = abs(perp_actual - perp_expected) if perp_expected > 0 else 0

            total_expected = spot_expected + perp_expected
            total_drift = spot_drift + perp_drift
            total_drift_pct = (
                total_drift / total_expected if total_expected > 0 else 0
            )

            # Drift 임계값 체크
            if total_drift_pct > self._drift_threshold:
                logger.warning(
                    "Balance drift detected",
                    spot_drift=spot_drift,
                    perp_drift=perp_drift,
                    total_drift_pct=f"{total_drift_pct:.2%}",
                )

                result = ReconcileResult(
                    success=False,
                    timestamp=timestamp,
                    spot_balance_expected=spot_expected,
                    spot_balance_actual=spot_actual,
                    perp_balance_expected=perp_expected,
                    perp_balance_actual=perp_actual,
                    spot_drift=spot_drift,
                    perp_drift=perp_drift,
                    total_drift_pct=total_drift_pct,
                    message=f"Balance drift exceeds threshold: {total_drift_pct:.2%}",
                    check_type=ReconcileCheckType.BALANCE,
                )
            else:
                # 장부 업데이트
                await self.ledger.update_equity(spot_actual + perp_actual)

                result = ReconcileResult(
                    success=True,
                    timestamp=timestamp,
                    spot_balance_expected=spot_expected,
                    spot_balance_actual=spot_actual,
                    perp_balance_expected=perp_expected,
                    perp_balance_actual=perp_actual,
                    spot_drift=spot_drift,
                    perp_drift=perp_drift,
                    total_drift_pct=total_drift_pct,
                    message="Balance reconcile successful",
                    check_type=ReconcileCheckType.BALANCE,
                )

            self._last_result = result
            return result

        except Exception as e:
            logger.error("Balance reconcile failed", error=str(e))
            return ReconcileResult(
                success=False,
                timestamp=timestamp,
                spot_balance_expected=self._expected_spot_balance,
                spot_balance_actual=0,
                perp_balance_expected=self._expected_perp_balance,
                perp_balance_actual=0,
                spot_drift=0,
                perp_drift=0,
                total_drift_pct=1.0,
                message=f"Balance reconcile error: {str(e)}",
                check_type=ReconcileCheckType.BALANCE,
            )

    async def _reconcile_positions(self) -> ReconcileResult:
        """포지션 Reconcile - 내부 vs 거래소 포지션 비교"""
        timestamp = datetime.utcnow()
        mismatches: list[PositionMismatch] = []

        try:
            # 거래소 포지션 조회
            exchange_positions = await self.perp.get_positions()

            # 거래소 포지션 맵 생성
            exchange_pos_map: dict[str, dict] = {}
            for pos in exchange_positions:
                exchange_pos_map[pos.symbol] = {
                    "side": pos.side,
                    "qty": pos.quantity,
                    "entry_price": pos.entry_price,
                }

            # 내부 포지션과 비교
            all_symbols = set(self._expected_positions.keys()) | set(
                exchange_pos_map.keys()
            )

            for symbol in all_symbols:
                internal = self._expected_positions.get(symbol, {})
                exchange = exchange_pos_map.get(symbol, {})

                internal_qty = internal.get("qty", 0)
                exchange_qty = exchange.get("qty", 0)
                internal_side = internal.get("side", "NONE")
                exchange_side = exchange.get("side", "NONE")

                # 수량 차이 계산
                if internal_qty > 0 or exchange_qty > 0:
                    max_qty = max(internal_qty, exchange_qty, 0.0001)
                    drift_pct = abs(internal_qty - exchange_qty) / max_qty

                    # 5% 이상 차이 시 불일치로 판단
                    if drift_pct > 0.05 or internal_side != exchange_side:
                        mismatches.append(
                            PositionMismatch(
                                symbol=symbol,
                                internal_qty=internal_qty,
                                exchange_qty=exchange_qty,
                                internal_side=internal_side,
                                exchange_side=exchange_side,
                                drift_pct=drift_pct,
                            )
                        )

            if mismatches:
                logger.warning(
                    "Position mismatches detected",
                    count=len(mismatches),
                    symbols=[m.symbol for m in mismatches],
                )

                return ReconcileResult(
                    success=False,
                    timestamp=timestamp,
                    spot_balance_expected=0,
                    spot_balance_actual=0,
                    perp_balance_expected=0,
                    perp_balance_actual=0,
                    spot_drift=0,
                    perp_drift=0,
                    total_drift_pct=0,
                    message=f"Position mismatches: {len(mismatches)} symbols",
                    position_mismatches=mismatches,
                    check_type=ReconcileCheckType.POSITION,
                )

            return ReconcileResult(
                success=True,
                timestamp=timestamp,
                spot_balance_expected=0,
                spot_balance_actual=0,
                perp_balance_expected=0,
                perp_balance_actual=0,
                spot_drift=0,
                perp_drift=0,
                total_drift_pct=0,
                message="Position reconcile successful",
                check_type=ReconcileCheckType.POSITION,
            )

        except Exception as e:
            logger.error("Position reconcile failed", error=str(e))
            return ReconcileResult(
                success=False,
                timestamp=timestamp,
                spot_balance_expected=0,
                spot_balance_actual=0,
                perp_balance_expected=0,
                perp_balance_actual=0,
                spot_drift=0,
                perp_drift=0,
                total_drift_pct=1.0,
                message=f"Position reconcile error: {str(e)}",
                check_type=ReconcileCheckType.POSITION,
            )

    async def _reconcile_orders(self) -> ReconcileResult:
        """주문 Reconcile - 미체결 주문 상태 확인, 누락된 체결 감지"""
        timestamp = datetime.utcnow()
        mismatches: list[OrderMismatch] = []

        try:
            # 거래소 미체결 주문 조회
            spot_open_orders = await self.spot.get_open_orders()
            perp_open_orders = await self.perp.get_open_orders()

            exchange_order_ids = set()
            for order in spot_open_orders + perp_open_orders:
                exchange_order_ids.add(str(order.get("orderId", "")))

            # 내부 활성 주문과 비교
            for order_id, order_info in self._active_orders.items():
                internal_status = order_info.get("status", "UNKNOWN")

                # 내부에서 활성이라고 생각하는 주문이 거래소에 없음
                if (
                    internal_status in ("NEW", "PENDING", "ACK", "PARTIAL")
                    and order_id not in exchange_order_ids
                ):
                    # 누락된 체결 가능성
                    mismatches.append(
                        OrderMismatch(
                            order_id=order_id,
                            symbol=order_info.get("symbol", ""),
                            issue="MISSING_FILL",
                            internal_status=internal_status,
                            exchange_status="NOT_FOUND",
                        )
                    )

            # 거래소에 있지만 내부에 없는 주문 (수동 주문 또는 동기화 문제)
            for order in spot_open_orders + perp_open_orders:
                order_id = str(order.get("orderId", ""))
                if order_id and order_id not in self._active_orders:
                    mismatches.append(
                        OrderMismatch(
                            order_id=order_id,
                            symbol=order.get("symbol", ""),
                            issue="UNKNOWN_ORDER",
                            internal_status="NOT_TRACKED",
                            exchange_status=order.get("status", "UNKNOWN"),
                        )
                    )

            if mismatches:
                logger.warning(
                    "Order mismatches detected",
                    count=len(mismatches),
                    issues=[m.issue for m in mismatches],
                )

                return ReconcileResult(
                    success=False,
                    timestamp=timestamp,
                    spot_balance_expected=0,
                    spot_balance_actual=0,
                    perp_balance_expected=0,
                    perp_balance_actual=0,
                    spot_drift=0,
                    perp_drift=0,
                    total_drift_pct=0,
                    message=f"Order mismatches: {len(mismatches)}",
                    order_mismatches=mismatches,
                    check_type=ReconcileCheckType.ORDER,
                )

            return ReconcileResult(
                success=True,
                timestamp=timestamp,
                spot_balance_expected=0,
                spot_balance_actual=0,
                perp_balance_expected=0,
                perp_balance_actual=0,
                spot_drift=0,
                perp_drift=0,
                total_drift_pct=0,
                message="Order reconcile successful",
                check_type=ReconcileCheckType.ORDER,
            )

        except Exception as e:
            logger.error("Order reconcile failed", error=str(e))
            return ReconcileResult(
                success=False,
                timestamp=timestamp,
                spot_balance_expected=0,
                spot_balance_actual=0,
                perp_balance_expected=0,
                perp_balance_actual=0,
                spot_drift=0,
                perp_drift=0,
                total_drift_pct=1.0,
                message=f"Order reconcile error: {str(e)}",
                check_type=ReconcileCheckType.ORDER,
            )

    def update_expected_balances(
        self,
        spot_balance: float,
        perp_balance: float,
    ) -> None:
        """예상 잔고 업데이트"""
        self._expected_spot_balance = spot_balance
        self._expected_perp_balance = perp_balance

    def update_expected_position(
        self,
        symbol: str,
        side: str,
        qty: float,
        entry_price: float,
    ) -> None:
        """예상 포지션 업데이트"""
        if qty > 0:
            self._expected_positions[symbol] = {
                "side": side,
                "qty": qty,
                "entry_price": entry_price,
            }
        else:
            # 포지션 청산
            self._expected_positions.pop(symbol, None)

    def remove_expected_position(self, symbol: str) -> None:
        """예상 포지션 제거"""
        self._expected_positions.pop(symbol, None)

    def track_order(
        self,
        order_id: str,
        symbol: str,
        status: str,
        qty: float,
    ) -> None:
        """주문 추적 시작"""
        self._active_orders[order_id] = {
            "symbol": symbol,
            "status": status,
            "qty": qty,
            "tracked_at": datetime.utcnow(),
        }

    def update_order_status(self, order_id: str, new_status: str) -> None:
        """주문 상태 업데이트"""
        if order_id in self._active_orders:
            self._active_orders[order_id]["status"] = new_status

            # 종료 상태면 추적 제거
            if new_status in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                self._active_orders.pop(order_id, None)

    def untrack_order(self, order_id: str) -> None:
        """주문 추적 종료"""
        self._active_orders.pop(order_id, None)

    def get_last_result(self) -> Optional[ReconcileResult]:
        """마지막 Reconcile 결과"""
        return self._last_result

    def get_reconcile_history(self, limit: int = 10) -> list[ReconcileResult]:
        """Reconcile 이력 반환"""
        return self._reconcile_history[-limit:]

    def get_status(self) -> dict:
        """Reconciler 상태 반환"""
        return {
            "running": self._running,
            "interval_sec": self._interval,
            "drift_threshold": self._drift_threshold,
            "consecutive_failures": self._consecutive_failures,
            "tracked_positions": len(self._expected_positions),
            "tracked_orders": len(self._active_orders),
            "last_result": (
                {
                    "success": self._last_result.success,
                    "timestamp": self._last_result.timestamp.isoformat(),
                    "total_drift_pct": self._last_result.total_drift_pct,
                    "message": self._last_result.message,
                }
                if self._last_result
                else None
            ),
        }
