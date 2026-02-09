"""기술적 지표 계산 모듈

RSI, MACD, Bollinger Band 등 기술적 분석 지표를 계산합니다.
모든 전략에서 공통으로 사용됩니다.
"""

from typing import Optional


def calculate_ema(closes: list[float], period: int) -> float:
    """EMA (지수이동평균) 계산

    Args:
        closes: 종가 리스트 (시간순)
        period: EMA 기간

    Returns:
        EMA 값
    """
    if len(closes) < period:
        return closes[-1] if closes else 0.0

    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period

    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def calculate_sma(closes: list[float], period: int) -> float:
    """SMA (단순이동평균) 계산

    Args:
        closes: 종가 리스트 (시간순)
        period: SMA 기간

    Returns:
        SMA 값
    """
    if len(closes) < period:
        return sum(closes) / len(closes) if closes else 0.0

    return sum(closes[-period:]) / period


def calculate_rsi(closes: list[float], period: int = 14) -> float:
    """RSI (상대강도지수) 계산

    Args:
        closes: 종가 리스트 (시간순)
        period: RSI 기간 (기본 14)

    Returns:
        RSI 값 (0-100)
    """
    if len(closes) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    if len(gains) < period:
        return 50.0

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_macd(
    closes: list[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[float, float, float]:
    """MACD 계산

    Args:
        closes: 종가 리스트 (시간순)
        fast_period: 빠른 EMA 기간 (기본 12)
        slow_period: 느린 EMA 기간 (기본 26)
        signal_period: 시그널 EMA 기간 (기본 9)

    Returns:
        (MACD, Signal, Histogram) 튜플
    """
    if len(closes) < slow_period:
        return 0.0, 0.0, 0.0

    ema_fast = calculate_ema(closes, fast_period)
    ema_slow = calculate_ema(closes, slow_period)

    macd_line = ema_fast - ema_slow

    macd_values = []
    for i in range(slow_period - 1, len(closes)):
        fast = calculate_ema(closes[:i + 1], fast_period)
        slow = calculate_ema(closes[:i + 1], slow_period)
        macd_values.append(fast - slow)

    if len(macd_values) >= signal_period:
        signal_line = calculate_ema(macd_values, signal_period)
    else:
        signal_line = macd_line

    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


def calculate_bollinger_bands(
    closes: list[float],
    period: int = 20,
    std_mult: float = 2.0,
) -> tuple[float, float, float]:
    """볼린저 밴드 계산

    Args:
        closes: 종가 리스트 (시간순)
        period: SMA 기간 (기본 20)
        std_mult: 표준편차 배수 (기본 2.0)

    Returns:
        (Upper Band, Middle Band, Lower Band) 튜플
    """
    if len(closes) < period:
        if closes:
            middle = sum(closes) / len(closes)
            return middle, middle, middle
        return 0.0, 0.0, 0.0

    recent = closes[-period:]
    middle = sum(recent) / period

    variance = sum((c - middle) ** 2 for c in recent) / period
    std = variance ** 0.5

    upper = middle + std_mult * std
    lower = middle - std_mult * std

    return upper, middle, lower


def calculate_atr(candles: list, period: int = 14) -> float:
    """ATR (Average True Range) 계산

    Args:
        candles: 캔들 객체 리스트 (high, low, close 속성 필요)
        period: ATR 기간 (기본 14)

    Returns:
        ATR 값
    """
    if len(candles) < 2:
        return 0.0

    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    return sum(true_ranges[-period:]) / period


def get_hourly_trend(btc_candles_1h: list, ema_period: int = 20) -> str:
    """1시간봉 기준 BTC 추세 판단

    Args:
        btc_candles_1h: BTC 1시간봉 캔들 리스트
        ema_period: EMA 기간 (기본 20)

    Returns:
        "BULLISH" | "BEARISH" | "SIDEWAYS"
    """
    if len(btc_candles_1h) < ema_period:
        return "SIDEWAYS"

    closes = [c.close for c in btc_candles_1h]
    current = closes[-1]
    ema = calculate_ema(closes, ema_period)

    pct_diff = (current - ema) / ema if ema > 0 else 0

    if pct_diff > 0.01:
        return "BULLISH"
    elif pct_diff < -0.01:
        return "BEARISH"

    return "SIDEWAYS"


def calculate_stochastic(
    candles: list,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[float, float]:
    """스토캐스틱 계산

    Args:
        candles: 캔들 객체 리스트
        k_period: %K 기간 (기본 14)
        d_period: %D 기간 (기본 3)

    Returns:
        (%K, %D) 튜플
    """
    if len(candles) < k_period:
        return 50.0, 50.0

    recent = candles[-k_period:]
    highest_high = max(c.high for c in recent)
    lowest_low = min(c.low for c in recent)
    current_close = candles[-1].close

    if highest_high == lowest_low:
        k = 50.0
    else:
        k = ((current_close - lowest_low) / (highest_high - lowest_low)) * 100

    k_values = []
    for i in range(k_period - 1, len(candles)):
        period_candles = candles[i - k_period + 1:i + 1]
        hh = max(c.high for c in period_candles)
        ll = min(c.low for c in period_candles)
        close = candles[i].close
        if hh != ll:
            k_values.append(((close - ll) / (hh - ll)) * 100)
        else:
            k_values.append(50.0)

    if len(k_values) >= d_period:
        d = sum(k_values[-d_period:]) / d_period
    else:
        d = k

    return k, d


def is_oversold(closes: list[float], rsi_threshold: float = 30.0) -> bool:
    """과매도 상태 확인

    Args:
        closes: 종가 리스트
        rsi_threshold: RSI 임계값 (기본 30)

    Returns:
        과매도 여부
    """
    rsi = calculate_rsi(closes)
    return rsi <= rsi_threshold


def is_overbought(closes: list[float], rsi_threshold: float = 70.0) -> bool:
    """과매수 상태 확인

    Args:
        closes: 종가 리스트
        rsi_threshold: RSI 임계값 (기본 70)

    Returns:
        과매수 여부
    """
    rsi = calculate_rsi(closes)
    return rsi >= rsi_threshold


def calculate_bb_position(price: float, upper: float, middle: float, lower: float) -> float:
    """볼린저 밴드 내 가격 위치 계산

    Args:
        price: 현재 가격
        upper: 상단 밴드
        middle: 중간 밴드
        lower: 하단 밴드

    Returns:
        -1 (하단) ~ 1 (상단) 사이 값
    """
    if upper == lower:
        return 0.0

    bb_width = upper - lower
    position = (price - middle) / (bb_width / 2)

    return max(-1.0, min(1.0, position))
