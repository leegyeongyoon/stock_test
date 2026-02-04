"""ExposureManager - 노출 한도 관리

"잔여 현금 0" 문제 해결을 위한 리스크/노출 기준 사이징.

핵심 원칙:
1. 잔여 현금이 아닌 "리스크/노출 한도" 기준으로 주문 가능 여부 판단
2. max_positions, max_total_exposure, max_symbol_exposure 등 하드 게이트
3. 최소 현금 보유량(min_cash_reserve) 유지
"""

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.portfolio.position_ledger import PositionLedger

logger = structlog.get_logger()


@dataclass
class ExposureConfig:
    """노출 한도 설정"""

    # 최대 동시 포지션 수
    max_positions: int = 5

    # 자본 대비 최대 총 노출 (%)
    max_total_exposure_pct: float = 0.70  # 70%

    # 자본 대비 심볼당 최대 노출 (%)
    max_symbol_exposure_pct: float = 0.15  # 15%

    # 자본 대비 전략당 최대 노출 (%)
    max_strategy_exposure_pct: float = 0.40  # 40%

    # 자본 대비 최소 현금 보유 (%)
    min_cash_reserve_pct: float = 0.20  # 20%

    # 자본 대비 단일 주문 최대 금액 (%)
    max_single_order_pct: float = 0.10  # 10%

    # 전략별 최대 포지션 수
    max_positions_per_strategy: dict[str, int] = None

    def __post_init__(self):
        if self.max_positions_per_strategy is None:
            self.max_positions_per_strategy = {
                "SATELLITE": 3,
                "ATTACK": 2,
                "IGNITION": 2,
                "SURGE": 2,
                "PULLBACK": 2,
            }


@dataclass
class ExposureCheckResult:
    """노출 체크 결과"""

    allowed: bool
    reason: str
    adjusted_amount: float  # 조정된 주문 금액 (0이면 불가)
    current_exposure: float  # 현재 총 노출
    available_exposure: float  # 가용 노출
    position_count: int  # 현재 포지션 수


