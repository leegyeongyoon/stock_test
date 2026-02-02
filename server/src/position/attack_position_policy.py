"""Attack Position Policy

공격 포지션 정책 모듈

역할:
- Attack 레벨에 따른 목표 비중 계산
- 리스크 캡 기반 실제 포지션 제한 (Capital Profile 연동)
- 손절가 계산 (구조적 스탑 vs ATR 스탑)
- 트랜치 분할 진입 관리

핵심 공식:
final_position = min(target_allocation, risk_cap, symbol_exposure_cap)

Where:
- target_allocation = equity * allocation_for_level
- risk_cap_value = (equity * max_risk_per_trade) / stop_distance_pct
- symbol_exposure_cap = equity * max_symbol_exposure

Capital Profile 연동:
- Growth Mode: 리스크 0.7%~1.0%, L1/L2/L3 허용
- Preserve Mode: 리스크 0.25%~0.5%, L1/L2만 허용, 슬리피지 가드 강화
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.risk.capital_profile import CapitalProfileManager, get_capital_profile_manager

logger = structlog.get_logger()
settings = get_settings()


class StopType(str, Enum):
    """스탑 타입"""

    STRUCT = "STRUCT"  # 구조적 스탑 (돌파 레벨 이탈)
    ATR = "ATR"  # ATR 기반 스탑
    HYBRID = "HYBRID"  # 더 타이트한 스탑 선택


@dataclass
class AttackPositionSizing:
    """Attack 포지션 사이징 결과"""

    symbol: str
    level: int  # Attack Level (1~3)
    attack_mode: str  # OFF/NORMAL/PLUS/MAX

    # 가격 정보
    entry_price: float
    stop_price: float
    stop_type: StopType
    stop_distance_pct: float  # 손절 거리 (%)

    # 비중 계산
    target_value: float  # 레벨 기반 목표 비중
    risk_cap_value: float  # 리스크 캡 기반 제한
    exposure_cap_value: float  # 심볼 노출 제한
    final_value: float  # 최종 포지션 가치

    # 수량
    quantity: float  # 최종 수량
    max_risk_amount: float  # 최대 손실액 (KRW)

    # 트랜치 정보
    tranches: list[dict] = field(default_factory=list)

    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "level": self.level,
            "attack_mode": self.attack_mode,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "stop_type": self.stop_type.value,
            "stop_distance_pct": self.stop_distance_pct,
            "target_value": self.target_value,
            "risk_cap_value": self.risk_cap_value,
            "exposure_cap_value": self.exposure_cap_value,
            "final_value": self.final_value,
            "quantity": self.quantity,
            "max_risk_amount": self.max_risk_amount,
            "tranches": self.tranches,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class StopCalculation:
    """스탑 계산 결과"""

    stop_price: float
    stop_type: StopType
    stop_distance_pct: float
    struct_stop: float
    atr_stop: float
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "stop_price": self.stop_price,
            "stop_type": self.stop_type.value,
            "stop_distance_pct": self.stop_distance_pct,
            "struct_stop": self.struct_stop,
            "atr_stop": self.atr_stop,
            "details": self.details,
        }


class AttackPositionPolicy:
    """
    Attack 포지션 정책

    포지션 사이징 핵심 원칙:
    1. 레벨 기반 목표 비중 (10%/30%/50%)
    2. 리스크 캡으로 실제 크기 제한 (Capital Profile 기반)
    3. 심볼당 노출 한도 적용
    4. 최종 = min(목표, 리스크캡, 노출한도)

    Capital Profile 연동:
    - Growth Mode: 높은 리스크 허용 (0.7%~1.0%)
    - Preserve Mode: 낮은 리스크 (0.25%~0.5%), 슬리피지 가드 강화
    """

    def __init__(
        self,
        capital_profile: CapitalProfileManager = None,
    ):
        # Capital Profile 연동
        self.capital_profile = capital_profile or get_capital_profile_manager()

        # 레벨별 목표 비중
        self.level_allocation = {
            1: settings.attack_alloc_l1,  # 0.10
            2: settings.attack_alloc_l2,  # 0.30
            3: settings.attack_alloc_l3,  # 0.50
        }

        # 모드별 트레이드당 최대 리스크 (기본값 - Capital Profile이 오버라이드)
        self.mode_risk = {
            "OFF": 0.0,
            "NORMAL": settings.attack_risk_per_trade_normal,  # 0.005
            "PLUS": settings.attack_risk_per_trade_plus,  # 0.006
            "MAX": settings.attack_risk_per_trade_max,  # 0.007
        }

        # 스탑 설정
        self.stop_struct_buffer = settings.attack_stop_struct_buffer  # 0.003
        self.stop_atr_mult = settings.attack_stop_atr_mult  # 1.5
        self.stop_min_pct = settings.attack_stop_min_pct  # 0.005
        self.stop_max_pct = settings.attack_stop_max_pct  # 0.015
        self.slippage_guard_base = settings.attack_slippage_guard  # 0.003

        # 트랜치 설정
        self.tranche_1_pct = settings.attack_tranche_1_pct  # 0.50
        self.tranche_2_pct = settings.attack_tranche_2_pct  # 0.30
        self.tranche_3_pct = settings.attack_tranche_3_pct  # 0.20

        # 노출 제한 (Attack: 50% 단일 포지션 지원)
        self.max_symbol_exposure = settings.max_symbol_exposure_attack  # 0.55

        logger.info(
            "AttackPositionPolicy initialized with Capital Profile",
            capital_mode=self.capital_profile.get_current_mode().value,
        )

    @property
    def slippage_guard(self) -> float:
        """슬리피지 가드 (Capital Profile 배수 적용)"""
        return self.capital_profile.get_slippage_guard(self.slippage_guard_base)

    def calculate_stop(
        self,
        entry_price: float,
        breakout_level: float,
        atr_pct: float = 0.01,
    ) -> StopCalculation:
        """
        스탑 가격 계산

        두 가지 스탑 중 더 타이트한(높은) 것 선택:
        1. 구조적 스탑: 돌파 레벨 - 버퍼
        2. ATR 스탑: 진입가 - (ATR * 배수)

        Args:
            entry_price: 진입가
            breakout_level: 돌파 레벨 (최근 고점)
            atr_pct: ATR 비율 (기본 1%)

        Returns:
            StopCalculation: 스탑 계산 결과
        """
        details = {}

        # 1. 구조적 스탑 계산
        # 돌파 레벨 바로 아래 + 슬리피지 가드
        struct_stop = breakout_level * (1 - self.stop_struct_buffer - self.slippage_guard)
        details["struct_buffer"] = self.stop_struct_buffer
        details["slippage_guard"] = self.slippage_guard

        # 2. ATR 스탑 계산
        atr_distance = atr_pct * self.stop_atr_mult
        atr_stop = entry_price * (1 - atr_distance - self.slippage_guard)
        details["atr_pct"] = atr_pct
        details["atr_mult"] = self.stop_atr_mult
        details["atr_distance"] = atr_distance

        # 3. 더 타이트한 스탑 선택 (높은 가격 = 손실 제한)
        if struct_stop >= atr_stop:
            stop_price = struct_stop
            stop_type = StopType.STRUCT
        else:
            stop_price = atr_stop
            stop_type = StopType.ATR

        # 4. 최소/최대 스탑 거리 적용
        raw_distance = (entry_price - stop_price) / entry_price
        details["raw_distance"] = raw_distance

        if raw_distance < self.stop_min_pct:
            # 최소 스탑 거리 적용
            stop_price = entry_price * (1 - self.stop_min_pct)
            stop_type = StopType.HYBRID
            details["adjusted"] = "min"
        elif raw_distance > self.stop_max_pct:
            # 최대 스탑 거리 적용
            stop_price = entry_price * (1 - self.stop_max_pct)
            stop_type = StopType.HYBRID
            details["adjusted"] = "max"

        # 최종 스탑 거리
        stop_distance_pct = (entry_price - stop_price) / entry_price

        logger.debug(
            "Stop calculated",
            entry=f"{entry_price:,.0f}",
            struct_stop=f"{struct_stop:,.0f}",
            atr_stop=f"{atr_stop:,.0f}",
            final_stop=f"{stop_price:,.0f}",
            stop_type=stop_type.value,
            distance=f"{stop_distance_pct:.2%}",
        )

        return StopCalculation(
            stop_price=stop_price,
            stop_type=stop_type,
            stop_distance_pct=stop_distance_pct,
            struct_stop=struct_stop,
            atr_stop=atr_stop,
            details=details,
        )

    def calculate_position(
        self,
        symbol: str,
        equity: float,
        entry_price: float,
        level: int,
        stop_distance_pct: float,
        attack_mode: str = "NORMAL",
        dd_tier: int = 0,
        regime: str = "NEUTRAL",
    ) -> AttackPositionSizing:
        """
        포지션 사이징 계산

        핵심 공식:
        final = min(target_value, risk_cap_value, exposure_cap_value)

        Capital Profile 연동:
        - Growth Mode: 높은 리스크 허용 (0.7%~1.0%)
        - Preserve Mode: 낮은 리스크 (0.25%~0.5%)

        Args:
            symbol: 심볼
            equity: 총 자본 (KRW)
            entry_price: 진입가
            level: Attack 레벨 (1~3)
            stop_distance_pct: 손절 거리 (%)
            attack_mode: Attack 모드 (OFF/NORMAL/PLUS/MAX)
            dd_tier: 현재 DD Tier (리스크 축소용)
            regime: BTC Regime (리스크 조정용)

        Returns:
            AttackPositionSizing: 포지션 사이징 결과
        """
        if level <= 0:
            # 레벨 0이면 포지션 없음
            return AttackPositionSizing(
                symbol=symbol,
                level=0,
                attack_mode=attack_mode,
                entry_price=entry_price,
                stop_price=0,
                stop_type=StopType.STRUCT,
                stop_distance_pct=0,
                target_value=0,
                risk_cap_value=0,
                exposure_cap_value=0,
                final_value=0,
                quantity=0,
                max_risk_amount=0,
                tranches=[],
            )

        # 1. 목표 비중 계산
        target_allocation = self.level_allocation.get(level, 0.10)
        target_value = equity * target_allocation

        # 2. 리스크 캡 계산 (Capital Profile 연동)
        # Capital Profile에서 리스크 가져오기 (DD Tier, Regime 반영됨)
        risk_result = self.capital_profile.get_risk_per_trade(
            dd_tier=dd_tier,
            attack_mode=attack_mode,
            attack_level=level,
            regime=regime,
        )
        base_risk = risk_result.risk_cap

        max_risk_amount = equity * base_risk

        # 손절 거리가 0이면 리스크 캡 무한대 방지
        if stop_distance_pct <= 0:
            stop_distance_pct = self.stop_min_pct

        risk_cap_value = max_risk_amount / stop_distance_pct

        # 3. 심볼 노출 제한
        exposure_cap_value = equity * self.max_symbol_exposure

        # 4. 최종 포지션 = min(목표, 리스크캡, 노출한도)
        final_value = min(target_value, risk_cap_value, exposure_cap_value)

        # 5. 수량 계산
        quantity = final_value / entry_price

        # 6. 트랜치 분할
        tranches = self._calculate_tranches(final_value, entry_price)

        logger.info(
            "Position sized",
            symbol=symbol,
            level=level,
            capital_mode=risk_result.profile_mode.value,
            risk_per_trade=f"{base_risk:.3%}",
            target=f"{target_value:,.0f}",
            risk_cap=f"{risk_cap_value:,.0f}",
            exposure_cap=f"{exposure_cap_value:,.0f}",
            final=f"{final_value:,.0f}",
            max_risk=f"{max_risk_amount:,.0f}",
            quantity=f"{quantity:.4f}",
            risk_reason=risk_result.reason,
        )

        return AttackPositionSizing(
            symbol=symbol,
            level=level,
            attack_mode=attack_mode,
            entry_price=entry_price,
            stop_price=entry_price * (1 - stop_distance_pct),
            stop_type=StopType.HYBRID,
            stop_distance_pct=stop_distance_pct,
            target_value=target_value,
            risk_cap_value=risk_cap_value,
            exposure_cap_value=exposure_cap_value,
            final_value=final_value,
            quantity=quantity,
            max_risk_amount=max_risk_amount,
            tranches=tranches,
        )

    def _calculate_tranches(
        self,
        total_value: float,
        entry_price: float,
    ) -> list[dict]:
        """
        트랜치 분할 계산

        분할 비율:
        - 1차: 50% (시그널 확인 직후)
        - 2차: 30% (다음 봉 돌파 유지)
        - 3차: 20% (VWAP 위 강한 종가)

        Args:
            total_value: 총 포지션 가치
            entry_price: 진입가

        Returns:
            list[dict]: 트랜치 정보 리스트
        """
        tranches = []

        # 1차 트랜치
        t1_value = total_value * self.tranche_1_pct
        t1_qty = t1_value / entry_price
        tranches.append(
            {
                "tranche": 1,
                "pct": self.tranche_1_pct,
                "value": t1_value,
                "quantity": t1_qty,
                "condition": "signal_confirmed",
                "filled": False,
            }
        )

        # 2차 트랜치
        t2_value = total_value * self.tranche_2_pct
        t2_qty = t2_value / entry_price
        tranches.append(
            {
                "tranche": 2,
                "pct": self.tranche_2_pct,
                "value": t2_value,
                "quantity": t2_qty,
                "condition": "breakout_held",
                "filled": False,
            }
        )

        # 3차 트랜치
        t3_value = total_value * self.tranche_3_pct
        t3_qty = t3_value / entry_price
        tranches.append(
            {
                "tranche": 3,
                "pct": self.tranche_3_pct,
                "value": t3_value,
                "quantity": t3_qty,
                "condition": "vwap_strong_close",
                "filled": False,
            }
        )

        return tranches

    def calculate_full(
        self,
        symbol: str,
        equity: float,
        entry_price: float,
        breakout_level: float,
        level: int,
        attack_mode: str = "NORMAL",
        atr_pct: float = 0.01,
        dd_tier: int = 0,
        regime: str = "NEUTRAL",
    ) -> AttackPositionSizing:
        """
        스탑 계산 + 포지션 사이징 통합

        Capital Profile 연동:
        - Growth Mode: 높은 리스크 허용
        - Preserve Mode: 낮은 리스크, 슬리피지 가드 강화

        Args:
            symbol: 심볼
            equity: 총 자본
            entry_price: 진입가
            breakout_level: 돌파 레벨
            level: Attack 레벨
            attack_mode: Attack 모드
            atr_pct: ATR 비율
            dd_tier: DD Tier
            regime: BTC Regime

        Returns:
            AttackPositionSizing: 완전한 포지션 사이징
        """
        # 1. 스탑 계산 (슬리피지 가드가 Capital Profile 기반)
        stop_calc = self.calculate_stop(
            entry_price=entry_price,
            breakout_level=breakout_level,
            atr_pct=atr_pct,
        )

        # 2. 포지션 사이징 (리스크가 Capital Profile 기반)
        sizing = self.calculate_position(
            symbol=symbol,
            equity=equity,
            entry_price=entry_price,
            level=level,
            stop_distance_pct=stop_calc.stop_distance_pct,
            attack_mode=attack_mode,
            dd_tier=dd_tier,
            regime=regime,
        )

        # 스탑 정보 업데이트
        sizing.stop_price = stop_calc.stop_price
        sizing.stop_type = stop_calc.stop_type
        sizing.stop_distance_pct = stop_calc.stop_distance_pct

        return sizing

    def get_status(self) -> dict:
        """현재 정책 설정 상태"""
        return {
            "level_allocation": self.level_allocation,
            "mode_risk": self.mode_risk,
            "stop_settings": {
                "struct_buffer": self.stop_struct_buffer,
                "atr_mult": self.stop_atr_mult,
                "min_pct": self.stop_min_pct,
                "max_pct": self.stop_max_pct,
                "slippage_guard": self.slippage_guard,
            },
            "tranche_settings": {
                "t1": self.tranche_1_pct,
                "t2": self.tranche_2_pct,
                "t3": self.tranche_3_pct,
            },
            "max_symbol_exposure": self.max_symbol_exposure,
        }


# Singleton
_attack_position_policy: Optional[AttackPositionPolicy] = None


def get_attack_position_policy() -> AttackPositionPolicy:
    """AttackPositionPolicy 싱글톤"""
    global _attack_position_policy
    if _attack_position_policy is None:
        _attack_position_policy = AttackPositionPolicy()
    return _attack_position_policy
