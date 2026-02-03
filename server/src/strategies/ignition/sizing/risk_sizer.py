"""
리스크 기반 포지션 사이징 (v4.0)

핵심 공식:
position_value = (equity * risk_per_trade) / effective_stop_distance_pct

비중(포지션 가치) = 크게 가능
리스크(손절까지 손실) = 작게 고정

이 방식으로 "50% 투자"를 하고 싶으면
손절이 짧고, 레벨이 명확한 경우에만 허용됨
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.config import get_settings
from src.strategies.ignition.sizing.attack_level_config import (
    AttackLevel,
    AttackLevelConfig,
    get_attack_level_config,
)

logger = structlog.get_logger()


@dataclass
class RiskSizingResult:
    """리스크 기반 사이징 결과"""

    # 포지션 정보
    position_value: float  # 포지션 금액 (KRW)
    position_pct: float  # 자산 대비 비중 (0-1)
    quantity: float  # 수량

    # 리스크 정보
    risk_amount: float  # 리스크 금액 (손절 시 손실액)
    risk_pct: float  # 리스크 비율 (자산 대비)
    effective_stop_pct: float  # 유효 손절폭 (%)

    # 메타 정보
    attack_level: AttackLevel
    entry_price: float
    stop_loss_price: float
    capped: bool = False  # 상한에 걸렸는지
    cap_reason: str = ""


class RiskSizer:
    """
    리스크 기반 포지션 사이저

    핵심:
    - "비중"이 아니라 "리스크"로 사이징
    - 손절폭이 짧으면 비중이 커지고, 길면 비중이 작아짐
    - MDD 5%를 지키면서도 공격적 운영 가능

    안전장치:
    - 최소 손절폭 체크 (너무 짧으면 비중 폭발 방지)
    - 최대 비중 캡 (레벨별 절대 상한)
    - DD Tier/일손실 기반 리스크 축소
    """

    # 안전장치: 최소/최대 손절폭
    MIN_STOP_DISTANCE_PCT = 0.003  # 0.3% 미만 손절은 금지
    MAX_STOP_DISTANCE_PCT = 0.025  # 2.5% 초과 손절은 감시 대상

    # 안전장치: 절대 최대 비중
    ABSOLUTE_MAX_POSITION_PCT = 0.65  # 65% 초과 금지 (Level 3 60% + 여유)

    def __init__(self) -> None:
        self._settings = get_settings()
        self._attack_config = get_attack_level_config()

    def calculate(
        self,
        equity: float,
        attack_level: AttackLevel,
        entry_price: float,
        stop_loss_price: float,
        dd_tier: int = 1,
        daily_loss_pct: float = 0,
        engine_type: str = "IGNITION",  # IGNITION or RETEST
    ) -> RiskSizingResult:
        """
        리스크 기반 포지션 사이즈 계산

        Args:
            equity: 총 자산 (KRW)
            attack_level: 공격 레벨 (1/2/3)
            entry_price: 진입가
            stop_loss_price: 손절가
            dd_tier: 현재 DD Tier (1-6)
            daily_loss_pct: 일손실률 (음수)
            engine_type: 엔진 타입 (IGNITION/RETEST)

        Returns:
            RiskSizingResult
        """
        # === 손절폭 계산 ===
        if entry_price <= 0 or stop_loss_price <= 0:
            return self._create_zero_result(attack_level, entry_price, stop_loss_price)

        stop_distance = entry_price - stop_loss_price
        stop_distance_pct = stop_distance / entry_price

        # 손절폭 유효성 검사
        if stop_distance_pct <= 0:
            logger.warning(
                "Invalid stop distance",
                entry=entry_price,
                stop=stop_loss_price,
            )
            return self._create_zero_result(attack_level, entry_price, stop_loss_price)

        # 최소 손절폭 체크 (너무 짧으면 비중 폭발 위험)
        capped = False
        cap_reason = ""

        if stop_distance_pct < self.MIN_STOP_DISTANCE_PCT:
            logger.warning(
                "Stop distance too short, adjusting",
                original=f"{stop_distance_pct:.4f}",
                adjusted=f"{self.MIN_STOP_DISTANCE_PCT:.4f}",
            )
            stop_distance_pct = self.MIN_STOP_DISTANCE_PCT
            capped = True
            cap_reason = "min_stop_distance"

        # === 리스크 조회 ===
        risk_per_trade = self._attack_config.get_risk_per_trade(
            level=attack_level,
            dd_tier=dd_tier,
            daily_loss_pct=daily_loss_pct,
        )

        # Retest 엔진은 추가 20% 리스크 감소 (더 보수적)
        if engine_type == "RETEST":
            risk_per_trade *= 0.8

        # === 핵심 공식 ===
        # position_value = (equity * risk_per_trade) / stop_distance_pct
        position_value = (equity * risk_per_trade) / stop_distance_pct

        # === 비중 상한/하한 체크 ===
        position_pct = position_value / equity

        # 레벨별 최대 비중
        max_position_pct = self._attack_config.get_max_position_value_pct(
            level=attack_level,
            stop_distance_pct=stop_distance_pct,
        )

        # 절대 상한
        max_position_pct = min(max_position_pct, self.ABSOLUTE_MAX_POSITION_PCT)

        # 레벨별 최소 비중 (Level 3는 급등주 대응으로 50% 보장)
        min_position_pct = self._attack_config.get_min_position_value_pct(
            level=attack_level,
        )

        # 비중 캡 적용
        if position_pct > max_position_pct:
            position_pct = max_position_pct
            position_value = equity * position_pct
            capped = True
            cap_reason = cap_reason or "max_position_pct"
        elif position_pct < min_position_pct and min_position_pct > 0:
            # 최소 비중 플로어 적용 (급등주 기회 포착)
            position_pct = min_position_pct
            position_value = equity * position_pct
            capped = True
            cap_reason = "min_position_pct_floor"
            logger.info(
                "Position boosted to minimum floor",
                level=attack_level.value,
                min_pct=f"{min_position_pct:.0%}",
                original_pct=f"{position_value / equity:.2%}",
            )

        # === 수량 계산 ===
        quantity = position_value / entry_price if entry_price > 0 else 0

        # === 실제 리스크 금액 ===
        risk_amount = position_value * stop_distance_pct
        risk_pct = risk_amount / equity if equity > 0 else 0

        result = RiskSizingResult(
            position_value=position_value,
            position_pct=position_pct,
            quantity=quantity,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            effective_stop_pct=stop_distance_pct,
            attack_level=attack_level,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            capped=capped,
            cap_reason=cap_reason,
        )

        logger.debug(
            "Risk sizing calculated",
            symbol="N/A",
            equity=f"{equity:,.0f}",
            level=attack_level.value,
            risk_per_trade=f"{risk_per_trade:.4f}",
            stop_pct=f"{stop_distance_pct:.4f}",
            position_pct=f"{position_pct:.2%}",
            position_value=f"{position_value:,.0f}",
            risk_amount=f"{risk_amount:,.0f}",
            capped=capped,
        )

        return result

    def calculate_for_target_position_pct(
        self,
        equity: float,
        target_position_pct: float,
        entry_price: float,
        attack_level: AttackLevel,
    ) -> tuple[float, float]:
        """
        목표 비중에 맞는 손절가 계산 (역산)

        "30% 비중으로 투자하려면 손절이 얼마여야 하나?"

        Args:
            equity: 총 자산
            target_position_pct: 목표 비중 (0-1)
            entry_price: 진입가
            attack_level: 공격 레벨

        Returns:
            (필요 손절폭 %, 필요 손절가)
        """
        config = self._attack_config.get_config(attack_level)
        risk_per_trade = config.risk_per_trade

        # 역산: stop_distance = risk_per_trade / position_pct
        required_stop_pct = risk_per_trade / target_position_pct

        # 손절가 계산
        required_stop_price = entry_price * (1 - required_stop_pct)

        return required_stop_pct, required_stop_price

    def _create_zero_result(
        self,
        attack_level: AttackLevel,
        entry_price: float,
        stop_loss_price: float,
    ) -> RiskSizingResult:
        """무효한 입력에 대한 0 결과 반환"""
        return RiskSizingResult(
            position_value=0,
            position_pct=0,
            quantity=0,
            risk_amount=0,
            risk_pct=0,
            effective_stop_pct=0,
            attack_level=attack_level,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            capped=True,
            cap_reason="invalid_input",
        )

    def get_sizing_summary(self, result: RiskSizingResult) -> dict:
        """사이징 결과 요약"""
        return {
            "position_value_krw": round(result.position_value),
            "position_pct": f"{result.position_pct:.2%}",
            "quantity": round(result.quantity, 8),
            "risk_amount_krw": round(result.risk_amount),
            "risk_pct": f"{result.risk_pct:.4%}",
            "stop_distance_pct": f"{result.effective_stop_pct:.4%}",
            "attack_level": result.attack_level.value,
            "entry_price": result.entry_price,
            "stop_loss_price": result.stop_loss_price,
            "capped": result.capped,
            "cap_reason": result.cap_reason,
        }


# 싱글톤
_risk_sizer: Optional[RiskSizer] = None


def get_risk_sizer() -> RiskSizer:
    """RiskSizer 싱글톤"""
    global _risk_sizer
    if _risk_sizer is None:
        _risk_sizer = RiskSizer()
    return _risk_sizer
