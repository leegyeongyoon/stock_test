"""
한국 주식 자동매매 시스템 - 데이터베이스 스키마

손실 최소화를 위한 다층 지표 시스템:
1. 기술적 지표 (Technical Indicators)
2. 수급 지표 (Supply/Demand - 외국인/기관/개인)
3. 기본적 지표 (Fundamental - PER, PBR, ROE 등)
4. 시장 심리 지표 (Sentiment - 공매도, 신용잔고)
5. 섹터/테마 지표 (Sector Rotation)
"""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional
from enum import Enum

from sqlalchemy import (
    DateTime, Date, Float, Integer, String, Text, JSON, Boolean,
    Index, UniqueConstraint, ForeignKey, Numeric, SmallInteger
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base


# ============================================================================
# ENUMS
# ============================================================================

class MarketType(str, Enum):
    """시장 구분"""
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"
    KONEX = "KONEX"


class TradingStatus(str, Enum):
    """거래 상태"""
    NORMAL = "NORMAL"           # 정상
    SUSPENDED = "SUSPENDED"     # 거래정지
    ADMIN_ISSUE = "ADMIN"       # 관리종목
    WARNING = "WARNING"         # 투자경고
    CAUTION = "CAUTION"         # 투자주의


class SignalStrength(str, Enum):
    """신호 강도"""
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class PositionStatus(str, Enum):
    """포지션 상태"""
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARTIAL = "PARTIAL"


# ============================================================================
# 1. 종목 마스터 (Stock Master)
# ============================================================================

class KRStockModel(Base):
    """
    종목 마스터 테이블 - 기본 정보 + 기본적 분석 지표

    손실 방지 활용:
    - 관리종목/거래정지 필터링
    - 시가총액 기반 유동성 필터
    - 기본적 지표 기반 가치투자 필터
    """
    __tablename__ = "kr_stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    stock_name: Mapped[str] = mapped_column(String(100))
    stock_name_en: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # 시장 정보
    market: Mapped[str] = mapped_column(String(10), index=True)  # KOSPI, KOSDAQ
    sector: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    industry: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # 거래 상태 (손실 방지 핵심!)
    trading_status: Mapped[str] = mapped_column(String(20), default="NORMAL")
    is_tradable: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_etf: Mapped[bool] = mapped_column(Boolean, default=False)
    is_etn: Mapped[bool] = mapped_column(Boolean, default=False)
    is_spac: Mapped[bool] = mapped_column(Boolean, default=False)

    # 시가총액 및 주식 수
    market_cap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 시가총액 (억원)
    shares_outstanding: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 상장주식수
    float_shares: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 유통주식수
    float_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 유통비율

    # ========================================
    # 기본적 분석 지표 (Fundamental Indicators)
    # 손실 방지: 고평가 종목 필터링
    # ========================================
    per: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # PER (주가수익비율)
    pbr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # PBR (주가순자산비율)
    psr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # PSR (주가매출비율)
    pcr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # PCR (주가현금흐름비율)

    roe: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # ROE (자기자본이익률)
    roa: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # ROA (총자산이익률)

    eps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # EPS (주당순이익)
    bps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # BPS (주당순자산)
    dps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # DPS (주당배당금)
    dividend_yield: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 배당수익률

    # 재무 안정성 (손실 방지: 부실 기업 필터링)
    debt_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 부채비율
    current_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 유동비율
    quick_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 당좌비율

    # 성장성
    revenue_growth_yoy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 매출성장률
    profit_growth_yoy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 이익성장률

    # 52주 가격 범위
    week_52_high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    week_52_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 베타 (시장 민감도)
    beta: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 메타데이터
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_kr_stocks_market_tradable", "market", "is_tradable"),
        Index("ix_kr_stocks_sector", "sector"),
        Index("ix_kr_stocks_market_cap", "market_cap"),
    )


# ============================================================================
# 2. 일봉 데이터 (Daily Candles) + 기술적 지표
# ============================================================================

