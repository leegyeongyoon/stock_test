"""Core 전략 - 현물-무기한선물 캐시앤캐리 (시장중립) - 강화 버전"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.models.schemas import OrderSide, StrategyType
from src.strategies.base import BaseStrategy, Signal, SignalType

logger = structlog.get_logger()
settings = get_settings()


class TrancheStatus(str, Enum):
    """트랜치 상태"""

    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


@dataclass
class Tranche:
    """분할 진입 트랜치"""

    tranche_id: int  # 1, 2, 3
    percentage: float  # 0.40, 0.30, 0.30
    status: TrancheStatus = TrancheStatus.PENDING
    scheduled_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    quantity: float = 0
    price: float = 0


@dataclass
class CarryOpportunity:
    """캐리 기회"""

    symbol: str
    spot_price: float
    perp_price: float
    funding_rate: float
    basis_pct: float  # 기초 edge (spot - perp) / spot
    net_edge_pct: float  # 수수료/슬리피지 차감 후 실제 edge
    expected_daily_return: float
    timestamp: datetime

    # 분할 진입 트랜치
    tranches: list[Tranche] = field(default_factory=list)
    total_executed_quantity: float = 0


@dataclass
class EdgeCalculation:
    """Edge 계산 결과"""

    basis_pct: float  # 기초 basis
    fee_total: float  # 총 수수료
    slippage_buffer: float  # 슬리피지 버퍼
    safety_buffer: float  # 안전 버퍼
    net_edge_pct: float  # 순 edge
    is_profitable: bool  # 수익성 여부


class CoreCarryStrategy(BaseStrategy):
    """
    Core 캐시앤캐리 전략 (강화 버전)

    원리:
    - 현물 매수 + 선물 매도 (숏)
    - 펀딩비가 +일 때 롱 포지션에서 숏 포지션으로 지불
    - 시장중립 포지션으로 펀딩비 수익 추구

    개선사항:
    1. Edge 계산: 수수료/슬리피지/안전 버퍼 반영
    2. 분할 진입: 3트랜치 (40%/30%/30%, 5분 간격)
    3. 청산 조건 강화

    진입 조건:
    - net_edge_pct >= 0.20% (수수료 차감 후)
    - 펀딩비 > 0 (숏에게 유리)
    - 레짐 필터 통과

    청산 조건:
    - edge_pct <= 0.05% (수렴)
    - 펀딩비 누적 손실 > 1%
    - hedge_error > 0.5%
    """

    # 수수료 상수 (Binance Testnet 기준)
    MAKER_FEE = 0.0001  # 0.01%
    TAKER_FEE = 0.0004  # 0.04%

    def __init__(self) -> None:
        super().__init__(StrategyType.CORE)

        # 설정
        self.min_edge_pct = settings.core_min_edge_pct  # 0.20%
        self.max_position_usd = settings.core_max_position_usd
        self.fee_buffer = settings.core_fee_buffer  # 8bps
        self.slippage_buffer = settings.core_slippage_buffer  # 6bps
        self.safety_buffer = 0.0002  # 2bps

        # 분할 진입 설정
        self.split_entry_enabled = settings.core_split_entry
        self.tranche_percentages = [
            settings.core_tranche_1_pct,
            settings.core_tranche_2_pct,
            settings.core_tranche_3_pct,
        ]
        self.tranche_delay_sec = settings.core_tranche_delay_sec

        # 청산 임계값
        self.exit_edge_threshold = 0.0005  # 0.05% 이하면 청산
        self.max_funding_loss = -0.01  # 1% 누적 손실
        self.max_hedge_error = 0.005  # 0.5% hedge error

        # 상태
        self._active_carries: dict[str, CarryOpportunity] = {}
        self._cumulative_funding: dict[str, float] = {}
        self._pending_tranches: dict[str, list[Tranche]] = {}

    def calculate_edge(
        self,
        spot_price: float,
        perp_price: float,
    ) -> EdgeCalculation:
        """
        실제 Edge 계산 (수수료/슬리피지 차감)

        공식:
        net_edge = basis_pct - fee_total - slippage_buffer - safety_buffer

        수수료 구조:
        - 진입: spot maker + perp taker
        - 청산: spot taker + perp maker
        - 합계: (maker * 2 + taker * 2)
        """
        # 기초 basis
        basis_pct = (spot_price - perp_price) / spot_price

        # 총 수수료 (진입 + 청산)
        fee_total = self.fee_buffer  # (maker * 2 + taker * 2) ≈ 10bps

        # 순 edge
        net_edge_pct = basis_pct - fee_total - self.slippage_buffer - self.safety_buffer

        return EdgeCalculation(
            basis_pct=basis_pct,
            fee_total=fee_total,
            slippage_buffer=self.slippage_buffer,
            safety_buffer=self.safety_buffer,
            net_edge_pct=net_edge_pct,
            is_profitable=net_edge_pct >= self.min_edge_pct,
        )

    async def generate_signal(self, market_data: dict) -> Optional[Signal]:
        """
        캐리 기회 탐색 및 진입 시그널 생성

        market_data:
            symbol: str
            spot_price: float
            perp_price: float
            funding_rate: float
            btc_regime: str (optional)
        """
        if not self._enabled:
            return None

        symbol = market_data.get("symbol")
        spot_price = market_data.get("spot_price", 0)
        perp_price = market_data.get("perp_price", 0)
        funding_rate = market_data.get("funding_rate", 0)
        btc_regime = market_data.get("btc_regime", "NEUTRAL")

        if spot_price <= 0 or perp_price <= 0:
            return None

        # 레짐 필터: VOLATILE에서는 신규 진입 금지
        if btc_regime == "VOLATILE":
            return None

        # Edge 계산 (수수료 차감)
        edge_calc = self.calculate_edge(spot_price, perp_price)

        # 진입 조건 체크
        # 1. net_edge가 충분한가?
        # 2. 펀딩비가 유리한가? (양수 = 숏에게 지불)
        if edge_calc.is_profitable and funding_rate > 0:
            logger.info(
                "Carry opportunity found",
                symbol=symbol,
                basis_pct=f"{edge_calc.basis_pct:.4%}",
                net_edge_pct=f"{edge_calc.net_edge_pct:.4%}",
                funding_rate=f"{funding_rate:.4%}",
            )

            # 분할 진입 트랜치 설정
            tranches = self._create_tranches()

            opportunity = CarryOpportunity(
                symbol=symbol,
                spot_price=spot_price,
                perp_price=perp_price,
                funding_rate=funding_rate,
                basis_pct=edge_calc.basis_pct,
                net_edge_pct=edge_calc.net_edge_pct,
                expected_daily_return=funding_rate * 3,
                timestamp=datetime.utcnow(),
                tranches=tranches,
            )

            self._active_carries[symbol] = opportunity
            self._pending_tranches[symbol] = tranches[1:]  # 2, 3차 트랜치

            # 1차 트랜치 즉시 실행 시그널
            return Signal(
                strategy=StrategyType.CORE,
                signal_type=SignalType.ENTRY,
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=0,  # get_position_size에서 계산
                reason=f"Carry T1: net_edge={edge_calc.net_edge_pct:.4%}, funding={funding_rate:.4%}",
                confidence=min(1.0, edge_calc.net_edge_pct / self.min_edge_pct),
                metadata={
                    "tranche": 1,
                    "tranche_pct": self.tranche_percentages[0],
                    "basis_pct": edge_calc.basis_pct,
                    "net_edge_pct": edge_calc.net_edge_pct,
                },
            )

        return None

    def _create_tranches(self) -> list[Tranche]:
        """분할 진입 트랜치 생성"""
        now = datetime.utcnow()
        tranches = []

        for i, pct in enumerate(self.tranche_percentages):
            tranche = Tranche(
                tranche_id=i + 1,
                percentage=pct,
                status=TrancheStatus.PENDING,
                scheduled_at=now + timedelta(seconds=self.tranche_delay_sec * i),
            )
            tranches.append(tranche)

        return tranches

    async def check_pending_tranches(self, market_data: dict) -> Optional[Signal]:
        """
        대기 중인 트랜치 실행 체크

        각 트랜치마다 조건 재확인:
        - net_edge >= min_edge
        - funding_rate 유리
        """
        symbol = market_data.get("symbol")
        pending = self._pending_tranches.get(symbol, [])

        if not pending:
            return None

        now = datetime.utcnow()
        spot_price = market_data.get("spot_price", 0)
        perp_price = market_data.get("perp_price", 0)
        funding_rate = market_data.get("funding_rate", 0)

        for tranche in pending:
            if tranche.status != TrancheStatus.PENDING:
                continue

            if tranche.scheduled_at and now < tranche.scheduled_at:
                continue

            # 조건 재확인
            edge_calc = self.calculate_edge(spot_price, perp_price)

            if not edge_calc.is_profitable or funding_rate <= 0:
                tranche.status = TrancheStatus.CANCELLED
                logger.warning(
                    "Tranche cancelled - conditions not met",
                    symbol=symbol,
                    tranche=tranche.tranche_id,
                    net_edge=f"{edge_calc.net_edge_pct:.4%}",
                )
                continue

            # 트랜치 실행 시그널
            tranche.status = TrancheStatus.EXECUTED
            tranche.executed_at = now

            logger.info(
                "Tranche execution triggered",
                symbol=symbol,
                tranche=tranche.tranche_id,
                percentage=tranche.percentage,
            )

            return Signal(
                strategy=StrategyType.CORE,
                signal_type=SignalType.ENTRY,
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=0,
                reason=f"Carry T{tranche.tranche_id}: net_edge={edge_calc.net_edge_pct:.4%}",
                confidence=min(1.0, edge_calc.net_edge_pct / self.min_edge_pct),
                metadata={
                    "tranche": tranche.tranche_id,
                    "tranche_pct": tranche.percentage,
                    "basis_pct": edge_calc.basis_pct,
                    "net_edge_pct": edge_calc.net_edge_pct,
                },
            )

        # 완료되지 않은 트랜치 정리
        self._pending_tranches[symbol] = [
            t for t in pending if t.status == TrancheStatus.PENDING
        ]

        return None

    async def should_exit(self, position: dict, market_data: dict) -> Optional[Signal]:
        """
        청산 조건 확인 (강화)

        조건:
        1. edge_pct <= 0.05% (수렴)
        2. 펀딩비 누적 손실 > 1%
        3. hedge_error > 0.5%
        4. margin_ratio 악화
        """
        symbol = position.get("symbol")
        entry_edge = position.get("entry_edge", 0)

        spot_price = market_data.get("spot_price", 0)
        perp_price = market_data.get("perp_price", 0)
        funding_rate = market_data.get("funding_rate", 0)
        margin_ratio = market_data.get("margin_ratio", 1.0)

        if spot_price <= 0 or perp_price <= 0:
            return None

        current_edge = (spot_price - perp_price) / spot_price

        # 1. Edge 수렴/역전
        if current_edge <= self.exit_edge_threshold:
            logger.warning(
                "Carry edge converged",
                symbol=symbol,
                entry_edge=f"{entry_edge:.4%}",
                current_edge=f"{current_edge:.4%}",
            )

            return Signal(
                strategy=StrategyType.CORE,
                signal_type=SignalType.EXIT,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=position.get("quantity", 0),
                reason=f"Edge converged: {current_edge:.4%} <= {self.exit_edge_threshold:.4%}",
            )

        # 2. 펀딩비 누적 손실
        cumulative = self._cumulative_funding.get(symbol, 0)
        if cumulative < self.max_funding_loss:
            logger.warning(
                "Cumulative funding loss exceeded",
                symbol=symbol,
                cumulative=f"{cumulative:.4%}",
            )

            return Signal(
                strategy=StrategyType.CORE,
                signal_type=SignalType.EXIT,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=position.get("quantity", 0),
                reason=f"Funding loss: {cumulative:.4%}",
            )

        # 3. Hedge error (spot과 perp 수량 불일치)
        spot_qty = position.get("spot_quantity", 0)
        perp_qty = position.get("perp_quantity", 0)
        max_qty = max(spot_qty, perp_qty, 1)
        hedge_error = abs(spot_qty - perp_qty) / max_qty

        if hedge_error > self.max_hedge_error:
            logger.warning(
                "Hedge error exceeded",
                symbol=symbol,
                spot_qty=spot_qty,
                perp_qty=perp_qty,
                hedge_error=f"{hedge_error:.4%}",
            )

            # 리밸런싱 시그널
            diff = spot_qty - perp_qty
            return Signal(
                strategy=StrategyType.CORE,
                signal_type=SignalType.REBALANCE,
                symbol=symbol,
                side=OrderSide.SELL if diff > 0 else OrderSide.BUY,
                quantity=abs(diff),
                reason=f"Hedge rebalance: error={hedge_error:.4%}",
            )

        # 4. Margin ratio 악화 (선물 쪽)
        if margin_ratio < 0.1:  # 10% 미만
            logger.warning(
                "Margin ratio critical",
                symbol=symbol,
                margin_ratio=f"{margin_ratio:.2%}",
            )

            return Signal(
                strategy=StrategyType.CORE,
                signal_type=SignalType.EXIT,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=position.get("quantity", 0),
                reason=f"Margin ratio critical: {margin_ratio:.2%}",
            )

        return None

    def get_position_size(
        self,
        signal: Signal,
        available_capital: float,
        tranche_pct: Optional[float] = None,
    ) -> float:
        """
        포지션 사이즈 계산 (트랜치 반영)

        Args:
            signal: 시그널
            available_capital: 가용 자본
            tranche_pct: 트랜치 비율 (없으면 signal.metadata에서)
        """
        # 최대 포지션 제한
        max_position = min(self.max_position_usd, available_capital * 0.5)

        opportunity = self._active_carries.get(signal.symbol)
        if not opportunity:
            return 0

        # 트랜치 비율 적용
        if tranche_pct is None:
            tranche_pct = signal.metadata.get("tranche_pct", 1.0) if signal.metadata else 1.0

        # 분할 진입이 비활성화면 100%
        if not self.split_entry_enabled:
            tranche_pct = 1.0

        # Edge 기반 사이즈 조절
        edge_multiplier = min(2.0, opportunity.net_edge_pct / self.min_edge_pct)
        position_usd = max_position * edge_multiplier * 0.5 * tranche_pct

        # 수량 계산
        return position_usd / opportunity.spot_price

    def update_funding(self, symbol: str, funding_pnl: float) -> None:
        """펀딩비 업데이트"""
        current = self._cumulative_funding.get(symbol, 0)
        self._cumulative_funding[symbol] = current + funding_pnl

    def reset_carry(self, symbol: str) -> None:
        """캐리 상태 초기화"""
        self._active_carries.pop(symbol, None)
        self._cumulative_funding.pop(symbol, None)
        self._pending_tranches.pop(symbol, None)

    def get_active_carry(self, symbol: str) -> Optional[CarryOpportunity]:
        """활성 캐리 기회 조회"""
        return self._active_carries.get(symbol)

    def get_pending_tranches(self, symbol: str) -> list[Tranche]:
        """대기 중인 트랜치 조회"""
        return self._pending_tranches.get(symbol, [])

    def get_status(self) -> dict:
        """전략 상태 조회"""
        return {
            "enabled": self._enabled,
            "min_edge_pct": self.min_edge_pct,
            "split_entry_enabled": self.split_entry_enabled,
            "active_carries": {
                symbol: {
                    "basis_pct": opp.basis_pct,
                    "net_edge_pct": opp.net_edge_pct,
                    "funding_rate": opp.funding_rate,
                    "tranches_executed": sum(
                        1 for t in opp.tranches if t.status == TrancheStatus.EXECUTED
                    ),
                }
                for symbol, opp in self._active_carries.items()
            },
            "cumulative_funding": dict(self._cumulative_funding),
        }
