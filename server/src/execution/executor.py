"""Execution Engine - 주문 실행 및 관리"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

import structlog

from src.config import get_settings
from src.execution.order_state import OrderStateMachine, OrderStateError
from src.models.schemas import OrderSide, OrderStatus, OrderType, StrategyType

if TYPE_CHECKING:
    from src.exchange.base import BaseExchange
    from src.risk.risk_engine import RiskEngine

logger = structlog.get_logger()
settings = get_settings()


class OrderUrgency(str, Enum):
    """주문 긴급도"""

    LOW = "LOW"  # 일반 진입 - maker 우선
    HIGH = "HIGH"  # 헤지/리스크 - taker 허용


@dataclass
class OrderPolicy:
    """주문 실행 정책"""

    urgency: OrderUrgency = OrderUrgency.LOW
    timeout_sec: float = 5.0  # 주문 타임아웃
    max_price_chase: int = 1  # 가격 추종 횟수
    max_slippage_bps: float = 6.0  # 최대 슬리피지 (bps)
    use_post_only: bool = True  # maker only 사용


@dataclass
class HedgeRecoveryResult:
    """헤지 복구 결과"""

    success: bool
    attempts: int
    spot_rollback_executed: bool = False
    rollback_quantity: float = 0.0
    total_slippage_bps: float = 0.0
    final_perp_order_id: Optional[str] = None


class ExecutionEngine:
    """
    Execution Engine

    책임:
    1. 주문 생성 및 전송
    2. 주문 상태 추적
    3. 한쪽 체결 대응 (hedge-follow)
    4. 주문 실패 처리
    5. 슬리피지 관리
    """

    def __init__(
        self,
        spot_exchange: "BaseExchange",
        perp_exchange: "BaseExchange",
        risk_engine: "RiskEngine",
    ) -> None:
        self.spot = spot_exchange
        self.perp = perp_exchange
        self.risk = risk_engine

        # 활성 주문 관리
        self._active_orders: dict[str, OrderStateMachine] = {}
        self._order_history: list[OrderStateMachine] = []

        # 헤지 페어 추적 (spot_order_id -> perp_order_id)
        self._hedge_pairs: dict[str, str] = {}

        # 헤지 복구 이력
        self._hedge_recovery_history: list[HedgeRecoveryResult] = []

        # 슬리피지 추적 (symbol -> list of slippage_bps)
        self._slippage_history: dict[str, list[float]] = {}

        self._lock = asyncio.Lock()

    def _get_policy_for_strategy(
        self, strategy: StrategyType, urgency: OrderUrgency
    ) -> OrderPolicy:
        """전략별 주문 정책 반환"""
        if urgency == OrderUrgency.HIGH:
            # 긴급 주문 (헤지/리스크)
            return OrderPolicy(
                urgency=OrderUrgency.HIGH,
                timeout_sec=2.0,
                max_price_chase=0,  # 즉시 IOC
                max_slippage_bps=settings.max_slippage_sat_bps,  # 15bps
                use_post_only=False,
            )

        # 일반 주문 - 전략별 슬리피지 한도
        if strategy == StrategyType.CORE:
            max_slip = settings.max_slippage_core_bps  # 6bps
        else:
            max_slip = settings.max_slippage_sat_bps  # 15bps

        return OrderPolicy(
            urgency=OrderUrgency.LOW,
            timeout_sec=5.0,
            max_price_chase=1,
            max_slippage_bps=max_slip,
            use_post_only=True,
        )

    def _calculate_slippage_bps(
        self, expected_price: float, actual_price: float, side: OrderSide
    ) -> float:
        """슬리피지 계산 (bps)"""
        if expected_price <= 0:
            return 0.0

        if side == OrderSide.BUY:
            # 매수: 실제가격이 높으면 불리
            slippage = (actual_price - expected_price) / expected_price
        else:
            # 매도: 실제가격이 낮으면 불리
            slippage = (expected_price - actual_price) / expected_price

        return slippage * 10000  # bps 변환

    def _record_slippage(self, symbol: str, slippage_bps: float) -> None:
        """슬리피지 기록"""
        if symbol not in self._slippage_history:
            self._slippage_history[symbol] = []

        self._slippage_history[symbol].append(slippage_bps)

        # 최근 100개만 유지
        if len(self._slippage_history[symbol]) > 100:
            self._slippage_history[symbol] = self._slippage_history[symbol][-100:]

    def get_avg_slippage_bps(self, symbol: str) -> float:
        """심볼별 평균 슬리피지 반환"""
        history = self._slippage_history.get(symbol, [])
        if not history:
            return 0.0
        return sum(history) / len(history)

    def get_slippage_top3(self) -> list[tuple[str, float]]:
        """슬리피지 TOP3 심볼 반환"""
        avg_slippages = [
            (symbol, self.get_avg_slippage_bps(symbol))
            for symbol in self._slippage_history
        ]
        avg_slippages.sort(key=lambda x: x[1], reverse=True)
        return avg_slippages[:3]

    async def create_order(
        self,
        symbol: str,
        strategy: StrategyType,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        is_spot: bool = True,
    ) -> OrderStateMachine:
        """주문 생성"""
        order = OrderStateMachine(
            symbol=symbol,
            strategy=strategy,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
        )

        async with self._lock:
            self._active_orders[order.order_id] = order

        logger.info(
            "Order created",
            order_id=order.order_id,
            symbol=symbol,
            side=side.value,
            quantity=quantity,
        )

        return order

    async def submit_order(
        self,
        order: OrderStateMachine,
        is_spot: bool = True,
        urgency: OrderUrgency = OrderUrgency.LOW,
    ) -> bool:
        """주문 제출"""
        if not self.risk.can_open_position and order.side == OrderSide.BUY:
            logger.warning(
                "Cannot submit order: risk engine blocked",
                order_id=order.order_id,
            )
            return False

        exchange = self.spot if is_spot else self.perp
        policy = self._get_policy_for_strategy(order.strategy, urgency)

        try:
            result = await self._execute_with_policy(
                exchange=exchange,
                order=order,
                policy=policy,
            )

            if result:
                self.risk.on_order_success()
                return True
            else:
                self.risk.on_order_failure()
                return False

        except Exception as e:
            logger.error(
                "Order submission failed",
                order_id=order.order_id,
                error=str(e),
            )
            self.risk.on_order_failure()
            return False

    async def _execute_with_policy(
        self,
        exchange: "BaseExchange",
        order: OrderStateMachine,
        policy: OrderPolicy,
    ) -> bool:
        """정책 기반 주문 실행"""
        expected_price = order.price

        # 1차 시도: maker 주문 (post-only)
        if policy.use_post_only and order.order_type == OrderType.LIMIT:
            result = await exchange.place_order(
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                price=order.price,
                post_only=True,
            )

            if result.success and result.exchange_order_id:
                order.mark_sent(result.exchange_order_id)

                # 타임아웃 대기
                filled = await self._wait_for_fill(
                    exchange=exchange,
                    order=order,
                    timeout_sec=policy.timeout_sec,
                )

                if filled:
                    self._check_and_record_slippage(
                        order, expected_price, policy.max_slippage_bps
                    )
                    return True

                # 미체결 - 취소 후 가격 추종
                await exchange.cancel_order(
                    symbol=order.symbol,
                    order_id=result.exchange_order_id,
                )

                if policy.max_price_chase > 0:
                    return await self._chase_price(
                        exchange=exchange,
                        order=order,
                        policy=policy,
                        expected_price=expected_price,
                    )

                return False

        # 긴급 주문 또는 maker 실패 후: IOC taker
        return await self._execute_ioc(
            exchange=exchange,
            order=order,
            policy=policy,
            expected_price=expected_price,
        )

    async def _wait_for_fill(
        self,
        exchange: "BaseExchange",
        order: OrderStateMachine,
        timeout_sec: float,
    ) -> bool:
        """체결 대기"""
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < timeout_sec:
            if not order.exchange_order_id:
                return False

            status = await exchange.get_order_status(
                symbol=order.symbol,
                order_id=order.exchange_order_id,
            )

            if status == OrderStatus.FILLED:
                order.mark_acked()
                # 체결 정보 업데이트 필요
                order_info = await exchange.get_order(
                    symbol=order.symbol,
                    order_id=order.exchange_order_id,
                )
                if order_info:
                    order.mark_filled(
                        filled_qty=order_info.get("filled_qty", order.quantity),
                        fill_price=order_info.get("avg_price", order.price),
                    )
                return True

            elif status == OrderStatus.PARTIAL:
                order.mark_acked()
                order_info = await exchange.get_order(
                    symbol=order.symbol,
                    order_id=order.exchange_order_id,
                )
                if order_info:
                    order.mark_partial_fill(
                        filled_qty=order_info.get("filled_qty", 0),
                        fill_price=order_info.get("avg_price", order.price),
                    )

            elif status in (OrderStatus.CANCELED, OrderStatus.REJECTED):
                return False

            await asyncio.sleep(0.5)

        return False

    async def _chase_price(
        self,
        exchange: "BaseExchange",
        order: OrderStateMachine,
        policy: OrderPolicy,
        expected_price: float,
    ) -> bool:
        """가격 추종 - 현재가로 재주문"""
        # 현재가 조회
        ticker = await exchange.get_ticker(order.symbol)
        if not ticker:
            return await self._execute_ioc(
                exchange, order, policy, expected_price
            )

        # 슬리피지 체크 (TickerData 객체에서 last 가격 사용)
        new_price = ticker.last if hasattr(ticker, "last") else expected_price
        slippage = self._calculate_slippage_bps(expected_price, new_price, order.side)

        if slippage > policy.max_slippage_bps:
            logger.warning(
                "Price chase rejected: slippage too high",
                order_id=order.order_id,
                slippage_bps=slippage,
                max_allowed=policy.max_slippage_bps,
            )
            return False

        # 새 가격으로 IOC 주문
        order.price = new_price
        return await self._execute_ioc(exchange, order, policy, expected_price)

    async def _execute_ioc(
        self,
        exchange: "BaseExchange",
        order: OrderStateMachine,
        policy: OrderPolicy,
        expected_price: float,
    ) -> bool:
        """IOC (Immediate-or-Cancel) 주문 실행"""
        result = await exchange.place_order(
            symbol=order.symbol,
            side=order.side,
            order_type=OrderType.MARKET,  # IOC는 시장가로
            quantity=order.quantity,
            price=None,
        )

        if result.success and result.exchange_order_id:
            order.mark_sent(result.exchange_order_id)

            if result.status == OrderStatus.FILLED:
                order.mark_acked()
                order.mark_filled(
                    filled_qty=result.filled_qty,
                    fill_price=result.avg_price,
                )

                # 슬리피지 기록
                if result.avg_price and expected_price:
                    slippage = self._calculate_slippage_bps(
                        expected_price, result.avg_price, order.side
                    )
                    self._record_slippage(order.symbol, slippage)

                    if slippage > policy.max_slippage_bps:
                        logger.warning(
                            "High slippage detected",
                            order_id=order.order_id,
                            slippage_bps=slippage,
                            max_allowed=policy.max_slippage_bps,
                        )

                return True

            elif result.status == OrderStatus.PARTIAL:
                order.mark_acked()
                order.mark_partial_fill(
                    filled_qty=result.filled_qty,
                    fill_price=result.avg_price,
                )
                return True

        order.mark_rejected(result.error or "IOC execution failed")
        return False

    def _check_and_record_slippage(
        self,
        order: OrderStateMachine,
        expected_price: float,
        max_slippage_bps: float,
    ) -> None:
        """슬리피지 체크 및 기록"""
        if not order.fill_price or not expected_price:
            return

        slippage = self._calculate_slippage_bps(
            expected_price, order.fill_price, order.side
        )
        self._record_slippage(order.symbol, slippage)

        if slippage > max_slippage_bps:
            logger.warning(
                "High slippage on filled order",
                order_id=order.order_id,
                slippage_bps=slippage,
                max_allowed=max_slippage_bps,
            )

    async def submit_hedge_pair(
        self,
        spot_order: OrderStateMachine,
        perp_order: OrderStateMachine,
        urgency: OrderUrgency = OrderUrgency.LOW,
    ) -> tuple[bool, bool]:
        """
        헤지 페어 주문 제출

        Returns:
            (spot_success, perp_success)
        """
        # 스팟 먼저
        spot_success = await self.submit_order(
            spot_order, is_spot=True, urgency=urgency
        )

        if not spot_success:
            logger.warning("Spot order failed, skipping perp")
            return (False, False)

        # 스팟 성공 시 퍼프 주문 (긴급도 높임)
        perp_urgency = OrderUrgency.HIGH if spot_success else urgency
        perp_success = await self.submit_order(
            perp_order, is_spot=False, urgency=perp_urgency
        )

        if spot_success and not perp_success:
            # 한쪽만 체결됨 - hedge follow 필요
            logger.critical(
                "Hedge failure: spot filled but perp failed",
                spot_order_id=spot_order.order_id,
                perp_order_id=perp_order.order_id,
            )
            await self.risk.on_hedge_failure(spot_order.symbol, spot_order.side.value)
            recovery_result = await self._attempt_hedge_recovery(spot_order, perp_order)
            self._hedge_recovery_history.append(recovery_result)

            # 복구 실패 시 결과 반영
            if not recovery_result.success:
                return (spot_success, False)
            else:
                # 복구 성공 시 페어 등록
                if recovery_result.final_perp_order_id:
                    self._hedge_pairs[spot_order.order_id] = (
                        recovery_result.final_perp_order_id
                    )
                return (spot_success, True)

        if spot_success and perp_success:
            self._hedge_pairs[spot_order.order_id] = perp_order.order_id

        return (spot_success, perp_success)

    async def _attempt_hedge_recovery(
        self,
        spot_order: OrderStateMachine,
        failed_perp_order: OrderStateMachine,
    ) -> HedgeRecoveryResult:
        """
        헤지 실패 복구 시도

        3회까지 재시도 (긴급 모드), 실패 시 스팟 청산
        슬리피지 추적 및 결과 리포팅
        """
        max_retries = 3
        total_slippage = 0.0
        expected_price = failed_perp_order.price or 0.0

        for attempt in range(max_retries):
            logger.info(
                f"Hedge recovery attempt {attempt + 1}/{max_retries}",
                spot_order_id=spot_order.order_id,
            )

            # 새로운 퍼프 주문 생성 (긴급 모드)
            new_perp = await self.create_order(
                symbol=failed_perp_order.symbol,
                strategy=failed_perp_order.strategy,
                side=failed_perp_order.side,
                order_type=OrderType.MARKET,  # 긴급이므로 시장가
                quantity=failed_perp_order.quantity,
            )

            # 긴급 모드로 제출
            success = await self.submit_order(
                new_perp, is_spot=False, urgency=OrderUrgency.HIGH
            )

            if success:
                # 슬리피지 계산
                if new_perp.fill_price and expected_price:
                    total_slippage = self._calculate_slippage_bps(
                        expected_price, new_perp.fill_price, failed_perp_order.side
                    )

                logger.info(
                    "Hedge recovery successful",
                    spot_order_id=spot_order.order_id,
                    new_perp_order_id=new_perp.order_id,
                    attempts=attempt + 1,
                    slippage_bps=total_slippage,
                )

                return HedgeRecoveryResult(
                    success=True,
                    attempts=attempt + 1,
                    spot_rollback_executed=False,
                    rollback_quantity=0.0,
                    total_slippage_bps=total_slippage,
                    final_perp_order_id=new_perp.order_id,
                )

            await asyncio.sleep(1)

        # 모든 재시도 실패 - 스팟 롤백 (청산)
        logger.critical(
            "Hedge recovery failed, executing spot rollback",
            spot_order_id=spot_order.order_id,
        )

        rollback_qty = spot_order.filled_quantity or spot_order.quantity
        close_side = (
            OrderSide.SELL if spot_order.side == OrderSide.BUY else OrderSide.BUY
        )

        close_order = await self.create_order(
            symbol=spot_order.symbol,
            strategy=spot_order.strategy,
            side=close_side,
            order_type=OrderType.MARKET,
            quantity=rollback_qty,
        )

        rollback_success = await self.submit_order(
            close_order, is_spot=True, urgency=OrderUrgency.HIGH
        )

        if rollback_success:
            logger.info(
                "Spot rollback completed",
                spot_order_id=spot_order.order_id,
                rollback_quantity=rollback_qty,
            )
        else:
            logger.critical(
                "Spot rollback FAILED - manual intervention required",
                spot_order_id=spot_order.order_id,
                symbol=spot_order.symbol,
                quantity=rollback_qty,
            )

        return HedgeRecoveryResult(
            success=False,
            attempts=max_retries,
            spot_rollback_executed=rollback_success,
            rollback_quantity=rollback_qty if rollback_success else 0.0,
            total_slippage_bps=total_slippage,
            final_perp_order_id=None,
        )

    def get_hedge_recovery_stats(self) -> dict:
        """헤지 복구 통계 반환"""
        if not self._hedge_recovery_history:
            return {
                "total_recoveries": 0,
                "success_count": 0,
                "failure_count": 0,
                "avg_attempts": 0.0,
                "avg_slippage_bps": 0.0,
                "total_rollback_quantity": 0.0,
            }

        total = len(self._hedge_recovery_history)
        successes = sum(1 for r in self._hedge_recovery_history if r.success)
        failures = total - successes
        avg_attempts = sum(r.attempts for r in self._hedge_recovery_history) / total
        avg_slippage = (
            sum(r.total_slippage_bps for r in self._hedge_recovery_history) / total
        )
        total_rollback = sum(
            r.rollback_quantity for r in self._hedge_recovery_history
        )

        return {
            "total_recoveries": total,
            "success_count": successes,
            "failure_count": failures,
            "success_rate": successes / total if total > 0 else 0.0,
            "avg_attempts": avg_attempts,
            "avg_slippage_bps": avg_slippage,
            "total_rollback_quantity": total_rollback,
        }

    async def cancel_order(
        self,
        order_id: str,
        is_spot: bool = True,
    ) -> bool:
        """주문 취소"""
        async with self._lock:
            order = self._active_orders.get(order_id)

        if not order:
            logger.warning("Order not found", order_id=order_id)
            return False

        if order.is_terminal:
            logger.warning("Order already terminal", order_id=order_id)
            return False

        exchange = self.spot if is_spot else self.perp

        try:
            success = await exchange.cancel_order(
                symbol=order.symbol,
                order_id=order.exchange_order_id or "",
            )

            if success:
                order.mark_canceled()
                return True

            return False

        except Exception as e:
            logger.error("Cancel failed", order_id=order_id, error=str(e))
            return False

    async def sync_order_status(
        self,
        order_id: str,
        is_spot: bool = True,
    ) -> Optional[OrderStatus]:
        """거래소와 주문 상태 동기화"""
        async with self._lock:
            order = self._active_orders.get(order_id)

        if not order or not order.exchange_order_id:
            return None

        exchange = self.spot if is_spot else self.perp

        try:
            status = await exchange.get_order_status(
                symbol=order.symbol,
                order_id=order.exchange_order_id,
            )

            if status and status != order.status:
                try:
                    order.transition_to(status)
                except OrderStateError as e:
                    logger.warning(
                        "Invalid status transition",
                        order_id=order_id,
                        error=str(e),
                    )

            return status

        except Exception as e:
            logger.error("Status sync failed", order_id=order_id, error=str(e))
            return None

    def get_active_orders(self) -> list[dict]:
        """활성 주문 목록"""
        return [
            order.to_dict()
            for order in self._active_orders.values()
            if order.is_active
        ]

    def get_order_history(self, limit: int = 100) -> list[dict]:
        """주문 이력"""
        return [order.to_dict() for order in self._order_history[-limit:]]

    async def cleanup_completed_orders(self) -> None:
        """완료된 주문 정리"""
        async with self._lock:
            completed = [
                order_id
                for order_id, order in self._active_orders.items()
                if order.is_terminal
            ]

            for order_id in completed:
                order = self._active_orders.pop(order_id)
                self._order_history.append(order)

            # 이력은 최대 10000개
            if len(self._order_history) > 10000:
                self._order_history = self._order_history[-10000:]
