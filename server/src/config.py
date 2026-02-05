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
    satellite_enabled: bool = Field(default=False)  # v4.2: OFF (Ignition으로 대체)
    satellite_max_position_usd: float = Field(
        default=5000.0,
        description="Satellite 전략 최대 포지션 (USD)",
    )
    satellite_hard_stop_pct: float = Field(
        default=-0.008,  # v5.1: -1.2% → -0.8% (더 빠른 손절)
        description="하드 손절 (-0.8%) - 손실 최소화 위해 타이트하게",
    )
    satellite_trailing_trigger_pct: float = Field(
        default=0.008,
        description="트레일링 활성화 트리거 (+0.8%) - 1.0%→0.8%: 더 빠른 트레일링",
    )
    satellite_trailing_stop_pct: float = Field(
        default=0.005,
        description="트레일링 스탑 (0.5%) - 0.6%→0.5%: 더 타이트한 보호",
    )
    satellite_time_stop_minutes: int = Field(
        default=20,
        description="타임스톱 (20분) - 30분→20분: 더 빠른 청산",
    )
    satellite_rvol_threshold: float = Field(
        default=2.5,
        description="RVOL 임계값 (2.5배) - 2.0→2.5: 거래량 필터 강화",
    )
    satellite_close_pos_threshold: float = Field(
        default=0.75,
        description="ClosePos 임계값 (0.75) - 0.70→0.75: 상단 마감 필터 강화",
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
        default="NORMAL",  # v5.2: NORMAL 활성화 (균형 모드)
        description="공격 모드 (OFF/NORMAL/PLUS/MAX)",
    )

    # Attack Score 임계값 (v5.4: 대폭 완화)
    attack_score_l1: float = Field(
        default=50.0,  # v5.4: 70 → 50 (하루 10-20건 예상, Candle Surge 없이도 진입)
        description="Level 1 임계값 (50점 이상)",
    )
    attack_score_l2: float = Field(
        default=70.0,  # v5.4: 82 → 70 (기존 L1 수준)
        description="Level 2 임계값 (70점 이상)",
    )
    attack_score_l3: float = Field(
        default=85.0,  # v5.4: 88 → 85 (약간 완화)
        description="Level 3 임계값 (85점 이상)",
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

    # === v5.0 Anti-Chase Gate 완화 ===
    anti_chase_gate_threshold: float = Field(
        default=0.30,
        description="Anti-Chase Gate 임계값 (일일 +30% 이상이면 차단) - v5.4: 20%→30% 완화",
    )

    # === v5.0 분봉 급등 듀얼 트리거 ===
    candle_surge_1m_change: float = Field(
        default=0.05,
        description="1분봉 극단 급등 임계값 (5%) - v5.4: 7%→5% 완화",
    )
    candle_surge_1m_rvol: float = Field(
        default=3.0,
        description="1분봉 RVOL 임계값 (3배)",
    )
    candle_surge_5m_change: float = Field(
        default=0.03,
        description="5분봉 급등 임계값 (3%) - v5.4: 4%→3% 완화",
    )
    candle_surge_5m_rvol: float = Field(
        default=2.0,
        description="5분봉 RVOL 임계값 (2배)",
    )
    candle_surge_bonus_1m: float = Field(
        default=30.0,
        description="1분봉 극단 급등 보너스 점수",
    )
    candle_surge_bonus_5m: float = Field(
        default=20.0,
        description="5분봉 급등 보너스 점수",
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

    # === v4.2 Rebound Scalper 전략 설정 (반등 스캘핑) ===
    rebound_mode: Literal["OFF", "SAFE", "NORMAL", "AGGRESSIVE"] = Field(
        default="NORMAL",
        description="반등 스캘퍼 모드 (OFF/SAFE/NORMAL/AGGRESSIVE)",
    )

    # Rebound Score 임계값
    rebound_score_l1: float = Field(
        default=55.0,
        description="Level 1 임계값 (55점 이상)",
    )
    rebound_score_l2: float = Field(
        default=70.0,
        description="Level 2 임계값 (70점 이상)",
    )
    rebound_score_l3: float = Field(
        default=85.0,
        description="Level 3 임계값 (85점 이상)",
    )

    # Rebound 레벨별 배분 (소액 분산)
    rebound_alloc_l1: float = Field(
        default=0.03,
        description="Level 1 목표 비중 (3%)",
    )
    rebound_alloc_l2: float = Field(
        default=0.05,
        description="Level 2 목표 비중 (5%)",
    )
    rebound_alloc_l3: float = Field(
        default=0.08,
        description="Level 3 목표 비중 (8%)",
    )

    # Rebound 포지션 관리
    rebound_max_positions: int = Field(
        default=5,
        description="최대 동시 포지션 수",
    )

    # Rebound 청산 설정 (스캘핑용 타이트 설정)
    rebound_stop_loss_pct: float = Field(
        default=-0.012,
        description="손절 (-1.2%)",
    )
    rebound_tp1_pct: float = Field(
        default=0.008,
        description="1차 익절 (+0.8%)",
    )
    rebound_tp1_ratio: float = Field(
        default=0.50,
        description="1차 익절 비율 (50%)",
    )
    rebound_tp2_pct: float = Field(
        default=0.015,
        description="2차 익절 (+1.5%)",
    )
    rebound_tp2_ratio: float = Field(
        default=0.30,
        description="2차 익절 비율 (30%)",
    )
    rebound_trailing_trigger_pct: float = Field(
        default=0.015,
        description="트레일링 활성화 트리거 (+1.5%)",
    )
    rebound_trailing_stop_pct: float = Field(
        default=0.005,
        description="트레일링 스탑 (고점 -0.5%)",
    )
    rebound_time_stop_minutes: int = Field(
        default=30,
        description="타임스톱 (30분)",
    )
    rebound_cooldown_min: int = Field(
        default=30,
        description="동일 종목 재진입 쿨다운 (30분)",
    )

    # Rebound 연속 손실 브레이크 (3대 보호장치 #1)
    rebound_consecutive_loss_limit: int = Field(
        default=3,
        description="연속 손실 제한 (3회)",
    )
    rebound_consecutive_loss_cooldown_min: int = Field(
        default=120,
        description="연속 손실 시 쿨다운 (2시간)",
    )

    # Rebound 변동성 필터 (3대 보호장치 #2)
    rebound_volatility_atr_mult: float = Field(
        default=2.0,
        description="휩쏘 감지 ATR 배수 (2배 이상 변동 시 OFF)",
    )
    rebound_disable_on_volatile: bool = Field(
        default=True,
        description="급변장에서 자동 비활성화",
    )

    # Rebound 추세 필터 (3대 보호장치 #3)
    rebound_trend_ma_period: int = Field(
        default=20,
        description="추세 판단 MA 기간 (5분봉 기준)",
    )
    rebound_disable_in_downtrend: bool = Field(
        default=True,
        description="하락 추세에서 자동 비활성화",
    )

    # Rebound RSI 설정
    rebound_rsi_oversold: float = Field(
        default=30.0,
        description="RSI 과매도 영역 (30 이하)",
    )
    rebound_rsi_confirm: float = Field(
        default=40.0,
        description="RSI 확인 영역 (40 이하)",
    )

    # Rebound Orderbook Imbalance
    rebound_orderbook_imbalance_threshold: float = Field(
        default=1.5,
        description="매수호가 우세 비율 (1.5배 이상)",
    )

    # === v4.0 Ignition 전략 설정 (Pre-Breakout Setup + Ignition) ===
    ignition_mode: Literal["OFF", "NORMAL", "ATTACK"] = Field(
        default="NORMAL",
        description="점화 전략 모드 (OFF/NORMAL/ATTACK)",
    )

    # Setup Engine 설정
    ignition_setup_min_score: float = Field(
        default=70.0,
        description="Setup 최소 점수 (Watchlist 진입 기준)",
    )
    ignition_setup_scan_interval_min: int = Field(
        default=1,
        description="Setup 스캔 주기 (분) - v4.2: 15분→1분 (조기 감지)",
    )
    ignition_watchlist_max: int = Field(
        default=30,
        description="Watchlist 최대 종목 수 (v4.2: 20→30)",
    )
    ignition_watchlist_ttl_hours: float = Field(
        default=2.0,
        description="Watchlist 항목 TTL (시간)",
    )

    # Setup Score 가중치 (S1~S5)
    ignition_s1_weight: float = Field(
        default=20.0,
        description="S1 변동성 수축 가중치",
    )
    ignition_s2_weight: float = Field(
        default=20.0,
        description="S2 거래량 건조 가중치",
    )
    ignition_s3_weight: float = Field(
        default=25.0,
        description="S3 매집 구조 가중치",
    )
    ignition_s4_weight: float = Field(
        default=20.0,
        description="S4 상대강도 가중치",
    )
    ignition_s5_weight: float = Field(
        default=15.0,
        description="S5 레인지 압박 가중치",
    )

    # Setup 개별 조건 임계값
    ignition_bb_width_pct_max: float = Field(
        default=20.0,
        description="S1: BB Width 백분위 최대 (낮을수록 수축)",
    )
    ignition_volume_dryup_ratio: float = Field(
        default=0.5,
        description="S2: 거래량 건조 비율 (평균 대비)",
    )
    ignition_higher_lows_min: int = Field(
        default=3,
        description="S3: Higher Lows 최소 횟수",
    )
    ignition_avg_close_pos_min: float = Field(
        default=0.55,
        description="S3: 평균 Close Position 최소값",
    )
    ignition_range_position_min: float = Field(
        default=0.70,
        description="S5: 레인지 내 위치 최소값 (상단 30%)",
    )
    ignition_range_touch_min: int = Field(
        default=3,
        description="S5: 레인지 상단 터치 최소 횟수",
    )

    # Ignition Engine 설정
    ignition_range_break_atr_mult: float = Field(
        default=0.05,
        description="돌파 버퍼 = ATR * 이 값 (0.15→0.05: 돌파 레벨에 더 가깝게 진입)",
    )
    ignition_volume_expansion_mult: float = Field(
        default=2.5,
        description="점화 거래량 = 평균 * 이 값 (1.6→2.5: 거래량 필터 강화)",
    )
    ignition_max_extension_atr_mult: float = Field(
        default=0.5,
        description="진입 허용 최대 확장폭 (ATR 기준) (0.8→0.5: 추격 방지)",
    )

    # Anti-Chase Gate 설정
    ignition_entry_window_sec: int = Field(
        default=120,
        description="점화 후 진입 허용 시간 (초, 2분) (180→120: 더 빠른 진입 요구)",
    )
    ignition_over_extension_atr_mult: float = Field(
        default=0.8,
        description="과확장 차단 기준 (ATR 기준) (1.2→0.8: 더 빠른 차단)",
    )
    ignition_max_spread_bps: float = Field(
        default=8.0,
        description="최대 허용 스프레드 (bps) (10→8: 유동성 필터 강화)",
    )
    ignition_distribution_wick_pct: float = Field(
        default=0.25,
        description="분배 캔들 윗꼬리 비율 임계값 (0.30→0.25: 분배 감지 민감도 증가)",
    )

    # Position Sizing 설정
    ignition_risk_normal: float = Field(
        default=0.004,
        description="NORMAL 모드 트레이드당 리스크 (0.4%)",
    )
    ignition_risk_attack: float = Field(
        default=0.010,
        description="ATTACK 모드 트레이드당 리스크 (1.0%)",
    )
    ignition_initial_position_pct: float = Field(
        default=0.20,
        description="초기 진입 비율 (목표의 20%)",
    )
    ignition_add_trigger_r: str = Field(
        default="0.5,1.0,1.5",
        description="증액 트리거 R배수 (콤마 구분)",
    )
    ignition_add_position_pct: str = Field(
        default="0.20,0.20,0.20",
        description="각 증액 단계 비율 (콤마 구분)",
    )
    ignition_max_add_steps_normal: int = Field(
        default=2,
        description="NORMAL 모드 최대 증액 횟수",
    )
    ignition_max_add_steps_attack: int = Field(
        default=3,
        description="ATTACK 모드 최대 증액 횟수",
    )

    # Exit 설정
    ignition_time_stop_min: int = Field(
        default=5,
        description="확장 실패시 타임스톱 (분)",
    )
    ignition_profit_protection_r: float = Field(
        default=0.7,
        description="+0.7R 도달시 일부 익절 (1.0→0.7: 더 빠른 수익 확보)",
    )
    ignition_profit_protection_pct: float = Field(
        default=0.50,
        description="Profit Protection 익절 비율 (50%)",
    )
    ignition_trailing_trigger_r: float = Field(
        default=1.5,
        description="트레일링 발동 R배수 (2.0→1.5: 더 빠른 트레일링)",
    )
    ignition_trailing_stop_atr_mult: float = Field(
        default=0.8,
        description="트레일링 스탑 ATR 배수 (1.0→0.8: 더 타이트한 트레일링)",
    )
    ignition_stop_buffer_pct: float = Field(
        default=0.003,
        description="구조 손절 버퍼 (0.3%)",
    )

    # === Surge Filter: Upbit Microstructure ===
    microstructure_max_spread_bps: float = Field(
        default=12.0,
        description="최대 스프레드 (12bps)",
    )
    microstructure_depth_mult: float = Field(
        default=6.0,
        description="호가 깊이 배수 (주문 사이즈 * 6)",
    )
    microstructure_max_slippage_pct: float = Field(
        default=0.0025,
        description="최대 슬리피지 (0.25%)",
    )

    # === Surge Filter: Hot Yesterday Policy ===
    hot_yesterday_threshold: float = Field(
        default=0.10,
        description="전일 급등 임계값 (+10%)",
    )
    hot_yesterday_position_mult: float = Field(
        default=0.6,
        description="전일 급등주 포지션 배수 (60%)",
    )
    hot_yesterday_time_stop_reduce: int = Field(
        default=2,
        description="전일 급등주 타임스톱 감소 (분)",
    )

    # === Surge Filter: Volume Overheat Guard ===
    vol_overheat_max_ratio: float = Field(
        default=8.0,
        description="거래량 과열 상한 (8x 초과시 차단)",
    )
    vol_overheat_optimal_lower: float = Field(
        default=2.0,
        description="최적 거래량 하한 (2x)",
    )
    vol_overheat_optimal_upper: float = Field(
        default=5.0,
        description="최적 거래량 상한 (5x)",
    )

    # === Structure Anti-Chase (v4.2 - 강화) ===
    structure_chase_breakout_mult: float = Field(
        default=0.5,
        description="돌파레벨 추격 임계값 (ATR 배수) - 0.7→0.5: 더 보수적",
    )
    structure_chase_vwap_mult: float = Field(
        default=0.7,
        description="VWAP 추격 임계값 (ATR 배수) - 0.9→0.7: 더 엄격",
    )
    structure_retest_vwap_zone: float = Field(
        default=0.25,
        description="리테스트 VWAP 존 (ATR 배수) - 0.3→0.25: 더 타이트",
    )
    structure_retest_breakout_zone: float = Field(
        default=0.20,
        description="리테스트 돌파레벨 존 (ATR 배수) - 0.25→0.20: 더 타이트",
    )
    structure_retest_position_mult: float = Field(
        default=0.6,
        description="리테스트 진입 사이징 (60%) - 0.7→0.6: 리테스트시 더 보수적",
    )

    # === v4.0 Risk-Based Sizing ===
    ignition_risk_per_trade_l1: float = Field(
        default=0.0020,
        description="ATTACK_LEVEL 1 트레이드당 리스크 (0.20%)",
    )
    ignition_risk_per_trade_l2: float = Field(
        default=0.0035,
        description="ATTACK_LEVEL 2 트레이드당 리스크 (0.35%)",
    )
    ignition_risk_per_trade_l3: float = Field(
        default=0.0050,
        description="ATTACK_LEVEL 3 트레이드당 리스크 (0.50%)",
    )
    ignition_min_position_pct_l3: float = Field(
        default=0.50,
        description="ATTACK_LEVEL 3 최소 포지션 비중 (50%) - 급등주 대응",
    )
    ignition_max_position_pct_l3: float = Field(
        default=0.60,
        description="ATTACK_LEVEL 3 최대 포지션 비중 (60%) - 급등주 대응",
    )

    # === v4.0 Anti-Chase (조정 - 강화) ===
    structure_chase_dist_atr_max: float = Field(
        default=10.0,
        description="dist_atr 차단 임계값 - 10.0: 20%까지 허용",
    )
    structure_chase_dist_breakout_max: float = Field(
        default=10.0,
        description="dist_breakout 차단 배수 - 10.0: 20%까지 허용",
    )

    # === v4.0 DD Tier 연동 ===
    ignition_disable_dd_tier: int = Field(
        default=4,
        description="Ignition 비활성화 DD Tier (4 이상이면 OFF)",
    )
    retest_max_dd_tier: int = Field(
        default=5,
        description="Retest 허용 최대 DD Tier",
    )

    # === v4.0 자정 유동성 강화 ===
    midnight_liquidity_mult: float = Field(
        default=1.5,
        description="자정(00:00~01:00) 유동성 기준 배수",
    )
    midnight_start_hour: int = Field(
        default=0,
        description="자정 시간대 시작 (UTC)",
    )
    midnight_end_hour: int = Field(
        default=1,
        description="자정 시간대 종료 (UTC)",
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
        description="KMVI T2 임계값 (2.0%) - Micro Scalper OFF",
    )
    kmvi_t3: float = Field(
        default=0.028,
        description="KMVI T3 임계값 (2.8%) - 신규 진입 OFF",
    )

    # === v4.2 Pump Setup Score ===
    pump_setup_enabled: bool = Field(
        default=True,
        description="Pump Setup Score 활성화",
    )
    pump_setup_score_threshold: float = Field(
        default=0.72,
        description="Pump Setup 최소 점수",
    )
    pump_setup_comp_min: float = Field(
        default=0.55,
        description="Compression 최소 점수",
    )
    pump_setup_exp_min: float = Field(
        default=0.75,
        description="Expansion 최소 점수 (돌파 근접도)",
    )
    pump_setup_ob_min: float = Field(
        default=0.65,
        description="Orderbook Support 최소 점수",
    )
    pump_setup_vol_min: float = Field(
        default=1.8,
        description="Volume 최소 배수",
    )
    pump_setup_vol_max: float = Field(
        default=4.0,
        description="Volume 최적 구간 상한 (이상이면 감점)",
    )
    pump_setup_vol_penalty_threshold: float = Field(
        default=10.0,
        description="Volume 페널티 임계값 (이상이면 차단)",
    )
    pump_setup_w_comp: float = Field(
        default=0.25,
        description="Compression 가중치",
    )
    pump_setup_w_exp: float = Field(
        default=0.20,
        description="Expansion 가중치",
    )
    pump_setup_w_vol: float = Field(
        default=0.15,
        description="Volume 가중치",
    )
    pump_setup_w_ob: float = Field(
        default=0.30,
        description="Orderbook Support 가중치",
    )
    pump_setup_w_heat: float = Field(
        default=0.20,
        description="Heat Penalty 가중치",
    )

    # === v4.2 Orderbook Support ===
    ob_range_pct: float = Field(
        default=0.003,
        description="호가 분석 범위 (±0.3%)",
    )
    ob_sample_interval_ms: int = Field(
        default=250,
        description="호가 샘플링 간격 (ms)",
    )
    ob_ema_span_sec: int = Field(
        default=20,
        description="호가 EMA span (초)",
    )
    ob_persist_sec: int = Field(
        default=30,
        description="호가 지속성 기준 (초)",
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

    # === Dip Scalper (급락 스캘퍼) ===
    dip_scalper_enabled: bool = Field(
        default=True,
        description="급락 스캘퍼 활성화",
    )
    dip_min_drop_pct: float = Field(
        default=-0.02,
        description="최소 급락률 (-2%)",
    )
    dip_max_drop_pct: float = Field(
        default=-0.08,
        description="최대 급락률 (-8%)",
    )
    dip_atr_multiplier: float = Field(
        default=1.2,
        description="ATR 배수 (급락 기준 = ATR * 이 값) - 1.2x로 완화",
    )
    dip_buy_amount_krw: float = Field(
        default=100000,
        description="매수 금액 (KRW)",
    )
    dip_max_positions: int = Field(
        default=5,
        description="최대 포지션 수",
    )
    dip_use_limit_order: bool = Field(
        default=True,
        description="지정가 주문 사용 여부",
    )
    dip_limit_offset_pct: float = Field(
        default=0.001,
        description="지정가 오프셋 (0.1% 아래)",
    )
    dip_take_profit_pct: float = Field(
        default=0.008,
        description="익절률 (+0.8%)",
    )
    dip_stop_loss_pct: float = Field(
        default=-0.012,
        description="손절률 (-1.2%)",
    )
    dip_time_stop_minutes: int = Field(
        default=10,
        description="타임스톱 (분)",
    )
    dip_cooldown_minutes: int = Field(
        default=5,
        description="동일 종목 재진입 쿨다운 (분)",
    )
    dip_min_volume_krw: float = Field(
        default=100000000,
        description="최소 거래대금 (1억원)",
    )

    # === Dip Scalper v2.0 추가 필터 ===
    dip_min_rvol: float = Field(
        default=2.0,
        description="최소 상대거래량 (RVOL) - 평소의 2배",
    )
    dip_btc_crash_threshold: float = Field(
        default=-0.03,
        description="BTC 급락 기준 (-3% 이하면 매수 중단)",
    )
    dip_min_bid_ratio: float = Field(
        default=0.4,
        description="최소 매수호가 비율 (40% 이상)",
    )

    # === Dip Scalper v2.2 캔들 인터벌 ===
    dip_candle_interval: int = Field(
        default=1,
        description="급락 감지 캔들 인터벌 (1분 또는 3분)",
    )

    # === Dip Scalper v2.3 급등주 필터 ===
    dip_hot_stock_threshold: float = Field(
        default=0.07,
        description="급등주 차단 기준 (5분봉 +7% 이상 오른 종목 차단)",
    )
    dip_hot_stock_lookback_min: int = Field(
        default=30,
        description="급등주 감지 기간 (최근 30분 이내)",
    )

    # === Surge Detector (급등 감지) ===
    surge_min_change_1m: float = Field(
        default=0.040,
        description="최소 1분 변화율 (4.0% 이상만 급등으로 인정)",
    )
    surge_min_volume_mult: float = Field(
        default=3.0,
        description="최소 거래량 배수 (평균 대비 3배 이상)",
    )
    surge_stop_loss_pct: float = Field(
        default=-0.04,
        description="손절률 (-4%, 충분한 여유)",
    )
    surge_trailing_stop_pct: float = Field(
        default=-0.06,
        description="트레일링 스톱 (고점 -6%, 되돌림 견딤)",
    )
    surge_take_profit_mult: float = Field(
        default=3.0,
        description="익절 목표 (리스크의 3배, 3R)",
    )
    surge_time_stop_minutes: int = Field(
        default=15,
        description="타임스톱 (15분, 충분한 시간 부여)",
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
