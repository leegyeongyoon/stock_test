"""Pullback Strategy - 눌림목 매수 전략

기존 Attack 전략 문제점:
- 급등 추격 → 고점 매수 → 하락 → 손실
- 후행 지표 (RVOL, Breakout) 사용

새로운 Pullback 전략:
- 급등 후 눌림목 매수 (저점 근처 진입)
- 선행 지표 (축적, 지지선, 반등 조짐) 사용
- 분산 투자 (10개까지, 종목당 5~10%)

전략 흐름:
1. 급등 이력 있는 종목 스캔 (매수 대상 Pool)
2. 눌림 발생 시 점수 계산
3. 지지선 근처 + 반등 조짐 → 진입
4. 손절: -3% / 익절: +5~10%
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.models.schemas import OrderSide
from src.strategies.pullback_score import (
    PullbackScoreCalculator,
    PullbackScoreResult,
    get_pullback_score_calculator,
)

logger = structlog.get_logger()
settings = get_settings()


class PullbackMode(str, Enum):
    """Pullback 전략 모드"""

    OFF = "OFF"  # 비활성화
    SAFE = "SAFE"  # 안전 모드 (레벨 3만)
    NORMAL = "NORMAL"  # 일반 모드 (레벨 2+)
    AGGRESSIVE = "AGGRESSIVE"  # 공격 모드 (레벨 1+)


@dataclass
class PullbackSignal:
    """눌림목 매수 시그널"""

    symbol: str
    score: float
    level: int
    target_allocation: float
    entry_price: float
    stop_loss: float
    quantity: float
    reason: str
    components: list
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": self.score,
            "level": self.level,
            "target_allocation": self.target_allocation,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "quantity": self.quantity,
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class PullbackPosition:
    """눌림목 포지션"""

    symbol: str
    entry_price: float
    quantity: float
    entry_time: datetime
    stop_loss: float
    take_profit: float  # 목표가
    highest_price: float  # 최고가 (트레일링용)
    trailing_active: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "entry_time": self.entry_time.isoformat(),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "highest_price": self.highest_price,
            "trailing_active": self.trailing_active,
        }


class PullbackStrategy:
    """
    눌림목 매수 전략

    핵심 철학:
    - "급등을 쫓지 말고, 급등 후 눌림을 사라"
    - 저점 근처 매수 → 반등 시 수익

    진입 조건:
    - Pullback Score >= 임계값 (55/70/85)
    - 급등 이력 필수 (상승 추세 종목)
    - 충분한 눌림 (3~10%)
    - 지지선 근처 + 반등 조짐

    청산 조건:
    - 손절: -3% (고정)
    - 1차 익절: +5% (50% 청산)
    - 트레일링 익절: 고점 -2%
    - 타임스톱: 4시간
    """

    def __init__(self):
        # 모드
        self._mode = PullbackMode.OFF
        self._enabled = False

        # 점수 계산기
        self.score_calculator = get_pullback_score_calculator()

        # 레벨별 임계값
        self.min_level_safe = 3  # SAFE: 레벨 3만
        self.min_level_normal = 2  # NORMAL: 레벨 2+
        self.min_level_aggressive = 1  # AGGRESSIVE: 레벨 1+

        # 포지션 관리
        self.max_positions = getattr(settings, "pullback_max_positions", 10)
        self._active_positions: dict[str, PullbackPosition] = {}
        self._pending_signals: dict[str, PullbackSignal] = {}

        # 청산 설정
        self.stop_loss_pct = -0.03  # -3% 손절
        self.take_profit_1_pct = 0.05  # +5% 1차 익절
        self.take_profit_1_ratio = 0.5  # 50% 청산
        self.trailing_trigger_pct = 0.03  # +3% 트레일링 시작
        self.trailing_stop_pct = 0.02  # 고점 -2% 청산
        self.time_stop_hours = 4  # 4시간 타임스톱

        # 쿨다운 (동일 종목 재진입 방지)
        self._last_exit: dict[str, datetime] = {}
        self.cooldown_minutes = 60

    def set_mode(self, mode: str) -> None:
        """모드 설정"""
        try:
            self._mode = PullbackMode(mode)
            self._enabled = self._mode != PullbackMode.OFF
            logger.info("Pullback mode set", mode=self._mode.value, enabled=self._enabled)
        except ValueError:
            logger.warning(f"Invalid pullback mode: {mode}, setting to OFF")
            self._mode = PullbackMode.OFF
            self._enabled = False

    def is_enabled(self) -> bool:
        """활성화 여부"""
        return self._enabled

    def get_min_level(self) -> int:
        """현재 모드의 최소 레벨"""
        if self._mode == PullbackMode.SAFE:
            return self.min_level_safe
        elif self._mode == PullbackMode.NORMAL:
            return self.min_level_normal
        elif self._mode == PullbackMode.AGGRESSIVE:
            return self.min_level_aggressive
        return 99  # OFF

    async def scan_for_signals(
        self, symbols: list[str], market_data_map: dict
    ) -> list[PullbackSignal]:
        """
        눌림목 시그널 스캔

        Args:
            symbols: 스캔 대상 심볼 리스트
            market_data_map: {symbol: market_data} 딕셔너리

        Returns:
            list[PullbackSignal]: 감지된 시그널 리스트
        """
        if not self._enabled:
            return []

        signals = []
        current_positions = len(self._active_positions)
        available_slots = self.max_positions - current_positions

        if available_slots <= 0:
            logger.debug("Pullback max positions reached", max=self.max_positions)
            return []

        min_level = self.get_min_level()

        for symbol in symbols:
            # 이미 포지션 있으면 스킵
            if symbol in self._active_positions:
                continue

            # 쿨다운 체크
            if not self._check_cooldown(symbol):
                continue

            # 시장 데이터 가져오기
            market_data = market_data_map.get(symbol)
            if not market_data:
                continue

            # 점수 계산
            score_result = self.score_calculator.calculate(market_data)

            # 레벨 체크
            if score_result.level < min_level:
                continue

            # 시그널 생성
            signal = self._create_signal(score_result, market_data)
            if signal:
                signals.append(signal)
                self._pending_signals[symbol] = signal

                if len(signals) >= available_slots:
                    break

        if signals:
            logger.info(
                "Pullback signals detected",
                count=len(signals),
                symbols=[s.symbol for s in signals],
            )

        return signals

    def _check_cooldown(self, symbol: str) -> bool:
        """쿨다운 체크"""
        last_exit = self._last_exit.get(symbol)
        if not last_exit:
            return True

        elapsed = datetime.utcnow() - last_exit
        return elapsed > timedelta(minutes=self.cooldown_minutes)

    def _create_signal(
        self, score_result: PullbackScoreResult, market_data: dict
    ) -> Optional[PullbackSignal]:
        """시그널 생성"""
        if score_result.level == 0:
            return None

        price = market_data.get("price", 0)
        if price <= 0:
            return None

        # 수량 계산 (나중에 엔진에서 재계산)
        quantity = 0

        # 이유 생성
        components_summary = ", ".join(
            [f"{c.name}:{c.score:.0f}/{c.max_score:.0f}" for c in score_result.components]
        )
        reason = f"L{score_result.level} Score={score_result.total_score:.0f} [{components_summary}]"

        return PullbackSignal(
            symbol=score_result.symbol,
            score=score_result.total_score,
            level=score_result.level,
            target_allocation=score_result.target_allocation,
            entry_price=score_result.entry_price_target or price,
            stop_loss=score_result.stop_loss_price or price * 0.97,
            quantity=quantity,
            reason=reason,
            components=[c.to_dict() for c in score_result.components],
        )

    def track_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        stop_loss: float,
    ) -> None:
        """포지션 추적 시작"""
        take_profit = entry_price * (1 + self.take_profit_1_pct)

        self._active_positions[symbol] = PullbackPosition(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.utcnow(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            highest_price=entry_price,
            trailing_active=False,
        )

        # 대기 시그널에서 제거
        self._pending_signals.pop(symbol, None)

        logger.info(
            "Pullback position tracked",
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def should_exit(self, symbol: str, current_price: float) -> Optional[dict]:
        """
        청산 조건 확인

        Returns:
            dict: {"action": "FULL"|"PARTIAL", "reason": str, "quantity": float}
            None: 청산 조건 미충족
        """
        pos = self._active_positions.get(symbol)
        if not pos:
            return None

        if current_price <= 0 or pos.entry_price <= 0:
            return None

        # 최고가 업데이트
        if current_price > pos.highest_price:
            pos.highest_price = current_price

        # PnL 계산
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price
        from_high_pct = (current_price - pos.highest_price) / pos.highest_price

        # 1. 손절 (-3%)
        if pnl_pct <= self.stop_loss_pct:
            return {
                "action": "FULL",
                "reason": f"Stop loss: {pnl_pct:.2%}",
                "quantity": pos.quantity,
            }

        # 2. 타임스톱 (4시간)
        elapsed = datetime.utcnow() - pos.entry_time
        if elapsed > timedelta(hours=self.time_stop_hours):
            return {
                "action": "FULL",
                "reason": f"Time stop: {elapsed.total_seconds()/3600:.1f}h",
                "quantity": pos.quantity,
            }

        # 3. 트레일링 스탑 활성화
        if not pos.trailing_active and pnl_pct >= self.trailing_trigger_pct:
            pos.trailing_active = True
            logger.info(
                "Pullback trailing activated",
                symbol=symbol,
                pnl_pct=f"{pnl_pct:.2%}",
            )

        # 4. 트레일링 스탑 (고점 -2%)
        if pos.trailing_active and from_high_pct <= -self.trailing_stop_pct:
            return {
                "action": "FULL",
                "reason": f"Trailing stop: from high {from_high_pct:.2%}",
                "quantity": pos.quantity,
            }

        # 5. 1차 익절 (+5%)
        if pnl_pct >= self.take_profit_1_pct and not pos.trailing_active:
            partial_qty = pos.quantity * self.take_profit_1_ratio
            return {
                "action": "PARTIAL",
                "reason": f"Take profit 1: {pnl_pct:.2%}",
                "quantity": partial_qty,
            }

        return None

    def close_position(self, symbol: str, partial: bool = False, sold_qty: float = 0) -> None:
        """포지션 청산"""
        pos = self._active_positions.get(symbol)
        if not pos:
            return

        if partial and sold_qty < pos.quantity:
            # 부분 청산
            pos.quantity -= sold_qty
            logger.info(
                "Pullback position partially closed",
                symbol=symbol,
                sold_qty=sold_qty,
                remaining=pos.quantity,
            )
        else:
            # 전량 청산
            self._active_positions.pop(symbol, None)
            self._last_exit[symbol] = datetime.utcnow()
            logger.info(
                "Pullback position closed",
                symbol=symbol,
            )

    def get_position(self, symbol: str) -> Optional[PullbackPosition]:
        """포지션 조회"""
        return self._active_positions.get(symbol)

    def get_all_positions(self) -> dict[str, PullbackPosition]:
        """전체 포지션 조회"""
        return self._active_positions.copy()

    def get_status(self) -> dict:
        """전략 상태 조회"""
        return {
            "enabled": self._enabled,
            "mode": self._mode.value,
            "min_level": self.get_min_level(),
            "max_positions": self.max_positions,
            "active_positions": len(self._active_positions),
            "pending_signals": len(self._pending_signals),
            "positions": {
                sym: pos.to_dict() for sym, pos in self._active_positions.items()
            },
            "signals": {
                sym: sig.to_dict() for sym, sig in self._pending_signals.items()
            },
        }


# Singleton
_pullback_strategy: Optional[PullbackStrategy] = None


def get_pullback_strategy() -> PullbackStrategy:
    """PullbackStrategy 싱글톤"""
    global _pullback_strategy
    if _pullback_strategy is None:
        _pullback_strategy = PullbackStrategy()
    return _pullback_strategy
