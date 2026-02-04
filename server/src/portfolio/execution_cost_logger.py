"""ExecutionCostLogger - 체결비용 실측 로깅

체결비용(fee + slippage)을 숫자로 기록하여 분석 가능하게 함.

기록 항목:
- order_id, type, requested_price, filled_price, filled_qty, timestamp
- fee_krw, slippage_bps, spread_bps_at_entry/exit
- 포지션 원장 스냅샷 (진입 전/후, 청산 전/후)
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.portfolio.position_ledger import FillEvent, PositionLedgerEntry

logger = structlog.get_logger()


@dataclass
class ExecutionCostRecord:
    """체결비용 기록"""

    order_id: str
    order_type: str  # "ENTRY", "EXIT", "PARTIAL_EXIT", "ADD"
    symbol: str
    strategy_id: str
    side: str  # "BUY" or "SELL"

    # 가격 정보
    requested_price: float  # 주문 시점 기대 가격
    filled_price: float  # 실제 체결 가격
    filled_qty: float
    timestamp: datetime

    # 비용 정보
    fee_krw: float
    slippage_bps: float  # (filled - requested) / requested * 10000
    spread_bps_at_fill: float  # 체결 시점 스프레드

    # 포지션 스냅샷
    position_qty_before: float
    position_qty_after: float
    position_avg_price_before: float
    position_avg_price_after: float

    # 추가 정보
    notional_krw: float  # 체결 금액
    total_cost_krw: float  # 수수료 + 슬리피지 비용
    cost_pct: float  # 비용 비율 (%)

    def to_dict(self) -> dict:
        """딕셔너리로 변환"""
        return {
            "order_id": self.order_id,
            "order_type": self.order_type,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "side": self.side,
            "requested_price": self.requested_price,
            "filled_price": self.filled_price,
            "filled_qty": self.filled_qty,
            "timestamp": self.timestamp.isoformat(),
            "fee_krw": self.fee_krw,
            "slippage_bps": self.slippage_bps,
            "spread_bps_at_fill": self.spread_bps_at_fill,
            "position_qty_before": self.position_qty_before,
            "position_qty_after": self.position_qty_after,
            "position_avg_price_before": self.position_avg_price_before,
            "position_avg_price_after": self.position_avg_price_after,
            "notional_krw": self.notional_krw,
            "total_cost_krw": self.total_cost_krw,
            "cost_pct": self.cost_pct,
        }


@dataclass
class DailyCostSummary:
    """일별 체결비용 요약"""

    date: str  # YYYY-MM-DD
    total_trades: int = 0
    total_notional: float = 0.0
    total_fees: float = 0.0
    total_slippage_cost: float = 0.0
    avg_slippage_bps: float = 0.0
    avg_spread_bps: float = 0.0
    max_slippage_bps: float = 0.0
    entry_count: int = 0
    exit_count: int = 0


class ExecutionCostLogger:
    """
    체결비용 로거

    책임:
    1. 모든 체결의 비용(수수료, 슬리피지, 스프레드) 기록
    2. 일별/심볼별/전략별 비용 통계
    3. 비용 분석 리포트 생성
    """

    def __init__(self, max_records: int = 1000) -> None:
        self._records: list[ExecutionCostRecord] = []
        self._max_records = max_records
        self._lock = asyncio.Lock()

        # 일별 요약 캐시
        self._daily_summaries: dict[str, DailyCostSummary] = {}

        # 심볼별 평균 슬리피지 캐시
        self._symbol_avg_slippage: dict[str, list[float]] = {}

    async def log_entry(
        self,
        fill_event: "FillEvent",
        position_before: Optional["PositionLedgerEntry"],
        position_after: "PositionLedgerEntry",
    ) -> ExecutionCostRecord:
        """
        진입 체결 비용 기록

        Args:
            fill_event: 체결 이벤트
            position_before: 체결 전 포지션 (신규면 None)
            position_after: 체결 후 포지션
        """
        return await self._log_execution(
            fill_event=fill_event,
            order_type="ENTRY" if position_before is None else "ADD",
            position_before=position_before,
            position_after=position_after,
        )

    async def log_exit(
        self,
        fill_event: "FillEvent",
        position_before: "PositionLedgerEntry",
        position_after: Optional["PositionLedgerEntry"],
    ) -> ExecutionCostRecord:
        """
        청산 체결 비용 기록

        Args:
            fill_event: 체결 이벤트
            position_before: 체결 전 포지션
            position_after: 체결 후 포지션 (전량 청산이면 None 또는 qty=0)
        """
        is_full_exit = position_after is None or position_after.quantity <= 0
        return await self._log_execution(
            fill_event=fill_event,
            order_type="EXIT" if is_full_exit else "PARTIAL_EXIT",
            position_before=position_before,
            position_after=position_after,
        )

    async def _log_execution(
        self,
        fill_event: "FillEvent",
        order_type: str,
        position_before: Optional["PositionLedgerEntry"],
        position_after: Optional["PositionLedgerEntry"],
    ) -> ExecutionCostRecord:
        """체결 비용 기록 내부 메서드"""

        # 슬리피지 계산 (bps)
        slippage_bps = 0.0
        if fill_event.requested_price > 0:
            slippage_bps = (
                (fill_event.fill_price - fill_event.requested_price)
                / fill_event.requested_price
                * 10000
            )
            # 매도 시 슬리피지 방향 반전 (낮게 팔리면 음수)
            if fill_event.side == "SELL":
                slippage_bps = -slippage_bps

        # 체결 금액
        notional_krw = fill_event.filled_quantity * fill_event.fill_price

        # 슬리피지 비용 (KRW)
        slippage_cost = abs(slippage_bps / 10000) * notional_krw

        # 총 비용
        total_cost = fill_event.fee + slippage_cost

        # 비용 비율 (%)
        cost_pct = (total_cost / notional_krw * 100) if notional_krw > 0 else 0

        # 포지션 스냅샷
        qty_before = position_before.quantity if position_before else 0
        qty_after = position_after.quantity if position_after else 0
        avg_before = position_before.avg_entry_price if position_before else 0
        avg_after = position_after.avg_entry_price if position_after else 0

        record = ExecutionCostRecord(
            order_id=fill_event.order_id,
            order_type=order_type,
            symbol=fill_event.symbol,
            strategy_id=fill_event.strategy_id,
            side=fill_event.side,
            requested_price=fill_event.requested_price,
            filled_price=fill_event.fill_price,
            filled_qty=fill_event.filled_quantity,
            timestamp=fill_event.timestamp,
            fee_krw=fill_event.fee,
            slippage_bps=slippage_bps,
            spread_bps_at_fill=fill_event.spread_bps_at_fill,
            position_qty_before=qty_before,
            position_qty_after=qty_after,
            position_avg_price_before=avg_before,
            position_avg_price_after=avg_after,
            notional_krw=notional_krw,
            total_cost_krw=total_cost,
            cost_pct=cost_pct,
        )

        async with self._lock:
            self._records.append(record)
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records:]

            # 심볼별 슬리피지 캐시 업데이트
            symbol = fill_event.symbol
            if symbol not in self._symbol_avg_slippage:
                self._symbol_avg_slippage[symbol] = []
            self._symbol_avg_slippage[symbol].append(abs(slippage_bps))
            # 최근 100개만 유지
            if len(self._symbol_avg_slippage[symbol]) > 100:
                self._symbol_avg_slippage[symbol] = self._symbol_avg_slippage[symbol][-100:]

            # 일별 요약 업데이트
            self._update_daily_summary(record)

        logger.info(
            "Execution cost logged",
            order_id=fill_event.order_id,
            symbol=fill_event.symbol,
            order_type=order_type,
            fee_krw=fill_event.fee,
            slippage_bps=round(slippage_bps, 2),
            total_cost_krw=round(total_cost, 2),
            cost_pct=round(cost_pct, 4),
        )

        return record

    def _update_daily_summary(self, record: ExecutionCostRecord) -> None:
        """일별 요약 업데이트"""
        today = record.timestamp.strftime("%Y-%m-%d")

        if today not in self._daily_summaries:
            self._daily_summaries[today] = DailyCostSummary(date=today)

        summary = self._daily_summaries[today]
        summary.total_trades += 1
        summary.total_notional += record.notional_krw
        summary.total_fees += record.fee_krw
        summary.total_slippage_cost += abs(record.slippage_bps / 10000) * record.notional_krw

        if record.order_type in ("ENTRY", "ADD"):
            summary.entry_count += 1
        else:
            summary.exit_count += 1

        # 평균 계산
        if summary.total_trades > 0:
            # 모든 기록에서 평균 계산 (간략화)
            recent_records = [r for r in self._records if r.timestamp.strftime("%Y-%m-%d") == today]
            if recent_records:
                summary.avg_slippage_bps = sum(abs(r.slippage_bps) for r in recent_records) / len(recent_records)
                summary.avg_spread_bps = sum(r.spread_bps_at_fill for r in recent_records) / len(recent_records)
                summary.max_slippage_bps = max(abs(r.slippage_bps) for r in recent_records)

    # === 조회 메서드 ===

    def get_avg_slippage_bps(self, symbol: str, lookback_count: int = 50) -> float:
        """심볼별 평균 슬리피지 (bps)"""
        if symbol not in self._symbol_avg_slippage:
            return 0.0

        recent = self._symbol_avg_slippage[symbol][-lookback_count:]
        if not recent:
            return 0.0

        return sum(recent) / len(recent)

    def get_recent_records(
        self,
        limit: int = 50,
        symbol: Optional[str] = None,
        strategy_id: Optional[str] = None,
    ) -> list[dict]:
        """최근 체결비용 기록 조회"""
        records = self._records

        if symbol:
            records = [r for r in records if r.symbol == symbol]

        if strategy_id:
            records = [r for r in records if r.strategy_id == strategy_id]

        return [r.to_dict() for r in records[-limit:]]

    def get_daily_summary(self, date_str: Optional[str] = None) -> Optional[DailyCostSummary]:
        """일별 요약 조회"""
        if date_str is None:
            date_str = date.today().isoformat()

        return self._daily_summaries.get(date_str)

    def get_cost_report(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """기간별 체결비용 리포트"""
        if start_date is None:
            start_date = date.today() - timedelta(days=7)
        if end_date is None:
            end_date = date.today()

        # 기간 내 기록 필터링
        filtered = [
            r for r in self._records
            if start_date <= r.timestamp.date() <= end_date
        ]

        if not filtered:
            return {
                "period": f"{start_date.isoformat()} ~ {end_date.isoformat()}",
                "total_trades": 0,
                "total_notional": 0,
                "total_fees": 0,
                "total_slippage_cost": 0,
                "avg_slippage_bps": 0,
                "avg_cost_pct": 0,
                "by_symbol": {},
                "by_strategy": {},
            }

        total_notional = sum(r.notional_krw for r in filtered)
        total_fees = sum(r.fee_krw for r in filtered)
        total_slippage_cost = sum(abs(r.slippage_bps / 10000) * r.notional_krw for r in filtered)

        # 심볼별 통계
        by_symbol = {}
        for r in filtered:
            if r.symbol not in by_symbol:
                by_symbol[r.symbol] = {
                    "trades": 0,
                    "notional": 0,
                    "fees": 0,
                    "avg_slippage_bps": 0,
                    "slippages": [],
                }
            by_symbol[r.symbol]["trades"] += 1
            by_symbol[r.symbol]["notional"] += r.notional_krw
            by_symbol[r.symbol]["fees"] += r.fee_krw
            by_symbol[r.symbol]["slippages"].append(abs(r.slippage_bps))

        for sym in by_symbol:
            slippages = by_symbol[sym]["slippages"]
            by_symbol[sym]["avg_slippage_bps"] = sum(slippages) / len(slippages) if slippages else 0
            del by_symbol[sym]["slippages"]

        # 전략별 통계
        by_strategy = {}
        for r in filtered:
            if r.strategy_id not in by_strategy:
                by_strategy[r.strategy_id] = {
                    "trades": 0,
                    "notional": 0,
                    "fees": 0,
                    "avg_slippage_bps": 0,
                    "slippages": [],
                }
            by_strategy[r.strategy_id]["trades"] += 1
            by_strategy[r.strategy_id]["notional"] += r.notional_krw
            by_strategy[r.strategy_id]["fees"] += r.fee_krw
            by_strategy[r.strategy_id]["slippages"].append(abs(r.slippage_bps))

        for strat in by_strategy:
            slippages = by_strategy[strat]["slippages"]
            by_strategy[strat]["avg_slippage_bps"] = sum(slippages) / len(slippages) if slippages else 0
            del by_strategy[strat]["slippages"]

        return {
            "period": f"{start_date.isoformat()} ~ {end_date.isoformat()}",
            "total_trades": len(filtered),
            "total_notional": total_notional,
            "total_fees": total_fees,
            "total_slippage_cost": total_slippage_cost,
            "total_cost": total_fees + total_slippage_cost,
            "avg_slippage_bps": sum(abs(r.slippage_bps) for r in filtered) / len(filtered),
            "avg_cost_pct": (total_fees + total_slippage_cost) / total_notional * 100 if total_notional > 0 else 0,
            "by_symbol": by_symbol,
            "by_strategy": by_strategy,
        }


# 싱글톤 인스턴스
_execution_cost_logger: Optional[ExecutionCostLogger] = None


def get_execution_cost_logger() -> ExecutionCostLogger:
    """ExecutionCostLogger 싱글톤 조회"""
    global _execution_cost_logger
    if _execution_cost_logger is None:
        _execution_cost_logger = ExecutionCostLogger()
    return _execution_cost_logger
