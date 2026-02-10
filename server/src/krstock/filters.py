"""
한국 주식 다층 필터 시스템

손실 최소화를 위한 5단계 필터링:
1. 기본 필터 (종목 상태, 유동성)
2. 기술적 필터 (추세, 과열/과매도)
3. 수급 필터 (외국인/기관 동향)
4. 시장 필터 (시장 상황, 섹터 강도)
5. 리스크 필터 (변동성, 공매도)

모든 필터를 통과해야만 진입 가능
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional
import numpy as np

from src.krstock.indicators import TechnicalIndicators


class FilterResult(Enum):
    """필터 결과"""
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"  # 경고 (진입은 가능하나 주의)


@dataclass(frozen=True)
class FilterCheckResult:
    """개별 필터 체크 결과"""
    filter_name: str
    result: FilterResult
    reason: str
    value: Optional[float] = None
    threshold: Optional[float] = None


@dataclass(frozen=True)
class MultiLayerFilterResult:
    """다층 필터 종합 결과"""
    can_enter: bool  # 진입 가능 여부
    total_score: float  # 종합 점수 (0-100)
    passed_filters: list[str]
    failed_filters: list[str]
    warning_filters: list[str]
    filter_details: list[FilterCheckResult]
    summary: str  # 요약 메시지


# ============================================================================
# 1. 기본 필터 (Basic Filters)
# ============================================================================

@dataclass(frozen=True)
class BasicFilterConfig:
    """기본 필터 설정"""
    min_market_cap: float = 1000.0  # 최소 시가총액 (억원)
    min_daily_volume: int = 100000  # 최소 거래량 (주)
    min_daily_value: float = 10.0  # 최소 거래대금 (억원)
    max_price_limit_ratio: float = 0.28  # 가격제한폭 도달 금지 (28%)
    block_admin_stocks: bool = True  # 관리종목 진입 금지
    block_suspended: bool = True  # 거래정지 종목 진입 금지


def check_basic_filters(
    config: BasicFilterConfig,
    market_cap: Optional[float],
    daily_volume: int,
    daily_value: float,
    change_pct: float,
    trading_status: str,
    is_tradable: bool
) -> list[FilterCheckResult]:
    """기본 필터 체크"""
    results = []

    # 1-1. 거래 가능 여부
    if not is_tradable:
        results.append(FilterCheckResult(
            filter_name="거래가능여부",
            result=FilterResult.FAIL,
            reason="거래 불가 종목",
            value=0,
            threshold=1
        ))
    else:
        results.append(FilterCheckResult(
            filter_name="거래가능여부",
            result=FilterResult.PASS,
            reason="거래 가능",
            value=1,
            threshold=1
        ))

    # 1-2. 관리종목/거래정지
    if config.block_admin_stocks and trading_status in ["ADMIN", "WARNING"]:
        results.append(FilterCheckResult(
            filter_name="관리종목",
            result=FilterResult.FAIL,
            reason=f"관리종목 또는 투자경고 ({trading_status})",
            value=None,
            threshold=None
        ))
    elif config.block_suspended and trading_status == "SUSPENDED":
        results.append(FilterCheckResult(
            filter_name="거래정지",
            result=FilterResult.FAIL,
            reason="거래정지 종목",
            value=None,
            threshold=None
        ))
    else:
        results.append(FilterCheckResult(
            filter_name="종목상태",
            result=FilterResult.PASS,
            reason="정상 거래 종목",
            value=None,
            threshold=None
        ))

    # 1-3. 시가총액
    if market_cap is not None and market_cap < config.min_market_cap:
        results.append(FilterCheckResult(
            filter_name="시가총액",
            result=FilterResult.FAIL,
            reason=f"시가총액 부족 ({market_cap:.0f}억 < {config.min_market_cap:.0f}억)",
            value=market_cap,
            threshold=config.min_market_cap
        ))
    elif market_cap is not None:
        results.append(FilterCheckResult(
            filter_name="시가총액",
            result=FilterResult.PASS,
            reason=f"시가총액 충분 ({market_cap:.0f}억)",
            value=market_cap,
            threshold=config.min_market_cap
        ))

    # 1-4. 거래량
    if daily_volume < config.min_daily_volume:
        results.append(FilterCheckResult(
            filter_name="거래량",
            result=FilterResult.FAIL,
            reason=f"거래량 부족 ({daily_volume:,}주 < {config.min_daily_volume:,}주)",
            value=daily_volume,
            threshold=config.min_daily_volume
        ))
    else:
        results.append(FilterCheckResult(
            filter_name="거래량",
            result=FilterResult.PASS,
            reason=f"거래량 충분 ({daily_volume:,}주)",
            value=daily_volume,
            threshold=config.min_daily_volume
        ))

    # 1-5. 거래대금 (억원)
    daily_value_억 = daily_value / 100_000_000
    if daily_value_억 < config.min_daily_value:
        results.append(FilterCheckResult(
            filter_name="거래대금",
            result=FilterResult.FAIL,
            reason=f"거래대금 부족 ({daily_value_억:.1f}억 < {config.min_daily_value:.0f}억)",
            value=daily_value_억,
            threshold=config.min_daily_value
        ))
    else:
        results.append(FilterCheckResult(
            filter_name="거래대금",
            result=FilterResult.PASS,
            reason=f"거래대금 충분 ({daily_value_억:.1f}억)",
            value=daily_value_억,
            threshold=config.min_daily_value
        ))

    # 1-6. 가격제한폭 도달 여부 (±30% 근처 진입 금지)
    if abs(change_pct) >= config.max_price_limit_ratio * 100:
        results.append(FilterCheckResult(
            filter_name="가격제한폭",
            result=FilterResult.FAIL,
            reason=f"가격제한폭 도달 ({change_pct:.1f}%)",
            value=abs(change_pct),
            threshold=config.max_price_limit_ratio * 100
        ))
    elif abs(change_pct) >= 20:
        results.append(FilterCheckResult(
            filter_name="가격제한폭",
            result=FilterResult.WARN,
            reason=f"급등/급락 주의 ({change_pct:.1f}%)",
            value=abs(change_pct),
            threshold=config.max_price_limit_ratio * 100
        ))
    else:
        results.append(FilterCheckResult(
            filter_name="가격제한폭",
            result=FilterResult.PASS,
            reason=f"정상 범위 ({change_pct:.1f}%)",
            value=abs(change_pct),
            threshold=config.max_price_limit_ratio * 100
        ))

    return results


# ============================================================================
# 2. 기술적 필터 (Technical Filters)
# ============================================================================

@dataclass(frozen=True)
class TechnicalFilterConfig:
    """기술적 필터 설정"""
    # 추세 필터
    require_above_sma_20: bool = True  # 20일선 위 필수
    require_above_sma_60: bool = False  # 60일선 위 필수
    require_above_sma_200: bool = False  # 200일선 위 필수

    # 과매수/과매도 필터
    rsi_overbought: float = 75.0  # RSI 과매수 (진입 금지)
    rsi_oversold: float = 25.0  # RSI 과매도 (매수 기회, 하지만 주의)

    # 변동성 필터
    max_atr_pct: float = 5.0  # 최대 ATR% (변동성 과다 필터링)
    min_atr_pct: float = 0.5  # 최소 ATR% (변동성 너무 낮음)

    # MACD 필터
    require_macd_positive: bool = False  # MACD > 0 필수
    require_macd_above_signal: bool = True  # MACD > Signal 필수

    # 볼린저밴드 필터
    bb_upper_threshold: float = 0.95  # 상단밴드 95% 이상 진입 금지
    bb_lower_threshold: float = 0.05  # 하단밴드 5% 이하 주의

    # ADX (추세 강도)
    min_adx: float = 15.0  # 최소 ADX (추세 존재 확인)
    max_adx: float = 50.0  # 최대 ADX (과열 추세 주의)


def check_technical_filters(
    config: TechnicalFilterConfig,
    indicators: TechnicalIndicators,
    current_price: float
) -> list[FilterCheckResult]:
    """기술적 필터 체크"""
    results = []

    # 2-1. 이동평균선 필터
    if config.require_above_sma_20 and indicators.sma_20 is not None:
        if current_price < indicators.sma_20:
            results.append(FilterCheckResult(
                filter_name="SMA20",
                result=FilterResult.FAIL,
                reason=f"20일선 하회 (현재가 {current_price:,.0f} < SMA20 {indicators.sma_20:,.0f})",
                value=current_price,
                threshold=indicators.sma_20
            ))
        else:
            pct_above = ((current_price / indicators.sma_20) - 1) * 100
            results.append(FilterCheckResult(
                filter_name="SMA20",
                result=FilterResult.PASS,
                reason=f"20일선 상회 (+{pct_above:.1f}%)",
                value=current_price,
                threshold=indicators.sma_20
            ))

    if config.require_above_sma_60 and indicators.sma_60 is not None:
        if current_price < indicators.sma_60:
            results.append(FilterCheckResult(
                filter_name="SMA60",
                result=FilterResult.FAIL,
                reason=f"60일선 하회",
                value=current_price,
                threshold=indicators.sma_60
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="SMA60",
                result=FilterResult.PASS,
                reason=f"60일선 상회",
                value=current_price,
                threshold=indicators.sma_60
            ))

    if config.require_above_sma_200 and indicators.sma_200 is not None:
        if current_price < indicators.sma_200:
            results.append(FilterCheckResult(
                filter_name="SMA200",
                result=FilterResult.WARN,
                reason=f"200일선 하회 (장기 하락추세)",
                value=current_price,
                threshold=indicators.sma_200
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="SMA200",
                result=FilterResult.PASS,
                reason=f"200일선 상회 (장기 상승추세)",
                value=current_price,
                threshold=indicators.sma_200
            ))

    # 2-2. RSI 필터
    if indicators.rsi_14 is not None:
        if indicators.rsi_14 >= config.rsi_overbought:
            results.append(FilterCheckResult(
                filter_name="RSI",
                result=FilterResult.FAIL,
                reason=f"RSI 과매수 ({indicators.rsi_14:.1f} >= {config.rsi_overbought})",
                value=indicators.rsi_14,
                threshold=config.rsi_overbought
            ))
        elif indicators.rsi_14 <= config.rsi_oversold:
            results.append(FilterCheckResult(
                filter_name="RSI",
                result=FilterResult.WARN,
                reason=f"RSI 과매도 ({indicators.rsi_14:.1f} <= {config.rsi_oversold}) - 반등 가능",
                value=indicators.rsi_14,
                threshold=config.rsi_oversold
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="RSI",
                result=FilterResult.PASS,
                reason=f"RSI 정상 범위 ({indicators.rsi_14:.1f})",
                value=indicators.rsi_14,
                threshold=None
            ))

    # 2-3. ATR (변동성) 필터
    if indicators.atr_pct is not None:
        if indicators.atr_pct > config.max_atr_pct:
            results.append(FilterCheckResult(
                filter_name="ATR",
                result=FilterResult.FAIL,
                reason=f"변동성 과다 (ATR {indicators.atr_pct:.2f}% > {config.max_atr_pct}%)",
                value=indicators.atr_pct,
                threshold=config.max_atr_pct
            ))
        elif indicators.atr_pct < config.min_atr_pct:
            results.append(FilterCheckResult(
                filter_name="ATR",
                result=FilterResult.WARN,
                reason=f"변동성 과소 (ATR {indicators.atr_pct:.2f}% < {config.min_atr_pct}%)",
                value=indicators.atr_pct,
                threshold=config.min_atr_pct
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="ATR",
                result=FilterResult.PASS,
                reason=f"변동성 적정 (ATR {indicators.atr_pct:.2f}%)",
                value=indicators.atr_pct,
                threshold=None
            ))

    # 2-4. MACD 필터
    if indicators.macd is not None and indicators.macd_signal is not None:
        if config.require_macd_above_signal and indicators.macd < indicators.macd_signal:
            results.append(FilterCheckResult(
                filter_name="MACD",
                result=FilterResult.FAIL,
                reason=f"MACD 하락신호 (MACD {indicators.macd:.2f} < Signal {indicators.macd_signal:.2f})",
                value=indicators.macd,
                threshold=indicators.macd_signal
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="MACD",
                result=FilterResult.PASS,
                reason=f"MACD 상승신호",
                value=indicators.macd,
                threshold=indicators.macd_signal
            ))

    # 2-5. 볼린저밴드 필터
    if indicators.bb_pct is not None:
        if indicators.bb_pct >= config.bb_upper_threshold:
            results.append(FilterCheckResult(
                filter_name="볼린저밴드",
                result=FilterResult.FAIL,
                reason=f"상단밴드 근접 (%B {indicators.bb_pct:.2f} >= {config.bb_upper_threshold})",
                value=indicators.bb_pct,
                threshold=config.bb_upper_threshold
            ))
        elif indicators.bb_pct <= config.bb_lower_threshold:
            results.append(FilterCheckResult(
                filter_name="볼린저밴드",
                result=FilterResult.WARN,
                reason=f"하단밴드 근접 (%B {indicators.bb_pct:.2f})",
                value=indicators.bb_pct,
                threshold=config.bb_lower_threshold
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="볼린저밴드",
                result=FilterResult.PASS,
                reason=f"밴드 내 정상 위치 (%B {indicators.bb_pct:.2f})",
                value=indicators.bb_pct,
                threshold=None
            ))

    # 2-6. ADX (추세 강도) 필터
    if indicators.adx is not None:
        if indicators.adx < config.min_adx:
            results.append(FilterCheckResult(
                filter_name="ADX",
                result=FilterResult.WARN,
                reason=f"약한 추세 (ADX {indicators.adx:.1f} < {config.min_adx})",
                value=indicators.adx,
                threshold=config.min_adx
            ))
        elif indicators.adx > config.max_adx:
            results.append(FilterCheckResult(
                filter_name="ADX",
                result=FilterResult.WARN,
                reason=f"과열 추세 (ADX {indicators.adx:.1f} > {config.max_adx})",
                value=indicators.adx,
                threshold=config.max_adx
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="ADX",
                result=FilterResult.PASS,
                reason=f"적정 추세 강도 (ADX {indicators.adx:.1f})",
                value=indicators.adx,
                threshold=None
            ))

    # 2-7. Stochastic 필터
    if indicators.stoch_slow_k is not None and indicators.stoch_slow_d is not None:
        if indicators.stoch_slow_k > 80 and indicators.stoch_slow_d > 80:
            results.append(FilterCheckResult(
                filter_name="스토캐스틱",
                result=FilterResult.WARN,
                reason=f"과매수 구간 (K:{indicators.stoch_slow_k:.1f}, D:{indicators.stoch_slow_d:.1f})",
                value=indicators.stoch_slow_k,
                threshold=80
            ))
        elif indicators.stoch_slow_k < 20 and indicators.stoch_slow_d < 20:
            results.append(FilterCheckResult(
                filter_name="스토캐스틱",
                result=FilterResult.WARN,
                reason=f"과매도 구간 - 반등 가능",
                value=indicators.stoch_slow_k,
                threshold=20
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="스토캐스틱",
                result=FilterResult.PASS,
                reason=f"정상 범위",
                value=indicators.stoch_slow_k,
                threshold=None
            ))

    return results


# ============================================================================
# 3. 수급 필터 (Supply/Demand Filters)
# ============================================================================

@dataclass(frozen=True)
class SupplyDemandFilterConfig:
    """수급 필터 설정"""
    # 외국인/기관 순매수 필터
    require_foreign_positive: bool = False  # 외국인 순매수 필수
    require_inst_positive: bool = False  # 기관 순매수 필수

    # 개인 쏠림 방지
    max_individual_ratio: float = 0.8  # 개인만 80% 이상 매수 시 진입 금지

    # 연속 순매도 필터
    max_consecutive_sell_days: int = 5  # 외국인/기관 연속 순매도일 제한

    # 수급 점수 임계값
    min_supply_demand_score: float = 30.0  # 최소 수급 점수 (0-100)


def check_supply_demand_filters(
    config: SupplyDemandFilterConfig,
    foreign_net: int,
    inst_net: int,
    individual_net: int,
    foreign_net_5d: int,
    inst_net_5d: int,
    total_volume: int
) -> list[FilterCheckResult]:
    """수급 필터 체크"""
    results = []

    # 3-1. 외국인 순매수 필터
    if config.require_foreign_positive and foreign_net < 0:
        results.append(FilterCheckResult(
            filter_name="외국인",
            result=FilterResult.FAIL,
            reason=f"외국인 순매도 ({foreign_net:,}주)",
            value=foreign_net,
            threshold=0
        ))
    elif foreign_net > 0:
        results.append(FilterCheckResult(
            filter_name="외국인",
            result=FilterResult.PASS,
            reason=f"외국인 순매수 (+{foreign_net:,}주)",
            value=foreign_net,
            threshold=0
        ))
    else:
        results.append(FilterCheckResult(
            filter_name="외국인",
            result=FilterResult.WARN if config.require_foreign_positive else FilterResult.PASS,
            reason=f"외국인 순매도 ({foreign_net:,}주)",
            value=foreign_net,
            threshold=0
        ))

    # 3-2. 기관 순매수 필터
    if config.require_inst_positive and inst_net < 0:
        results.append(FilterCheckResult(
            filter_name="기관",
            result=FilterResult.FAIL,
            reason=f"기관 순매도 ({inst_net:,}주)",
            value=inst_net,
            threshold=0
        ))
    elif inst_net > 0:
        results.append(FilterCheckResult(
            filter_name="기관",
            result=FilterResult.PASS,
            reason=f"기관 순매수 (+{inst_net:,}주)",
            value=inst_net,
            threshold=0
        ))
    else:
        results.append(FilterCheckResult(
            filter_name="기관",
            result=FilterResult.WARN if config.require_inst_positive else FilterResult.PASS,
            reason=f"기관 순매도 ({inst_net:,}주)",
            value=inst_net,
            threshold=0
        ))

    # 3-3. 개인 쏠림 방지
    if total_volume > 0 and individual_net > 0:
        individual_buy_ratio = individual_net / total_volume
        if individual_buy_ratio >= config.max_individual_ratio:
            results.append(FilterCheckResult(
                filter_name="개인쏠림",
                result=FilterResult.FAIL,
                reason=f"개인 과도 매수 ({individual_buy_ratio*100:.1f}% > {config.max_individual_ratio*100:.0f}%)",
                value=individual_buy_ratio * 100,
                threshold=config.max_individual_ratio * 100
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="개인쏠림",
                result=FilterResult.PASS,
                reason=f"개인 매수 비율 정상 ({individual_buy_ratio*100:.1f}%)",
                value=individual_buy_ratio * 100,
                threshold=config.max_individual_ratio * 100
            ))

    # 3-4. 5일 누적 수급
    combined_5d = foreign_net_5d + inst_net_5d
    if combined_5d < 0:
        # 외국인+기관 5일 연속 순매도
        if foreign_net_5d < 0 and inst_net_5d < 0:
            results.append(FilterCheckResult(
                filter_name="5일수급",
                result=FilterResult.FAIL,
                reason=f"외국인+기관 5일 순매도 (외국인 {foreign_net_5d:,}주, 기관 {inst_net_5d:,}주)",
                value=combined_5d,
                threshold=0
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="5일수급",
                result=FilterResult.WARN,
                reason=f"5일 순매도 우세",
                value=combined_5d,
                threshold=0
            ))
    else:
        results.append(FilterCheckResult(
            filter_name="5일수급",
            result=FilterResult.PASS,
            reason=f"5일 순매수 우세 (+{combined_5d:,}주)",
            value=combined_5d,
            threshold=0
        ))

    return results


# ============================================================================
# 4. 시장 필터 (Market Filters)
# ============================================================================

@dataclass(frozen=True)
class MarketFilterConfig:
    """시장 필터 설정"""
    # 시장 추세 필터
    require_market_uptrend: bool = True  # 시장 상승 추세 필수
    max_market_decline: float = -1.5  # 시장 하락 시 진입 금지 (%)

    # 섹터 강도 필터
    min_sector_rs_rank: int = 15  # 섹터 상대강도 순위 (상위 15위 이내)

    # VIX 필터
    max_vkospi: float = 25.0  # VKOSPI 25 이상 시 진입 금지

    # 시장 분위기
    min_advance_decline_ratio: float = 0.4  # 최소 등락비율 (상승종목 비율)


def check_market_filters(
    config: MarketFilterConfig,
    market_change_pct: float,
    market_trend: int,  # 1: bull, 0: side, -1: bear
    sector_rs_rank: Optional[int],
    vkospi: Optional[float],
    advance_count: int,
    decline_count: int
) -> list[FilterCheckResult]:
    """시장 필터 체크"""
    results = []

    # 4-1. 시장 추세 필터
    if config.require_market_uptrend:
        if market_change_pct < config.max_market_decline:
            results.append(FilterCheckResult(
                filter_name="시장추세",
                result=FilterResult.FAIL,
                reason=f"시장 급락 ({market_change_pct:.2f}% < {config.max_market_decline}%)",
                value=market_change_pct,
                threshold=config.max_market_decline
            ))
        elif market_trend == -1:
            results.append(FilterCheckResult(
                filter_name="시장추세",
                result=FilterResult.WARN,
                reason=f"시장 하락 추세 ({market_change_pct:.2f}%)",
                value=market_change_pct,
                threshold=0
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="시장추세",
                result=FilterResult.PASS,
                reason=f"시장 상승/횡보 ({market_change_pct:.2f}%)",
                value=market_change_pct,
                threshold=0
            ))

    # 4-2. 섹터 상대강도 필터
    if sector_rs_rank is not None:
        if sector_rs_rank > config.min_sector_rs_rank:
            results.append(FilterCheckResult(
                filter_name="섹터강도",
                result=FilterResult.WARN,
                reason=f"약세 섹터 (순위 {sector_rs_rank}위 > {config.min_sector_rs_rank}위)",
                value=sector_rs_rank,
                threshold=config.min_sector_rs_rank
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="섹터강도",
                result=FilterResult.PASS,
                reason=f"강세 섹터 (순위 {sector_rs_rank}위)",
                value=sector_rs_rank,
                threshold=config.min_sector_rs_rank
            ))

    # 4-3. VKOSPI 필터
    if vkospi is not None:
        if vkospi > config.max_vkospi:
            results.append(FilterCheckResult(
                filter_name="VKOSPI",
                result=FilterResult.FAIL,
                reason=f"변동성 지수 과열 (VKOSPI {vkospi:.1f} > {config.max_vkospi})",
                value=vkospi,
                threshold=config.max_vkospi
            ))
        elif vkospi > 20:
            results.append(FilterCheckResult(
                filter_name="VKOSPI",
                result=FilterResult.WARN,
                reason=f"변동성 지수 상승 (VKOSPI {vkospi:.1f})",
                value=vkospi,
                threshold=config.max_vkospi
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="VKOSPI",
                result=FilterResult.PASS,
                reason=f"변동성 지수 안정 (VKOSPI {vkospi:.1f})",
                value=vkospi,
                threshold=config.max_vkospi
            ))

    # 4-4. 등락비율 필터
    total_stocks = advance_count + decline_count
    if total_stocks > 0:
        advance_ratio = advance_count / total_stocks
        if advance_ratio < config.min_advance_decline_ratio:
            results.append(FilterCheckResult(
                filter_name="등락비율",
                result=FilterResult.WARN,
                reason=f"하락종목 우세 (상승 {advance_ratio*100:.1f}%)",
                value=advance_ratio * 100,
                threshold=config.min_advance_decline_ratio * 100
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="등락비율",
                result=FilterResult.PASS,
                reason=f"상승종목 우세 (상승 {advance_ratio*100:.1f}%)",
                value=advance_ratio * 100,
                threshold=config.min_advance_decline_ratio * 100
            ))

    return results


# ============================================================================
# 5. 리스크 필터 (Risk Filters)
# ============================================================================

@dataclass(frozen=True)
class RiskFilterConfig:
    """리스크 필터 설정"""
    # 공매도 필터
    max_short_ratio: float = 10.0  # 최대 공매도 비율 (%)
    max_short_increase: float = 50.0  # 공매도 급증 필터 (전일대비 %)

    # 신용잔고 필터
    max_credit_ratio: float = 5.0  # 최대 신용잔고 비율 (%)

    # 대차잔고 필터
    max_lending_ratio: float = 5.0  # 최대 대차잔고 비율 (%)

    # 52주 고점/저점 필터
    near_52w_high_threshold: float = 0.95  # 52주 고점 95% 이상 주의
    near_52w_low_threshold: float = 1.10  # 52주 저점 110% 이하 주의


def check_risk_filters(
    config: RiskFilterConfig,
    short_ratio: Optional[float],
    short_change: Optional[float],
    credit_ratio: Optional[float],
    lending_ratio: Optional[float],
    current_price: float,
    week_52_high: Optional[float],
    week_52_low: Optional[float]
) -> list[FilterCheckResult]:
    """리스크 필터 체크"""
    results = []

    # 5-1. 공매도 비율 필터
    if short_ratio is not None:
        if short_ratio > config.max_short_ratio:
            results.append(FilterCheckResult(
                filter_name="공매도비율",
                result=FilterResult.FAIL,
                reason=f"공매도 과다 ({short_ratio:.1f}% > {config.max_short_ratio}%)",
                value=short_ratio,
                threshold=config.max_short_ratio
            ))
        elif short_ratio > config.max_short_ratio * 0.7:
            results.append(FilterCheckResult(
                filter_name="공매도비율",
                result=FilterResult.WARN,
                reason=f"공매도 주의 ({short_ratio:.1f}%)",
                value=short_ratio,
                threshold=config.max_short_ratio
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="공매도비율",
                result=FilterResult.PASS,
                reason=f"공매도 정상 ({short_ratio:.1f}%)",
                value=short_ratio,
                threshold=config.max_short_ratio
            ))

    # 5-2. 공매도 급증 필터
    if short_change is not None and short_change > config.max_short_increase:
        results.append(FilterCheckResult(
            filter_name="공매도급증",
            result=FilterResult.FAIL,
            reason=f"공매도 급증 (전일대비 +{short_change:.1f}%)",
            value=short_change,
            threshold=config.max_short_increase
        ))
    elif short_change is not None:
        results.append(FilterCheckResult(
            filter_name="공매도급증",
            result=FilterResult.PASS,
            reason=f"공매도 변동 정상 ({short_change:+.1f}%)",
            value=short_change,
            threshold=config.max_short_increase
        ))

    # 5-3. 신용잔고 필터
    if credit_ratio is not None:
        if credit_ratio > config.max_credit_ratio:
            results.append(FilterCheckResult(
                filter_name="신용잔고",
                result=FilterResult.WARN,
                reason=f"신용잔고 과다 ({credit_ratio:.1f}% > {config.max_credit_ratio}%)",
                value=credit_ratio,
                threshold=config.max_credit_ratio
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="신용잔고",
                result=FilterResult.PASS,
                reason=f"신용잔고 정상 ({credit_ratio:.1f}%)",
                value=credit_ratio,
                threshold=config.max_credit_ratio
            ))

    # 5-4. 대차잔고 필터
    if lending_ratio is not None:
        if lending_ratio > config.max_lending_ratio:
            results.append(FilterCheckResult(
                filter_name="대차잔고",
                result=FilterResult.WARN,
                reason=f"대차잔고 과다 ({lending_ratio:.1f}%) - 공매도 예비 물량",
                value=lending_ratio,
                threshold=config.max_lending_ratio
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="대차잔고",
                result=FilterResult.PASS,
                reason=f"대차잔고 정상 ({lending_ratio:.1f}%)",
                value=lending_ratio,
                threshold=config.max_lending_ratio
            ))

    # 5-5. 52주 고점/저점 필터
    if week_52_high is not None and week_52_high > 0:
        high_ratio = current_price / week_52_high
        if high_ratio >= config.near_52w_high_threshold:
            results.append(FilterCheckResult(
                filter_name="52주고점",
                result=FilterResult.WARN,
                reason=f"52주 고점 근접 ({high_ratio*100:.1f}%)",
                value=high_ratio * 100,
                threshold=config.near_52w_high_threshold * 100
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="52주고점",
                result=FilterResult.PASS,
                reason=f"52주 고점 대비 {high_ratio*100:.1f}%",
                value=high_ratio * 100,
                threshold=config.near_52w_high_threshold * 100
            ))

    if week_52_low is not None and week_52_low > 0:
        low_ratio = current_price / week_52_low
        if low_ratio <= config.near_52w_low_threshold:
            results.append(FilterCheckResult(
                filter_name="52주저점",
                result=FilterResult.WARN,
                reason=f"52주 저점 근접 (저점대비 {low_ratio*100:.1f}%)",
                value=low_ratio * 100,
                threshold=config.near_52w_low_threshold * 100
            ))
        else:
            results.append(FilterCheckResult(
                filter_name="52주저점",
                result=FilterResult.PASS,
                reason=f"52주 저점 대비 충분히 상승 ({low_ratio*100:.1f}%)",
                value=low_ratio * 100,
                threshold=config.near_52w_low_threshold * 100
            ))

    return results


# ============================================================================
# 종합 필터 시스템
# ============================================================================

@dataclass(frozen=True)
class MultiLayerFilterConfig:
    """다층 필터 종합 설정"""
    basic: BasicFilterConfig = BasicFilterConfig()
    technical: TechnicalFilterConfig = TechnicalFilterConfig()
    supply_demand: SupplyDemandFilterConfig = SupplyDemandFilterConfig()
    market: MarketFilterConfig = MarketFilterConfig()
    risk: RiskFilterConfig = RiskFilterConfig()

    # 종합 설정
    min_pass_score: float = 70.0  # 최소 통과 점수
    allow_warnings: bool = True  # 경고 상태에서 진입 허용


def run_multi_layer_filters(
    config: MultiLayerFilterConfig,
    # 기본 정보
    market_cap: Optional[float],
    daily_volume: int,
    daily_value: float,
    change_pct: float,
    trading_status: str,
    is_tradable: bool,
    # 기술적 지표
    indicators: TechnicalIndicators,
    current_price: float,
    # 수급 정보
    foreign_net: int,
    inst_net: int,
    individual_net: int,
    foreign_net_5d: int,
    inst_net_5d: int,
    total_volume: int,
    # 시장 정보
    market_change_pct: float,
    market_trend: int,
    sector_rs_rank: Optional[int],
    vkospi: Optional[float],
    advance_count: int,
    decline_count: int,
    # 리스크 정보
    short_ratio: Optional[float],
    short_change: Optional[float],
    credit_ratio: Optional[float],
    lending_ratio: Optional[float],
    week_52_high: Optional[float],
    week_52_low: Optional[float]
) -> MultiLayerFilterResult:
    """
    다층 필터 종합 실행

    5단계 필터를 모두 실행하고 종합 결과 반환
    """
    all_results: list[FilterCheckResult] = []

    # 1. 기본 필터
    basic_results = check_basic_filters(
        config.basic, market_cap, daily_volume, daily_value,
        change_pct, trading_status, is_tradable
    )
    all_results.extend(basic_results)

    # 2. 기술적 필터
    technical_results = check_technical_filters(
        config.technical, indicators, current_price
    )
    all_results.extend(technical_results)

    # 3. 수급 필터
    supply_results = check_supply_demand_filters(
        config.supply_demand, foreign_net, inst_net, individual_net,
        foreign_net_5d, inst_net_5d, total_volume
    )
    all_results.extend(supply_results)

    # 4. 시장 필터
    market_results = check_market_filters(
        config.market, market_change_pct, market_trend,
        sector_rs_rank, vkospi, advance_count, decline_count
    )
    all_results.extend(market_results)

    # 5. 리스크 필터
    risk_results = check_risk_filters(
        config.risk, short_ratio, short_change, credit_ratio,
        lending_ratio, current_price, week_52_high, week_52_low
    )
    all_results.extend(risk_results)

    # 결과 분류
    passed = [r.filter_name for r in all_results if r.result == FilterResult.PASS]
    failed = [r.filter_name for r in all_results if r.result == FilterResult.FAIL]
    warnings = [r.filter_name for r in all_results if r.result == FilterResult.WARN]

    # 점수 계산
    total_filters = len(all_results)
    if total_filters == 0:
        score = 0.0
    else:
        # PASS = 100점, WARN = 50점, FAIL = 0점
        score_sum = len(passed) * 100 + len(warnings) * 50
        score = score_sum / total_filters

    # 진입 가능 여부 판단
    # FAIL이 하나라도 있으면 진입 불가
    has_fail = len(failed) > 0
    has_critical_warning = len(warnings) >= 3  # 경고 3개 이상도 주의

    can_enter = (
        not has_fail and
        score >= config.min_pass_score and
        (config.allow_warnings or len(warnings) == 0)
    )

    # 요약 메시지 생성
    if can_enter:
        summary = f"✅ 진입 가능 (점수: {score:.1f}점, 통과: {len(passed)}, 경고: {len(warnings)})"
    else:
        if has_fail:
            summary = f"❌ 진입 불가 - 필터 실패: {', '.join(failed[:3])}"
        else:
            summary = f"❌ 진입 불가 - 점수 미달 ({score:.1f}점 < {config.min_pass_score}점)"

    return MultiLayerFilterResult(
        can_enter=can_enter,
        total_score=score,
        passed_filters=passed,
        failed_filters=failed,
        warning_filters=warnings,
        filter_details=all_results,
        summary=summary
    )