class KRDailyCandleModel(Base):
    """
    일봉 캔들 데이터 + 기술적 지표 + 수급 데이터

    손실 방지 활용:
    - 추세 지표 (MA, MACD) - 역추세 매매 방지
    - 과열/과매도 지표 (RSI, Stochastic) - 극단적 진입 방지
    - 변동성 지표 (ATR, BB) - 변동성 과다 종목 필터링
    - 수급 지표 - 외국인/기관 순매도 시 진입 금지
    """
    __tablename__ = "kr_daily_candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(10), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)

    # OHLCV
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)  # 거래량 (주)
    trade_value: Mapped[float] = mapped_column(Float)  # 거래대금 (원)

    # 등락 정보
    change: Mapped[float] = mapped_column(Float, default=0.0)  # 전일대비
    change_pct: Mapped[float] = mapped_column(Float, default=0.0)  # 등락률

    # ========================================
    # 기술적 지표 (Technical Indicators)
    # ========================================

    # 이동평균선 (Moving Averages) - 추세 판단
    sma_5: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sma_10: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sma_20: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sma_60: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sma_120: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sma_200: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    ema_5: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ema_10: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ema_20: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ema_60: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # MACD (추세 전환 감지)
    macd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    macd_signal: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    macd_histogram: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # RSI (과매수/과매도)
    rsi_14: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rsi_7: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Stochastic (과매수/과매도)
    stoch_k: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stoch_d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stoch_slow_k: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stoch_slow_d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Bollinger Bands (변동성)
    bb_upper: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bb_middle: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bb_lower: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bb_width: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 밴드 폭
    bb_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # %B

    # ATR (변동성)
    atr_14: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    atr_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # ATR / Close

    # Volume 지표
    volume_sma_20: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rvol: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 상대 거래량
    obv: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # On Balance Volume

    # VWAP
    vwap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ADX (추세 강도)
    adx: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    plus_di: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    minus_di: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # CCI (Commodity Channel Index)
    cci_20: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Williams %R
    williams_r: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Parabolic SAR
    psar: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    psar_trend: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)  # 1: up, -1: down

    # Ichimoku Cloud
    ichimoku_tenkan: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 전환선
    ichimoku_kijun: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 기준선
    ichimoku_senkou_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 선행스팬A
    ichimoku_senkou_b: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 선행스팬B

    # ========================================
    # 수급 지표 (Supply/Demand Indicators)
    # 손실 방지 핵심: 기관/외국인 순매도 시 진입 금지
    # ========================================

    # 투자자별 순매수 (주)
    foreign_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 외국인 순매수
    inst_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 기관 순매수
    individual_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 개인 순매수

    # 투자자별 거래량 (주)
    foreign_buy: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    foreign_sell: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    inst_buy: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    inst_sell: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 수급 누적 (N일 누적)
    foreign_net_5d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 5일 누적
    foreign_net_20d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 20일 누적
    inst_net_5d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    inst_net_20d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ========================================
    # 시장 심리 지표 (Market Sentiment)
    # ========================================

    # 공매도
    short_volume: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 공매도량
    short_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 공매도 대금
    short_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 공매도 비율

    # 대차잔고
    lending_balance: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 대차잔고
    lending_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 대차잔고 비율

    # 신용잔고
    credit_balance: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 신용잔고
    credit_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 신용잔고 비율

    # 프로그램 매매
    program_buy: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 프로그램 매수
    program_sell: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 프로그램 매도
    program_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 프로그램 순매수

    # ========================================
    # 신호 및 점수 (Pre-calculated)
    # ========================================

    # 복합 신호 강도 (-100 ~ +100)
    technical_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    supply_demand_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    combined_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 추세 상태
    trend_short: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)  # 1: up, 0: side, -1: down
    trend_medium: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    trend_long: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_kr_daily_candle"),
        Index("ix_kr_daily_lookup", "stock_code", "trade_date"),
        Index("ix_kr_daily_date", "trade_date"),
    )


# ============================================================================
# 3. 분봉 데이터 (Minute Candles)
# ============================================================================

class KRMinuteCandleModel(Base):
    """
    분봉 캔들 데이터 (1분, 5분, 15분, 30분, 60분)

    장중 전략용 (Opening Range, VWAP Pullback, Volume Surge 등)
    """
    __tablename__ = "kr_minute_candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(10), index=True)
    interval: Mapped[str] = mapped_column(String(5), index=True)  # 1m, 5m, 15m, 30m, 60m
    open_time: Mapped[datetime] = mapped_column(DateTime, index=True)

    # OHLCV
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    trade_value: Mapped[float] = mapped_column(Float)

    # 실시간 지표
    vwap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rvol: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 수급 (분봉 기준)
    foreign_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    inst_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("stock_code", "interval", "open_time", name="uq_kr_minute_candle"),
        Index("ix_kr_minute_lookup", "stock_code", "interval", "open_time"),
    )


