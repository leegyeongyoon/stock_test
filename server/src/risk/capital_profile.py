"""Capital Profile Manager (2-Stage: Growth/Preserve)

2단계 자본 프로필 시스템:
- Growth Mode (자산 < 10M KRW): 공격적 성장, 높은 리스크 허용
- Preserve Mode (자산 >= 10M KRW): 수익 보존, 보수적 운용

핵심 원칙:
- "비중"이 아니라 "리스크캡"으로 공격성 정의
- 최종 진입 수량 = min(목표비중, 리스크캡 기반 수량, 심볼 노출 한도)
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.config import get_settings
from src.risk.config import get_risk_config
from src.risk.hysteresis_tracker import (
    CapitalMode,
    HysteresisTracker,
    get_hysteresis_tracker,
)

logger = structlog.get_logger()
settings = get_settings()
risk_config = get_risk_config()


@dataclass
class CapitalProfileConfig:
    """프로필별 설정"""
    mode: CapitalMode
    risk_per_trade_min: float  # 최소 리스크
    risk_per_trade_max: float  # 최대 리스크
    total_risk_limit: float    # 총 리스크 한도
    allowed_attack_levels: list[int]  # 허용 Attack Level
    allowed_attack_modes: list[str]   # 허용 Attack Mode
    slippage_guard_multiplier: float  # 슬리피지 가드 배수


@dataclass
class RiskPerTradeResult:
    """트레이드당 리스크 계산 결과"""
    base_risk: float          # 기본 리스크
    adjusted_risk: float      # 조정된 리스크 (DD Tier 반영)
    risk_cap: float           # 최종 리스크 캡
    dd_multiplier: float      # DD Tier 배수
    profile_mode: CapitalMode # 적용된 프로필 모드
    reason: str               # 조정 사유


class CapitalProfileManager:
    """Capital Profile 통합 관리자"""

    # Profile별 기본 설정
    GROWTH_CONFIG = CapitalProfileConfig(
        mode=CapitalMode.GROWTH,
        risk_per_trade_min=0.007,  # 0.7%
        risk_per_trade_max=0.010,  # 1.0%
        total_risk_limit=0.020,    # 2.0%
        allowed_attack_levels=[1, 2, 3],
        allowed_attack_modes=["NORMAL", "PLUS", "MAX"],
        slippage_guard_multiplier=1.0,
    )

    PRESERVE_CONFIG = CapitalProfileConfig(
        mode=CapitalMode.PRESERVE,
        risk_per_trade_min=0.0025,  # 0.25%
        risk_per_trade_max=0.005,   # 0.5%
        total_risk_limit=0.012,     # 1.2%
        allowed_attack_levels=[1, 2],  # L3 금지
        allowed_attack_modes=["NORMAL", "OFF"],
        slippage_guard_multiplier=1.5,  # 슬리피지 가드 강화
    )

    def __init__(
        self,
        hysteresis_tracker: HysteresisTracker = None,
    ):
        """
        Args:
            hysteresis_tracker: 히스테리시스 추적기 (기본: 싱글톤 사용)
        """
        self.tracker = hysteresis_tracker or get_hysteresis_tracker()
        self.enabled = settings.capital_profile_enabled

        # 환경변수에서 설정 오버라이드
        self._load_config_from_settings()

        logger.info(
            "CapitalProfileManager initialized",
            enabled=self.enabled,
            current_mode=self.get_current_mode().value,
        )

    def _load_config_from_settings(self) -> None:
        """환경변수에서 설정 로드"""
        # Growth Mode 설정
        self.GROWTH_CONFIG.risk_per_trade_min = settings.capital_risk_growth_min
        self.GROWTH_CONFIG.risk_per_trade_max = settings.capital_risk_growth_max
        self.GROWTH_CONFIG.total_risk_limit = settings.capital_growth_total_risk_limit
        self.GROWTH_CONFIG.allowed_attack_levels = risk_config.growth_attack_levels_list
        self.GROWTH_CONFIG.allowed_attack_modes = risk_config.growth_attack_modes_list

        # Preserve Mode 설정
        self.PRESERVE_CONFIG.risk_per_trade_min = settings.capital_risk_preserve_min
        self.PRESERVE_CONFIG.risk_per_trade_max = settings.capital_risk_preserve_max
        self.PRESERVE_CONFIG.total_risk_limit = settings.capital_preserve_total_risk_limit
        self.PRESERVE_CONFIG.allowed_attack_levels = risk_config.preserve_attack_levels_list
        self.PRESERVE_CONFIG.allowed_attack_modes = risk_config.preserve_attack_modes_list
        self.PRESERVE_CONFIG.slippage_guard_multiplier = settings.capital_preserve_slippage_mult

    def get_current_mode(self) -> CapitalMode:
        """현재 Capital Mode 반환"""
        if not self.enabled:
            return CapitalMode.GROWTH  # 비활성화 시 기본값
        return self.tracker.get_current_mode()

    def get_config(self, mode: CapitalMode = None) -> CapitalProfileConfig:
        """프로필별 설정 반환"""
        mode = mode or self.get_current_mode()
        if mode == CapitalMode.PRESERVE:
            return self.PRESERVE_CONFIG
        return self.GROWTH_CONFIG

    def get_risk_per_trade(
        self,
        dd_tier: int = 1,
        attack_mode: str = "NORMAL",
        attack_level: int = 1,
        regime: str = "NEUTRAL",
    ) -> RiskPerTradeResult:
        """
        트레이드당 리스크 계산

        Args:
            dd_tier: 현재 DD Tier (1~6)
            attack_mode: Attack Mode (NORMAL/PLUS/MAX)
            attack_level: Attack Level (1/2/3)
            regime: BTC Regime (RISK_ON/NEUTRAL/RISK_OFF/VOLATILE)

        Returns:
            RiskPerTradeResult
        """
        config = self.get_config()

        # 기본 리스크 (프로필에 따라)
        base_risk = config.risk_per_trade_min

        # Attack Mode/Level에 따른 조정
        if attack_mode == "MAX" and attack_level == 3:
            # L3 + MAX: 최대 리스크
            base_risk = config.risk_per_trade_max
        elif attack_mode == "PLUS" or attack_level >= 2:
            # PLUS 또는 L2 이상: 중간 리스크
            base_risk = (config.risk_per_trade_min + config.risk_per_trade_max) / 2
        else:
            # 기본
            base_risk = config.risk_per_trade_min

        # DD Tier에 따른 배수 적용
        dd_multipliers = {
            1: 1.0,   # Tier 1: 풀사이즈
            2: 0.8,   # Tier 2: 20% 감소
            3: 0.6,   # Tier 3: 40% 감소
            4: 0.3,   # Tier 4: 70% 감소
            5: 0.1,   # Tier 5: 90% 감소
            6: 0.0,   # Tier 6: 진입 금지
        }
        dd_mult = dd_multipliers.get(dd_tier, 1.0)
        adjusted_risk = base_risk * dd_mult

        # Regime에 따른 추가 조정
        regime_multipliers = {
            "RISK_ON": 1.0,
            "NEUTRAL": 1.0,
            "RISK_OFF": 0.5,
            "VOLATILE": 0.3,
        }
        regime_mult = regime_multipliers.get(regime, 1.0)
        adjusted_risk *= regime_mult

        # 최종 리스크 캡 (총 리스크 한도 내에서)
        risk_cap = min(adjusted_risk, config.total_risk_limit)

        # 조정 사유
        reasons = []
        if dd_mult < 1.0:
            reasons.append(f"DD Tier {dd_tier} ({dd_mult:.1f}x)")
        if regime_mult < 1.0:
            reasons.append(f"Regime {regime} ({regime_mult:.1f}x)")
        reason = ", ".join(reasons) if reasons else "Full risk allowed"

        return RiskPerTradeResult(
            base_risk=base_risk,
            adjusted_risk=adjusted_risk,
            risk_cap=risk_cap,
            dd_multiplier=dd_mult,
            profile_mode=config.mode,
            reason=reason,
        )

    def can_use_attack_level(self, level: int, dd_tier: int = 1, regime: str = "NEUTRAL") -> tuple[bool, str]:
        """
        Attack Level 사용 가능 여부 확인

        Args:
            level: Attack Level (1/2/3)
            dd_tier: 현재 DD Tier
            regime: BTC Regime

        Returns:
            (허용 여부, 사유)
        """
        config = self.get_config()

        # 프로필에서 허용하지 않는 레벨
        if level not in config.allowed_attack_levels:
            return False, f"Level {level} not allowed in {config.mode.value} mode"

        # L3 추가 조건 체크 (Growth 모드에서만)
        if level == 3:
            # DD Tier 1 필수
            if risk_config.capital_l3_require_dd_tier1 and dd_tier > 1:
                return False, f"L3 requires DD Tier 1, current: Tier {dd_tier}"

            # RiskOn/Neutral 필수
            if risk_config.capital_l3_require_risk_on and regime in ["RISK_OFF", "VOLATILE"]:
                return False, f"L3 requires RISK_ON or NEUTRAL regime, current: {regime}"

        return True, "Allowed"

    def can_use_attack_mode(self, mode: str) -> tuple[bool, str]:
        """
        Attack Mode 사용 가능 여부 확인

        Args:
            mode: Attack Mode (NORMAL/PLUS/MAX/OFF)

        Returns:
            (허용 여부, 사유)
        """
        config = self.get_config()

        if mode not in config.allowed_attack_modes:
            return False, f"Mode {mode} not allowed in {config.mode.value} profile"

        return True, "Allowed"

    def check_overheat_restriction(
        self,
        change_rate_24h: float,
        level: int,
    ) -> tuple[bool, str]:
        """
        과열 상태에서 레벨 제한 체크

        Args:
            change_rate_24h: 24시간 변화율 (예: 0.15 = +15%)
            level: 요청된 Attack Level

        Returns:
            (허용 여부, 사유)
        """
        # L3 과열 제한 (전일 +15% 이상)
        if level == 3 and change_rate_24h >= risk_config.capital_overheat_l3_block_threshold:
            return False, f"L3 blocked: 24h change {change_rate_24h:.1%} >= {risk_config.capital_overheat_l3_block_threshold:.0%}"

        # L2 과열 제한 (전일 +20% 이상)
        if level == 2 and change_rate_24h >= risk_config.capital_overheat_l2_block_threshold:
            return False, f"L2 blocked: 24h change {change_rate_24h:.1%} >= {risk_config.capital_overheat_l2_block_threshold:.0%}"

        return True, "Not overheated"

    def get_slippage_guard(self, base_guard: float = None) -> float:
        """
        슬리피지 가드 반환 (프로필별 배수 적용)

        Args:
            base_guard: 기본 슬리피지 가드 (기본: settings 값)

        Returns:
            조정된 슬리피지 가드
        """
        base = base_guard or settings.attack_slippage_guard
        config = self.get_config()
        return base * config.slippage_guard_multiplier

    def update_equity(self, equity: float) -> CapitalMode:
        """
        실시간 Equity 업데이트 (일별 기록 X, 현재 모드만 반환)

        Args:
            equity: 현재 Equity (KRW)

        Returns:
            현재 모드
        """
        return self.tracker.check_instant_equity(equity)

    def on_day_close(self, equity: float) -> tuple[CapitalMode, bool, str]:
        """
        일 마감 시 호출 - 모드 전환 체크

        Args:
            equity: 종가 기준 Equity (KRW)

        Returns:
            (현재 모드, 전환 여부, 전환 사유)
        """
        return self.tracker.record_daily_equity(equity)

    def get_status_dict(self) -> dict:
        """API 응답용 상태 딕셔너리"""
        config = self.get_config()
        tracker_status = self.tracker.get_status_dict()

        return {
            "enabled": self.enabled,
            "current_mode": config.mode.value,
            "config": {
                "risk_per_trade_min": config.risk_per_trade_min,
                "risk_per_trade_max": config.risk_per_trade_max,
                "total_risk_limit": config.total_risk_limit,
                "allowed_attack_levels": config.allowed_attack_levels,
                "allowed_attack_modes": config.allowed_attack_modes,
                "slippage_guard_multiplier": config.slippage_guard_multiplier,
            },
            "hysteresis": tracker_status,
        }


# Singleton
_manager: CapitalProfileManager = None


def get_capital_profile_manager() -> CapitalProfileManager:
    """CapitalProfileManager 싱글톤"""
    global _manager
    if _manager is None:
        _manager = CapitalProfileManager()
    return _manager
