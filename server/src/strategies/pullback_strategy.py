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
4. 손절: -1.5% / 익절: +1% (config 기반)
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
    entry_price: float  # 최초 진입가
    quantity: float
    entry_time: datetime
    stop_loss: float
    take_profit: float  # 목표가
    highest_price: float  # 최고가 (트레일링용)
    trailing_active: bool = False
    avg_down_price: float = 0.0  # 물타기 후 실제 평균 매수가 (0=물타기 없음)

    @property
    def is_averaged_down(self) -> bool:
        """물타기 여부"""
        return self.avg_down_price > 0 and self.avg_down_price < self.entry_price

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
            "avg_down_price": self.avg_down_price,
            "is_averaged_down": self.is_averaged_down,
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
    - 손절: -1.5% (config)
    - 1차 익절: +1% (50% 청산)
    - 트레일링 익절: 고점 -0.5% (+1.5% 트리거)
    - 타임스톱: 2시간
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

        # 청산 설정 (config에서 로드)
        self.stop_loss_pct = getattr(settings, "pullback_stop_loss_pct", -0.015)
        self.take_profit_1_pct = getattr(settings, "pullback_take_profit_pct", 0.01)
        self.take_profit_1_ratio = 0.5  # 50% 청산
        self.trailing_trigger_pct = getattr(settings, "pullback_trailing_trigger_pct", 0.015)
        self.trailing_stop_pct = getattr(settings, "pullback_trailing_stop_pct", 0.005)
        self.time_stop_hours = getattr(settings, "pullback_time_stop_hours", 2)

        # 쿨다운 (동일 종목 재진입 방지)
        self._last_exit: dict[str, datetime] = {}
        self.cooldown_minutes = getattr(settings, "pullback_cooldown_min", 60)

        # 통계
        self._stats = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl_pct": 0.0,
            "stop_loss_hits": 0,
            "trailing_hits": 0,
            "time_stop_hits": 0,
            "take_profit_hits": 0,
            "breakeven_hits": 0,
        }

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
        stop_loss: float = 0.0,
    ) -> None:
        """포지션 추적 시작"""
        # SL/TP를 config 기반으로 계산 (signal 값 무시)
        computed_sl = entry_price * (1 + self.stop_loss_pct)
        take_profit = entry_price * (1 + self.take_profit_1_pct)

        self._active_positions[symbol] = PullbackPosition(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.utcnow(),
            stop_loss=computed_sl,
            take_profit=take_profit,
            highest_price=entry_price,
            trailing_active=False,
        )

        # 대기 시그널에서 제거
        self._pending_signals.pop(symbol, None)

        # 진입 시점부터 쿨다운 시작 (같은 종목 재진입 방지)
        self._last_exit[symbol] = datetime.utcnow()

        logger.info(
            "Pullback position tracked",
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=computed_sl,
            take_profit=take_profit,
        )

    # Upbit 수수료 (매수 0.05% + 매도 0.05% = 편도 0.05%)
    UPBIT_FEE_PCT = 0.0005
    # 물타기 본전 청산 버퍼 (수수료 + 여유분)
    BREAKEVEN_BUFFER_PCT = 0.0015  # 0.15% (수수료 0.1% + 슬리피지 0.05%)

    def update_avg_price(self, symbol: str, exchange_avg_price: float) -> None:
        """업비트 실제 평균매수가로 물타기 감지/업데이트"""
        pos = self._active_positions.get(symbol)
        if not pos:
            return

        if exchange_avg_price <= 0:
            return

        # 업비트 평균가가 전략 진입가보다 낮으면 → 물타기 발생
        price_diff_pct = abs(exchange_avg_price - pos.entry_price) / pos.entry_price
        if price_diff_pct > 0.001:  # 0.1% 이상 차이나면 업데이트
            if not pos.is_averaged_down and exchange_avg_price < pos.entry_price:
                logger.info(
                    "Pullback avg-down detected",
                    symbol=symbol,
                    original_entry=pos.entry_price,
                    exchange_avg=exchange_avg_price,
                )
            pos.avg_down_price = exchange_avg_price
            # SL/TP를 물타기 평균가 기준으로 재계산
            pos.stop_loss = exchange_avg_price * (1 + self.stop_loss_pct)
            pos.take_profit = exchange_avg_price * (1 + self.take_profit_1_pct)

    def should_exit(self, symbol: str, current_price: float) -> Optional[dict]:
        """
        청산 조건 확인

        Returns:
            dict: {"action": "FULL"|"PARTIAL", "reason": str, "quantity": float, "exit_type": str}
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

        # PnL 기준가: 물타기 시 실제 평균가 사용, 아니면 원래 진입가
        ref_price = pos.avg_down_price if pos.is_averaged_down else pos.entry_price
        pnl_pct = (current_price - ref_price) / ref_price
        from_high_pct = (current_price - pos.highest_price) / pos.highest_price

        # 0. 물타기 본전 청산 (최우선)
        # 물타기 후 수수료 커버할 정도로 +가 되면 즉시 전량 청산
        if pos.is_averaged_down and pnl_pct >= self.BREAKEVEN_BUFFER_PCT:
            return {
                "action": "FULL",
                "reason": f"Avg-down breakeven: {pnl_pct:.2%} (avg₩{ref_price:,.0f})",
                "quantity": pos.quantity,
                "exit_type": "breakeven",
            }

        # 1. 손절
        if pnl_pct <= self.stop_loss_pct:
            return {
                "action": "FULL",
                "reason": f"Stop loss: {pnl_pct:.2%}",
                "quantity": pos.quantity,
                "exit_type": "stop_loss",
            }

        # 2. 타임스톱
        elapsed = datetime.utcnow() - pos.entry_time
        if elapsed > timedelta(hours=self.time_stop_hours):
            return {
                "action": "FULL",
                "reason": f"Time stop: {elapsed.total_seconds()/3600:.1f}h",
                "quantity": pos.quantity,
                "exit_type": "time_stop",
            }

        # 3. 트레일링 스탑 활성화
        if not pos.trailing_active and pnl_pct >= self.trailing_trigger_pct:
            pos.trailing_active = True
            logger.info(
                "Pullback trailing activated",
                symbol=symbol,
                pnl_pct=f"{pnl_pct:.2%}",
            )

        # 4. 트레일링 스탑
        if pos.trailing_active and from_high_pct <= -self.trailing_stop_pct:
            return {
                "action": "FULL",
                "reason": f"Trailing stop: from high {from_high_pct:.2%}",
                "quantity": pos.quantity,
                "exit_type": "trailing",
            }

        # 5. 1차 익절
        if pnl_pct >= self.take_profit_1_pct and not pos.trailing_active:
            partial_qty = pos.quantity * self.take_profit_1_ratio
            return {
                "action": "PARTIAL",
                "reason": f"Take profit 1: {pnl_pct:.2%}",
                "quantity": partial_qty,
                "exit_type": "take_profit",
            }

        return None

    def close_position(
        self,
        symbol: str,
        partial: bool = False,
        sold_qty: float = 0,
        exit_type: str = "",
        pnl_pct: float = 0.0,
    ) -> None:
        """포지션 청산"""
        pos = self._active_positions.get(symbol)
        if not pos:
            return

        if partial and sold_qty < pos.quantity:
            # 부분 청산
            pos.quantity -= sold_qty
            # 부분 익절 시에도 쿨다운 갱신 (재진입 방지)
            self._last_exit[symbol] = datetime.utcnow()
            logger.info(
                "Pullback position partially closed",
                symbol=symbol,
                sold_qty=sold_qty,
                remaining=pos.quantity,
                exit_type=exit_type,
            )
        else:
            # 전량 청산
            self._active_positions.pop(symbol, None)
            self._last_exit[symbol] = datetime.utcnow()

            # 통계 업데이트
            self._update_stats(pnl_pct, exit_type)

            logger.info(
                "Pullback position closed",
                symbol=symbol,
                pnl_pct=f"{pnl_pct:.2%}",
                exit_type=exit_type,
            )

    def _update_stats(self, pnl_pct: float, exit_type: str) -> None:
        """통계 업데이트"""
        self._stats["total_trades"] += 1
        self._stats["total_pnl_pct"] += pnl_pct

        if pnl_pct >= 0:
            self._stats["winning_trades"] += 1
        else:
            self._stats["losing_trades"] += 1

        if exit_type == "stop_loss":
            self._stats["stop_loss_hits"] += 1
        elif exit_type == "trailing":
            self._stats["trailing_hits"] += 1
        elif exit_type == "time_stop":
            self._stats["time_stop_hits"] += 1
        elif exit_type == "take_profit":
            self._stats["take_profit_hits"] += 1
        elif exit_type == "breakeven":
            self._stats["breakeven_hits"] += 1

    def get_position(self, symbol: str) -> Optional[PullbackPosition]:
        """포지션 조회"""
        return self._active_positions.get(symbol)

    def get_all_positions(self) -> dict[str, PullbackPosition]:
        """전체 포지션 조회"""
        return self._active_positions.copy()

    def get_monitoring_data(self) -> dict:
        """대시보드 모니터링용 데이터"""
        win_rate = (
            self._stats["winning_trades"] / self._stats["total_trades"] * 100
            if self._stats["total_trades"] > 0
            else 0
        )

        return {
            "strategy": "PULLBACK",
            "enabled": self._enabled,
            "mode": self._mode.value,
            "positions": {
                sym: {
                    "entry_price": pos.entry_price,
                    "avg_down_price": pos.avg_down_price,
                    "is_averaged_down": pos.is_averaged_down,
                    "quantity": pos.quantity,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "highest_price": pos.highest_price,
                    "trailing_active": pos.trailing_active,
                    "hold_time_min": (datetime.utcnow() - pos.entry_time).total_seconds() / 60,
                    "pnl_pct": (
                        (pos.highest_price - (pos.avg_down_price if pos.is_averaged_down else pos.entry_price))
                        / (pos.avg_down_price if pos.is_averaged_down else pos.entry_price) * 100
                        if pos.entry_price > 0
                        else 0
                    ),
                }
                for sym, pos in self._active_positions.items()
            },
            "pending_signals": {
                sym: {
                    "score": sig.score,
                    "level": sig.level,
                    "reason": sig.reason,
                }
                for sym, sig in self._pending_signals.items()
            },
            "stats": {
                "total_trades": self._stats["total_trades"],
                "winning_trades": self._stats["winning_trades"],
                "losing_trades": self._stats["losing_trades"],
                "total_pnl_pct": self._stats["total_pnl_pct"],
                "win_rate": win_rate,
                "stop_loss_hits": self._stats["stop_loss_hits"],
                "trailing_hits": self._stats["trailing_hits"],
                "time_stop_hits": self._stats["time_stop_hits"],
                "take_profit_hits": self._stats["take_profit_hits"],
                "breakeven_hits": self._stats["breakeven_hits"],
            },
            "settings": {
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_1_pct": self.take_profit_1_pct,
                "trailing_trigger_pct": self.trailing_trigger_pct,
                "trailing_stop_pct": self.trailing_stop_pct,
                "time_stop_hours": self.time_stop_hours,
            },
        }

    def get_status(self) -> dict:
        """전략 상태 조회"""
        win_rate = (
            self._stats["winning_trades"] / self._stats["total_trades"] * 100
            if self._stats["total_trades"] > 0
            else 0
        )

        return {
            "enabled": self._enabled,
            "mode": self._mode.value,
            "min_level": self.get_min_level(),
            "max_positions": self.max_positions,
            "active_positions": len(self._active_positions),
            "pending_signals": len(self._pending_signals),
            "settings": {
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_1_pct": self.take_profit_1_pct,
                "trailing_trigger_pct": self.trailing_trigger_pct,
                "trailing_stop_pct": self.trailing_stop_pct,
                "time_stop_hours": self.time_stop_hours,
            },
            "stats": {
                **self._stats,
                "win_rate": f"{win_rate:.1f}%",
            },
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