# ============================================================================
# 4. 투자자별 매매동향 (상세)
# ============================================================================

class KRInvestorFlowModel(Base):
    """
    투자자별 상세 매매동향

    손실 방지 활용:
    - 외국인/기관 연속 순매도 감지
    - 개인 쏠림 현상 감지 (개인만 매수 = 위험 신호)
    """
    __tablename__ = "kr_investor_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(10), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)

    # 외국인
    foreign_buy_vol: Mapped[int] = mapped_column(Integer, default=0)
    foreign_sell_vol: Mapped[int] = mapped_column(Integer, default=0)
    foreign_net_vol: Mapped[int] = mapped_column(Integer, default=0)
    foreign_buy_val: Mapped[float] = mapped_column(Float, default=0.0)  # 백만원
    foreign_sell_val: Mapped[float] = mapped_column(Float, default=0.0)
    foreign_net_val: Mapped[float] = mapped_column(Float, default=0.0)
    foreign_holding_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 외국인 지분율

    # 기관 (세부)
    inst_total_buy_vol: Mapped[int] = mapped_column(Integer, default=0)
    inst_total_sell_vol: Mapped[int] = mapped_column(Integer, default=0)
    inst_total_net_vol: Mapped[int] = mapped_column(Integer, default=0)

    # 기관 세부: 금융투자, 보험, 투신, 사모, 은행, 기타금융, 연기금
    inst_finance_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 금융투자
    inst_insurance_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 보험
    inst_trust_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 투신
    inst_private_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 사모
    inst_bank_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 은행
    inst_pension_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 연기금 (중요!)
    inst_other_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 기타금융

    # 개인
    individual_buy_vol: Mapped[int] = mapped_column(Integer, default=0)
    individual_sell_vol: Mapped[int] = mapped_column(Integer, default=0)
    individual_net_vol: Mapped[int] = mapped_column(Integer, default=0)

    # 프로그램 매매
    program_buy_vol: Mapped[int] = mapped_column(Integer, default=0)
    program_sell_vol: Mapped[int] = mapped_column(Integer, default=0)
    program_net_vol: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_kr_investor_flow"),
        Index("ix_kr_investor_flow_lookup", "stock_code", "trade_date"),
    )


# ============================================================================
# 5. 공매도/대차 잔고
# ============================================================================

class KRShortInterestModel(Base):
    """
    공매도 및 대차잔고 데이터

    손실 방지 활용:
    - 공매도 급증 = 하락 신호
    - 대차잔고 증가 = 공매도 예비 물량
    """
    __tablename__ = "kr_short_interest"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(10), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)

    # 공매도
    short_volume: Mapped[int] = mapped_column(Integer, default=0)  # 공매도량
    short_value: Mapped[float] = mapped_column(Float, default=0.0)  # 공매도 대금
    short_ratio: Mapped[float] = mapped_column(Float, default=0.0)  # 공매도 비율 (%)

    # 대차잔고
    lending_new: Mapped[int] = mapped_column(Integer, default=0)  # 신규 대차
    lending_return: Mapped[int] = mapped_column(Integer, default=0)  # 상환
    lending_balance: Mapped[int] = mapped_column(Integer, default=0)  # 잔고
    lending_balance_value: Mapped[float] = mapped_column(Float, default=0.0)  # 잔고 금액
    lending_ratio: Mapped[float] = mapped_column(Float, default=0.0)  # 대차잔고 비율 (%)

    # 변화율 (전일대비)
    short_change: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lending_change: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_kr_short_interest"),
        Index("ix_kr_short_interest_lookup", "stock_code", "trade_date"),
    )


# ============================================================================
# 6. 섹터/업종 데이터
# ============================================================================

