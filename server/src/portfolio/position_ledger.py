"""PositionLedger - 단일 진실 원장 (Single Source of Truth)

모든 포지션 상태의 단일 원장. 체결 이벤트 기반으로만 업데이트됨.

핵심 원칙:
1. 주문 제출이 아닌 "체결 확인" 후에만 포지션 업데이트
2. 모든 컴포넌트(PSM, Reconciler 등)는 이 원장을 참조
3. 거래소 잔고와 주기적으로 reconcile하여 정합성 보장
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy import select

from src.models.database import PositionLedgerModel, async_session
from src.models.schemas import OrderSide, StrategyType

logger = structlog.get_logger()


@dataclass
class PositionLedgerEntry:
    """포지션 원장 엔트리 - 단일 포지션의 모든 상태"""

    position_id: str  # UUID
    strategy_id: str  # "VOLATILE_OVERSOLD_BOUNCE", "CRASH_RECOVERY", "TRIPLE_BEARISH_REVERSAL"
    symbol: str  # "KRW-BTC"
    side: str  # "LONG" (업비트는 LONG only)
    quantity: float  # 보유 수량
    avg_entry_price: float  # 평균 진입가
    realized_pnl: float  # 실현 손익 (KRW)
    unrealized_pnl: float  # 미실현 손익 (KRW)
    total_fees: float  # 총 수수료 (KRW)
    status: str  # "OPEN", "PARTIAL_CLOSED", "CLOSED"
    entry_time: datetime
    last_fill_time: datetime
    fill_count: int  # 체결 횟수

    # 스탑/익절 정보
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None

    # R-multiple 계산용
    initial_stop_distance: float = 0.0  # 진입 시 스탑 거리 (가격)

    # 메타데이터
    metadata: dict = field(default_factory=dict)

    @property
    def notional_value(self) -> float:
        """현재 포지션 명목 가치 (KRW)"""
        return self.quantity * self.avg_entry_price

    @property
    def is_open(self) -> bool:
        """포지션 오픈 여부"""
        return self.status in ("OPEN", "PARTIAL_CLOSED")

    def calc_unrealized_pnl(self, current_price: float) -> float:
        """미실현 손익 계산"""
        if self.side == "LONG":
            return (current_price - self.avg_entry_price) * self.quantity
        else:
            return (self.avg_entry_price - current_price) * self.quantity

    def calc_r_pnl(self, current_price: float) -> float:
        """R-multiple 계산 (손익을 스탑 거리 기준으로 표준화)"""
        if self.initial_stop_distance <= 0:
            return 0.0

        pnl_per_unit = current_price - self.avg_entry_price
        if self.side == "SHORT":
            pnl_per_unit = -pnl_per_unit

        return pnl_per_unit / self.initial_stop_distance

    def to_dict(self) -> dict:
        """딕셔너리로 변환"""
        return {
            "position_id": self.position_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "avg_entry_price": self.avg_entry_price,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "total_fees": self.total_fees,
            "status": self.status,
            "entry_time": self.entry_time.isoformat(),
            "last_fill_time": self.last_fill_time.isoformat(),
            "fill_count": self.fill_count,
            "stop_price": self.stop_price,
            "take_profit_price": self.take_profit_price,
            "initial_stop_distance": self.initial_stop_distance,
            "notional_value": self.notional_value,
            "is_open": self.is_open,
            "metadata": self.metadata,
        }


@dataclass
class FillEvent:
    """체결 이벤트 - 원장 업데이트의 유일한 입력"""

    order_id: str
    exchange_order_id: str
    position_id: Optional[str]  # 기존 포지션 추가 매수시
    symbol: str
    strategy_id: str
    side: str  # "BUY" or "SELL"
    filled_quantity: float
    fill_price: float
    fee: float
    fee_asset: str
    timestamp: datetime

    # 체결비용 분석용
    requested_price: float  # 주문 요청 가격
    spread_bps_at_fill: float = 0.0  # 체결 시점 스프레드

    # 스탑 정보 (신규 진입시)
    initial_stop_price: Optional[float] = None


@dataclass
class ReconcileResult:
    """Reconcile 결과"""

    is_matched: bool
    discrepancies: list[dict] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)


class PositionLedger:
    """
    단일 진실 원장 (Single Source of Truth)

    모든 포지션 상태를 관리하며, 체결 이벤트를 통해서만 업데이트됨.
    다른 컴포넌트(PSM, Reconciler 등)는 이 원장을 참조해야 함.
    """

    def __init__(self) -> None:
        # position_id -> PositionLedgerEntry
        self._positions: dict[str, PositionLedgerEntry] = {}

        # symbol -> list[position_id] (심볼별 포지션 조회용)
        self._symbol_positions: dict[str, list[str]] = {}

        # strategy -> list[position_id] (전략별 포지션 조회용)
        self._strategy_positions: dict[str, list[str]] = {}

        # 동시성 제어
        self._lock = asyncio.Lock()

        # 체결 히스토리 (최근 100건)
        self._fill_history: list[FillEvent] = []

    async def restore_from_database(self) -> None:
        """데이터베이스에서 오픈 포지션 복원"""
        try:
            async with async_session() as session:
                stmt = select(PositionLedgerModel).where(
                    PositionLedgerModel.status == "OPEN"
                )
                result = await session.execute(stmt)
                db_positions = result.scalars().all()

                restored_count = 0
                for db_pos in db_positions:
                    # 메타데이터 파싱
                    metadata = {}
                    if db_pos.metadata_json:
                        try:
                            metadata = json.loads(db_pos.metadata_json)
                        except json.JSONDecodeError:
                            logger.warning(
                                "Failed to parse metadata JSON",
                                position_id=db_pos.position_id,
                            )

                    # PositionLedgerEntry 재구성
                    position = PositionLedgerEntry(
                        position_id=db_pos.position_id,
                        strategy_id=db_pos.strategy_id,
                        symbol=db_pos.symbol,
                        side=db_pos.side,
                        quantity=db_pos.quantity,
                        avg_entry_price=db_pos.avg_entry_price,
                        realized_pnl=db_pos.realized_pnl,
                        unrealized_pnl=0.0,  # 재계산 필요
                        total_fees=db_pos.total_fees,
                        status=db_pos.status,
                        entry_time=db_pos.entry_time,
                        last_fill_time=db_pos.last_fill_time,
                        fill_count=db_pos.fill_count,
                        stop_price=db_pos.stop_price,
                        initial_stop_distance=db_pos.initial_stop_distance,
                        metadata=metadata,
                    )

                    # 인덱스에 추가
                    self._positions[position.position_id] = position
                    self._add_to_index(position)
                    restored_count += 1

                logger.info(
                    "Positions restored from database",
                    restored_count=restored_count,
                    symbols=[p.symbol for p in self._positions.values()],
                    strategies=[p.strategy_id for p in self._positions.values()],
                )

        except Exception as e:
            logger.error(
                "Failed to restore positions from database",
                error=str(e),
                exc_info=True,
            )

    async def _persist_position_to_db(self, position: PositionLedgerEntry) -> None:
        """포지션을 데이터베이스에 저장/업데이트"""
        try:
            async with async_session() as session:
                # 기존 포지션 조회
                stmt = select(PositionLedgerModel).where(
                    PositionLedgerModel.position_id == position.position_id
                )
                result = await session.execute(stmt)
                db_position = result.scalar_one_or_none()

                # 메타데이터 JSON 직렬화
                metadata_json = json.dumps(position.metadata) if position.metadata else None

                if db_position:
                    # 업데이트
                    db_position.quantity = position.quantity
                    db_position.avg_entry_price = position.avg_entry_price
                    db_position.realized_pnl = position.realized_pnl
                    db_position.total_fees = position.total_fees
                    db_position.status = position.status
                    db_position.last_fill_time = position.last_fill_time
                    db_position.fill_count = position.fill_count
                    db_position.stop_price = position.stop_price
                    db_position.metadata_json = metadata_json
                else:
                    # 신규 생성
                    db_position = PositionLedgerModel(
                        position_id=position.position_id,
                        strategy_id=position.strategy_id,
                        symbol=position.symbol,
                        side=position.side,
                        quantity=position.quantity,
                        avg_entry_price=position.avg_entry_price,
                        realized_pnl=position.realized_pnl,
                        total_fees=position.total_fees,
                        status=position.status,
                        entry_time=position.entry_time,
                        last_fill_time=position.last_fill_time,
                        fill_count=position.fill_count,
                        stop_price=position.stop_price,
                        initial_stop_distance=position.initial_stop_distance,
                        metadata_json=metadata_json,
                    )
                    session.add(db_position)

                await session.commit()

                logger.debug(
                    "Position persisted to database",
                    position_id=position.position_id,
                    symbol=position.symbol,
                    strategy_id=position.strategy_id,
                )

        except Exception as e:
            logger.error(
                "Failed to persist position to database",
                position_id=position.position_id,
                error=str(e),
                exc_info=True,
            )

    async def on_buy_fill(self, fill: FillEvent) -> PositionLedgerEntry:
        """
        매수 체결 처리 - 신규 또는 추가 매수

        Args:
            fill: 체결 이벤트

        Returns:
            업데이트된 포지션 엔트리
        """
        async with self._lock:
            # 기존 포지션 확인
            existing_position_id = fill.position_id
            if existing_position_id and existing_position_id in self._positions:
                # 추가 매수 (평균 단가 조정)
                position = self._positions[existing_position_id]
                return self._add_to_position(position, fill)
            else:
                # 동일 심볼+전략 오픈 포지션 있는지 확인
                existing = self._find_open_position(fill.symbol, fill.strategy_id)
                if existing:
                    return self._add_to_position(existing, fill)

                # 신규 포지션 생성
                return self._create_new_position(fill)

    async def on_sell_fill(self, fill: FillEvent) -> Optional[PositionLedgerEntry]:
        """
        매도 체결 처리 - 부분 또는 전량 청산

        Args:
            fill: 체결 이벤트

        Returns:
            업데이트된 포지션 엔트리 (청산 완료시 None)
        """
        async with self._lock:
            # 청산할 포지션 찾기
            position = None
            if fill.position_id and fill.position_id in self._positions:
                position = self._positions[fill.position_id]
            else:
                position = self._find_open_position(fill.symbol, fill.strategy_id)

            if not position:
                logger.warning(
                    "No position found for sell fill",
                    symbol=fill.symbol,
                    strategy=fill.strategy_id,
                    order_id=fill.order_id,
                )
                return None

            return self._close_position(position, fill)

    def _create_new_position(self, fill: FillEvent) -> PositionLedgerEntry:
        """신규 포지션 생성"""
        position_id = str(uuid.uuid4())

        # 초기 스탑 거리 계산
        initial_stop_distance = 0.0
        if fill.initial_stop_price:
            initial_stop_distance = abs(fill.fill_price - fill.initial_stop_price)

        position = PositionLedgerEntry(
            position_id=position_id,
            strategy_id=fill.strategy_id,
            symbol=fill.symbol,
            side="LONG",  # 업비트 현물은 LONG만
            quantity=fill.filled_quantity,
            avg_entry_price=fill.fill_price,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            total_fees=fill.fee,
            status="OPEN",
            entry_time=fill.timestamp,
            last_fill_time=fill.timestamp,
            fill_count=1,
            stop_price=fill.initial_stop_price,
            initial_stop_distance=initial_stop_distance,
            metadata={
                "entry_order_id": fill.order_id,
                "entry_spread_bps": fill.spread_bps_at_fill,
                "entry_slippage_bps": self._calc_slippage_bps(
                    fill.requested_price, fill.fill_price
                ),
            },
        )

        logger.info(
            "Created new position in ledger",
            position_id=position_id,
            symbol=fill.symbol,
            strategy_id=fill.strategy_id,
            quantity=fill.filled_quantity,
            entry_price=fill.fill_price,
        )

        # 인덱스 업데이트
        self._positions[position_id] = position
        self._add_to_index(position)

        # 체결 히스토리 추가
        self._add_fill_history(fill)

        # 데이터베이스에 저장 (비동기)
        asyncio.create_task(self._persist_position_to_db(position))

        logger.info(
            "New position created",
            position_id=position_id,
            symbol=fill.symbol,
            strategy=fill.strategy_id,
            quantity=fill.filled_quantity,
            price=fill.fill_price,
            fee=fill.fee,
        )

        return position

    def _add_to_position(
        self, position: PositionLedgerEntry, fill: FillEvent
    ) -> PositionLedgerEntry:
        """기존 포지션에 추가 매수"""
        old_qty = position.quantity
        old_avg = position.avg_entry_price
        new_qty = fill.filled_quantity
        new_price = fill.fill_price

        # 평균 단가 재계산
        total_cost = (old_qty * old_avg) + (new_qty * new_price)
        total_qty = old_qty + new_qty
        new_avg = total_cost / total_qty if total_qty > 0 else 0

        position.quantity = total_qty
        position.avg_entry_price = new_avg
        position.total_fees += fill.fee
        position.last_fill_time = fill.timestamp
        position.fill_count += 1

        # 체결 히스토리 추가
        self._add_fill_history(fill)

        logger.info(
            "Position added",
            position_id=position.position_id,
            symbol=fill.symbol,
            old_qty=old_qty,
            new_qty=total_qty,
            old_avg=old_avg,
            new_avg=new_avg,
        )

        # 데이터베이스에 저장
        asyncio.create_task(self._persist_position_to_db(position))

        return position

    def _close_position(
        self, position: PositionLedgerEntry, fill: FillEvent
    ) -> Optional[PositionLedgerEntry]:
        """포지션 청산 (부분 또는 전량)"""
        sell_qty = fill.filled_quantity
        sell_price = fill.fill_price

        # 실현 손익 계산
        pnl = (sell_price - position.avg_entry_price) * sell_qty
        position.realized_pnl += pnl
        position.total_fees += fill.fee

        # 수량 차감
        position.quantity -= sell_qty
        position.last_fill_time = fill.timestamp
        position.fill_count += 1

        # 체결 히스토리 추가
        self._add_fill_history(fill)

        if position.quantity <= 0.0001:  # 소수점 오차 허용
            # 전량 청산
            position.quantity = 0
            position.status = "CLOSED"

            # 인덱스에서 제거
            self._remove_from_index(position)

            logger.info(
                "Position fully closed",
                position_id=position.position_id,
                symbol=fill.symbol,
                strategy=position.strategy_id,
                realized_pnl=position.realized_pnl,
                total_fees=position.total_fees,
                fill_count=position.fill_count,
            )

            # 데이터베이스에 저장
            asyncio.create_task(self._persist_position_to_db(position))

            return position
        else:
            # 부분 청산
            position.status = "PARTIAL_CLOSED"

            logger.info(
                "Position partially closed",
                position_id=position.position_id,
                symbol=fill.symbol,
                sold_qty=sell_qty,
                remaining_qty=position.quantity,
                pnl_this_close=pnl,
            )

            # 데이터베이스에 저장
            asyncio.create_task(self._persist_position_to_db(position))

            return position

    def _find_open_position(
        self, symbol: str, strategy_id: str
    ) -> Optional[PositionLedgerEntry]:
        """심볼+전략으로 오픈 포지션 찾기"""
        position_ids = self._symbol_positions.get(symbol, [])
        for pid in position_ids:
            pos = self._positions.get(pid)
            if pos and pos.is_open and pos.strategy_id == strategy_id:
                return pos
        return None

    def _add_to_index(self, position: PositionLedgerEntry) -> None:
        """인덱스에 추가"""
        symbol = position.symbol
        strategy = position.strategy_id
        pid = position.position_id

        if symbol not in self._symbol_positions:
            self._symbol_positions[symbol] = []
        if pid not in self._symbol_positions[symbol]:
            self._symbol_positions[symbol].append(pid)

        if strategy not in self._strategy_positions:
            self._strategy_positions[strategy] = []
        if pid not in self._strategy_positions[strategy]:
            self._strategy_positions[strategy].append(pid)

    def _remove_from_index(self, position: PositionLedgerEntry) -> None:
        """인덱스에서 제거"""
        symbol = position.symbol
        strategy = position.strategy_id
        pid = position.position_id

        if symbol in self._symbol_positions and pid in self._symbol_positions[symbol]:
            self._symbol_positions[symbol].remove(pid)

        if (
            strategy in self._strategy_positions
            and pid in self._strategy_positions[strategy]
        ):
            self._strategy_positions[strategy].remove(pid)

    def _add_fill_history(self, fill: FillEvent) -> None:
        """체결 히스토리 추가 (최근 100건 유지)"""
        self._fill_history.append(fill)
        if len(self._fill_history) > 100:
            self._fill_history = self._fill_history[-100:]

    def _calc_slippage_bps(
        self, requested_price: float, fill_price: float
    ) -> float:
        """슬리피지 계산 (bps)"""
        if requested_price <= 0:
            return 0.0
        return ((fill_price - requested_price) / requested_price) * 10000

    # === 조회 메서드 ===

    async def get_position(self, position_id: str) -> Optional[PositionLedgerEntry]:
        """position_id로 포지션 조회"""
        return self._positions.get(position_id)

    async def get_position_by_symbol(
        self, symbol: str, strategy_id: Optional[str] = None
    ) -> Optional[PositionLedgerEntry]:
        """심볼로 오픈 포지션 조회"""
        async with self._lock:
            position_ids = self._symbol_positions.get(symbol, [])
            for pid in position_ids:
                pos = self._positions.get(pid)
                if pos and pos.is_open:
                    if strategy_id is None or pos.strategy_id == strategy_id:
                        return pos
            return None

    async def get_positions_by_symbol(self, symbol: str) -> list[PositionLedgerEntry]:
        """심볼의 모든 오픈 포지션 조회"""
        result = []
        position_ids = self._symbol_positions.get(symbol, [])
        for pid in position_ids:
            pos = self._positions.get(pid)
            if pos and pos.is_open:
                result.append(pos)
        return result

    async def get_positions_by_strategy(
        self, strategy_id: str
    ) -> list[PositionLedgerEntry]:
        """전략의 모든 오픈 포지션 조회"""
        result = []
        position_ids = self._strategy_positions.get(strategy_id, [])
        for pid in position_ids:
            pos = self._positions.get(pid)
            if pos and pos.is_open:
                result.append(pos)
        return result

    async def get_open_positions(self) -> list[PositionLedgerEntry]:
        """모든 오픈 포지션 조회"""
        open_positions = [pos for pos in self._positions.values() if pos.is_open]
        logger.debug(
            "get_open_positions called",
            total_positions=len(self._positions),
            open_positions=len(open_positions),
            symbols=[p.symbol for p in open_positions],
            strategies=[p.strategy_id for p in open_positions],
        )
        return open_positions

    async def get_all_positions(self) -> list[PositionLedgerEntry]:
        """모든 포지션 조회 (청산 포함)"""
        return list(self._positions.values())

    async def get_open_position_count(self) -> int:
        """오픈 포지션 수"""
        return len([p for p in self._positions.values() if p.is_open])

    async def get_total_exposure(self) -> float:
        """총 노출 금액 (KRW)"""
        total = 0.0
        for pos in self._positions.values():
            if pos.is_open:
                total += pos.notional_value
        return total

    async def get_symbol_exposure(self, symbol: str) -> float:
        """심볼별 노출 금액"""
        total = 0.0
        position_ids = self._symbol_positions.get(symbol, [])
        for pid in position_ids:
            pos = self._positions.get(pid)
            if pos and pos.is_open:
                total += pos.notional_value
        return total

    async def get_strategy_exposure(self, strategy_id: str) -> float:
        """전략별 노출 금액"""
        total = 0.0
        position_ids = self._strategy_positions.get(strategy_id, [])
        for pid in position_ids:
            pos = self._positions.get(pid)
            if pos and pos.is_open:
                total += pos.notional_value
        return total

    # === Reconcile ===

    async def reconcile_with_exchange(
        self, exchange_balances: dict[str, float]
    ) -> ReconcileResult:
        """
        거래소 잔고와 원장 동기화

        Args:
            exchange_balances: {symbol: quantity} 형태의 거래소 잔고

        Returns:
            ReconcileResult
        """
        async with self._lock:
            discrepancies = []
            actions = []

            # 1. 원장 포지션 vs 거래소 잔고 비교
            for pos in self._positions.values():
                if not pos.is_open:
                    continue

                symbol = pos.symbol
                ledger_qty = pos.quantity
                exchange_qty = exchange_balances.get(symbol, 0.0)

                # 허용 오차: 0.1% 또는 절대값 0.0001
                tolerance = max(ledger_qty * 0.001, 0.0001)

                if abs(ledger_qty - exchange_qty) > tolerance:
                    discrepancies.append(
                        {
                            "symbol": symbol,
                            "position_id": pos.position_id,
                            "strategy": pos.strategy_id,
                            "ledger_qty": ledger_qty,
                            "exchange_qty": exchange_qty,
                            "diff": exchange_qty - ledger_qty,
                            "diff_pct": (
                                ((exchange_qty - ledger_qty) / ledger_qty * 100)
                                if ledger_qty > 0
                                else 0
                            ),
                        }
                    )

                    logger.warning(
                        "Position quantity mismatch",
                        symbol=symbol,
                        ledger_qty=ledger_qty,
                        exchange_qty=exchange_qty,
                        diff=exchange_qty - ledger_qty,
                    )

            # 2. 거래소에만 있는 잔고 (원장에 없음)
            ledger_symbols = {
                pos.symbol for pos in self._positions.values() if pos.is_open
            }
            for symbol, qty in exchange_balances.items():
                if qty > 0.0001 and symbol not in ledger_symbols:
                    discrepancies.append(
                        {
                            "symbol": symbol,
                            "position_id": None,
                            "strategy": "UNKNOWN",
                            "ledger_qty": 0,
                            "exchange_qty": qty,
                            "diff": qty,
                            "type": "orphan_balance",
                        }
                    )

                    logger.warning(
                        "Orphan balance found (not in ledger)",
                        symbol=symbol,
                        exchange_qty=qty,
                    )

            is_matched = len(discrepancies) == 0

            if is_matched:
                logger.info("Reconcile completed: all positions matched")
            else:
                logger.warning(
                    "Reconcile completed with discrepancies",
                    count=len(discrepancies),
                )

            return ReconcileResult(
                is_matched=is_matched,
                discrepancies=discrepancies,
                actions_taken=actions,
            )

    async def force_sync_quantity(
        self, symbol: str, strategy_id: str, exchange_qty: float
    ) -> Optional[PositionLedgerEntry]:
        """
        강제 수량 동기화 (reconcile 불일치 해결용)

        주의: 이 메서드는 원장 정합성을 깨뜨릴 수 있음.
        reconcile에서 불일치 발견 시에만 사용.
        """
        async with self._lock:
            position = self._find_open_position(symbol, strategy_id)
            if not position:
                return None

            old_qty = position.quantity
            position.quantity = exchange_qty

            if exchange_qty <= 0.0001:
                position.status = "CLOSED"
                self._remove_from_index(position)

            logger.warning(
                "Force sync quantity",
                symbol=symbol,
                old_qty=old_qty,
                new_qty=exchange_qty,
                position_id=position.position_id,
            )

            return position

    # === 스탑/익절 업데이트 ===

    async def update_stop_price(
        self, position_id: str, stop_price: float
    ) -> Optional[PositionLedgerEntry]:
        """스탑 가격 업데이트"""
        async with self._lock:
            position = self._positions.get(position_id)
            if position:
                position.stop_price = stop_price
                return position
            return None

    async def update_take_profit_price(
        self, position_id: str, tp_price: float
    ) -> Optional[PositionLedgerEntry]:
        """익절 가격 업데이트"""
        async with self._lock:
            position = self._positions.get(position_id)
            if position:
                position.take_profit_price = tp_price
                return position
            return None

    # === 요약/통계 ===

    def get_summary(self) -> dict:
        """원장 요약 정보"""
        open_positions = [p for p in self._positions.values() if p.is_open]
        closed_positions = [p for p in self._positions.values() if not p.is_open]

        total_exposure = sum(p.notional_value for p in open_positions)
        total_realized_pnl = sum(p.realized_pnl for p in self._positions.values())
        total_fees = sum(p.total_fees for p in self._positions.values())

        return {
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "total_exposure_krw": total_exposure,
            "total_realized_pnl": total_realized_pnl,
            "total_fees": total_fees,
            "net_pnl": total_realized_pnl - total_fees,
            "positions": [p.to_dict() for p in open_positions],
        }

    def get_fill_history(self, limit: int = 50) -> list[dict]:
        """최근 체결 히스토리"""
        fills = self._fill_history[-limit:]
        return [
            {
                "order_id": f.order_id,
                "symbol": f.symbol,
                "strategy": f.strategy_id,
                "side": f.side,
                "quantity": f.filled_quantity,
                "price": f.fill_price,
                "fee": f.fee,
                "timestamp": f.timestamp.isoformat(),
                "slippage_bps": self._calc_slippage_bps(f.requested_price, f.fill_price),
            }
            for f in reversed(fills)
        ]
