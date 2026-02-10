"""
한국 주식 기술적 지표 계산 모듈

다양한 기술적 지표를 계산하여 손실 최소화에 활용
"""

from dataclasses import dataclass
from typing import Optional
import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class TechnicalIndicators:
    """기술적 지표 모음"""
    # 이동평균
    sma_5: Optional[float] = None
    sma_10: Optional[float] = None
    sma_20: Optional[float] = None
    sma_60: Optional[float] = None
    sma_120: Optional[float] = None
    sma_200: Optional[float] = None

    ema_5: Optional[float] = None
    ema_10: Optional[float] = None
    ema_20: Optional[float] = None
    ema_60: Optional[float] = None

    # MACD
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None

    # RSI
    rsi_7: Optional[float] = None
    rsi_14: Optional[float] = None

    # Stochastic
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None
    stoch_slow_k: Optional[float] = None
    stoch_slow_d: Optional[float] = None

    # Bollinger Bands
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_width: Optional[float] = None
    bb_pct: Optional[float] = None

    # ATR
    atr_14: Optional[float] = None
    atr_pct: Optional[float] = None

    # Volume
    volume_sma_20: Optional[float] = None
    rvol: Optional[float] = None
    obv: Optional[float] = None

    # VWAP
    vwap: Optional[float] = None

    # ADX
    adx: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None

    # Others
    cci_20: Optional[float] = None
    williams_r: Optional[float] = None
    psar: Optional[float] = None
    psar_trend: Optional[int] = None

    # Ichimoku
    ichimoku_tenkan: Optional[float] = None
    ichimoku_kijun: Optional[float] = None
    ichimoku_senkou_a: Optional[float] = None
    ichimoku_senkou_b: Optional[float] = None


def calculate_sma(closes: NDArray[np.float64], period: int) -> Optional[float]:
    """단순 이동평균 계산"""
    if len(closes) < period:
        return None
    return float(np.mean(closes[-period:]))


def calculate_ema(closes: NDArray[np.float64], period: int) -> Optional[float]:
    """지수 이동평균 계산"""
    if len(closes) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = closes[0]

    for price in closes[1:]:
        ema = (price - ema) * multiplier + ema

    return float(ema)


def calculate_rsi(closes: NDArray[np.float64], period: int = 14) -> Optional[float]:
    """RSI (Relative Strength Index) 계산"""
    if len(closes) < period + 1:
        return None

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return float(rsi)


