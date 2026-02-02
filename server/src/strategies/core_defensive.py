"""Defensive Core Strategy for Upbit (Spot-only)

업비트 Core 전략 - Cash & Carry 대체

핵심 개념:
- "현금이 헤지다" (Cash is Hedge)
- Futures 없이 현물만 거래 가능한 환경
- 현금 비중으로 리스크 조절
- MDD 5% 절대 방어

역할:
- 방패 (돈을 버는 엔진이 아님)
- Satellite가 삐끗했을 때 계좌를 살리는 버퍼

작동 방식:
1. 시장 레짐에 따라 현금 비중 조절
2. Risk-On: 현금 50%, 자산 50%
3. Neutral: 현금 70%, 자산 30%
4. Risk-Off: 현금 85%, 자산 15%
5. Volatile: 현금 90%, 자산 10%

리밸런싱:
- 30~60분 간격으로 실행
- 목표 비중과 5% 이상 괴리 시에만 실행
- 잦은 리밸런싱 방지 (슬리피지 절약)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Optional

import structlog

from src.config import get_settings
from src.models.schemas import OrderSide, StrategyType
from src.strategies.base import BaseStrategy, Signal, SignalType

if TYPE_CHECKING:
    from src.features.feature_engine import FeatureEngine

logger = structlog.get_logger()
settings = get_settings()


class MarketRegime(str, Enum):
    """시장 레짐"""

    RISK_ON = "RISK_ON"  # 강세 (적극 투자)
    NEUTRAL = "NEUTRAL"  # 중립
    RISK_OFF = "RISK_OFF"  # 약세 (방어적)
    VOLATILE = "VOLATILE"  # 급변장 (최대 방어)


@dataclass
class TargetAllocation:
    """목표 자산 배분"""

    cash_ratio: float  # 현금 비중 (0~1)
    asset_ratio: float  # 자산 비중 (0~1)
    regime: MarketRegime
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "cash_ratio": self.cash_ratio,
            "asset_ratio": self.asset_ratio,
            "regime": self.regime.value,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class RebalanceDecision:
    """리밸런싱 결정"""

    should_rebalance: bool
    direction: str  # "BUY", "SELL", "NONE"
    target_cash_ratio: float
    current_cash_ratio: float
    delta_pct: float  # 조정 필요량 (%)
    reason: str

    def to_dict(self) -> dict:
        return {
            "should_rebalance": self.should_rebalance,
            "direction": self.direction,
            "target_cash_ratio": self.target_cash_ratio,
            "current_cash_ratio": self.current_cash_ratio,
            "delta_pct": self.delta_pct,
            "reason": self.reason,
        }


class DefensiveCoreStrategy(BaseStrategy):
    """
    Defensive Core Strategy for Upbit

    업비트 전용 Core 전략:
    - 현물만 거래 (Futures 없음)
    - 현금 비중으로 리스크 조절
    - 시장 레짐에 따른 동적 자산 배분

    목표:
    - 원금 보전 (MDD 5% 방어)
    - Satellite 손실 버퍼
    - 안정적인 자산 성장
    """

    def __init__(self, feature_engine: Optional["FeatureEngine"] = None) -> None:
        super().__init__(StrategyType.CORE)

        self.feature_engine = feature_engine

        # 설정 로드
        self.cash_ratio_risk_on = settings.core_cash_ratio_risk_on  # 0.50
        self.cash_ratio_neutral = settings.core_cash_ratio_neutral  # 0.70
        self.cash_ratio_risk_off = settings.core_cash_ratio_risk_off  # 0.85
        self.cash_ratio_volatile = settings.core_cash_ratio_volatile  # 0.90

        self.rebalance_interval_min = settings.core_rebalance_interval_min  # 60분
        self.rebalance_threshold = settings.core_rebalance_threshold  # 0.05 (5%)

        # 상태
        self._current_regime: MarketRegime = MarketRegime.NEUTRAL
        self._last_rebalance: datetime = datetime.utcnow() - timedelta(hours=1)
        self._target_allocation: Optional[TargetAllocation] = None

    def set_feature_engine(self, feature_engine: "FeatureEngine") -> None:
        """Feature Engine 설정"""
        self.feature_engine = feature_engine

    def get_target_cash_ratio(self, regime: MarketRegime) -> float:
        """레짐별 목표 현금 비중"""
        ratios = {
            MarketRegime.RISK_ON: self.cash_ratio_risk_on,
            MarketRegime.NEUTRAL: self.cash_ratio_neutral,
            MarketRegime.RISK_OFF: self.cash_ratio_risk_off,
            MarketRegime.VOLATILE: self.cash_ratio_volatile,
        }
        return ratios.get(regime, self.cash_ratio_neutral)

    def update_regime(self, regime_str: str, is_volatile: bool = False) -> None:
        """시장 레짐 업데이트"""
        try:
            if is_volatile:
                new_regime = MarketRegime.VOLATILE
            elif regime_str.upper() == "BULLISH":
                new_regime = MarketRegime.RISK_ON
            elif regime_str.upper() == "BEARISH":
                new_regime = MarketRegime.RISK_OFF
            else:
                new_regime = MarketRegime.NEUTRAL
        except Exception:
            new_regime = MarketRegime.NEUTRAL

        old_regime = self._current_regime
        self._current_regime = new_regime

        if old_regime != new_regime:
            logger.info(
                "DefensiveCore regime changed",
                old=old_regime.value,
                new=new_regime.value,
            )

            # 목표 배분 업데이트
            target_cash = self.get_target_cash_ratio(new_regime)
            self._target_allocation = TargetAllocation(
                cash_ratio=target_cash,
                asset_ratio=1.0 - target_cash,
                regime=new_regime,
            )

    def _check_regime_from_feature_engine(self) -> None:
        """FeatureEngine에서 레짐 업데이트"""
        if self.feature_engine:
            btc_regime_info = self.feature_engine.check_btc_regime()
            self.update_regime(
                regime_str=btc_regime_info.get("regime", "NEUTRAL"),
                is_volatile=btc_regime_info.get("is_volatile", False),
            )

    def _should_rebalance(self, current_cash_ratio: float) -> RebalanceDecision:
        """리밸런싱 필요 여부 판단"""
        now = datetime.utcnow()
        target_cash = self.get_target_cash_ratio(self._current_regime)

        # 시간 조건: 최소 리밸런싱 간격
        elapsed = now - self._last_rebalance
        if elapsed < timedelta(minutes=self.rebalance_interval_min):
            return RebalanceDecision(
                should_rebalance=False,
                direction="NONE",
                target_cash_ratio=target_cash,
                current_cash_ratio=current_cash_ratio,
                delta_pct=0,
                reason=f"Cooldown: {elapsed.total_seconds()/60:.0f}min < {self.rebalance_interval_min}min",
            )

        # 비중 괴리 체크
        delta = target_cash - current_cash_ratio

        if abs(delta) < self.rebalance_threshold:
            return RebalanceDecision(
                should_rebalance=False,
                direction="NONE",
                target_cash_ratio=target_cash,
                current_cash_ratio=current_cash_ratio,
                delta_pct=delta,
                reason=f"Within threshold: |{delta:.1%}| < {self.rebalance_threshold:.1%}",
            )

        # 리밸런싱 방향 결정
        if delta > 0:
            # 현금이 부족 → 자산 매도
            direction = "SELL"
        else:
            # 현금이 과다 → 자산 매수
            direction = "BUY"

        return RebalanceDecision(
            should_rebalance=True,
            direction=direction,
            target_cash_ratio=target_cash,
            current_cash_ratio=current_cash_ratio,
            delta_pct=delta,
            reason=f"Rebalance needed: {current_cash_ratio:.1%} → {target_cash:.1%}",
        )

    async def generate_signal(self, market_data: dict) -> Optional[Signal]:
        """
        리밸런싱 시그널 생성

        market_data:
            total_equity: float (총 자산 KRW 가치)
            cash_balance: float (현금 KRW)
            asset_value: float (보유 자산 KRW 가치)
            price: float (대표 자산 현재가)
            symbol: str (대표 심볼, 예: KRW-BTC)
        """
        if not self._enabled:
            return None

        # 레짐 업데이트
        self._check_regime_from_feature_engine()

        total_equity = market_data.get("total_equity", 0)
        cash_balance = market_data.get("cash_balance", 0)

        if total_equity <= 0:
            return None

        # 현재 현금 비중 계산
        current_cash_ratio = cash_balance / total_equity

        # 리밸런싱 결정
        decision = self._should_rebalance(current_cash_ratio)

        if not decision.should_rebalance:
            return None

        # 리밸런싱 금액 계산
        target_cash = decision.target_cash_ratio * total_equity
        rebalance_amount = abs(target_cash - cash_balance)

        # 최소 거래 금액 체크 (10,000 KRW)
        if rebalance_amount < 10000:
            return None

        symbol = market_data.get("symbol", "KRW-BTC")
        price = market_data.get("price", 0)

        if price <= 0:
            return None

        # 수량 계산
        quantity = rebalance_amount / price

        # 방향에 따른 OrderSide
        side = OrderSide.SELL if decision.direction == "SELL" else OrderSide.BUY

        # 리밸런싱 시간 업데이트
        self._last_rebalance = datetime.utcnow()

        logger.info(
            "DefensiveCore rebalance signal",
            regime=self._current_regime.value,
            direction=decision.direction,
            current_cash_ratio=f"{current_cash_ratio:.1%}",
            target_cash_ratio=f"{decision.target_cash_ratio:.1%}",
            rebalance_amount=f"{rebalance_amount:,.0f} KRW",
            symbol=symbol,
        )

        return Signal(
            strategy=StrategyType.CORE,
            signal_type=SignalType.REBALANCE,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            reason=decision.reason,
            confidence=1.0,
            metadata={
                "regime": self._current_regime.value,
                "current_cash_ratio": current_cash_ratio,
                "target_cash_ratio": decision.target_cash_ratio,
                "rebalance_amount": rebalance_amount,
            },
        )

    async def should_exit(self, position: dict, market_data: dict) -> Optional[Signal]:
        """
        Core 전략은 청산보다는 리밸런싱으로 관리

        급격한 하락 시에만 강제 청산 시그널 발생
        """
        # 레짐 업데이트
        self._check_regime_from_feature_engine()

        # VOLATILE 레짐에서 자산 비중이 높으면 청산 시그널
        if self._current_regime == MarketRegime.VOLATILE:
            total_equity = market_data.get("total_equity", 0)
            cash_balance = market_data.get("cash_balance", 0)

            if total_equity <= 0:
                return None

            current_cash_ratio = cash_balance / total_equity
            target_cash = self.cash_ratio_volatile

            # 목표 대비 10% 이상 자산 과다 보유 시
            if current_cash_ratio < target_cash - 0.10:
                symbol = position.get("symbol")
                quantity = position.get("quantity", 0)
                price = market_data.get("price", 0)

                # 50% 축소
                reduce_quantity = quantity * 0.50

                logger.warning(
                    "DefensiveCore VOLATILE exit",
                    symbol=symbol,
                    current_cash_ratio=f"{current_cash_ratio:.1%}",
                    target_cash_ratio=f"{target_cash:.1%}",
                    reduce_quantity=reduce_quantity,
                )

                return Signal(
                    strategy=StrategyType.CORE,
                    signal_type=SignalType.EXIT,
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=reduce_quantity,
                    price=price,
                    reason=f"VOLATILE regime: reduce exposure ({current_cash_ratio:.1%} < {target_cash:.1%})",
                    metadata={
                        "regime": self._current_regime.value,
                        "reduction_pct": 0.50,
                    },
                )

        return None

    def get_position_size(self, signal: Signal, available_capital: float) -> float:
        """포지션 사이즈 계산"""
        if signal.price and signal.price > 0:
            # 리밸런싱 금액은 이미 계산됨
            if signal.metadata and "rebalance_amount" in signal.metadata:
                return signal.metadata["rebalance_amount"] / signal.price
            return signal.quantity

        return 0

    def get_status(self) -> dict:
        """전략 상태 조회"""
        target_cash = self.get_target_cash_ratio(self._current_regime)
        return {
            "enabled": self._enabled,
            "current_regime": self._current_regime.value,
            "target_cash_ratio": target_cash,
            "target_asset_ratio": 1.0 - target_cash,
            "cash_ratio_by_regime": {
                "RISK_ON": self.cash_ratio_risk_on,
                "NEUTRAL": self.cash_ratio_neutral,
                "RISK_OFF": self.cash_ratio_risk_off,
                "VOLATILE": self.cash_ratio_volatile,
            },
            "last_rebalance": self._last_rebalance.isoformat(),
            "rebalance_interval_min": self.rebalance_interval_min,
            "rebalance_threshold": self.rebalance_threshold,
        }

    def force_regime(self, regime: MarketRegime) -> None:
        """레짐 강제 설정 (테스트/긴급 상황용)"""
        old_regime = self._current_regime
        self._current_regime = regime

        logger.warning(
            "DefensiveCore regime forced",
            old=old_regime.value,
            new=regime.value,
        )

        # 목표 배분 업데이트
        target_cash = self.get_target_cash_ratio(regime)
        self._target_allocation = TargetAllocation(
            cash_ratio=target_cash,
            asset_ratio=1.0 - target_cash,
            regime=regime,
        )


# Singleton
_defensive_core: DefensiveCoreStrategy = None


def get_defensive_core_strategy() -> DefensiveCoreStrategy:
    """DefensiveCoreStrategy 싱글톤"""
    global _defensive_core
    if _defensive_core is None:
        _defensive_core = DefensiveCoreStrategy()
    return _defensive_core
