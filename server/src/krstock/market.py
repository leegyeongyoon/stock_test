"""
한국 주식 시장 정보 모듈

거래 시간, 가격제한폭, 호가단위 등 한국 주식시장 특성 관리
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo


# 한국 시간대
KST = ZoneInfo("Asia/Seoul")


class MarketSession(Enum):
    """시장 세션"""
    PRE_MARKET = "PRE_MARKET"       # 장전 시간외 (08:00-08:30)
    REGULAR = "REGULAR"              # 정규장 (09:00-15:30)
    POST_MARKET = "POST_MARKET"      # 장후 시간외 (15:40-18:00)
    CLOSED = "CLOSED"                # 폐장


class MarketType(Enum):
    """시장 구분"""
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"
    KONEX = "KONEX"


@dataclass(frozen=True)
class TradingHours:
    """거래 시간 정보"""
    pre_market_start: time = time(8, 0)
    pre_market_end: time = time(8, 30)
    regular_start: time = time(9, 0)
    regular_end: time = time(15, 30)
    post_market_start: time = time(15, 40)
    post_market_end: time = time(18, 0)


# 2024-2025 한국 공휴일 (매년 업데이트 필요)
KOREAN_HOLIDAYS_2024 = {
    date(2024, 1, 1),   # 신정
    date(2024, 2, 9),   # 설 연휴
    date(2024, 2, 10),  # 설날
    date(2024, 2, 11),  # 설 연휴
    date(2024, 2, 12),  # 대체공휴일
    date(2024, 3, 1),   # 삼일절
    date(2024, 4, 10),  # 22대 총선
    date(2024, 5, 5),   # 어린이날
    date(2024, 5, 6),   # 대체공휴일
    date(2024, 5, 15),  # 부처님 오신 날
    date(2024, 6, 6),   # 현충일
    date(2024, 8, 15),  # 광복절
    date(2024, 9, 16),  # 추석 연휴
    date(2024, 9, 17),  # 추석
    date(2024, 9, 18),  # 추석 연휴
    date(2024, 10, 3),  # 개천절
    date(2024, 10, 9),  # 한글날
    date(2024, 12, 25), # 크리스마스
    date(2024, 12, 31), # 연말 폐장
}

KOREAN_HOLIDAYS_2025 = {
    date(2025, 1, 1),   # 신정
    date(2025, 1, 28),  # 설 연휴
    date(2025, 1, 29),  # 설날
    date(2025, 1, 30),  # 설 연휴
    date(2025, 3, 1),   # 삼일절 (토요일)
    date(2025, 3, 3),   # 대체공휴일
    date(2025, 5, 5),   # 어린이날
    date(2025, 5, 6),   # 대체공휴일
    date(2025, 5, 5),   # 부처님 오신 날
    date(2025, 6, 6),   # 현충일
    date(2025, 8, 15),  # 광복절
    date(2025, 10, 3),  # 개천절
    date(2025, 10, 5),  # 추석 연휴
    date(2025, 10, 6),  # 추석
    date(2025, 10, 7),  # 추석 연휴
    date(2025, 10, 8),  # 대체공휴일
    date(2025, 10, 9),  # 한글날
    date(2025, 12, 25), # 크리스마스
    date(2025, 12, 31), # 연말 폐장
}

KOREAN_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # 신정
    date(2026, 2, 16),  # 설 연휴
    date(2026, 2, 17),  # 설날
    date(2026, 2, 18),  # 설 연휴
    date(2026, 3, 1),   # 삼일절 (일요일)
    date(2026, 3, 2),   # 대체공휴일
    date(2026, 5, 5),   # 어린이날
    date(2026, 5, 24),  # 부처님 오신 날
    date(2026, 6, 6),   # 현충일 (토요일)
    date(2026, 8, 15),  # 광복절 (토요일)
    date(2026, 9, 24),  # 추석 연휴
    date(2026, 9, 25),  # 추석
    date(2026, 9, 26),  # 추석 연휴
    date(2026, 10, 3),  # 개천절 (토요일)
    date(2026, 10, 9),  # 한글날
    date(2026, 12, 25), # 크리스마스
    date(2026, 12, 31), # 연말 폐장
}

ALL_HOLIDAYS = KOREAN_HOLIDAYS_2024 | KOREAN_HOLIDAYS_2025 | KOREAN_HOLIDAYS_2026


def is_trading_day(check_date: date) -> bool:
    """거래일인지 확인"""
    # 주말 체크
    if check_date.weekday() >= 5:  # 토, 일
        return False

    # 공휴일 체크
    if check_date in ALL_HOLIDAYS:
        return False

    return True


def get_next_trading_day(from_date: date) -> date:
    """다음 거래일 반환"""
    next_day = from_date + timedelta(days=1)
    while not is_trading_day(next_day):
        next_day += timedelta(days=1)
    return next_day


def get_prev_trading_day(from_date: date) -> date:
    """이전 거래일 반환"""
    prev_day = from_date - timedelta(days=1)
    while not is_trading_day(prev_day):
        prev_day -= timedelta(days=1)
    return prev_day


def get_trading_days_in_range(start_date: date, end_date: date) -> list[date]:
    """기간 내 거래일 목록 반환"""
    trading_days = []
    current = start_date
    while current <= end_date:
        if is_trading_day(current):
            trading_days.append(current)
        current += timedelta(days=1)
    return trading_days


def get_current_session(check_time: Optional[datetime] = None) -> MarketSession:
    """현재 시장 세션 반환"""
    if check_time is None:
        check_time = datetime.now(KST)
    elif check_time.tzinfo is None:
        check_time = check_time.replace(tzinfo=KST)

    # 거래일이 아니면 폐장
    if not is_trading_day(check_time.date()):
        return MarketSession.CLOSED

    current_time = check_time.time()
    hours = TradingHours()

    if hours.pre_market_start <= current_time < hours.pre_market_end:
        return MarketSession.PRE_MARKET
    elif hours.regular_start <= current_time < hours.regular_end:
        return MarketSession.REGULAR
    elif hours.post_market_start <= current_time < hours.post_market_end:
        return MarketSession.POST_MARKET
    else:
        return MarketSession.CLOSED


def is_market_open(check_time: Optional[datetime] = None) -> bool:
    """정규장 개장 여부"""
    session = get_current_session(check_time)
    return session == MarketSession.REGULAR


def get_time_to_market_open(from_time: Optional[datetime] = None) -> Optional[timedelta]:
    """장 시작까지 남은 시간"""
    if from_time is None:
        from_time = datetime.now(KST)
    elif from_time.tzinfo is None:
        from_time = from_time.replace(tzinfo=KST)

    today = from_time.date()

    # 오늘이 거래일이고 아직 장 시작 전이면
    if is_trading_day(today):
        market_open = datetime.combine(today, TradingHours().regular_start, KST)
        if from_time < market_open:
            return market_open - from_time

    # 다음 거래일 장 시작 시간
    next_day = get_next_trading_day(today)
    next_open = datetime.combine(next_day, TradingHours().regular_start, KST)
    return next_open - from_time


def get_time_to_market_close(from_time: Optional[datetime] = None) -> Optional[timedelta]:
    """장 마감까지 남은 시간 (정규장 중에만 유효)"""
    if from_time is None:
        from_time = datetime.now(KST)
    elif from_time.tzinfo is None:
        from_time = from_time.replace(tzinfo=KST)

    if not is_market_open(from_time):
        return None

    market_close = datetime.combine(from_time.date(), TradingHours().regular_end, KST)
    return market_close - from_time


# ============================================================================
# 가격제한폭 및 호가단위
# ============================================================================

# 가격제한폭: ±30% (2015년 6월 15일부터)
PRICE_LIMIT_RATIO = 0.30


def calculate_price_limits(base_price: float) -> tuple[int, int]:
    """
    상한가/하한가 계산

    Args:
        base_price: 기준가 (전일 종가)

    Returns:
        (상한가, 하한가) 튜플
    """
    upper_limit = int(base_price * (1 + PRICE_LIMIT_RATIO))
    lower_limit = int(base_price * (1 - PRICE_LIMIT_RATIO))

    # 호가단위로 조정
    upper_limit = align_to_tick(upper_limit, up=False)  # 상한가는 내림
    lower_limit = align_to_tick(lower_limit, up=True)   # 하한가는 올림

    return upper_limit, lower_limit


def get_tick_size(price: float) -> int:
    """
    호가단위 반환

    한국 주식 호가단위 (2023년 기준)
    - 2,000원 미만: 1원
    - 2,000원 이상 ~ 5,000원 미만: 5원
    - 5,000원 이상 ~ 20,000원 미만: 10원
    - 20,000원 이상 ~ 50,000원 미만: 50원
    - 50,000원 이상 ~ 200,000원 미만: 100원
    - 200,000원 이상 ~ 500,000원 미만: 500원
    - 500,000원 이상: 1,000원
    """
    if price < 2_000:
        return 1
    elif price < 5_000:
        return 5
    elif price < 20_000:
        return 10
    elif price < 50_000:
        return 50
    elif price < 200_000:
        return 100
    elif price < 500_000:
        return 500
    else:
        return 1_000


def align_to_tick(price: float, up: bool = True) -> int:
    """
    호가단위에 맞게 가격 조정

    Args:
        price: 원래 가격
        up: True면 올림, False면 내림

    Returns:
        호가단위에 맞춘 가격
    """
    tick = get_tick_size(price)
    if up:
        return int((price + tick - 1) // tick * tick)
    else:
        return int(price // tick * tick)


def get_valid_order_price(target_price: float, is_buy: bool) -> int:
    """
    유효한 주문 가격 반환

    Args:
        target_price: 목표 가격
        is_buy: 매수 주문 여부

    Returns:
        호가단위에 맞는 유효 가격
    """
    if is_buy:
        # 매수는 올림 (유리하게)
        return align_to_tick(target_price, up=True)
    else:
        # 매도는 내림 (유리하게)
        return align_to_tick(target_price, up=False)


# ============================================================================
# 거래 비용 계산
# ============================================================================

@dataclass(frozen=True)
class TradingCostConfig:
    """거래 비용 설정"""
    # 증권사 수수료 (매수/매도 각각)
    commission_rate: float = 0.00015  # 0.015% (온라인 기준)

    # 거래세 (매도 시에만)
    # KOSPI: 0.18% (2023년 기준, 2024년부터 단계적 인하)
    # KOSDAQ: 0.18%
    # 농특세: 0.15% (KOSPI만)
    kospi_tax_rate: float = 0.0023    # 0.23% (세금 + 농특세)
    kosdaq_tax_rate: float = 0.0023   # 0.23%


def calculate_trading_cost(
    price: float,
    quantity: int,
    is_sell: bool,
    market: MarketType,
    config: TradingCostConfig = TradingCostConfig()
) -> tuple[float, float, float]:
    """
    거래 비용 계산

    Args:
        price: 체결 가격
        quantity: 수량
        is_sell: 매도 여부
        market: 시장 구분
        config: 비용 설정

    Returns:
        (수수료, 세금, 총비용) 튜플
    """
    trade_value = price * quantity

    # 수수료 (매수/매도 모두)
    commission = trade_value * config.commission_rate

    # 세금 (매도 시에만)
    if is_sell:
        if market == MarketType.KOSPI:
            tax = trade_value * config.kospi_tax_rate
        else:
            tax = trade_value * config.kosdaq_tax_rate
    else:
        tax = 0.0

    total_cost = commission + tax

    return commission, tax, total_cost


def calculate_breakeven_pct(
    market: MarketType,
    config: TradingCostConfig = TradingCostConfig()
) -> float:
    """
    손익분기점 수익률 계산

    왕복 거래 비용을 고려한 최소 필요 수익률
    """
    # 매수 수수료 + 매도 수수료 + 매도 세금
    if market == MarketType.KOSPI:
        total_cost_pct = (
            config.commission_rate +  # 매수 수수료
            config.commission_rate +  # 매도 수수료
            config.kospi_tax_rate     # 매도 세금
        )
    else:
        total_cost_pct = (
            config.commission_rate +
            config.commission_rate +
            config.kosdaq_tax_rate
        )

    return total_cost_pct * 100  # 백분율로 반환


# ============================================================================
# 시장 정보 조회
# ============================================================================

@dataclass(frozen=True)
class MarketInfo:
    """시장 정보"""
    date: date
    is_trading_day: bool
    session: MarketSession
    kospi_change_pct: Optional[float] = None
    kosdaq_change_pct: Optional[float] = None
    vkospi: Optional[float] = None
    advance_count: int = 0
    decline_count: int = 0


def get_market_info(check_datetime: Optional[datetime] = None) -> MarketInfo:
    """현재 시장 정보 조회"""
    if check_datetime is None:
        check_datetime = datetime.now(KST)

    check_date = check_datetime.date()

    return MarketInfo(
        date=check_date,
        is_trading_day=is_trading_day(check_date),
        session=get_current_session(check_datetime)
    )