def calculate_macd(
    closes: NDArray[np.float64],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """MACD 계산 - (macd, signal, histogram)"""
    if len(closes) < slow_period + signal_period:
        return None, None, None

    # EMA 계산용 함수
    def ema_series(data: NDArray[np.float64], period: int) -> NDArray[np.float64]:
        multiplier = 2 / (period + 1)
        ema_arr = np.zeros_like(data)
        ema_arr[0] = data[0]
        for i in range(1, len(data)):
            ema_arr[i] = (data[i] - ema_arr[i-1]) * multiplier + ema_arr[i-1]
        return ema_arr

    fast_ema = ema_series(closes, fast_period)
    slow_ema = ema_series(closes, slow_period)
    macd_line = fast_ema - slow_ema

    # Signal line
    signal_line = ema_series(macd_line[slow_period-1:], signal_period)

    macd = float(macd_line[-1])
    signal = float(signal_line[-1])
    histogram = macd - signal

    return macd, signal, histogram


def calculate_stochastic(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    k_period: int = 14,
    d_period: int = 3,
    smooth_k: int = 3
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Stochastic 계산 - (fast_k, fast_d, slow_k, slow_d)"""
    if len(closes) < k_period + d_period + smooth_k:
        return None, None, None, None

    # Fast %K 계산
    fast_k_values = []
    for i in range(k_period - 1, len(closes)):
        highest_high = np.max(highs[i - k_period + 1:i + 1])
        lowest_low = np.min(lows[i - k_period + 1:i + 1])

        if highest_high == lowest_low:
            fast_k_values.append(50.0)
        else:
            fast_k = ((closes[i] - lowest_low) / (highest_high - lowest_low)) * 100
            fast_k_values.append(fast_k)

    fast_k_arr = np.array(fast_k_values)

    # Fast %D (Fast %K의 SMA)
    fast_d = float(np.mean(fast_k_arr[-d_period:]))

    # Slow %K (Fast %K의 SMA)
    slow_k = float(np.mean(fast_k_arr[-smooth_k:]))

    # Slow %D (Slow %K의 SMA)
    slow_k_values = []
    for i in range(smooth_k - 1, len(fast_k_arr)):
        slow_k_values.append(np.mean(fast_k_arr[i - smooth_k + 1:i + 1]))
    slow_d = float(np.mean(slow_k_values[-d_period:])) if len(slow_k_values) >= d_period else None

    return float(fast_k_arr[-1]), fast_d, slow_k, slow_d


def calculate_bollinger_bands(
    closes: NDArray[np.float64],
    period: int = 20,
    num_std: float = 2.0
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    """볼린저 밴드 계산 - (upper, middle, lower, width, pct_b)"""
    if len(closes) < period:
        return None, None, None, None, None

    middle = float(np.mean(closes[-period:]))
    std = float(np.std(closes[-period:], ddof=1))

    upper = middle + (std * num_std)
    lower = middle - (std * num_std)

    # 밴드 폭 (%)
    width = ((upper - lower) / middle) * 100 if middle != 0 else 0

    # %B (현재 가격의 밴드 내 위치)
    current_price = float(closes[-1])
    if upper != lower:
        pct_b = (current_price - lower) / (upper - lower)
    else:
        pct_b = 0.5

    return upper, middle, lower, width, pct_b


def calculate_atr(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 14
) -> tuple[Optional[float], Optional[float]]:
    """ATR 계산 - (atr, atr_pct)"""
    if len(closes) < period + 1:
        return None, None

    true_ranges = []
    for i in range(1, len(closes)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i-1])
        low_close = abs(lows[i] - closes[i-1])
        true_range = max(high_low, high_close, low_close)
        true_ranges.append(true_range)

    atr = float(np.mean(true_ranges[-period:]))
    atr_pct = (atr / closes[-1]) * 100 if closes[-1] != 0 else 0

    return atr, atr_pct


def calculate_adx(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 14
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """ADX 계산 - (adx, plus_di, minus_di)"""
    if len(closes) < period * 2:
        return None, None, None

    # +DM, -DM 계산
    plus_dm = []
    minus_dm = []
    tr_list = []

    for i in range(1, len(closes)):
        high_diff = highs[i] - highs[i-1]
        low_diff = lows[i-1] - lows[i]

        if high_diff > low_diff and high_diff > 0:
            plus_dm.append(high_diff)
        else:
            plus_dm.append(0)

        if low_diff > high_diff and low_diff > 0:
            minus_dm.append(low_diff)
        else:
            minus_dm.append(0)

        # True Range
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr_list.append(tr)

    # Smoothed 계산 (Wilder's smoothing)
    def smooth(data: list, period: int) -> list:
        smoothed = [sum(data[:period])]
        for i in range(period, len(data)):
            smoothed.append(smoothed[-1] - (smoothed[-1] / period) + data[i])
        return smoothed

    smoothed_tr = smooth(tr_list, period)
    smoothed_plus_dm = smooth(plus_dm, period)
    smoothed_minus_dm = smooth(minus_dm, period)

    # +DI, -DI 계산
    plus_di_list = []
    minus_di_list = []

    for i in range(len(smoothed_tr)):
        if smoothed_tr[i] != 0:
            plus_di_list.append((smoothed_plus_dm[i] / smoothed_tr[i]) * 100)
            minus_di_list.append((smoothed_minus_dm[i] / smoothed_tr[i]) * 100)
        else:
            plus_di_list.append(0)
            minus_di_list.append(0)

    # DX, ADX 계산
    dx_list = []
    for i in range(len(plus_di_list)):
        di_sum = plus_di_list[i] + minus_di_list[i]
        if di_sum != 0:
            dx = (abs(plus_di_list[i] - minus_di_list[i]) / di_sum) * 100
            dx_list.append(dx)
        else:
            dx_list.append(0)

    if len(dx_list) < period:
        return None, None, None

    adx = float(np.mean(dx_list[-period:]))

    return adx, float(plus_di_list[-1]), float(minus_di_list[-1])


def calculate_cci(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 20
) -> Optional[float]:
    """CCI (Commodity Channel Index) 계산"""
    if len(closes) < period:
        return None

    typical_prices = (highs + lows + closes) / 3
    tp_sma = np.mean(typical_prices[-period:])

    # Mean Deviation
    mean_deviation = np.mean(np.abs(typical_prices[-period:] - tp_sma))

    if mean_deviation == 0:
        return 0.0

    cci = (typical_prices[-1] - tp_sma) / (0.015 * mean_deviation)
    return float(cci)


def calculate_williams_r(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 14
) -> Optional[float]:
    """Williams %R 계산"""
    if len(closes) < period:
        return None

    highest_high = np.max(highs[-period:])
    lowest_low = np.min(lows[-period:])

    if highest_high == lowest_low:
        return -50.0

    williams_r = ((highest_high - closes[-1]) / (highest_high - lowest_low)) * -100
    return float(williams_r)


def calculate_obv(
    closes: NDArray[np.float64],
    volumes: NDArray[np.float64]
) -> Optional[float]:
    """OBV (On Balance Volume) 계산"""
    if len(closes) < 2:
        return None

    obv = 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv += volumes[i]
        elif closes[i] < closes[i-1]:
            obv -= volumes[i]

    return obv


def calculate_vwap(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    volumes: NDArray[np.float64]
) -> Optional[float]:
    """VWAP (Volume Weighted Average Price) 계산"""
    if len(closes) < 1 or np.sum(volumes) == 0:
        return None

    typical_prices = (highs + lows + closes) / 3
    vwap = np.sum(typical_prices * volumes) / np.sum(volumes)
    return float(vwap)


def calculate_rvol(
    volumes: NDArray[np.float64],
    period: int = 20
) -> Optional[float]:
    """RVOL (Relative Volume) 계산"""
    if len(volumes) < period:
        return None

    avg_volume = np.mean(volumes[-period-1:-1])  # 이전 period일 평균
    if avg_volume == 0:
        return 0.0

    current_volume = volumes[-1]
    return float(current_volume / avg_volume)


def calculate_ichimoku(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """일목균형표 계산 - (전환선, 기준선, 선행스팬A, 선행스팬B)"""
    if len(highs) < senkou_b_period:
        return None, None, None, None

    # 전환선 (Tenkan-sen)
    tenkan = (np.max(highs[-tenkan_period:]) + np.min(lows[-tenkan_period:])) / 2

    # 기준선 (Kijun-sen)
    kijun = (np.max(highs[-kijun_period:]) + np.min(lows[-kijun_period:])) / 2

    # 선행스팬 A (Senkou Span A)
    senkou_a = (tenkan + kijun) / 2

    # 선행스팬 B (Senkou Span B)
    senkou_b = (np.max(highs[-senkou_b_period:]) + np.min(lows[-senkou_b_period:])) / 2

    return float(tenkan), float(kijun), float(senkou_a), float(senkou_b)


def calculate_parabolic_sar(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    af_start: float = 0.02,
    af_increment: float = 0.02,
    af_max: float = 0.2
) -> tuple[Optional[float], Optional[int]]:
    """Parabolic SAR 계산 - (sar, trend: 1=up, -1=down)"""
    if len(highs) < 5:
        return None, None

    n = len(highs)

    # 초기화
    trend = 1  # 1: 상승, -1: 하락
    sar = lows[0]
    ep = highs[0]  # Extreme Point
    af = af_start

    for i in range(1, n):
        prev_sar = sar

        # SAR 계산
        sar = prev_sar + af * (ep - prev_sar)

        # 상승 추세
        if trend == 1:
            sar = min(sar, lows[i-1], lows[i-2] if i >= 2 else lows[i-1])

            if lows[i] < sar:
                # 추세 전환
                trend = -1
                sar = ep
                ep = lows[i]
                af = af_start
            else:
                if highs[i] > ep:
                    ep = highs[i]
                    af = min(af + af_increment, af_max)

        # 하락 추세
        else:
            sar = max(sar, highs[i-1], highs[i-2] if i >= 2 else highs[i-1])

            if highs[i] > sar:
                # 추세 전환
                trend = 1
                sar = ep
                ep = highs[i]
                af = af_start
            else:
                if lows[i] < ep:
                    ep = lows[i]
                    af = min(af + af_increment, af_max)

    return float(sar), trend


def calculate_all_indicators(
    opens: NDArray[np.float64],
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    volumes: NDArray[np.float64]
) -> TechnicalIndicators:
    """모든 기술적 지표 계산"""

    # SMA
    sma_5 = calculate_sma(closes, 5)
    sma_10 = calculate_sma(closes, 10)
    sma_20 = calculate_sma(closes, 20)
    sma_60 = calculate_sma(closes, 60)
    sma_120 = calculate_sma(closes, 120)
    sma_200 = calculate_sma(closes, 200)

    # EMA
    ema_5 = calculate_ema(closes, 5)
    ema_10 = calculate_ema(closes, 10)
    ema_20 = calculate_ema(closes, 20)
    ema_60 = calculate_ema(closes, 60)

    # MACD
    macd, macd_signal, macd_histogram = calculate_macd(closes)

    # RSI
    rsi_7 = calculate_rsi(closes, 7)
    rsi_14 = calculate_rsi(closes, 14)

    # Stochastic
    stoch_k, stoch_d, stoch_slow_k, stoch_slow_d = calculate_stochastic(highs, lows, closes)

    # Bollinger Bands
    bb_upper, bb_middle, bb_lower, bb_width, bb_pct = calculate_bollinger_bands(closes)

    # ATR
    atr_14, atr_pct = calculate_atr(highs, lows, closes)

    # ADX
    adx, plus_di, minus_di = calculate_adx(highs, lows, closes)

    # Volume indicators
    volume_sma_20 = calculate_sma(volumes, 20)
    rvol = calculate_rvol(volumes)
    obv = calculate_obv(closes, volumes)
    vwap = calculate_vwap(highs, lows, closes, volumes)

    # Others
    cci_20 = calculate_cci(highs, lows, closes)
    williams_r = calculate_williams_r(highs, lows, closes)

    # Ichimoku
    tenkan, kijun, senkou_a, senkou_b = calculate_ichimoku(highs, lows)

    # Parabolic SAR
    psar, psar_trend = calculate_parabolic_sar(highs, lows)

    return TechnicalIndicators(
        sma_5=sma_5,
        sma_10=sma_10,
        sma_20=sma_20,
        sma_60=sma_60,
        sma_120=sma_120,
        sma_200=sma_200,
        ema_5=ema_5,
        ema_10=ema_10,
        ema_20=ema_20,
        ema_60=ema_60,
        macd=macd,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
        rsi_7=rsi_7,
        rsi_14=rsi_14,
        stoch_k=stoch_k,
        stoch_d=stoch_d,
        stoch_slow_k=stoch_slow_k,
        stoch_slow_d=stoch_slow_d,
        bb_upper=bb_upper,
        bb_middle=bb_middle,
        bb_lower=bb_lower,
        bb_width=bb_width,
        bb_pct=bb_pct,
        atr_14=atr_14,
        atr_pct=atr_pct,
        volume_sma_20=volume_sma_20,
        rvol=rvol,
        obv=obv,
        vwap=vwap,
        adx=adx,
        plus_di=plus_di,
        minus_di=minus_di,
        cci_20=cci_20,
        williams_r=williams_r,
        psar=psar,
        psar_trend=psar_trend,
        ichimoku_tenkan=tenkan,
        ichimoku_kijun=kijun,
        ichimoku_senkou_a=senkou_a,
        ichimoku_senkou_b=senkou_b,
    )
