"""Risk Overlay Configuration (v3.1)

MDD 5% 방어를 위한 리스크 설정

6-Tier DD Multiplier:
- 0~1%: 1.0x (풀사이즈)
- 1~2%: 0.8x (주의)
- 2~3.5%: 0.6x (SAFE 트리거)
- 3.5~4.5%: 0.3x (Soft DD - Satellite 신규진입 OFF)
- 4.5~5%: 0.1x (위험)
- ≥5%: 0.0x (Hard DD - 신규진입 HALT, 분할청산)

v3.1 변경사항:
- Core 비중: 고정 70% → 30~60% 동적 조절
- SAFE 트리거: DD 1% → DD 2%+ 또는 조합 트리거
- Correlation: 단일 임계값 → 2단계 (warn/action)
- Hard DD: 즉시 전량청산 → 분할/조건부 청산
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class RiskOverlayConfig(BaseSettings):
    """Risk Overlay 설정 - 환경변수로 오버라이드 가능"""

    # === Drawdown 6-Tier Thresholds ===
    # Tier 1: 0~1% → 1.0x
    dd_tier1_threshold: float = Field(
        default=0.01,
        description="Tier 1 상한 (1% DD) → 1.0x",
    )
    dd_tier1_multiplier: float = Field(
        default=1.0,
        description="Tier 1 사이징 배수",
    )
    # Tier 2: 1~2% → 0.8x (주의 단계)
    dd_tier2_threshold: float = Field(
        default=0.02,
        description="Tier 2 상한 (2% DD) → 0.8x (주의)",
    )
    dd_tier2_multiplier: float = Field(
        default=0.8,
        description="Tier 2 사이징 배수",
    )
    # Tier 3: 2~3.5% → 0.6x (SAFE 트리거 시작)
    dd_tier3_threshold: float = Field(
        default=0.035,
        description="Tier 3 상한 (3.5% DD) → 0.6x (Soft DD 직전)",
    )
    dd_tier3_multiplier: float = Field(
        default=0.6,
        description="Tier 3 사이징 배수",
    )
    # Tier 4: 3.5~4.5% → 0.3x (Soft DD - Satellite 신규진입 OFF)
    dd_tier4_threshold: float = Field(
        default=0.045,
        description="Tier 4 상한 (4.5% DD) → 0.3x, Satellite 신규진입 OFF",
    )
    dd_tier4_multiplier: float = Field(
        default=0.3,
        description="Tier 4 사이징 배수 (Core/헤지에만 적용)",
    )
    # Tier 5: 4.5~5% → 0.1x
    dd_tier5_threshold: float = Field(
        default=0.05,
        description="Tier 5 상한 (5% DD, Hard DD) → 0.1x",
    )
    dd_tier5_multiplier: float = Field(
        default=0.1,
        description="Tier 5 사이징 배수",
    )
    # Tier 6: ≥5% → 0.0x (Hard DD - 신규진입 HALT, 분할청산)
    dd_tier6_multiplier: float = Field(
        default=0.0,
        description="Tier 6 사이징 배수 (신규진입 HALT)",
    )

    # === DD 모드 트리거 (v3.1 수정) ===
    # SAFE 트리거: DD 2%+ 또는 DD 1% + 다른 트리거 조합
    risk_dd_safe_threshold: float = Field(
        default=0.02,
        description="2% DD → SAFE 모드 (v3.1: 1%→2%)",
    )
    risk_dd_safe_combined_threshold: float = Field(
        default=0.01,
        description="1% DD + 다른 트리거(corr_shock 등) → SAFE",
    )
    risk_dd_reduce_threshold: float = Field(
        default=0.035,
        description="3.5% DD → Soft DD, Satellite 신규진입 OFF",
    )
    risk_dd_halt_threshold: float = Field(
        default=0.05,
        description="5% DD → Hard DD (신규진입 HALT, 분할청산)",
    )

    # === Daily Loss Limits ===
    daily_loss_safe: float = Field(
        default=-0.01,
        description="-1.0% 일손실 → SAFE 모드",
    )
    daily_loss_halt: float = Field(
        default=-0.015,
        description="-1.5% 일손실 → HALT 모드",
    )

    # === Core 전략 비중 (v3.1 신규: 동적 조절) ===
    core_allocation_min: float = Field(
        default=0.30,
        description="Core 최소 비중 (30%)",
    )
    core_allocation_max: float = Field(
        default=0.60,
        description="Core 최대 비중 (60%)",
    )
    core_allocation_default: float = Field(
        default=0.50,
        description="Core 기본 비중 (50%)",
    )

    # === Portfolio Risk Management (신규) ===
    max_concurrent_positions: int = Field(
        default=5,
        description="최대 동시 포지션 수",
    )
    risk_per_trade: float = Field(
        default=0.003,
        description="기본 트레이드당 리스크 (0.30%)",
    )
    max_risk_per_trade: float = Field(
        default=0.005,
        description="최대 트레이드당 리스크 (0.50%)",
    )
    total_risk_limit_normal: float = Field(
        default=0.015,
        description="정상 시장 총 리스크 한도 (1.5%)",
    )
    total_risk_limit_risky: float = Field(
        default=0.008,
        description="위험 시장 총 리스크 한도 (0.8%)",
    )

    # === Position Reducer (v3.1: 분할 청산) ===
    reduce_rate_risk_off: float = Field(
        default=0.40,
        description="RISK_OFF 시 포지션 축소율 (40%)",
    )
    reduce_rate_corr_shock_min: float = Field(
        default=0.30,
        description="상관 충격 시 최소 축소율 (30%)",
    )
    reduce_rate_corr_shock_max: float = Field(
        default=0.60,
        description="상관 충격 시 최대 축소율 (60%)",
    )
    reduce_stay_duration_minutes: int = Field(
        default=60,
        description="축소 후 유지 시간 (분)",
    )

    # === Hard DD 분할 청산 (v3.1: 즉시 전량 → 단계적) ===
    hard_dd_phase1_reduce_pct: float = Field(
        default=0.50,
        description="Hard DD 1단계: 손실 포지션 50% 축소",
    )
    hard_dd_phase2_hedge: bool = Field(
        default=True,
        description="Hard DD 2단계: 헤지 포지션 추가",
    )
    hard_dd_phase3_full_close_condition: str = Field(
        default="DD_EXCEED_6PCT_OR_DAILY_LOSS_2PCT",
        description="Hard DD 3단계: 전량 청산 조건",
    )
    hard_dd_gradual_close_enabled: bool = Field(
        default=True,
        description="분할 청산 활성화 (False면 즉시 전량 청산)",
    )

    # === Execution Health ===
    exec_ws_latency_warn_ms: float = Field(
        default=500,
        description="WS 지연 경고 임계값 (ms)",
    )
    exec_ws_latency_safe_ms: float = Field(
        default=1000,
        description="WS 지연 SAFE 전환 임계값 (ms)",
    )
    exec_order_failure_rate_max: float = Field(
        default=0.10,
        description="주문 실패율 최대치 (10%)",
    )
    exec_order_ack_timeout_ms: float = Field(
        default=3000,
        description="주문 ACK 타임아웃 (ms)",
    )
    exec_reconcile_drift_max: float = Field(
        default=0.01,
        description="리콘실 drift 최대치 (1%)",
    )
    exec_health_window: int = Field(
        default=20,
        description="최근 N건 기준 건강도 계산",
    )

    # === Correlation Guard (v3.1: 2단계화) ===
    corr_warn_threshold: float = Field(
        default=0.70,
        description="상관 경고 임계값 (WARN: 필터 강화)",
    )
    corr_action_threshold: float = Field(
        default=0.80,
        description="상관 액션 임계값 (REDUCE/BLOCK)",
    )
    corr_spike_threshold: float = Field(
        default=0.80,
        description="상관 급증 임계값 (하위호환)",
    )
    corr_lookback_bars: int = Field(
        default=20,
        description="상관 계산 룩백 기간",
    )
    corr_btc_drop_pct: float = Field(
        default=-0.03,
        description="BTC 충격 판단 하락률 (-3%)",
    )
    corr_btc_volatility_spike: float = Field(
        default=2.0,
        description="BTC 변동성 급증 배수",
    )

    # === Position Sizing ===
    slippage_buffer_pct: float = Field(
        default=0.005,
        description="슬리피지 버퍼 (0.5%)",
    )
    max_slippage_pct: float = Field(
        default=0.01,
        description="최대 허용 슬리피지 (1%)",
    )

    # === Fee Parameters (v3.1: 분리) ===
    fee_maker_pct: float = Field(
        default=0.0002,
        description="메이커 수수료 (2bps)",
    )
    fee_taker_pct: float = Field(
        default=0.0004,
        description="테이커 수수료 (4bps)",
    )
    fee_tier_vip0: float = Field(
        default=0.0004,
        description="VIP0 테이커 수수료",
    )
    fee_tier_vip1: float = Field(
        default=0.00036,
        description="VIP1 테이커 수수료",
    )
    fee_use_maker_for_core: bool = Field(
        default=True,
        description="Core 전략 메이커 주문 사용",
    )
    fee_use_taker_for_satellite: bool = Field(
        default=True,
        description="Satellite 전략 테이커 주문 사용 (빠른 체결)",
    )

    # === Satellite ATR-based Stop (v3.1) ===
    sat_stop_atr_multiplier: float = Field(
        default=1.5,
        description="Satellite 스탑 ATR 배수",
    )
    sat_stop_min_pct: float = Field(
        default=0.005,
        description="Satellite 최소 스탑 (0.5%)",
    )
    sat_stop_max_pct: float = Field(
        default=0.015,
        description="Satellite 최대 스탑 (1.5%) - 안전장치",
    )
    sat_use_atr_stop: bool = Field(
        default=True,
        description="ATR 기반 스탑 사용 (False면 고정 퍼센트)",
    )

    # === Core Strategy Safety ===
    core_allowed_symbols: str = Field(
        default="ALL",
        description="Core 허용 심볼 (ALL=전체, 또는 쉼표 구분 목록)",
    )
    core_min_funding_rate: float = Field(
        default=-0.0001,
        description="펀딩 역전 경고 임계값",
    )
    core_negative_funding_exit: float = Field(
        default=-0.0003,
        description="펀딩 역전 청산 임계값",
    )
    core_min_edge_bps: float = Field(
        default=5,
        description="최소 edge (bp)",
    )

    # === Exposure Limits ===
    max_total_exposure_pct: float = Field(
        default=0.80,
        description="최대 총 노출 (자본의 80%)",
    )
    max_single_position_pct: float = Field(
        default=0.10,
        description="단일 포지션 최대 (자본의 10%)",
    )
    max_correlation_exposure: int = Field(
        default=3,
        description="동일 상관군 최대 포지션 수",
    )

    @property
    def core_symbols_list(self) -> list[str]:
        """Core 허용 심볼 리스트"""
        if self.core_allowed_symbols.upper() == "ALL":
            return []  # 빈 리스트 = 전체 허용
        return [s.strip() for s in self.core_allowed_symbols.split(",")]

    @property
    def core_allow_all(self) -> bool:
        """Core 전체 심볼 허용 여부"""
        return self.core_allowed_symbols.upper() == "ALL"

    class Config:
        env_prefix = ""
        extra = "ignore"


# Singleton
_config: RiskOverlayConfig = None


def get_risk_config() -> RiskOverlayConfig:
    """Risk Overlay 설정 싱글톤"""
    global _config
    if _config is None:
        _config = RiskOverlayConfig()
    return _config