class KRSectorModel(Base):
    """
    섹터/업종 데이터 (일별)

    손실 방지 활용:
    - 약세 섹터 종목 진입 금지
    - 섹터 순환 전략
    """
    __tablename__ = "kr_sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sector_code: Mapped[str] = mapped_column(String(10), index=True)
    sector_name: Mapped[str] = mapped_column(String(50))
    trade_date: Mapped[date] = mapped_column(Date, index=True)

    # 지수
    index_value: Mapped[float] = mapped_column(Float)
    change_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # 거래
    volume: Mapped[int] = mapped_column(Integer, default=0)
    trade_value: Mapped[float] = mapped_column(Float, default=0.0)

    # 수급
    foreign_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    inst_net: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 상대강도
    relative_strength: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rs_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 전체 섹터 중 순위

    # 등락 종목수
    advance_count: Mapped[int] = mapped_column(Integer, default=0)  # 상승
    decline_count: Mapped[int] = mapped_column(Integer, default=0)  # 하락
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)  # 보합

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("sector_code", "trade_date", name="uq_kr_sector"),
        Index("ix_kr_sector_lookup", "sector_code", "trade_date"),
    )


# ============================================================================
# 7. 시장 지수 (KOSPI, KOSDAQ)
# ============================================================================

class KRMarketIndexModel(Base):
    """
    시장 지수 데이터

    손실 방지 활용:
    - 시장 하락 시 신규 진입 금지
    - 시장 추세 확인
    """
    __tablename__ = "kr_market_indices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    index_code: Mapped[str] = mapped_column(String(10), index=True)  # KOSPI, KOSDAQ, KOSPI200
    trade_date: Mapped[date] = mapped_column(Date, index=True)

    # OHLCV
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    trade_value: Mapped[float] = mapped_column(Float)

    # 등락
    change: Mapped[float] = mapped_column(Float, default=0.0)
    change_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # 이동평균
    sma_5: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sma_20: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sma_60: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sma_120: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 수급
    foreign_net: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 억원
    inst_net: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    individual_net: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 시장 심리
    vix: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # VKOSPI
    put_call_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 등락 종목수
    advance_count: Mapped[int] = mapped_column(Integer, default=0)
    decline_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    limit_up_count: Mapped[int] = mapped_column(Integer, default=0)  # 상한가
    limit_down_count: Mapped[int] = mapped_column(Integer, default=0)  # 하한가

    # 시장 상태 판단
    market_trend: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)  # 1: bull, 0: side, -1: bear
    market_breadth: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 등락비율

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("index_code", "trade_date", name="uq_kr_market_index"),
        Index("ix_kr_market_index_lookup", "index_code", "trade_date"),
    )


# ============================================================================
# 8. 전략 정의
# ============================================================================

class KRStrategyModel(Base):
    """전략 정의 테이블"""
    __tablename__ = "kr_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    strategy_type: Mapped[str] = mapped_column(String(30))  # MOMENTUM, MEAN_REVERSION, etc.
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 전략 파라미터 (JSON)
    parameters_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 리스크 파라미터
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=-0.02)  # -2%
    take_profit_pct: Mapped[float] = mapped_column(Float, default=0.04)  # +4%
    trailing_stop_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_holding_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 포지션 사이징
    max_position_size_pct: Mapped[float] = mapped_column(Float, default=0.10)  # 10%

    # 필터 조건 (JSON) - 진입 전 필수 통과 조건
    entry_filters_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 활성화 상태
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================================
# 9. 거래 기록 (Trades)
# ============================================================================

class KRTradeModel(Base):
    """
    실거래 기록 테이블

    모든 거래의 상세 기록 및 분석용
    """
    __tablename__ = "kr_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    stock_code: Mapped[str] = mapped_column(String(10), index=True)
    strategy_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("kr_strategies.id"), nullable=True)
    strategy_name: Mapped[str] = mapped_column(String(50), index=True)

    # 진입
    side: Mapped[str] = mapped_column(String(4))  # BUY, SELL
    entry_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    entry_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    entry_value: Mapped[float] = mapped_column(Float)

    # 청산
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 손익
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # 비용
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    tax: Mapped[float] = mapped_column(Float, default=0.0)  # 거래세
    slippage: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)  # 순손익

    # 보유 정보
    holding_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_profit_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # 진입 시점 지표 (분석용)
    entry_indicators_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 상태
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN, CLOSED

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_kr_trades_status", "status"),
        Index("ix_kr_trades_strategy_time", "strategy_name", "entry_time"),
    )


# ============================================================================
# 10. 포지션 관리
# ============================================================================