class ExposureManager:
    """
    노출 한도 관리자

    책임:
    1. 신규 진입 가능 여부 체크
    2. 주문 금액 조정 (한도 초과 시 축소)
    3. 전략별/심볼별 노출 관리
    """

    def __init__(
        self,
        ledger: "PositionLedger",
        config: Optional[ExposureConfig] = None,
    ) -> None:
        self._ledger = ledger
        self._config = config or ExposureConfig()

    @property
    def config(self) -> ExposureConfig:
        """현재 설정"""
        return self._config

    def update_config(self, **kwargs) -> None:
        """설정 업데이트"""
        for key, value in kwargs.items():
            if hasattr(self._config, key):
                setattr(self._config, key, value)

    async def can_open_position(
        self,
        symbol: str,
        strategy_id: str,
        order_amount: float,
        total_equity: float,
        available_cash: float,
    ) -> ExposureCheckResult:
        """
        신규 포지션 오픈 가능 여부 체크

        Args:
            symbol: 거래 심볼
            strategy_id: 전략 ID
            order_amount: 주문 금액 (KRW)
            total_equity: 총 자산 (KRW)
            available_cash: 가용 현금 (KRW)

        Returns:
            ExposureCheckResult
        """
        cfg = self._config

        # 1. 최대 포지션 수 체크
        open_positions = await self._ledger.get_open_positions()
        position_count = len(open_positions)

        if position_count >= cfg.max_positions:
            logger.warning(
                "Max positions reached",
                current=position_count,
                max=cfg.max_positions,
            )
            return ExposureCheckResult(
                allowed=False,
                reason=f"Max positions reached: {position_count}/{cfg.max_positions}",
                adjusted_amount=0,
                current_exposure=await self._ledger.get_total_exposure(),
                available_exposure=0,
                position_count=position_count,
            )

        # 2. 전략별 최대 포지션 수 체크
        strategy_max = cfg.max_positions_per_strategy.get(strategy_id, 2)
        strategy_positions = await self._ledger.get_positions_by_strategy(strategy_id)
        strategy_position_count = len(strategy_positions)

        if strategy_position_count >= strategy_max:
            logger.warning(
                "Max strategy positions reached",
                strategy=strategy_id,
                current=strategy_position_count,
                max=strategy_max,
            )
            return ExposureCheckResult(
                allowed=False,
                reason=f"Max {strategy_id} positions: {strategy_position_count}/{strategy_max}",
                adjusted_amount=0,
                current_exposure=await self._ledger.get_total_exposure(),
                available_exposure=0,
                position_count=position_count,
            )

        # 3. 총 노출 체크
        current_exposure = await self._ledger.get_total_exposure()
        max_total_exposure = total_equity * cfg.max_total_exposure_pct

        if current_exposure >= max_total_exposure:
            logger.warning(
                "Max total exposure reached",
                current=current_exposure,
                max=max_total_exposure,
            )
            return ExposureCheckResult(
                allowed=False,
                reason=f"Max total exposure: ₩{current_exposure:,.0f}/₩{max_total_exposure:,.0f}",
                adjusted_amount=0,
                current_exposure=current_exposure,
                available_exposure=0,
                position_count=position_count,
            )

        # 4. 전략별 총 노출 체크
        strategy_exposure = await self._ledger.get_strategy_exposure(strategy_id)
        max_strategy_exposure = total_equity * cfg.max_strategy_exposure_pct

        if strategy_exposure >= max_strategy_exposure:
            logger.warning(
                "Max strategy exposure reached",
                strategy=strategy_id,
                current=strategy_exposure,
                max=max_strategy_exposure,
            )
            return ExposureCheckResult(
                allowed=False,
                reason=f"Max {strategy_id} exposure: ₩{strategy_exposure:,.0f}/₩{max_strategy_exposure:,.0f}",
                adjusted_amount=0,
                current_exposure=current_exposure,
                available_exposure=max_total_exposure - current_exposure,
                position_count=position_count,
            )

        # 5. 심볼별 노출 체크
        symbol_exposure = await self._ledger.get_symbol_exposure(symbol)
        max_symbol_exposure = total_equity * cfg.max_symbol_exposure_pct

        if symbol_exposure >= max_symbol_exposure:
            logger.warning(
                "Max symbol exposure reached",
                symbol=symbol,
                current=symbol_exposure,
                max=max_symbol_exposure,
            )
            return ExposureCheckResult(
                allowed=False,
                reason=f"Max {symbol} exposure: ₩{symbol_exposure:,.0f}/₩{max_symbol_exposure:,.0f}",
                adjusted_amount=0,
                current_exposure=current_exposure,
                available_exposure=max_total_exposure - current_exposure,
                position_count=position_count,
            )

        # 6. 가용 노출 계산
        available_for_total = max_total_exposure - current_exposure
        available_for_strategy = max_strategy_exposure - strategy_exposure
        available_for_symbol = max_symbol_exposure - symbol_exposure

        # 7. 최소 현금 보유량 체크
        min_cash = total_equity * cfg.min_cash_reserve_pct
        available_after_reserve = available_cash - min_cash

        if available_after_reserve <= 0:
            logger.warning(
                "Insufficient cash reserve",
                available=available_cash,
                min_reserve=min_cash,
            )
            return ExposureCheckResult(
                allowed=False,
                reason=f"Cash reserve required: ₩{min_cash:,.0f}, available: ₩{available_cash:,.0f}",
                adjusted_amount=0,
                current_exposure=current_exposure,
                available_exposure=available_for_total,
                position_count=position_count,
            )

        # 8. 단일 주문 최대 금액 체크
        max_single_order = total_equity * cfg.max_single_order_pct

        # 9. 조정된 주문 금액 계산
        adjusted_amount = min(
            order_amount,
            available_for_total,
            available_for_strategy,
            available_for_symbol,
            available_after_reserve,
            max_single_order,
        )

        # 최소 주문 금액 (5,000원) 미달 시 거부
        if adjusted_amount < 5000:
            return ExposureCheckResult(
                allowed=False,
                reason=f"Adjusted amount too small: ₩{adjusted_amount:,.0f} < ₩5,000",
                adjusted_amount=0,
                current_exposure=current_exposure,
                available_exposure=available_for_total,
                position_count=position_count,
            )

        # 조정 사유 결정
        adjustment_reason = "OK"
        if adjusted_amount < order_amount:
            reasons = []
            if order_amount > available_for_total:
                reasons.append("total exposure")
            if order_amount > available_for_strategy:
                reasons.append("strategy exposure")
            if order_amount > available_for_symbol:
                reasons.append("symbol exposure")
            if order_amount > available_after_reserve:
                reasons.append("cash reserve")
            if order_amount > max_single_order:
                reasons.append("single order limit")

            adjustment_reason = f"Adjusted for: {', '.join(reasons)}"
            logger.info(
                "Order amount adjusted",
                original=order_amount,
                adjusted=adjusted_amount,
                reasons=reasons,
            )

        return ExposureCheckResult(
            allowed=True,
            reason=adjustment_reason,
            adjusted_amount=adjusted_amount,
            current_exposure=current_exposure,
            available_exposure=available_for_total,
            position_count=position_count,
        )

    async def get_available_for_entry(
        self,
        strategy_id: str,
        total_equity: float,
        available_cash: float,
    ) -> float:
        """
        진입 가능한 최대 금액 계산 (심볼 무관)

        Returns:
            진입 가능한 최대 금액 (KRW)
        """
        cfg = self._config

        # 포지션 수 체크
        open_positions = await self._ledger.get_open_positions()
        if len(open_positions) >= cfg.max_positions:
            return 0

        # 전략 포지션 수 체크
        strategy_max = cfg.max_positions_per_strategy.get(strategy_id, 2)
        strategy_positions = await self._ledger.get_positions_by_strategy(strategy_id)
        if len(strategy_positions) >= strategy_max:
            return 0

        # 노출 계산
        current_exposure = await self._ledger.get_total_exposure()
        strategy_exposure = await self._ledger.get_strategy_exposure(strategy_id)

        max_total = total_equity * cfg.max_total_exposure_pct
        max_strategy = total_equity * cfg.max_strategy_exposure_pct
        min_cash = total_equity * cfg.min_cash_reserve_pct
        max_single = total_equity * cfg.max_single_order_pct

        available = min(
            max_total - current_exposure,
            max_strategy - strategy_exposure,
            available_cash - min_cash,
            max_single,
        )

        return max(0, available)

    async def get_exposure_summary(self, total_equity: float) -> dict:
        """노출 현황 요약"""
        cfg = self._config

        open_positions = await self._ledger.get_open_positions()
        current_exposure = await self._ledger.get_total_exposure()

        # 전략별 노출
        strategy_exposures = {}
        for strategy in ["SATELLITE", "ATTACK", "IGNITION", "SURGE", "PULLBACK"]:
            strategy_exposures[strategy] = {
                "exposure": await self._ledger.get_strategy_exposure(strategy),
                "positions": len(await self._ledger.get_positions_by_strategy(strategy)),
                "max_exposure": total_equity * cfg.max_strategy_exposure_pct,
                "max_positions": cfg.max_positions_per_strategy.get(strategy, 2),
            }

        return {
            "total_equity": total_equity,
            "current_exposure": current_exposure,
            "max_exposure": total_equity * cfg.max_total_exposure_pct,
            "exposure_pct": (current_exposure / total_equity * 100) if total_equity > 0 else 0,
            "position_count": len(open_positions),
            "max_positions": cfg.max_positions,
            "available_exposure": total_equity * cfg.max_total_exposure_pct - current_exposure,
            "min_cash_reserve": total_equity * cfg.min_cash_reserve_pct,
            "max_single_order": total_equity * cfg.max_single_order_pct,
            "strategy_exposures": strategy_exposures,
            "config": {
                "max_positions": cfg.max_positions,
                "max_total_exposure_pct": cfg.max_total_exposure_pct,
                "max_symbol_exposure_pct": cfg.max_symbol_exposure_pct,
                "max_strategy_exposure_pct": cfg.max_strategy_exposure_pct,
                "min_cash_reserve_pct": cfg.min_cash_reserve_pct,
                "max_single_order_pct": cfg.max_single_order_pct,
            },
        }


# 싱글톤 인스턴스
_exposure_manager: Optional[ExposureManager] = None


def get_exposure_manager() -> Optional[ExposureManager]:
    """ExposureManager 싱글톤 조회"""
    return _exposure_manager


def init_exposure_manager(
    ledger: "PositionLedger",
    config: Optional[ExposureConfig] = None,
) -> ExposureManager:
    """ExposureManager 초기화"""
    global _exposure_manager
    _exposure_manager = ExposureManager(ledger=ledger, config=config)
    logger.info(
        "ExposureManager initialized",
        max_positions=_exposure_manager.config.max_positions,
        max_total_exposure_pct=_exposure_manager.config.max_total_exposure_pct,
    )
    return _exposure_manager
