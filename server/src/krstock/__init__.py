"""
한국 주식 자동매매 시스템 - 핵심 모듈

구성요소:
- indicators: 기술적 지표 계산
- filters: 다층 필터 시스템 (손실 방지)
- market: 시장 정보 (거래일, 호가단위, 비용)
"""

from src.krstock.indicators import (
    TechnicalIndicators,
    calculate_sma,
    calculate_ema,
    calculate_rsi,
    calculate_macd,
    calculate_stochastic,
    calculate_bollinger_bands,
    calculate_atr,
    calculate_adx,
    calculate_cci,
    calculate_williams_r,
    calculate_obv,
    calculate_vwap,
    calculate_rvol,
    calculate_ichimoku,
    calculate_parabolic_sar,
    calculate_all_indicators,
)

from src.krstock.filters import (
    FilterResult,
    FilterCheckResult,
    MultiLayerFilterResult,
    BasicFilterConfig,
    TechnicalFilterConfig,
    SupplyDemandFilterConfig,
    MarketFilterConfig,
    RiskFilterConfig,
    MultiLayerFilterConfig,
    check_basic_filters,
    check_technical_filters,
    check_supply_demand_filters,
    check_market_filters,
    check_risk_filters,
    run_multi_layer_filters,
)

from src.krstock.market import (
    MarketType,
    MarketSession,
    TradingHours,
    TradingCostConfig,
    MarketInfo,
    KST,
    is_trading_day,
    get_next_trading_day,
    get_prev_trading_day,
    get_trading_days_in_range,
    get_current_session,
    is_market_open,
    get_time_to_market_open,
    get_time_to_market_close,
    get_tick_size,
    align_to_tick,
    get_valid_order_price,
    calculate_price_limits,
    calculate_trading_cost,
    calculate_breakeven_pct,
    get_market_info,
    PRICE_LIMIT_RATIO,
)


__all__ = [
    # Indicators
    "TechnicalIndicators",
    "calculate_all_indicators",
    # Filters
    "FilterResult",
    "MultiLayerFilterResult",
    "MultiLayerFilterConfig",
    "run_multi_layer_filters",
    # Market
    "MarketType",
    "MarketSession",
    "is_trading_day",
    "is_market_open",
    "get_tick_size",
    "align_to_tick",
    "calculate_trading_cost",
    "calculate_price_limits",
    "PRICE_LIMIT_RATIO",
]