class KRPositionModel(Base):
    """
    현재 포지션 관리 테이블
    """
    __tablename__ = "kr_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    stock_code: Mapped[str] = mapped_column(String(10), index=True)
    stock_name: Mapped[str] = mapped_column(String(100))
    strategy_name: Mapped[str] = mapped_column(String(50))

    # 수량 및 가격
    quantity: Mapped[int] = mapped_column(Integer)
    avg_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 손익
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # 리스크 관리
    stop_loss_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trailing_stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 최고/최저
    highest_price: Mapped[float] = mapped_column(Float)
    lowest_price: Mapped[float] = mapped_column(Float)
    max_profit_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # 상태
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN, CLOSED, PARTIAL

    opened_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_kr_positions_status", "status"),
        Index("ix_kr_positions_strategy", "strategy_name"),
    )


# ============================================================================
# 11. 백테스트 결과
# ============================================================================

class KRBacktestResultModel(Base):
    """백테스트 결과 저장"""
    __tablename__ = "kr_backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    strategy_name: Mapped[str] = mapped_column(String(50), index=True)
    market: Mapped[str] = mapped_column(String(10))  # KOSPI, KOSDAQ, ALL

    # 기간
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)

    # 자본
    initial_capital: Mapped[float] = mapped_column(Float)
    final_equity: Mapped[float] = mapped_column(Float)

    # 핵심 지표
    total_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    cagr: Mapped[float] = mapped_column(Float, default=0.0)  # 연환산 수익률
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    sortino_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    calmar_ratio: Mapped[float] = mapped_column(Float, default=0.0)

    # 거래 통계
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    winning_trades: Mapped[int] = mapped_column(Integer, default=0)
    losing_trades: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, default=0.0)

    # 평균
    avg_win_pct: Mapped[float] = mapped_column(Float, default=0.0)
    avg_loss_pct: Mapped[float] = mapped_column(Float, default=0.0)
    avg_holding_days: Mapped[float] = mapped_column(Float, default=0.0)

    # 비용
    total_commission: Mapped[float] = mapped_column(Float, default=0.0)
    total_tax: Mapped[float] = mapped_column(Float, default=0.0)

    # 상세 (JSON)
    parameters_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metrics_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    equity_curve_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    monthly_returns_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ============================================================================
# 12. 신호 기록 (Signal History)
# ============================================================================

class KRSignalHistoryModel(Base):
    """
    생성된 매매 신호 기록

    분석용: 신호 vs 실제 결과 비교
    """
    __tablename__ = "kr_signal_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    stock_code: Mapped[str] = mapped_column(String(10), index=True)
    strategy_name: Mapped[str] = mapped_column(String(50), index=True)
    signal_time: Mapped[datetime] = mapped_column(DateTime, index=True)

    # 신호 정보
    signal_type: Mapped[str] = mapped_column(String(10))  # BUY, SELL
    signal_strength: Mapped[str] = mapped_column(String(20))  # STRONG_BUY, BUY, etc.
    signal_score: Mapped[float] = mapped_column(Float)  # 점수 (0-100)

    # 가격 정보
    signal_price: Mapped[float] = mapped_column(Float)

    # 신호 생성 시 지표들 (JSON)
    indicators_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 필터 통과 여부
    filters_passed_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 실행 여부
    was_executed: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # 사후 분석 (신호 후 가격 변동)
    price_after_1d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_after_5d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    return_1d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    return_5d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_kr_signals_time", "signal_time"),
        Index("ix_kr_signals_strategy", "strategy_name", "signal_time"),
    )


# ============================================================================
# 13. 필터 조건 마스터 (손실 방지 필터)
# ============================================================================

class KRFilterConditionModel(Base):
    """
    진입 필터 조건 정의

    손실 최소화를 위한 다층 필터 시스템
    """
    __tablename__ = "kr_filter_conditions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filter_name: Mapped[str] = mapped_column(String(50), unique=True)
    filter_category: Mapped[str] = mapped_column(String(30))  # TECHNICAL, FUNDAMENTAL, SUPPLY_DEMAND, RISK
    description: Mapped[str] = mapped_column(Text)

    # 조건 정의 (JSON)
    # 예: {"indicator": "rsi_14", "operator": ">=", "value": 30}
    condition_json: Mapped[dict] = mapped_column(JSON)

    # 우선순위 (높을수록 먼저 적용)
    priority: Mapped[int] = mapped_column(Integer, default=0)

    # 활성화
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_blocking: Mapped[bool] = mapped_column(Boolean, default=True)  # True면 불통과시 진입 금지

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
