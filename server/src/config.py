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
    exchange_type: Literal["upbit"] = Field(
        default="upbit",
        description="사용할 거래소 (Upbit 전용)",
    )

    # === Upbit API ===
    upbit_access_key: SecretStr = Field(default=SecretStr(""))
    upbit_secret_key: SecretStr = Field(default=SecretStr(""))

    # === 운영 모드 ===
    enable_live_trading: bool = Field(
        default=False,
        description="True일 때만 실제 주문 실행. 기본값 False (Paper 모드)",
    )

    # === 데이터베이스 ===
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/trading.db",
        description="SQLAlchemy async DB URL",
    )

    # === Telegram ===
    telegram_bot_token: SecretStr = Field(default=SecretStr(""))
    telegram_chat_id: str = Field(default="")

    # === OpenAI API ===
    openai_api_key: SecretStr = Field(default=SecretStr(""))

    # === 서버 설정 ===
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")

    # === Risk 파라미터 ===
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

    # === 유동성 필터 파라미터 ===
    min_liquidity_krw: float = Field(
        default=1_000_000_000,
        description="최소 24h 거래대금 (10억 KRW) - Upbit용",
    )
    max_spread_bps: float = Field(
        default=10,
        description="최대 스프레드 (10bps)",
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

    # === v3 전략 설정 ===
    v3_enabled: bool = Field(
        default=True,
        description="v3 전략 활성화",
    )
    v3_max_positions: int = Field(
        default=6,
        description="v3 최대 동시 포지션 수",
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

    # === v4.2 KMVI (KRW Market Volatility Index) ===
    kmvi_enabled: bool = Field(
        default=True,
        description="KMVI 활성화",
    )
    kmvi_top_n: int = Field(
        default=20,
        description="KMVI 계산에 사용할 상위 코인 수",
    )
    kmvi_percentile: int = Field(
        default=80,
        description="KMVI 분위수 (80 = 80%ile)",
    )
    kmvi_t1: float = Field(
        default=0.012,
        description="KMVI T1 임계값 (1.2%) - ELEVATED",
    )
    kmvi_t2: float = Field(
        default=0.020,
        description="KMVI T2 임계값 (2.0%)",
    )
    kmvi_t3: float = Field(
        default=0.028,
        description="KMVI T3 임계값 (2.8%) - 신규 진입 OFF",
    )

    # === v4.2 Stop Watchdog ===
    stop_watchdog_enabled: bool = Field(
        default=True,
        description="Stop Watchdog 활성화",
    )
    stop_watchdog_loop_ms: int = Field(
        default=300,
        description="Watchdog 루프 주기 (ms)",
    )
    stop_watchdog_ws_timeout_sec: int = Field(
        default=3,
        description="WebSocket 타임아웃 (초)",
    )
    stop_watchdog_fast_crash_pct: float = Field(
        default=0.03,
        description="Fast Crash 감지 임계값 (3%)",
    )
    stop_watchdog_fast_crash_window_sec: int = Field(
        default=5,
        description="Fast Crash 감지 윈도우 (초)",
    )

    # === Anti-Chase Gate ===
    anti_chase_gate_threshold: float = Field(
        default=0.30,
        description="Anti-Chase Gate 임계값 (일일 +30% 이상이면 차단)",
    )

    @property
    def is_paper_mode(self) -> bool:
        """Paper 모드 여부"""
        return not self.enable_live_trading

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
