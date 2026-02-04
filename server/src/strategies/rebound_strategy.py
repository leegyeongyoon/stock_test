"""
Rebound Scalper Strategy v4.2

핵심 철학:
- "추격 매수 ❌ → 지지선/과매도에서 반등 매수 ✅"
- 급등 이력 불필요 (Pullback과의 핵심 차이)
- 짧은 타겟 (0.8~2%) + 빠른 손절 (1.2%)

진입 조건:
- Rebound Score >= 임계값 (55/70/85)
- 필터 통과 (변동성, 추세, 연속손실)

청산 조건:
- 손절: -1.2% (고정)
- TP1: +0.8% → 50% 청산 + BE Stop
- TP2: +1.5% → 30% 청산
- 트레일링: 나머지 20%
- 타임스톱: 2시간

특징:
- 급등 이력 필수 ❌ (기존 Pullback의 문제점 해결)
- 3중 안전 메커니즘 (휩쏘/하락추세/연속손실)
- BE Stop (TP1 후 손절을 진입가로 이동)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.strategies.rebound_score import (
    ReboundScoreCalculator,
    ReboundScoreResult,
    get_rebound_score_calculator,
)
from src.strategies.rebound_filters import (
    ReboundFilterManager,
    VolatilityFilter,
    TrendFilter,
    ConsecutiveLossBreaker,
    FilterResult,
)

logger = structlog.get_logger()
settings = get_settings()


class ReboundMode(str, Enum):
    """Rebound 전략 모드"""

    OFF = "OFF"  # 비활성화
    SAFE = "SAFE"  # 안전 모드 (레벨 3만)
    NORMAL = "NORMAL"  # 일반 모드 (레벨 2+)
    AGGRESSIVE = "AGGRESSIVE"  # 공격 모드 (레벨 1+)


@dataclass
class ReboundSignal:
    """Rebound 매수 시그널"""

    symbol: str
    score: float
    level: int
    target_allocation: float
    entry_price: float
    stop_loss: float
    quantity: float
    reason: str
    components: list
    filter_result: Optional[dict] = None
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
            "filter_result": self.filter_result,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ReboundPosition:
    """Rebound 포지션"""

    symbol: str
    entry_price: float
    quantity: float
    initial_quantity: float  # 초기 수량 (부분 청산 추적용)
    entry_time: datetime
    stop_loss: float
    tp1_price: float  # +0.8%
    tp2_price: float  # +1.5%
    highest_price: float  # 최고가 (트레일링용)

    # 청산 상태
    tp1_hit: bool = False
    tp2_hit: bool = False
    be_stop_active: bool = False  # BE Stop 활성화 여부
    trailing_active: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "initial_quantity": self.initial_quantity,
            "entry_time": self.entry_time.isoformat(),
            "stop_loss": self.stop_loss,
            "tp1_price": self.tp1_price,
            "tp2_price": self.tp2_price,
            "highest_price": self.highest_price,
            "tp1_hit": self.tp1_hit,
            "tp2_hit": self.tp2_hit,
            "be_stop_active": self.be_stop_active,
            "trailing_active": self.trailing_active,
            "pnl_pct": (self.highest_price - self.entry_price) / self.entry_price if self.entry_price > 0 else 0,
        }


class ReboundScalperStrategy:
    """
    Rebound Scalper 전략

    핵심 철학:
    - "급등을 쫓지 말고, 지지선에서 반등을 잡아라"
    - 짧고 빠른 수익 실현 (스캘핑)
    - 철저한 리스크 관리

    진입 조건:
    - Rebound Score >= 임계값
    - 3가지 필터 통과
    - 쿨다운 기간 충족

    청산 조건:
    - 손절: -1.2% (TP1 후 BE Stop)
    - TP1: +0.8% → 50% 청산
    - TP2: +1.5% → 30% 청산
    - 트레일링: 나머지 (고점 -0.5%)
    - 타임스톱: 2시간
    """

    def __init__(self):
        # 모드
        self._mode = ReboundMode.OFF
        self._enabled = False

        # 설정 로드
        self._load_settings()

        # 점수 계산기
        self.score_calculator = get_rebound_score_calculator()

        # 필터 매니저
        self.filter_manager = ReboundFilterManager(
            volatility_filter=VolatilityFilter(
                atr_multiplier=self.volatility_atr_mult,
            ),
            trend_filter=TrendFilter(),
            loss_breaker=ConsecutiveLossBreaker(
                max_consecutive_losses=self.consecutive_loss_limit,
                cooldown_minutes=self.consecutive_loss_cooldown_min,
            ),
            disable_on_volatile=self.disable_on_volatile,
            disable_in_downtrend=self.disable_in_downtrend,
        )

        # 포지션 관리
        self._active_positions: dict[str, ReboundPosition] = {}
        self._pending_signals: dict[str, ReboundSignal] = {}

        # 쿨다운 (동일 종목 재진입 방지)
        self._last_exit: dict[str, datetime] = {}

        # 통계
        self._stats = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl_pct": 0.0,
            "tp1_hits": 0,
            "tp2_hits": 0,
            "stop_loss_hits": 0,
            "time_stop_hits": 0,
        }

    def _load_settings(self) -> None:
        """설정 로드"""
        # 레벨별 임계값
        self.score_l1 = getattr(settings, "rebound_score_l1", 55.0)
        self.score_l2 = getattr(settings, "rebound_score_l2", 70.0)
        self.score_l3 = getattr(settings, "rebound_score_l3", 85.0)

        # 레벨별 할당
        self.alloc_l1 = getattr(settings, "rebound_alloc_l1", 0.03)
        self.alloc_l2 = getattr(settings, "rebound_alloc_l2", 0.05)
        self.alloc_l3 = getattr(settings, "rebound_alloc_l3", 0.08)

        # 청산 설정
        self.stop_loss_pct = getattr(settings, "rebound_stop_loss_pct", -0.012)
        self.tp1_pct = getattr(settings, "rebound_tp1_pct", 0.008)
        self.tp1_ratio = getattr(settings, "rebound_tp1_ratio", 0.50)
        self.tp2_pct = getattr(settings, "rebound_tp2_pct", 0.015)
        self.tp2_ratio = getattr(settings, "rebound_tp2_ratio", 0.30)
        self.trailing_trigger_pct = getattr(settings, "rebound_trailing_trigger_pct", 0.02)
        self.trailing_stop_pct = getattr(settings, "rebound_trailing_stop_pct", 0.005)
        self.time_stop_hours = getattr(settings, "rebound_time_stop_hours", 2)

        # 필터 설정
        self.consecutive_loss_limit = getattr(settings, "rebound_consecutive_loss_limit", 3)
        self.consecutive_loss_cooldown_min = getattr(settings, "rebound_consecutive_loss_cooldown_min", 120)
        self.volatility_atr_mult = getattr(settings, "rebound_volatility_atr_mult", 2.0)
        self.disable_on_volatile = getattr(settings, "rebound_disable_on_volatile", True)
        self.disable_in_downtrend = getattr(settings, "rebound_disable_in_downtrend", True)

        # 기타 설정
        self.max_positions = getattr(settings, "rebound_max_positions", 5)
        self.cooldown_minutes = getattr(settings, "rebound_cooldown_minutes", 30)

    def set_mode(self, mode: str) -> None:
        """모드 설정"""
        try:
            self._mode = ReboundMode(mode)
            self._enabled = self._mode != ReboundMode.OFF
            logger.info("Rebound mode set", mode=self._mode.value, enabled=self._enabled)
        except ValueError:
            logger.warning(f"Invalid rebound mode: {mode}, setting to OFF")
            self._mode = ReboundMode.OFF
            self._enabled = False

    def is_enabled(self) -> bool:
        """활성화 여부"""
        return self._enabled

    def get_min_level(self) -> int:
        """현재 모드의 최소 레벨"""
        if self._mode == ReboundMode.SAFE:
            return 3  # 레벨 3만
        elif self._mode == ReboundMode.NORMAL:
            return 2  # 레벨 2+
        elif self._mode == ReboundMode.AGGRESSIVE:
            return 1  # 레벨 1+
        return 99  # OFF

    def get_allocation_for_level(self, level: int) -> float:
        """레벨에 따른 자본 할당률"""
        if level >= 3:
            return self.alloc_l3
        elif level >= 2:
            return self.alloc_l2
        elif level >= 1:
            return self.alloc_l1
        return 0.0

    async def scan_for_signals(
        self, symbols: list[str], market_data_map: dict
    ) -> list[ReboundSignal]:
        """
        Rebound 시그널 스캔

        Args:
            symbols: 스캔 대상 심볼 리스트
            market_data_map: {symbol: market_data} 딕셔너리

        Returns:
            list[ReboundSignal]: 감지된 시그널 리스트
        """
        if not self._enabled:
            return []

        signals = []
        current_positions = len(self._active_positions)
        available_slots = self.max_positions - current_positions

        if available_slots <= 0:
            logger.debug("Rebound max positions reached", max=self.max_positions)
            return []

        min_level = self.get_min_level()
        candidates = []

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

            # 필터 체크 (먼저)
            filter_result = self.filter_manager.check_all(symbol, market_data)
            if not filter_result.can_enter:
                logger.debug(
                    "Rebound filtered",
                    symbol=symbol,
                    blocked_by=filter_result.blocked_by,
                    reasons=filter_result.reasons,
                )
                continue

            # 점수 계산
            score_result = self.score_calculator.calculate(market_data)

            # 레벨 체크
            if score_result.level < min_level:
                continue

            candidates.append((score_result, market_data, filter_result))

        # 점수 순으로 정렬 (높은 점수 우선)
        candidates.sort(key=lambda x: x[0].total_score, reverse=True)

        # 상위 N개만 시그널 생성
        for score_result, market_data, filter_result in candidates[:available_slots]:
            signal = self._create_signal(score_result, market_data, filter_result)
            if signal:
                signals.append(signal)
                self._pending_signals[score_result.symbol] = signal

        if signals:
            logger.info(
                "Rebound signals detected",
                count=len(signals),
                symbols=[s.symbol for s in signals],
                scores=[s.score for s in signals],
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
        self,
        score_result: ReboundScoreResult,
        market_data: dict,
        filter_result: FilterResult
    ) -> Optional[ReboundSignal]:
        """시그널 생성"""
        if score_result.level == 0:
            return None

        price = market_data.get("price", 0)
        if price <= 0:
            return None

        # 할당률 결정
        target_allocation = self.get_allocation_for_level(score_result.level)

        # 손절가 계산
        stop_loss = price * (1 + self.stop_loss_pct)

        # 이유 생성
        components_summary = ", ".join(
            [f"{c.name}:{c.score:.0f}/{c.max_score:.0f}" for c in score_result.components]
        )
        reason = f"L{score_result.level} Score={score_result.total_score:.0f} [{components_summary}]"

        return ReboundSignal(
            symbol=score_result.symbol,
            score=score_result.total_score,
            level=score_result.level,
            target_allocation=target_allocation,
            entry_price=price,
            stop_loss=stop_loss,
            quantity=0,  # 엔진에서 계산
            reason=reason,
            components=[c.to_dict() for c in score_result.components],
            filter_result={
                "volatility": filter_result.volatility_state.__dict__ if filter_result.volatility_state else None,
                "trend": filter_result.trend_state.__dict__ if filter_result.trend_state else None,
                "cooldown": filter_result.cooldown_state.__dict__ if filter_result.cooldown_state else None,
            }
        )

    def track_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
    ) -> None:
        """포지션 추적 시작"""
        # TP 가격 계산
        tp1_price = entry_price * (1 + self.tp1_pct)
        tp2_price = entry_price * (1 + self.tp2_pct)

        # 손절가 계산
        stop_loss = entry_price * (1 + self.stop_loss_pct)

        self._active_positions[symbol] = ReboundPosition(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            initial_quantity=quantity,
            entry_time=datetime.utcnow(),
            stop_loss=stop_loss,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            highest_price=entry_price,
            tp1_hit=False,
            tp2_hit=False,
            be_stop_active=False,
            trailing_active=False,
        )

        # 대기 시그널에서 제거
        self._pending_signals.pop(symbol, None)

        logger.info(
            "Rebound position tracked",
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
        )

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

        # PnL 계산
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price
        from_high_pct = (current_price - pos.highest_price) / pos.highest_price if pos.highest_price > 0 else 0

        # 1. 손절 체크 (BE Stop 적용 여부에 따라)
        effective_stop_loss = pos.entry_price if pos.be_stop_active else pos.stop_loss
        if current_price <= effective_stop_loss:
            exit_type = "be_stop" if pos.be_stop_active else "stop_loss"
            return {
                "action": "FULL",
                "reason": f"{'BE Stop' if pos.be_stop_active else 'Stop loss'}: {pnl_pct:.2%}",
                "quantity": pos.quantity,
                "exit_type": exit_type,
            }

        # 2. 타임스톱 (2시간)
        elapsed = datetime.utcnow() - pos.entry_time
        if elapsed > timedelta(hours=self.time_stop_hours):
            return {
                "action": "FULL",
                "reason": f"Time stop: {elapsed.total_seconds()/3600:.1f}h",
                "quantity": pos.quantity,
                "exit_type": "time_stop",
            }

        # 3. TP1 (+0.8%) - 50% 청산
        if not pos.tp1_hit and current_price >= pos.tp1_price:
            pos.tp1_hit = True
            pos.be_stop_active = True  # BE Stop 활성화
            partial_qty = pos.initial_quantity * self.tp1_ratio

            logger.info(
                "Rebound TP1 hit + BE Stop activated",
                symbol=symbol,
                pnl_pct=f"{pnl_pct:.2%}",
            )

            return {
                "action": "PARTIAL",
                "reason": f"TP1: {pnl_pct:.2%} + BE Stop",
                "quantity": partial_qty,
                "exit_type": "tp1",
            }

        # 4. TP2 (+1.5%) - 30% 청산
        if pos.tp1_hit and not pos.tp2_hit and current_price >= pos.tp2_price:
            pos.tp2_hit = True
            pos.trailing_active = True  # 트레일링 활성화
            partial_qty = pos.initial_quantity * self.tp2_ratio

            logger.info(
                "Rebound TP2 hit + Trailing activated",
                symbol=symbol,
                pnl_pct=f"{pnl_pct:.2%}",
            )

            return {
                "action": "PARTIAL",
                "reason": f"TP2: {pnl_pct:.2%} + Trailing",
                "quantity": partial_qty,
                "exit_type": "tp2",
            }

        # 5. 트레일링 스탑 (고점 -0.5%)
        if pos.trailing_active and from_high_pct <= -self.trailing_stop_pct:
            return {
                "action": "FULL",
                "reason": f"Trailing stop: from high {from_high_pct:.2%}",
                "quantity": pos.quantity,
                "exit_type": "trailing",
            }

        return None

    def close_position(
        self,
        symbol: str,
        partial: bool = False,
        sold_qty: float = 0,
        exit_type: str = "",
        pnl_pct: float = 0.0
    ) -> None:
        """포지션 청산"""
        pos = self._active_positions.get(symbol)
        if not pos:
            return

        if partial and sold_qty < pos.quantity:
            # 부분 청산
            pos.quantity -= sold_qty
            logger.info(
                "Rebound position partially closed",
                symbol=symbol,
                sold_qty=sold_qty,
                remaining=pos.quantity,
                exit_type=exit_type,
            )
        else:
            # 전량 청산
            self._active_positions.pop(symbol, None)
            self._last_exit[symbol] = datetime.utcnow()

            # 필터 매니저에 결과 기록
            self.filter_manager.record_trade_result(symbol, pnl_pct, exit_type)

            # 통계 업데이트
            self._update_stats(pnl_pct, exit_type)

            logger.info(
                "Rebound position closed",
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

        if exit_type == "tp1":
            self._stats["tp1_hits"] += 1
        elif exit_type == "tp2":
            self._stats["tp2_hits"] += 1
        elif exit_type in ["stop_loss", "be_stop"]:
            self._stats["stop_loss_hits"] += 1
        elif exit_type == "time_stop":
            self._stats["time_stop_hits"] += 1

    def get_position(self, symbol: str) -> Optional[ReboundPosition]:
        """포지션 조회"""
        return self._active_positions.get(symbol)

    def get_all_positions(self) -> dict[str, ReboundPosition]:
        """전체 포지션 조회"""
        return self._active_positions.copy()

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
                "tp1_pct": self.tp1_pct,
                "tp1_ratio": self.tp1_ratio,
                "tp2_pct": self.tp2_pct,
                "tp2_ratio": self.tp2_ratio,
                "trailing_stop_pct": self.trailing_stop_pct,
                "time_stop_hours": self.time_stop_hours,
            },
            "stats": {
                **self._stats,
                "win_rate": f"{win_rate:.1f}%",
                "avg_pnl": (
                    self._stats["total_pnl_pct"] / self._stats["total_trades"] * 100
                    if self._stats["total_trades"] > 0
                    else 0
                ),
            },
            "filter_data": self.filter_manager.get_dashboard_data(),
            "positions": {
                sym: pos.to_dict() for sym, pos in self._active_positions.items()
            },
            "signals": {
                sym: sig.to_dict() for sym, sig in self._pending_signals.items()
            },
        }

    def get_monitoring_data(self) -> dict:
        """대시보드 모니터링용 데이터"""
        return {
            "strategy": "REBOUND",
            "enabled": self._enabled,
            "mode": self._mode.value,
            "positions": {
                sym: {
                    "entry_price": pos.entry_price,
                    "current_qty": pos.quantity,
                    "tp1_hit": pos.tp1_hit,
                    "tp2_hit": pos.tp2_hit,
                    "be_stop_active": pos.be_stop_active,
                    "trailing_active": pos.trailing_active,
                    "highest_price": pos.highest_price,
                    "hold_time_min": (datetime.utcnow() - pos.entry_time).total_seconds() / 60,
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
                "win_rate": (
                    self._stats["winning_trades"] / self._stats["total_trades"] * 100
                    if self._stats["total_trades"] > 0
                    else 0
                ),
                "tp1_hits": self._stats["tp1_hits"],
                "tp2_hits": self._stats["tp2_hits"],
                "stop_loss_hits": self._stats["stop_loss_hits"],
            },
            "filters": self.filter_manager.get_dashboard_data(),
        }


# Singleton
_rebound_strategy: Optional[ReboundScalperStrategy] = None


def get_rebound_strategy() -> ReboundScalperStrategy:
    """ReboundScalperStrategy 싱글톤"""
    global _rebound_strategy
    if _rebound_strategy is None:
        _rebound_strategy = ReboundScalperStrategy()
    return _rebound_strategy
