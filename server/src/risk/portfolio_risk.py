"""Portfolio Risk Manager

총 포트폴리오 리스크 관리:
- 최대 동시 포지션 제한 (5개)
- 트레이드당 리스크 제한 (0.30% 기본, 최대 0.50%)
- 총 리스크 한도 (정상 1.5%, 위험 0.8%)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import structlog

from src.risk.config import get_risk_config

logger = structlog.get_logger()


class MarketCondition(str, Enum):
    """시장 상태"""

    NORMAL = "NORMAL"  # 정상 시장
    RISKY = "RISKY"  # 위험 시장 (RISK_OFF, corr_shock 등)


@dataclass
class PositionRisk:
    """개별 포지션 리스크"""

    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    current_price: float
    quantity: float
    stop_price: float
    risk_amount: float  # 손절 시 손실 금액
    risk_pct: float  # 자본 대비 리스크 %
    strategy: str = "UNKNOWN"
    opened_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "quantity": self.quantity,
            "stop_price": self.stop_price,
            "risk_amount": self.risk_amount,
            "risk_pct": self.risk_pct,
            "strategy": self.strategy,
            "opened_at": self.opened_at.isoformat(),
        }


@dataclass
class PortfolioRiskState:
    """포트폴리오 리스크 상태"""

    # 포지션 수
    open_positions: int = 0
    max_positions: int = 5

    # 리스크
    total_risk_pct: float = 0.0
    total_risk_limit: float = 0.015  # 1.5%
    available_risk_pct: float = 0.015

    # 시장 상태
    market_condition: MarketCondition = MarketCondition.NORMAL

    # 진입 가능 여부
    can_open_new: bool = True
    block_reason: str = ""

    # 각 포지션별 리스크
    position_risks: list[PositionRisk] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "open_positions": self.open_positions,
            "max_positions": self.max_positions,
            "total_risk_pct": round(self.total_risk_pct * 100, 3),
            "total_risk_limit": round(self.total_risk_limit * 100, 3),
            "available_risk_pct": round(self.available_risk_pct * 100, 3),
            "market_condition": self.market_condition.value,
            "can_open_new": self.can_open_new,
            "block_reason": self.block_reason,
            "positions": [p.to_dict() for p in self.position_risks],
        }


class PortfolioRiskManager:
    """
    포트폴리오 리스크 관리자

    책임:
    1. 동시 포지션 수 제한 (max 5)
    2. 트레이드당 리스크 계산 및 제한
    3. 총 리스크 한도 관리
    4. 시장 상태에 따른 리스크 한도 조정
    """

    def __init__(self):
        self.config = get_risk_config()

        # 포지션 리스크 추적 (symbol -> PositionRisk)
        self._position_risks: dict[str, PositionRisk] = {}

        # 시장 상태
        self._market_condition = MarketCondition.NORMAL

        # 자본금 (외부에서 업데이트)
        self._equity: float = 0.0

    def update_equity(self, equity: float) -> None:
        """자본금 업데이트"""
        self._equity = equity

    def set_market_condition(self, condition: MarketCondition) -> None:
        """시장 상태 설정"""
        prev = self._market_condition
        self._market_condition = condition

        if prev != condition:
            logger.info(
                "Portfolio risk: Market condition changed",
                prev=prev.value,
                new=condition.value,
            )

    def get_risk_limit(self) -> float:
        """현재 시장 상태에 따른 총 리스크 한도"""
        if self._market_condition == MarketCondition.RISKY:
            return self.config.total_risk_limit_risky
        return self.config.total_risk_limit_normal

    def add_position_risk(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        stop_price: float,
        strategy: str = "UNKNOWN",
    ) -> Optional[PositionRisk]:
        """
        포지션 리스크 추가

        Args:
            symbol: 심볼
            side: "LONG" or "SHORT"
            entry_price: 진입가
            quantity: 수량
            stop_price: 손절가
            strategy: 전략명

        Returns:
            PositionRisk or None (한도 초과 시)
        """
        if self._equity <= 0:
            logger.warning("Portfolio risk: Equity not set")
            return None

        # 리스크 계산
        if side == "LONG":
            risk_per_unit = entry_price - stop_price
        else:
            risk_per_unit = stop_price - entry_price

        risk_amount = abs(risk_per_unit * quantity)
        risk_pct = risk_amount / self._equity

        position_risk = PositionRisk(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            current_price=entry_price,
            quantity=quantity,
            stop_price=stop_price,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            strategy=strategy,
        )

        self._position_risks[symbol] = position_risk

        logger.info(
            "Portfolio risk: Position added",
            symbol=symbol,
            risk_pct=f"{risk_pct*100:.3f}%",
            total_positions=len(self._position_risks),
        )

        return position_risk

    def remove_position_risk(self, symbol: str) -> None:
        """포지션 리스크 제거"""
        if symbol in self._position_risks:
            del self._position_risks[symbol]
            logger.info(
                "Portfolio risk: Position removed",
                symbol=symbol,
                remaining=len(self._position_risks),
            )

    def update_position_price(self, symbol: str, current_price: float) -> None:
        """포지션 현재가 업데이트"""
        if symbol in self._position_risks:
            self._position_risks[symbol].current_price = current_price

    def get_total_risk_pct(self) -> float:
        """총 리스크 비율 계산"""
        return sum(p.risk_pct for p in self._position_risks.values())

    def get_available_risk_pct(self) -> float:
        """사용 가능한 리스크 비율"""
        limit = self.get_risk_limit()
        used = self.get_total_risk_pct()
        return max(0, limit - used)

    def calc_position_size(
        self,
        entry_price: float,
        stop_price: float,
        risk_pct: Optional[float] = None,
    ) -> tuple[float, float]:
        """
        리스크 기반 포지션 사이즈 계산

        Args:
            entry_price: 진입가
            stop_price: 손절가
            risk_pct: 사용할 리스크 % (기본: config.risk_per_trade)

        Returns:
            tuple[quantity, actual_risk_pct]
        """
        if self._equity <= 0:
            return (0.0, 0.0)

        if risk_pct is None:
            risk_pct = self.config.risk_per_trade

        # 최대 리스크 제한
        risk_pct = min(risk_pct, self.config.max_risk_per_trade)

        # 사용 가능한 리스크 제한
        available = self.get_available_risk_pct()
        risk_pct = min(risk_pct, available)

        if risk_pct <= 0:
            return (0.0, 0.0)

        # 손절 거리
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return (0.0, 0.0)

        # 리스크 금액
        risk_amount = self._equity * risk_pct

        # 수량 계산
        quantity = risk_amount / stop_distance

        return (quantity, risk_pct)

    def can_open_position(self, symbol: str) -> tuple[bool, str]:
        """
        신규 포지션 오픈 가능 여부

        Returns:
            tuple[bool, str]: (가능 여부, 사유)
        """
        # 1. 이미 동일 심볼 보유 중
        if symbol in self._position_risks:
            return (False, f"Already holding position in {symbol}")

        # 2. 최대 포지션 수 제한
        if len(self._position_risks) >= self.config.max_concurrent_positions:
            return (
                False,
                f"Max positions reached: {len(self._position_risks)}/{self.config.max_concurrent_positions}",
            )

        # 3. 총 리스크 한도
        available = self.get_available_risk_pct()
        min_required = self.config.risk_per_trade * 0.5  # 최소 절반이라도 필요

        if available < min_required:
            return (
                False,
                f"Insufficient risk budget: {available*100:.2f}% < {min_required*100:.2f}%",
            )

        return (True, "OK")

    def get_state(self) -> PortfolioRiskState:
        """현재 포트폴리오 리스크 상태"""
        can_open, reason = self.can_open_position("_CHECK_")
        # _CHECK_는 실제 심볼이 아니므로 이미 보유 체크 제외
        if reason.startswith("Already"):
            can_open, reason = True, "OK"

        return PortfolioRiskState(
            open_positions=len(self._position_risks),
            max_positions=self.config.max_concurrent_positions,
            total_risk_pct=self.get_total_risk_pct(),
            total_risk_limit=self.get_risk_limit(),
            available_risk_pct=self.get_available_risk_pct(),
            market_condition=self._market_condition,
            can_open_new=can_open,
            block_reason=reason if not can_open else "",
            position_risks=list(self._position_risks.values()),
        )

    def get_positions_to_reduce(self, reduce_pct: float) -> list[tuple[str, float]]:
        """
        축소할 포지션 목록 반환

        Args:
            reduce_pct: 축소 비율 (0.0~1.0)

        Returns:
            list of (symbol, reduce_quantity)
        """
        result = []
        for symbol, pos in self._position_risks.items():
            reduce_qty = pos.quantity * reduce_pct
            if reduce_qty > 0:
                result.append((symbol, reduce_qty))
        return result


# Singleton
_portfolio_risk: PortfolioRiskManager = None


def get_portfolio_risk_manager() -> PortfolioRiskManager:
    """PortfolioRiskManager 싱글톤"""
    global _portfolio_risk
    if _portfolio_risk is None:
        _portfolio_risk = PortfolioRiskManager()
    return _portfolio_risk
