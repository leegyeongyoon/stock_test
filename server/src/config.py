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

    # === Binance API ===
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
        default=True,
        description="True면 Core 전략에서 Spot 대신 Futures Long 사용 (테스트용). Live 전환 시 False로 변경",
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
        description="최소 24h 거래대금 (50M USDT)",
    )
    max_spread_bps: float = Field(
        default=8,
        description="최대 스프레드 (8bps)",
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


@lru_cache
def get_settings() -> Settings:
    """설정 싱글톤"""
    return Settings()
