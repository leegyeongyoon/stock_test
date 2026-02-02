"""설정 관리 - pydantic-settings 기반"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """애플리케이션 설정"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # === 거래소 선택 ===
    exchange_type: Literal["upbit", "binance"] = Field(
        default="upbit",
        description="사용할 거래소 (upbit 또는 binance)",
    )

    # === Upbit API ===
    upbit_access_key: SecretStr = Field(default=SecretStr(""))
    upbit_secret_key: SecretStr = Field(default=SecretStr(""))

    # === Binance API (레거시, 필요시 사용) ===
    # Spot Testnet
    binance_spot_testnet_api_key: SecretStr = Field(default=SecretStr(""))
    binance_spot_testnet_secret: SecretStr = Field(default=SecretStr(""))
    # Futures Testnet
    binance_futures_testnet_api_key: SecretStr = Field(default=SecretStr(""))
    binance_futures_testnet_secret: SecretStr = Field(default=SecretStr(""))
    # Live (미사용)
    binance_api_key: SecretStr = Field(default=SecretStr(""))
    binance_secret: SecretStr = Field(default=SecretStr(""))

    # === 운영 모드 ===
    enable_live_trading: bool = Field(
        default=False,
        description="True일 때만 실제 주문 실행. 기본값 False (Paper 모드)",
    )
    futures_only_mode: bool = Field(
        default=False,  # Upbit은 Futures 없음
        description="True면 Core 전략에서 Spot 대신 Futures Long 사용 (Binance 전용)",
    )

    # === 데이터베이스 ===
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/trading.db",
        description="SQLAlchemy async DB URL",
    )

    # === Telegram ===
    telegram_bot_token: SecretStr = Field(default=SecretStr(""))
    telegram_chat_id: str = Field(default="")

    # === 서버 설정 ===
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")

    # === Risk 파라미터 (강화) ===
    daily_loss_limit_safe: float = Field(
        default=-0.01,
        description="SAFE 모드 전환 일손실 임계값 (-1.0%)",
    )
    daily_loss_limit_halt: float = Field(
        default=-0.015,
        description="HALT 모드 전환 일손실 임계값 (-1.5%)",
    )
    reconcile_interval_sec: int = Field(
        default=10,
        description="Reconcile 주기 (초)",
    )
    max_order_failure_rate: float = Field(
        default=0.3,
        description="주문 실패율 임계값 (30%)",
    )

    # === Core 전략 파라미터 ===
    core_min_edge_pct: float = Field(
        default=0.001,
        description="캐시앤캐리 최소 edge (%)",
    )
    core_max_position_usd: float = Field(
        default=10000.0,
        description="Core 전략 최대 포지션 (USD)",
    )

    # === Satellite 전략 파라미터 ===
    satellite_enabled: bool = Field(default=True)
    satellite_max_position_usd: float = Field(
        default=5000.0,
        description="Satellite 전략 최대 포지션 (USD)",
    )
    satellite_hard_stop_pct: float = Field(
        default=-0.008,
        description="하드 손절 (-0.8%)",
    )
    satellite_trailing_trigger_pct: float = Field(
        default=0.01,
        description="트레일링 활성화 트리거 (+1.0%)",
    )
    satellite_trailing_stop_pct: float = Field(
        default=0.006,
        description="트레일링 스탑 (0.6%)",
    )
    satellite_time_stop_minutes: int = Field(
        default=30,
        description="타임스톱 (30분)",
    )
    satellite_rvol_threshold: float = Field(
        default=2.0,
        description="RVOL 임계값 (2.0배) - 추정치 사용으로 완화",
    )
    satellite_close_pos_threshold: float = Field(
        default=0.70,
        description="ClosePos 임계값 (0.70) - 상단 70% 이상",
    )
    satellite_confirmation_entry: bool = Field(
        default=True,
        description="확인 진입 활성화",
    )

    # === 유동성 필터 파라미터 ===
    min_liquidity_usdt: float = Field(
        default=50_000_000,
        description="최소 24h 거래대금 (50M USDT) - Binance용",
    )
    min_liquidity_krw: float = Field(
        default=1_000_000_000,
        description="최소 24h 거래대금 (10억 KRW) - Upbit용",
    )
    max_spread_bps: float = Field(
        default=10,
        description="최대 스프레드 (10bps)",
    )

    # === 노출 제한 파라미터 ===
    max_gross_exposure: float = Field(
        default=1.2,
        description="총 노출 제한 (equity * 1.2)",
    )
    max_symbol_exposure_core: float = Field(
        default=0.10,
        description="Core 심볼당 노출 제한 (10%)",
    )
    max_symbol_exposure_sat: float = Field(
        default=0.03,
        description="Satellite 심볼당 노출 제한 (3%)",
    )
    max_symbol_exposure_attack: float = Field(
        default=0.55,
        description="Attack 심볼당 노출 제한 (55% - 50% + 슬리피지 여유)",
    )
    max_slippage_core_bps: float = Field(
        default=6,
        description="Core 슬리피지 상한 (6bps)",
    )
    max_slippage_sat_bps: float = Field(
        default=15,
        description="Satellite 슬리피지 상한 (15bps)",
    )

    # === Kill Switch 파라미터 ===
    weekly_loss_limit: float = Field(
        default=-0.05,
        description="주간 손실 제한 (-5%) - Satellite 비활성화",
    )
    min_liquidation_distance: float = Field(
        default=0.025,
        description="최소 청산 거리 (2.5%) - HALT 트리거",
    )

    # === Core 분할 진입 파라미터 ===
    core_split_entry: bool = Field(
        default=True,
        description="분할 진입 활성화",
    )
    core_fee_buffer: float = Field(
        default=0.0008,
        description="수수료 버퍼 (8bps)",
    )
    core_slippage_buffer: float = Field(
        default=0.0006,
        description="슬리피지 버퍼 (6bps)",
    )
    core_tranche_1_pct: float = Field(
        default=0.40,
        description="1차 진입 비율 (40%)",
    )
    core_tranche_2_pct: float = Field(
        default=0.30,
        description="2차 진입 비율 (30%)",
    )
    core_tranche_3_pct: float = Field(
        default=0.30,
        description="3차 진입 비율 (30%)",
    )
    core_tranche_delay_sec: int = Field(
        default=300,
        description="트랜치 간 딜레이 (5분)",
    )

    # === Slack 알림 파라미터 ===
    slack_webhook_url: str = Field(
        default="",
        description="Slack Webhook URL",
    )
    slack_channel: str = Field(
        default="#trading-alerts",
        description="Slack 채널",
    )

    # === Defensive Core 설정 (Upbit용) ===
    core_cash_ratio_risk_on: float = Field(
        default=0.50,
        description="Risk-On 시 현금 비중 (50%)",
    )
    core_cash_ratio_neutral: float = Field(
        default=0.70,
        description="Neutral 시 현금 비중 (70%)",
    )
    core_cash_ratio_risk_off: float = Field(
        default=0.85,
        description="Risk-Off 시 현금 비중 (85%)",
    )
    core_cash_ratio_volatile: float = Field(
        default=0.90,
        description="Volatile 시 현금 비중 (90%)",
    )
    core_rebalance_interval_min: int = Field(
        default=60,
        description="Core 리밸런싱 간격 (분)",
    )
    core_rebalance_threshold: float = Field(
        default=0.05,
        description="리밸런싱 트리거 임계값 (5%)",
    )

    # === Attack Module 설정 (급등주 공격) ===
    attack_mode: Literal["OFF", "NORMAL", "PLUS", "MAX"] = Field(
        default="NORMAL",
        description="공격 모드 (OFF/NORMAL/PLUS/MAX)",
    )

    # Attack Score 임계값
    attack_score_l1: float = Field(
        default=70.0,
        description="Level 1 임계값 (70점 이상)",
    )
    attack_score_l2: float = Field(
        default=80.0,
        description="Level 2 임계값 (80점 이상)",
    )
    attack_score_l3: float = Field(
        default=90.0,
        description="Level 3 임계값 (90점 이상)",
    )

    # Attack 목표 비중
    attack_alloc_l1: float = Field(
        default=0.10,
        description="Level 1 목표 비중 (10%)",
    )
    attack_alloc_l2: float = Field(
        default=0.30,
        description="Level 2 목표 비중 (30%)",
    )
    attack_alloc_l3: float = Field(
        default=0.50,
        description="Level 3 목표 비중 (50%)",
    )

    # 리스크 캡 (트레이드당 최대 손실)
    attack_risk_per_trade_normal: float = Field(
        default=0.005,
        description="NORMAL 모드 트레이드당 최대 손실 (0.5%)",
    )
    attack_risk_per_trade_plus: float = Field(
        default=0.006,
        description="PLUS 모드 트레이드당 최대 손실 (0.6%)",
    )
    attack_risk_per_trade_max: float = Field(
        default=0.007,
        description="MAX 모드 트레이드당 최대 손실 (0.7%)",
    )

    # Attack 하드 게이트
    attack_max_dd_tier: int = Field(
        default=1,
        description="Attack 허용 최대 DD Tier (1이면 Tier1에서만 L3 허용)",
    )
    attack_disable_if_safe: bool = Field(
        default=True,
        description="SAFE 모드에서 Attack 비활성화",
    )
    attack_disable_if_risk_off: bool = Field(
        default=True,
        description="RISK_OFF 레짐에서 Attack 비활성화",
    )
    attack_disable_if_volatile: bool = Field(
        default=True,
        description="VOLATILE 레짐에서 Attack 비활성화",
    )
    attack_overheat_threshold: float = Field(
        default=0.15,
        description="과열 임계값 (전일 +15% 이상이면 Attack 금지)",
    )
    attack_max_positions: int = Field(
        default=1,
        description="Attack 최대 동시 포지션 수 (50% 단일 포지션 전략)",
    )

    # Attack 스탑 설정
    attack_stop_struct_buffer: float = Field(
        default=0.003,
        description="구조적 손절 버퍼 (0.3%)",
    )
    attack_stop_atr_mult: float = Field(
        default=1.5,
        description="ATR 손절 배수",
    )
    attack_stop_min_pct: float = Field(
        default=0.005,
        description="최소 손절폭 (0.5%)",
    )
    attack_stop_max_pct: float = Field(
        default=0.015,
        description="최대 손절폭 (1.5%)",
    )

    # Attack 트레일링/타임스탑
    attack_trail_trigger_r: float = Field(
        default=1.2,
        description="트레일링 발동 R배수 (1.2R)",
    )
    attack_trail_trigger_pct: float = Field(
        default=0.02,
        description="트레일링 발동 수익률 (2%)",
    )
    attack_time_stop_min: int = Field(
        default=45,
        description="타임스탑 (45분)",
    )
    attack_slippage_guard: float = Field(
        default=0.003,
        description="슬리피지 가드 (0.3%)",
    )

    # Attack 트랜치 설정
    attack_tranche_1_pct: float = Field(
        default=0.50,
        description="1차 진입 비율 (50%)",
    )
    attack_tranche_2_pct: float = Field(
        default=0.30,
        description="2차 진입 비율 (30%)",
    )
    attack_tranche_3_pct: float = Field(
        default=0.20,
        description="3차 진입 비율 (20%)",
    )

    # === Capital Profile (2-Stage: Growth/Preserve) ===
    capital_profile_enabled: bool = Field(
        default=True,
        description="Capital Profile 2단계 시스템 활성화",
    )
    capital_profile_threshold_krw: float = Field(
        default=10_000_000,
        description="Preserve 모드 전환 기준 (KRW)",
    )
    capital_profile_growth_lower_krw: float = Field(
        default=9_000_000,
        description="Growth 모드 복귀 기준 (KRW)",
    )
    capital_profile_preserve_days: int = Field(
        default=3,
        description="Preserve 진입 연속 일수",
    )
    capital_profile_growth_days: int = Field(
        default=2,
        description="Growth 복귀 연속 일수",
    )

    # Growth Mode 리스크 설정 (자산 < 10M)
    capital_risk_growth_min: float = Field(
        default=0.007,
        description="Growth 모드 최소 리스크 (0.7%)",
    )
    capital_risk_growth_max: float = Field(
        default=0.010,
        description="Growth 모드 최대 리스크 (1.0%)",
    )
    capital_growth_total_risk_limit: float = Field(
        default=0.020,
        description="Growth 모드 총 리스크 한도 (2.0%)",
    )

    # Preserve Mode 리스크 설정 (자산 >= 10M)
    capital_risk_preserve_min: float = Field(
        default=0.0025,
        description="Preserve 모드 최소 리스크 (0.25%)",
    )
    capital_risk_preserve_max: float = Field(
        default=0.005,
        description="Preserve 모드 최대 리스크 (0.5%)",
    )
    capital_preserve_total_risk_limit: float = Field(
        default=0.012,
        description="Preserve 모드 총 리스크 한도 (1.2%)",
    )
    capital_preserve_max_attack_level: int = Field(
        default=2,
        description="Preserve 모드 최대 Attack Level (L3 금지)",
    )
    capital_preserve_slippage_mult: float = Field(
        default=1.5,
        description="Preserve 모드 슬리피지 가드 배수",
    )

    # 펌프&덤프 필터
    pump_dump_filter_enabled: bool = Field(
        default=True,
        description="펌프&덤프 필터 활성화",
    )
    pump_dump_rvol_threshold: float = Field(
        default=10.0,
        description="펌프 의심 RVOL 임계값 (10배)",
    )
    pump_dump_price_spike_1h: float = Field(
        default=0.20,
        description="펌프 의심 1시간 가격 상승률 (20%)",
    )
    pump_dump_depth_imbalance: float = Field(
        default=5.0,
        description="호가 불균형 임계값",
    )

    # === Pullback 전략 설정 (눌림목 매수) ===
    pullback_mode: Literal["OFF", "SAFE", "NORMAL", "AGGRESSIVE"] = Field(
        default="NORMAL",
        description="눌림목 전략 모드 (OFF/SAFE/NORMAL/AGGRESSIVE)",
    )

    # Pullback Score 임계값
    pullback_score_l1: float = Field(
        default=55.0,
        description="Level 1 임계값 (55점 이상)",
    )
    pullback_score_l2: float = Field(
        default=70.0,
        description="Level 2 임계값 (70점 이상)",
    )
    pullback_score_l3: float = Field(
        default=85.0,
        description="Level 3 임계값 (85점 이상)",
    )

    # Pullback 레벨별 배분 (분산 투자)
    pullback_alloc_l1: float = Field(
        default=0.05,
        description="Level 1 목표 비중 (5%)",
    )
    pullback_alloc_l2: float = Field(
        default=0.08,
        description="Level 2 목표 비중 (8%)",
    )
    pullback_alloc_l3: float = Field(
        default=0.10,
        description="Level 3 목표 비중 (10%)",
    )

    # Pullback 포지션 관리
    pullback_max_positions: int = Field(
        default=10,
        description="최대 동시 포지션 수 (분산 투자)",
    )

    # Pullback 청산 설정
    pullback_stop_loss_pct: float = Field(
        default=-0.03,
        description="손절 (-3%)",
    )
    pullback_take_profit_pct: float = Field(
        default=0.05,
        description="1차 익절 (+5%)",
    )
    pullback_trailing_trigger_pct: float = Field(
        default=0.03,
        description="트레일링 활성화 트리거 (+3%)",
    )
    pullback_trailing_stop_pct: float = Field(
        default=0.02,
        description="트레일링 스탑 (고점 -2%)",
    )
    pullback_time_stop_hours: int = Field(
        default=4,
        description="타임스톱 (4시간)",
    )

    # Pullback 쿨다운
    pullback_cooldown_min: int = Field(
        default=60,
        description="동일 종목 재진입 쿨다운 (60분)",
    )

    @property
    def is_paper_mode(self) -> bool:
        """Paper 모드 여부"""
        return not self.enable_live_trading

    @property
    def spot_api_key(self) -> str:
        """Spot API 키"""
        if self.is_paper_mode:
            return self.binance_spot_testnet_api_key.get_secret_value()
        return self.binance_api_key.get_secret_value()

    @property
    def spot_secret(self) -> str:
        """Spot Secret"""
        if self.is_paper_mode:
            return self.binance_spot_testnet_secret.get_secret_value()
        return self.binance_secret.get_secret_value()

    @property
    def futures_api_key(self) -> str:
        """Futures API 키"""
        if self.is_paper_mode:
            return self.binance_futures_testnet_api_key.get_secret_value()
        return self.binance_api_key.get_secret_value()

    @property
    def futures_secret(self) -> str:
        """Futures Secret"""
        if self.is_paper_mode:
            return self.binance_futures_testnet_secret.get_secret_value()
        return self.binance_secret.get_secret_value()

    # 하위 호환성
    @property
    def active_api_key(self) -> str:
        """현재 모드에 맞는 API 키 (Spot 기본)"""
        return self.spot_api_key

    @property
    def active_secret(self) -> str:
        """현재 모드에 맞는 Secret (Spot 기본)"""
        return self.spot_secret

    # === Upbit 전용 속성 ===
    @property
    def is_upbit(self) -> bool:
        """Upbit 거래소 사용 여부"""
        return self.exchange_type == "upbit"

    @property
    def upbit_api_key(self) -> str:
        """Upbit Access Key"""
        return self.upbit_access_key.get_secret_value()

    @property
    def upbit_secret(self) -> str:
        """Upbit Secret Key"""
        return self.upbit_secret_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """설정 싱글톤"""
    return Settings()
