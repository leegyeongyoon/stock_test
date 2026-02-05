"""Attack Breakout Strategy

급등주 공격 전략 - MDD 5% 방어 기반 공격적 브레이크아웃 트레이딩

역할:
- 브레이크아웃 시그널 감지
- Attack Score 계산 및 레벨 결정
- Attack Gate 체크 (리스크 관문)
- 포지션 사이징 (리스크 캡 적용)
- 트랜치 분할 진입
- 트레일링/타임스톱 관리

진입 조건:
1. Attack Score ≥ L1 (70점)
2. Attack Gate 통과
3. 사용자 모드 허용 범위 내

진입 방식:
- 2~3트랜치 분할 진입
- 1차 50%: 시그널 확인 직후
- 2차 30%: 다음 5m 봉 돌파 유지
- 3차 20%: VWAP 위 강한 종가

청산 조건:
- 구조적 손절 (돌파 레벨 이탈)
- ATR 손절 (변동성 기반)
- 트레일링 스탑 (+1.2R 또는 +2%)
- 타임스톱 (45분)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Optional

import structlog

from src.config import get_settings
from src.models.schemas import OrderSide, StrategyType
from src.position.attack_position_policy import (
    AttackPositionPolicy,
    AttackPositionSizing,
    get_attack_position_policy,
)
from src.risk.attack_gate import AttackGate, AttackGateResult, get_attack_gate
from src.strategies.attack_score import (
    AttackScoreCalculator,
    AttackScoreResult,
    get_attack_score_calculator,
)
from src.strategies.base import BaseStrategy, Signal, SignalType

if TYPE_CHECKING:
    from src.features.feature_engine import FeatureEngine

logger = structlog.get_logger()
settings = get_settings()


class AttackPhase(str, Enum):
    """Attack 진입 페이즈"""

    NONE = "NONE"  # 시그널 없음
    SIGNAL_DETECTED = "SIGNAL_DETECTED"  # 시그널 감지 (1차 진입 대기)
    TRANCHE_1_FILLED = "TRANCHE_1_FILLED"  # 1차 체결 (2차 대기)
    TRANCHE_2_FILLED = "TRANCHE_2_FILLED"  # 2차 체결 (3차 대기)
    FULLY_ENTERED = "FULLY_ENTERED"  # 전체 진입 완료
    EXITING = "EXITING"  # 청산 중


@dataclass
class AttackPosition:
    """Attack 포지션 추적"""

    symbol: str
    side: OrderSide
    entry_price: float
    quantity: float
    entry_time: datetime
    stop_price: float
    stop_distance_pct: float

    # 트랜치 정보
    total_target_quantity: float
    filled_tranches: int = 0
    phase: AttackPhase = AttackPhase.TRANCHE_1_FILLED

    # 트레일링 정보
    highest_price: float = 0.0
    lowest_price: float = float("inf")
    trailing_active: bool = False
    max_pnl_pct: float = 0.0

    # Attack 정보
    attack_level: int = 0
    attack_score: float = 0.0
    overheat_score: float = 0.0  # v5.2: 과열 점수 (-15~0) - 동적 청산 전략에 사용

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "entry_time": self.entry_time.isoformat(),
            "stop_price": self.stop_price,
            "stop_distance_pct": self.stop_distance_pct,
            "phase": self.phase.value,
            "filled_tranches": self.filled_tranches,
            "trailing_active": self.trailing_active,
            "max_pnl_pct": self.max_pnl_pct,
            "attack_level": self.attack_level,
            "attack_score": self.attack_score,
            "overheat_score": self.overheat_score,
        }


@dataclass
class PendingAttackSignal:
    """대기 중인 Attack 시그널"""

    symbol: str
    detected_at: datetime
    score_result: AttackScoreResult
    gate_result: AttackGateResult
    sizing: AttackPositionSizing
    breakout_level: float
    phase: AttackPhase = AttackPhase.SIGNAL_DETECTED

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "detected_at": self.detected_at.isoformat(),
            "score": self.score_result.total_score,
            "level": self.score_result.level,
            "gate_allowed": self.gate_result.allowed,
            "final_level": self.gate_result.final_level,
            "phase": self.phase.value,
        }


class AttackBreakoutStrategy(BaseStrategy):
    """
    급등주 공격 전략

    MDD 5% 제약 하에서 브레이크아웃 공격
    Signal Score → Gate Check → Position Sizing → Entry
    """

    def __init__(self, feature_engine: Optional["FeatureEngine"] = None) -> None:
        super().__init__(StrategyType.ATTACK)

        self.feature_engine = feature_engine

        # 구성요소
        self.score_calculator = get_attack_score_calculator()
        self.attack_gate = get_attack_gate()
        self.position_policy = get_attack_position_policy()

        # 설정
        self.attack_mode = settings.attack_mode  # OFF/NORMAL/PLUS/MAX

        # 트레일링 설정
        self.trail_trigger_r = settings.attack_trail_trigger_r  # 1.2R
        self.trail_trigger_pct = settings.attack_trail_trigger_pct  # 2%
        self.time_stop_min = settings.attack_time_stop_min  # 45분

        # 상태
        self._btc_regime: str = "NEUTRAL"
        self._btc_is_volatile: bool = False
        self._dd_tier: int = 0
        self._daily_loss: float = 0.0

        # 포지션 추적
        self._active_positions: dict[str, AttackPosition] = {}
        self._pending_signals: dict[str, PendingAttackSignal] = {}

        # 통계
        self._stats = {
            "signals_detected": 0,
            "signals_entered": 0,
            "signals_blocked": 0,
            "exits_stop": 0,
            "exits_trailing": 0,
            "exits_time": 0,
        }

    def set_feature_engine(self, feature_engine: "FeatureEngine") -> None:
        """Feature Engine 설정"""
        self.feature_engine = feature_engine

    def update_risk_state(
        self,
        dd_tier: int,
        daily_loss: float,
        regime: str = "NEUTRAL",
        is_volatile: bool = False,
    ) -> None:
        """리스크 상태 업데이트 (RiskOverlay에서 호출)"""
        self._dd_tier = dd_tier
        self._daily_loss = daily_loss
        self._btc_regime = regime
        self._btc_is_volatile = is_volatile

    def set_attack_mode(self, mode: str) -> None:
        """Attack 모드 변경"""
        mode_upper = mode.upper()
        if mode_upper in ["OFF", "NORMAL", "PLUS", "MAX"]:
            self.attack_mode = mode_upper
            logger.info("Attack mode changed", mode=mode_upper)

    async def generate_signal(self, market_data: dict) -> Optional[Signal]:
        """
        Attack 시그널 생성

        market_data:
            symbol: str
            price: float (현재가)
            highest_12_5m: float (최근 12개 5m 고점)
            highest_24h: float (24h 고점, optional)
            rvol: float (RVOL_5m)
            volume_24h: float (24h 거래대금)
            close_pos: float (ClosePos 0~1)
            vwap: float (VWAP_5m)
            sma_50_1h: float (1h SMA50, optional)
            atr_pct_1h: float (1h ATR%, optional)
            spread_bps: float (스프레드 bps)
            change_rate: float (전일 대비 변화율)
            equity: float (총 자본)
        """
        if not self._enabled:
            return None

        # Attack Mode OFF
        if self.attack_mode == "OFF":
            return None

        symbol = market_data.get("symbol")
        price = market_data.get("price", 0)
        equity = market_data.get("equity", 0)

        if price <= 0 or equity <= 0:
            return None

        # 이미 포지션이 있으면 스킵
        if symbol in self._active_positions:
            return None

        # 대기 중인 시그널 확인 (트랜치 2, 3 진입)
        pending = self._pending_signals.get(symbol)
        if pending:
            return await self._check_pending_tranche(pending, market_data)

        # 새 시그널 감지
        return await self._detect_attack_signal(market_data)

    async def _detect_attack_signal(self, market_data: dict) -> Optional[Signal]:
        """Attack 시그널 감지"""
        symbol = market_data.get("symbol")
        price = market_data.get("price", 0)
        equity = market_data.get("equity", 0)
        change_rate = market_data.get("change_rate", 0)
        atr_pct = market_data.get("atr_pct_1h", 0.01)
        highest_12 = market_data.get("highest_12_5m", 0)

        # 1. Attack Score 계산
        score_result = self.score_calculator.calculate(market_data)

        if score_result.level == 0:
            return None  # 점수 미달

        # 2. Attack Gate 체크
        gate_result = self.attack_gate.check(
            attack_level=score_result.level,
            dd_tier=self._dd_tier,
            regime=self._btc_regime,
            daily_loss=self._daily_loss,
            is_volatile=self._btc_is_volatile,
            change_rate=change_rate,
            attack_mode=self.attack_mode,
        )

        if not gate_result.allowed:
            self._stats["signals_blocked"] += 1
            logger.debug(
                "Attack signal blocked by gate",
                symbol=symbol,
                reasons=gate_result.reasons,
            )
            return None

        # 3. 포지션 사이징
        breakout_level = highest_12 if highest_12 > 0 else price * 0.99

        sizing = self.position_policy.calculate_full(
            symbol=symbol,
            equity=equity,
            entry_price=price,
            breakout_level=breakout_level,
            level=gate_result.final_level,
            attack_mode=self.attack_mode,
            atr_pct=atr_pct,
            dd_tier=self._dd_tier,
        )

        if sizing.final_value <= 0:
            return None

        # 4. 대기 시그널 등록 (1차 트랜치)
        pending = PendingAttackSignal(
            symbol=symbol,
            detected_at=datetime.utcnow(),
            score_result=score_result,
            gate_result=gate_result,
            sizing=sizing,
            breakout_level=breakout_level,
            phase=AttackPhase.SIGNAL_DETECTED,
        )
        self._pending_signals[symbol] = pending

        self._stats["signals_detected"] += 1

        logger.info(
            "Attack signal detected",
            symbol=symbol,
            score=f"{score_result.total_score:.1f}",
            level=gate_result.final_level,
            target_value=f"{sizing.final_value:,.0f}",
            stop=f"{sizing.stop_price:,.0f}",
        )

        # 1차 트랜치 시그널 반환
        tranche_1 = sizing.tranches[0] if sizing.tranches else {}

        return Signal(
            strategy=StrategyType.ATTACK,
            signal_type=SignalType.ENTRY,
            symbol=symbol,
            side=OrderSide.BUY,  # Attack은 Long-only
            quantity=tranche_1.get("quantity", sizing.quantity * 0.5),
            price=price,
            reason=f"Attack L{gate_result.final_level}, Score={score_result.total_score:.0f}",
            confidence=min(1.0, score_result.total_score / 100),
            metadata={
                "attack_level": gate_result.final_level,
                "attack_score": score_result.total_score,
                "overheat_score": score_result.overheat_score,  # v5.2: 동적 청산 전략용
                "tranche": 1,
                "total_tranches": len(sizing.tranches),
                "stop_price": sizing.stop_price,
                "stop_distance_pct": sizing.stop_distance_pct,
                "target_total_quantity": sizing.quantity,
                "breakout_level": breakout_level,
            },
        )

    async def _check_pending_tranche(
        self,
        pending: PendingAttackSignal,
        market_data: dict,
    ) -> Optional[Signal]:
        """대기 중인 트랜치 진입 체크"""
        symbol = pending.symbol
        price = market_data.get("price", 0)
        vwap = market_data.get("vwap", 0)
        rvol = market_data.get("rvol", 1.0)

        elapsed = (datetime.utcnow() - pending.detected_at).total_seconds()

        # 2차 트랜치 조건 (5분 후, 돌파 유지)
        if pending.phase == AttackPhase.TRANCHE_1_FILLED:
            if elapsed >= 300:  # 5분 경과
                if price > pending.breakout_level and rvol >= 1.5:
                    pending.phase = AttackPhase.TRANCHE_2_FILLED

                    tranche_2 = (
                        pending.sizing.tranches[1]
                        if len(pending.sizing.tranches) > 1
                        else {}
                    )

                    logger.info(
                        "Attack tranche 2 triggered",
                        symbol=symbol,
                        price=price,
                    )

                    return Signal(
                        strategy=StrategyType.ATTACK,
                        signal_type=SignalType.ENTRY,
                        symbol=symbol,
                        side=OrderSide.BUY,
                        quantity=tranche_2.get(
                            "quantity", pending.sizing.quantity * 0.3
                        ),
                        price=price,
                        reason="Attack Tranche 2",
                        metadata={
                            "tranche": 2,
                            "attack_level": pending.gate_result.final_level,
                        },
                    )
                else:
                    # 돌파 실패 - 대기 종료
                    self._pending_signals.pop(symbol, None)
                    logger.warning(
                        "Attack tranche 2 conditions not met",
                        symbol=symbol,
                        price=price,
                        breakout_level=pending.breakout_level,
                    )

        # 3차 트랜치 조건 (10분 후, VWAP 위 강한 종가)
        elif pending.phase == AttackPhase.TRANCHE_2_FILLED:
            if elapsed >= 600:  # 10분 경과
                if price > vwap and price > pending.breakout_level * 1.005:
                    pending.phase = AttackPhase.FULLY_ENTERED
                    self._pending_signals.pop(symbol, None)

                    tranche_3 = (
                        pending.sizing.tranches[2]
                        if len(pending.sizing.tranches) > 2
                        else {}
                    )

                    logger.info(
                        "Attack tranche 3 triggered",
                        symbol=symbol,
                        price=price,
                    )

                    return Signal(
                        strategy=StrategyType.ATTACK,
                        signal_type=SignalType.ENTRY,
                        symbol=symbol,
                        side=OrderSide.BUY,
                        quantity=tranche_3.get(
                            "quantity", pending.sizing.quantity * 0.2
                        ),
                        price=price,
                        reason="Attack Tranche 3",
                        metadata={
                            "tranche": 3,
                            "attack_level": pending.gate_result.final_level,
                        },
                    )
                else:
                    # 3차 조건 미충족 - 대기 종료 (2차까지만)
                    self._pending_signals.pop(symbol, None)
                    logger.info(
                        "Attack tranche 3 skipped - conditions not optimal",
                        symbol=symbol,
                    )

        return None

    def _get_dynamic_exit_params(self, overheat_score: float) -> dict:
        """
        v5.2: 과열도 기반 동적 청산 파라미터 계산

        과열도 높음 (overheat <= -10): 이미 많이 올랐음 → 빨리 익절
        과열도 중간 (-10 < overheat <= -5): 기본값
        과열도 낮음 (overheat > -5): 초기 단계 → 오래 홀드
        """
        if overheat_score <= -10:
            # 과열 심함: 빠른 익절, 타이트한 트레일링
            return {
                "trail_trigger_r": 0.8,       # 0.8R에서 트레일링 활성화 (빠름)
                "trail_trigger_pct": 0.015,   # 1.5%에서 활성화
                "trailing_mult": 0.7,         # 손절거리의 70%로 타이트하게
                "time_stop_min": 30,          # 30분 타임스톱 (짧게)
                "profit_extend_threshold": 0.01,  # 1% 이상 수익 시만 연장
            }
        elif overheat_score <= -5:
            # 과열 중간: 기본값
            return {
                "trail_trigger_r": self.trail_trigger_r,
                "trail_trigger_pct": self.trail_trigger_pct,
                "trailing_mult": 0.5,
                "time_stop_min": self.time_stop_min,
                "profit_extend_threshold": 0.005,
            }
        else:
            # 과열 낮음: 오래 홀드, 느슨한 트레일링
            return {
                "trail_trigger_r": 1.5,       # 1.5R에서 트레일링 활성화 (느림)
                "trail_trigger_pct": 0.025,   # 2.5%에서 활성화
                "trailing_mult": 0.3,         # 손절거리의 30%로 느슨하게
                "time_stop_min": 60,          # 60분 타임스톱 (길게)
                "profit_extend_threshold": 0.003,  # 0.3% 이상 수익 시 연장
            }

    async def should_exit(self, position: dict, market_data: dict) -> Optional[Signal]:
        """
        청산 조건 확인

        v5.2 업데이트: 과열도 기반 동적 청산 전략
        - 과열 높음 → 빠른 익절 (1.5%, 0.8R)
        - 과열 낮음 → 오래 홀드 (2.5%, 1.5R)

        조건:
        1. 구조적 손절 (스탑 가격 이탈)
        2. 트레일링 스탑 (과열도에 따라 동적)
        3. 타임스톱 (과열도에 따라 동적)
        4. VOLATILE 레짐 (선택적)
        """
        symbol = position.get("symbol")
        entry_price = position.get("avg_price", 0)
        quantity = position.get("quantity", 0)
        entry_time = position.get("opened_at")

        current_price = market_data.get("price", 0)

        if entry_price <= 0 or current_price <= 0:
            return None

        # Attack 포지션 추적
        attack_pos = self._active_positions.get(symbol)
        if not attack_pos:
            return None

        # v5.2: 과열도 기반 동적 파라미터
        exit_params = self._get_dynamic_exit_params(attack_pos.overheat_score)

        # 고점 업데이트
        if current_price > attack_pos.highest_price:
            attack_pos.highest_price = current_price

        # PnL 계산
        pnl_pct = (current_price - entry_price) / entry_price
        if pnl_pct > attack_pos.max_pnl_pct:
            attack_pos.max_pnl_pct = pnl_pct

        from_high_drop = (current_price - attack_pos.highest_price) / attack_pos.highest_price

        # 1. 구조적 손절 (스탑 가격 이탈)
        if current_price <= attack_pos.stop_price:
            self._stats["exits_stop"] += 1
            logger.warning(
                "Attack stop loss triggered",
                symbol=symbol,
                price=current_price,
                stop=attack_pos.stop_price,
                pnl_pct=f"{pnl_pct:.2%}",
            )
            return Signal(
                strategy=StrategyType.ATTACK,
                signal_type=SignalType.EXIT,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=quantity,
                reason=f"Stop loss: {pnl_pct:.2%}",
                metadata={"exit_type": "stop_loss"},
            )

        # 2. 트레일링 스탑 (v5.2: 과열도 기반 동적 파라미터)
        # R 배수 계산 (리스크 대비 수익)
        r_multiple = pnl_pct / attack_pos.stop_distance_pct if attack_pos.stop_distance_pct > 0 else 0

        # 트레일링 활성화 체크 (동적 임계값)
        if not attack_pos.trailing_active:
            if r_multiple >= exit_params["trail_trigger_r"] or pnl_pct >= exit_params["trail_trigger_pct"]:
                attack_pos.trailing_active = True
                logger.info(
                    "Attack trailing activated (dynamic)",
                    symbol=symbol,
                    r_multiple=f"{r_multiple:.1f}R",
                    pnl_pct=f"{pnl_pct:.2%}",
                    overheat=f"{attack_pos.overheat_score:.0f}",
                    trigger_r=exit_params["trail_trigger_r"],
                    trigger_pct=f"{exit_params['trail_trigger_pct']:.1%}",
                )

        # 트레일링 스탑 체크 (동적 배수)
        if attack_pos.trailing_active:
            # 고점 대비 하락폭이 손절 거리의 X% 초과 시 청산 (X는 과열도에 따라 다름)
            trailing_stop_pct = attack_pos.stop_distance_pct * exit_params["trailing_mult"]
            if from_high_drop <= -trailing_stop_pct:
                self._stats["exits_trailing"] += 1
                logger.info(
                    "Attack trailing stop triggered (dynamic)",
                    symbol=symbol,
                    from_high=f"{from_high_drop:.2%}",
                    pnl_pct=f"{pnl_pct:.2%}",
                    trailing_mult=exit_params["trailing_mult"],
                )
                return Signal(
                    strategy=StrategyType.ATTACK,
                    signal_type=SignalType.EXIT,
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=quantity,
                    reason=f"Trailing stop: {from_high_drop:.2%}",
                    metadata={
                        "exit_type": "trailing",
                        "r_multiple": r_multiple,
                        "overheat_score": attack_pos.overheat_score,
                    },
                )

        # 3. 타임스톱 (v5.2: 과열도 기반 동적)
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)

            elapsed = datetime.utcnow() - entry_time
            dynamic_time_stop = exit_params["time_stop_min"]

            if elapsed > timedelta(minutes=dynamic_time_stop):
                # 수익 중이면 타임스톱 연장 (동적 임계값)
                if pnl_pct > exit_params["profit_extend_threshold"]:
                    pass  # 충분한 수익 시 연장
                else:
                    self._stats["exits_time"] += 1
                    logger.info(
                        "Attack time stop triggered (dynamic)",
                        symbol=symbol,
                        elapsed_min=elapsed.total_seconds() / 60,
                        pnl_pct=f"{pnl_pct:.2%}",
                        time_stop_min=dynamic_time_stop,
                    )
                    return Signal(
                        strategy=StrategyType.ATTACK,
                        signal_type=SignalType.EXIT,
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=quantity,
                        reason=f"Time stop: {elapsed.total_seconds()/60:.0f}min",
                        metadata={
                            "exit_type": "time_stop",
                            "overheat_score": attack_pos.overheat_score,
                        },
                    )

        # 4. VOLATILE 레짐 (선택적 - 손익 상태에 따라)
        if self._btc_is_volatile:
            if pnl_pct < 0:
                # 손실 중이면 VOLATILE에서 청산
                logger.warning(
                    "Attack VOLATILE exit",
                    symbol=symbol,
                    pnl_pct=f"{pnl_pct:.2%}",
                )
                return Signal(
                    strategy=StrategyType.ATTACK,
                    signal_type=SignalType.EXIT,
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=quantity,
                    reason="VOLATILE regime with loss",
                    metadata={"exit_type": "volatile"},
                )

        return None

    def get_position_size(self, signal: Signal, available_capital: float) -> float:
        """포지션 사이즈 계산 (이미 시그널에 포함됨)"""
        return signal.quantity

    def track_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        stop_price: float,
        stop_distance_pct: float,
        attack_level: int = 0,
        attack_score: float = 0.0,
        total_target_quantity: float = 0.0,
        overheat_score: float = 0.0,  # v5.2: 과열 점수
    ) -> None:
        """포지션 추적 시작"""
        self._active_positions[symbol] = AttackPosition(
            symbol=symbol,
            side=OrderSide.BUY,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.utcnow(),
            stop_price=stop_price,
            stop_distance_pct=stop_distance_pct,
            total_target_quantity=total_target_quantity or quantity,
            highest_price=entry_price,
            attack_level=attack_level,
            attack_score=attack_score,
            overheat_score=overheat_score,
        )
        self._stats["signals_entered"] += 1

    def update_position(self, symbol: str, added_quantity: float, tranche: int) -> None:
        """트랜치 추가 시 포지션 업데이트"""
        pos = self._active_positions.get(symbol)
        if pos:
            pos.quantity += added_quantity
            pos.filled_tranches = tranche
            if tranche >= 3:
                pos.phase = AttackPhase.FULLY_ENTERED

    def close_position(self, symbol: str) -> None:
        """포지션 추적 종료"""
        self._active_positions.pop(symbol, None)
        self._pending_signals.pop(symbol, None)

    def get_active_positions(self) -> dict:
        """활성 Attack 포지션 조회"""
        return {sym: pos.to_dict() for sym, pos in self._active_positions.items()}

    def get_pending_signals(self) -> dict:
        """대기 중인 시그널 조회"""
        return {sym: sig.to_dict() for sym, sig in self._pending_signals.items()}

    def get_status(self) -> dict:
        """전략 상태 조회"""
        return {
            "enabled": self._enabled,
            "attack_mode": self.attack_mode,
            "dd_tier": self._dd_tier,
            "daily_loss": self._daily_loss,
            "btc_regime": self._btc_regime,
            "btc_is_volatile": self._btc_is_volatile,
            "active_positions": len(self._active_positions),
            "pending_signals": len(self._pending_signals),
            "stats": self._stats,
            "gate_status": self.attack_gate.get_status(),
            "position_policy": self.position_policy.get_status(),
        }

    def get_attack_score(self, market_data: dict) -> AttackScoreResult:
        """특정 심볼의 Attack Score 조회"""
        return self.score_calculator.calculate(market_data)


# Singleton
_attack_strategy: Optional[AttackBreakoutStrategy] = None


def get_attack_strategy() -> AttackBreakoutStrategy:
    """AttackBreakoutStrategy 싱글톤"""
    global _attack_strategy
    if _attack_strategy is None:
        _attack_strategy = AttackBreakoutStrategy()
    return _attack_strategy
