"""Risk Overlay Configuration

MDD 5% 방어를 위한 리스크 설정
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class RiskOverlayConfig(BaseSettings):
    """Risk Overlay 설정 - 환경변수로 오버라이드 가능"""

    # === Drawdown Thresholds ===
    risk_dd_safe_threshold: float = Field(
        default=0.01,
        description="1% DD → 사이징 절반",
    )
    risk_dd_reduce_threshold: float = Field(
        default=0.03,
        description="3% DD → 최소 사이징, Satellite OFF",
    )
    risk_dd_halt_threshold: float = Field(
        default=0.05,
        description="5% DD → 모든 엔진 HALT",
    )

    # === Daily Loss Limits ===
    daily_loss_safe: float = Field(
        default=-0.015,
        description="-1.5% 일손실 → SAFE 모드",
    )
    daily_loss_halt: float = Field(
        default=-0.03,
        description="-3% 일손실 → HALT 모드",
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

    # === Correlation Guard ===
    corr_spike_threshold: float = Field(
        default=0.80,
        description="상관 급증 임계값",
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

    # === Core Strategy Safety ===
    core_allowed_symbols: str = Field(
        default="BTCUSDT,ETHUSDT",
        description="Core 허용 심볼 (쉼표 구분)",
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
        return [s.strip() for s in self.core_allowed_symbols.split(",")]

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
