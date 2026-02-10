"""DB 분봉 데이터 역분석: 실제 수익 패턴 탐색

각 5분봉 캔들에서:
1. 향후 18봉(90분) 내에 TP(+2%)가 SL(-1.5%)보다 먼저 도달하는지 확인
2. 성공 케이스의 진입 시점 지표(RSI, RVOL, MA위치, 캔들패턴 등) 분석
3. 높은 승률을 보이는 지표 조합 탐색
"""

import asyncio
import math
from collections import defaultdict
from datetime import datetime

from src.models.database import async_session
from sqlalchemy import text


# 분석 파라미터
TP_PCT = 0.020       # +2.0% 목표
SL_PCT = -0.015      # -1.5% 손절
FORWARD_CANDLES = 18  # 90분 (5분 × 18)
COMMISSION = 0.001    # 왕복 수수료 0.1%


async def load_candles_for_symbol(db, symbol: str) -> list[dict]:
    """심볼의 5분봉 데이터 로드"""
    r = await db.execute(
        text("""
            SELECT open_time, open, high, low, close, volume, quote_volume
            FROM backtest_candles
            WHERE symbol = :s AND interval = '5m'
            ORDER BY open_time ASC
        """),
        {"s": symbol},
    )
    rows = r.fetchall()
    return [
        {
            "time": row[0],
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "quote_volume": float(row[6]) if row[6] else 0,
        }
        for row in rows
    ]


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calc_atr(candles: list[dict], period: int) -> float | None:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        h = candles[i]["high"]
        lo = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr = max(h - lo, abs(h - pc), abs(lo - pc))
        trs.append(tr)
    return sum(trs) / len(trs)


def analyze_candle_features(candles: list[dict], idx: int) -> dict | None:
    """idx 위치의 캔들에 대한 기술적 지표 계산"""
    if idx < 200:
        return None

    window = candles[max(0, idx - 250) : idx + 1]
    closes = [c["close"] for c in window]
    volumes = [c["volume"] for c in window]
    current = candles[idx]

    price = current["close"]
    if price <= 0:
        return None

    # 이동평균
    sma20 = calc_sma(closes, 20)
    ema50 = calc_ema(closes, 50)
    sma200 = calc_sma(closes, 200)
    if not sma20 or not ema50 or not sma200 or sma200 <= 0:
        return None

    # RSI
    rsi = calc_rsi(closes, 14)
    if rsi is None:
        return None

    # RVOL (현재 거래량 / 20봉 평균)
    avg_vol = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else None
    rvol = current["volume"] / avg_vol if avg_vol and avg_vol > 0 else None
    if rvol is None:
        return None

    # ATR%
    atr = calc_atr(window, 14)
    atr_pct = (atr / price * 100) if atr and price > 0 else None
    if atr_pct is None:
        return None

    # 최근 6봉 변화율
    if idx >= 6:
        price_6_ago = candles[idx - 6]["close"]
        change_6 = (price - price_6_ago) / price_6_ago if price_6_ago > 0 else 0
    else:
        change_6 = 0

    # 최근 12봉 변화율 (1시간)
    if idx >= 12:
        price_12_ago = candles[idx - 12]["close"]
        change_12 = (price - price_12_ago) / price_12_ago if price_12_ago > 0 else 0
    else:
        change_12 = 0

    # 캔들 패턴
    body = current["close"] - current["open"]
    candle_range = current["high"] - current["low"]
    lower_wick = min(current["open"], current["close"]) - current["low"]
    upper_wick = current["high"] - max(current["open"], current["close"])
    is_bullish = body > 0
    body_ratio = abs(body) / candle_range if candle_range > 0 else 0
    lower_wick_ratio = lower_wick / abs(body) if abs(body) > 0 else 0

    # 24h(288봉) 최저가 대비 위치
    lookback_288 = min(288, idx)
    if lookback_288 > 0:
        lows_24h = [candles[idx - j]["low"] for j in range(lookback_288)]
        highs_24h = [candles[idx - j]["high"] for j in range(lookback_288)]
        low_24h = min(lows_24h)
        high_24h = max(highs_24h)
        range_24h = high_24h - low_24h
        pos_in_24h = (price - low_24h) / range_24h if range_24h > 0 else 0.5
        dist_from_low = (price - low_24h) / low_24h if low_24h > 0 else 0
    else:
        pos_in_24h = 0.5
        dist_from_low = 0

    # 직전 3봉 음봉 수
    bearish_3 = 0
    if idx >= 3:
        for j in range(1, 4):
            if candles[idx - j]["close"] < candles[idx - j]["open"]:
                bearish_3 += 1

    # MA 상대 위치
    price_vs_sma20 = (price - sma20) / sma20
    price_vs_ema50 = (price - ema50) / ema50
    price_vs_sma200 = (price - sma200) / sma200

    # BB 위치 (간단)
    if len(closes) >= 20:
        mean20 = sum(closes[-20:]) / 20
        std20 = math.sqrt(sum((x - mean20) ** 2 for x in closes[-20:]) / 20)
    else:
        std20 = 0
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    bb_width = (bb_upper - bb_lower) / sma20 if sma20 > 0 else 0
    bb_position = (price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5

    return {
        "rsi": rsi,
        "rvol": rvol,
        "atr_pct": atr_pct,
        "change_6": change_6,     # 30분 변화율
        "change_12": change_12,   # 1시간 변화율
        "is_bullish": is_bullish,
        "body_ratio": body_ratio,
        "lower_wick_ratio": lower_wick_ratio,
        "pos_in_24h": pos_in_24h,       # 24시간 레인지 내 위치 (0=저점, 1=고점)
        "dist_from_low": dist_from_low,  # 24h 저점 대비 거리
        "bearish_3": bearish_3,           # 직전 3봉 음봉 수
        "price_vs_sma20": price_vs_sma20,
        "price_vs_ema50": price_vs_ema50,
        "price_vs_sma200": price_vs_sma200,
        "bb_position": bb_position,
        "bb_width": bb_width,
    }


def check_forward_result(candles: list[dict], idx: int) -> str:
    """idx 이후 FORWARD_CANDLES 봉 동안 TP/SL 중 먼저 도달하는 것 확인

    Returns: 'win', 'loss', 'timeout'
    """
    entry_price = candles[idx]["close"]
    if entry_price <= 0:
        return "skip"

    tp_price = entry_price * (1 + TP_PCT)
    sl_price = entry_price * (1 + SL_PCT)

    end_idx = min(idx + FORWARD_CANDLES + 1, len(candles))

    for i in range(idx + 1, end_idx):
        c = candles[i]
        # SL 먼저 체크 (low가 SL 이하)
        if c["low"] <= sl_price:
            return "loss"
        # TP 체크 (high가 TP 이상)
        if c["high"] >= tp_price:
            return "win"

    return "timeout"


def calc_forward_max_return(candles: list[dict], idx: int) -> float:
    """idx 이후 FORWARD_CANDLES 봉 동안 최대 수익률"""
    entry_price = candles[idx]["close"]
    if entry_price <= 0:
        return 0

    max_price = entry_price
    end_idx = min(idx + FORWARD_CANDLES + 1, len(candles))

    for i in range(idx + 1, end_idx):
        max_price = max(max_price, candles[i]["high"])

    return (max_price - entry_price) / entry_price


async def analyze_symbol(db, symbol: str) -> dict:
    """단일 심볼 분석"""
    candles = await load_candles_for_symbol(db, symbol)
    if len(candles) < 300:
        return {"symbol": symbol, "total": 0, "wins": 0, "losses": 0, "features": []}

    results = {"symbol": symbol, "total": 0, "wins": 0, "losses": 0, "timeouts": 0, "features": []}

    for idx in range(200, len(candles) - FORWARD_CANDLES):
        features = analyze_candle_features(candles, idx)
        if features is None:
            continue

        outcome = check_forward_result(candles, idx)
        if outcome == "skip":
            continue

        results["total"] += 1
        if outcome == "win":
            results["wins"] += 1
        elif outcome == "loss":
            results["losses"] += 1
        else:
            results["timeouts"] += 1

        features["outcome"] = outcome
        features["symbol"] = symbol
        results["features"].append(features)

    return results


def bucket(value, thresholds: list) -> str:
    """값을 구간으로 분류"""
    for t in thresholds:
        if value <= t:
            return f"<={t}"
    return f">{thresholds[-1]}"


def analyze_patterns(all_features: list[dict]):
    """패턴별 승률 분석"""
    print(f"\n{'=' * 120}")
    print(f"  전체 데이터: {len(all_features):,}건")
    wins = sum(1 for f in all_features if f["outcome"] == "win")
    losses = sum(1 for f in all_features if f["outcome"] == "loss")
    timeouts = sum(1 for f in all_features if f["outcome"] == "timeout")
    print(f"  승: {wins:,} | 패: {losses:,} | 타임아웃: {timeouts:,} | 기본 승률: {wins / len(all_features) * 100:.1f}%")
    print(f"  (TP +{TP_PCT*100:.1f}% | SL {SL_PCT*100:.1f}% | Forward {FORWARD_CANDLES}봉={FORWARD_CANDLES*5}분)")
    print(f"{'=' * 120}")

    # === 1. 단일 지표별 승률 ===
    print(f"\n{'─' * 80}")
    print("  [1] 단일 지표별 승률")
    print(f"{'─' * 80}")

    indicator_configs = {
        "RSI": ("rsi", [20, 30, 40, 50, 60, 70, 80]),
        "RVOL": ("rvol", [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]),
        "30분 변화율%": ("change_6", [-0.03, -0.02, -0.01, -0.005, 0, 0.005, 0.01, 0.02]),
        "1시간 변화율%": ("change_12", [-0.05, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03]),
        "24h 레인지 위치": ("pos_in_24h", [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]),
        "직전3봉 음봉수": ("bearish_3", [0, 1, 2]),
        "price vs SMA20%": ("price_vs_sma20", [-0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03]),
        "BB 위치": ("bb_position", [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0]),
        "ATR%": ("atr_pct", [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0]),
        "양봉여부": ("is_bullish", [False]),
        "몸통비율": ("body_ratio", [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]),
        "하위꼬리비율": ("lower_wick_ratio", [0.5, 1.0, 1.5, 2.0, 3.0]),
    }

    for label, (key, thresholds) in indicator_configs.items():
        print(f"\n  [{label}]")
        if key == "is_bullish":
            for val in [True, False]:
                subset = [f for f in all_features if f[key] == val]
                if not subset:
                    continue
                w = sum(1 for f in subset if f["outcome"] == "win")
                l = sum(1 for f in subset if f["outcome"] == "loss")
                wr = w / len(subset) * 100
                print(f"    {'양봉' if val else '음봉':>12s}: {len(subset):>7,}건 | 승률 {wr:5.1f}% | W:{w:,} L:{l:,}")
        else:
            buckets = defaultdict(list)
            for f in all_features:
                b = bucket(f[key], thresholds)
                buckets[b].append(f)

            for b_key in sorted(buckets.keys(), key=lambda x: float(x.replace("<=", "").replace(">", "")) if x.replace("<=", "").replace(">", "").replace("-", "").replace(".", "").isdigit() else 0):
                subset = buckets[b_key]
                if len(subset) < 50:
                    continue
                w = sum(1 for f in subset if f["outcome"] == "win")
                l = sum(1 for f in subset if f["outcome"] == "loss")
                wr = w / len(subset) * 100
                tag = " ★" if wr >= 50 else ""
                print(f"    {b_key:>12s}: {len(subset):>7,}건 | 승률 {wr:5.1f}% | W:{w:,} L:{l:,}{tag}")

    # === 2. 복합 조건 승률 (유망 패턴 탐색) ===
    print(f"\n{'─' * 80}")
    print("  [2] 복합 조건 패턴 탐색 (승률 55%+ & 50건+)")
    print(f"{'─' * 80}")

    conditions = [
        # (이름, 필터 함수)
        ("RSI<30 + RVOL>2", lambda f: f["rsi"] < 30 and f["rvol"] > 2),
        ("RSI<30 + RVOL>3", lambda f: f["rsi"] < 30 and f["rvol"] > 3),
        ("RSI<25 + RVOL>2", lambda f: f["rsi"] < 25 and f["rvol"] > 2),
        ("RSI<30 + 30분 -2%이상 하락", lambda f: f["rsi"] < 30 and f["change_6"] < -0.02),
        ("RSI<30 + 30분 -3%이상 하락", lambda f: f["rsi"] < 30 and f["change_6"] < -0.03),
        ("RSI<40 + RVOL>3 + 양봉", lambda f: f["rsi"] < 40 and f["rvol"] > 3 and f["is_bullish"]),
        ("RSI<30 + 양봉 + 꼬리>1.5x", lambda f: f["rsi"] < 30 and f["is_bullish"] and f["lower_wick_ratio"] > 1.5),
        ("RSI<30 + BB<0.1", lambda f: f["rsi"] < 30 and f["bb_position"] < 0.1),
        ("24h하위10% + RVOL>2", lambda f: f["pos_in_24h"] < 0.10 and f["rvol"] > 2),
        ("24h하위10% + RVOL>3", lambda f: f["pos_in_24h"] < 0.10 and f["rvol"] > 3),
        ("24h하위10% + 양봉", lambda f: f["pos_in_24h"] < 0.10 and f["is_bullish"]),
        ("24h하위10% + RSI<30", lambda f: f["pos_in_24h"] < 0.10 and f["rsi"] < 30),
        ("24h하위5% + RVOL>2 + 양봉", lambda f: f["pos_in_24h"] < 0.05 and f["rvol"] > 2 and f["is_bullish"]),
        ("30분-3%하락 + RVOL>3", lambda f: f["change_6"] < -0.03 and f["rvol"] > 3),
        ("30분-3%하락 + RVOL>3 + 양봉", lambda f: f["change_6"] < -0.03 and f["rvol"] > 3 and f["is_bullish"]),
        ("30분-2%하락 + RVOL>2 + 양봉", lambda f: f["change_6"] < -0.02 and f["rvol"] > 2 and f["is_bullish"]),
        ("30분-2%하락 + RSI<30 + 양봉", lambda f: f["change_6"] < -0.02 and f["rsi"] < 30 and f["is_bullish"]),
        ("1시간-3%하락 + RVOL>2", lambda f: f["change_12"] < -0.03 and f["rvol"] > 2),
        ("1시간-5%하락 + RVOL>3", lambda f: f["change_12"] < -0.05 and f["rvol"] > 3),
        ("1시간-3%하락 + RSI<30 + 양봉", lambda f: f["change_12"] < -0.03 and f["rsi"] < 30 and f["is_bullish"]),
        ("SMA20 아래 + RVOL>3 + 양봉", lambda f: f["price_vs_sma20"] < 0 and f["rvol"] > 3 and f["is_bullish"]),
        ("SMA20 -2%아래 + RVOL>2 + 양봉", lambda f: f["price_vs_sma20"] < -0.02 and f["rvol"] > 2 and f["is_bullish"]),
        ("SMA200 위 + RSI<30 + 양봉", lambda f: f["price_vs_sma200"] > 0 and f["rsi"] < 30 and f["is_bullish"]),
        ("3봉연속음봉 + RSI<30", lambda f: f["bearish_3"] == 3 and f["rsi"] < 30),
        ("3봉연속음봉 + RSI<30 + 양봉", lambda f: f["bearish_3"] == 3 and f["rsi"] < 30 and f["is_bullish"]),
        ("3봉연속음봉 + RVOL>3 + 양봉", lambda f: f["bearish_3"] == 3 and f["rvol"] > 3 and f["is_bullish"]),
        ("3봉연속음봉 + 30분-2%하락 + 양봉", lambda f: f["bearish_3"] == 3 and f["change_6"] < -0.02 and f["is_bullish"]),
        ("BB<0.1 + RVOL>2 + 양봉", lambda f: f["bb_position"] < 0.1 and f["rvol"] > 2 and f["is_bullish"]),
        ("BB<0.05 + RVOL>2", lambda f: f["bb_position"] < 0.05 and f["rvol"] > 2),
        ("해머(꼬리3x) + RSI<30", lambda f: f["lower_wick_ratio"] > 3 and f["rsi"] < 30),
        ("해머(꼬리3x) + RSI<30 + RVOL>2", lambda f: f["lower_wick_ratio"] > 3 and f["rsi"] < 30 and f["rvol"] > 2),
        # 복합 4~5조건
        ("RSI<30 + RVOL>2 + 양봉 + 3봉음봉", lambda f: f["rsi"] < 30 and f["rvol"] > 2 and f["is_bullish"] and f["bearish_3"] >= 2),
        ("24h하위10% + RSI<30 + RVOL>2 + 양봉", lambda f: f["pos_in_24h"] < 0.10 and f["rsi"] < 30 and f["rvol"] > 2 and f["is_bullish"]),
        ("30분-2%하락 + RVOL>2 + 양봉 + RSI<40", lambda f: f["change_6"] < -0.02 and f["rvol"] > 2 and f["is_bullish"] and f["rsi"] < 40),
        ("BB<0.1 + RSI<30 + 양봉 + RVOL>2", lambda f: f["bb_position"] < 0.1 and f["rsi"] < 30 and f["is_bullish"] and f["rvol"] > 2),
        ("SMA200위 + 24h하위20% + RSI<40 + 양봉", lambda f: f["price_vs_sma200"] > 0 and f["pos_in_24h"] < 0.2 and f["rsi"] < 40 and f["is_bullish"]),
        ("ATR>1.5% + RSI<30 + RVOL>2 + 양봉", lambda f: f["atr_pct"] > 1.5 and f["rsi"] < 30 and f["rvol"] > 2 and f["is_bullish"]),
    ]

    promising = []
    for name, filter_fn in conditions:
        subset = [f for f in all_features if filter_fn(f)]
        if len(subset) < 30:
            continue
        w = sum(1 for f in subset if f["outcome"] == "win")
        l = sum(1 for f in subset if f["outcome"] == "loss")
        t = sum(1 for f in subset if f["outcome"] == "timeout")
        wr = w / len(subset) * 100

        # 수수료 감안 기대수익
        expected = (w * TP_PCT + l * SL_PCT) / len(subset) - COMMISSION
        expected_pct = expected * 100

        tag = ""
        if wr >= 55 and expected_pct > 0:
            tag = " ★★★"
            promising.append((name, len(subset), wr, expected_pct))
        elif wr >= 50 and expected_pct > 0:
            tag = " ★★"
        elif wr >= 50:
            tag = " ★"

        print(
            f"  {name:45s} | {len(subset):>5,}건 | 승률 {wr:5.1f}% | "
            f"W:{w:>4,} L:{l:>4,} T:{t:>4,} | "
            f"기대수익 {expected_pct:+.3f}%{tag}"
        )

    # === 3. 유망 패턴 요약 ===
    if promising:
        print(f"\n{'=' * 80}")
        print("  [3] ★★★ 유망 패턴 요약 (승률 55%+ & 기대수익 양수)")
        print(f"{'=' * 80}")
        promising.sort(key=lambda x: -x[2])
        for name, count, wr, exp in promising:
            print(f"  {name:45s} | {count:>5,}건 | 승률 {wr:5.1f}% | 기대수익 {exp:+.3f}%/trade")
    else:
        print(f"\n  ★★★ 유망 패턴 없음 (승률 55%+ & 기대수익 양수인 패턴이 없음)")

    # === 4. TP/SL 비율별 분석 ===
    print(f"\n{'─' * 80}")
    print("  [4] TP/SL 비율별 전체 승률 (최적 비율 탐색)")
    print(f"{'─' * 80}")

    # 이건 forward_result를 다시 계산해야 하므로 여기서는 기존 데이터 기준 통계만
    print(f"  현재 설정: TP +{TP_PCT*100:.1f}% | SL {SL_PCT*100:.1f}%")
    print(f"  기본 승률: {wins / len(all_features) * 100:.1f}%")
    base_expected = (wins * TP_PCT + losses * SL_PCT) / len(all_features) - COMMISSION
    print(f"  기대수익: {base_expected * 100:+.4f}%/trade")
    print(f"  수수료 포함 손익분기 승률: {(abs(SL_PCT) + COMMISSION) / (TP_PCT + abs(SL_PCT)) * 100:.1f}%")


async def main():
    print("=" * 120)
    print("  DB 분봉 데이터 역분석: 실제 수익 패턴 탐색")
    print(f"  TP: +{TP_PCT*100:.1f}% | SL: {SL_PCT*100:.1f}% | Forward: {FORWARD_CANDLES}봉({FORWARD_CANDLES*5}분) | 수수료: {COMMISSION*100:.1f}%")
    print("=" * 120)

    # 상위 거래량 심볼 30개만 분석 (속도)
    async with async_session() as db:
        r = await db.execute(
            text("""
                SELECT symbol, COUNT(*) as cnt
                FROM backtest_candles
                WHERE interval = '5m'
                GROUP BY symbol
                HAVING COUNT(*) >= 5000
                ORDER BY cnt DESC
                LIMIT 30
            """)
        )
        symbols_data = r.fetchall()
        symbols = [row[0] for row in symbols_data]
        print(f"\n  분석 대상: {len(symbols)}개 심볼")

    all_features = []
    total_wins = 0
    total_losses = 0
    total_timeouts = 0

    for i, symbol in enumerate(symbols):
        async with async_session() as db:
            result = await analyze_symbol(db, symbol)

        if result["total"] > 0:
            wr = result["wins"] / result["total"] * 100
            print(
                f"  [{i+1:2d}/{len(symbols)}] {symbol:15s} | "
                f"{result['total']:>6,}건 | 승률 {wr:5.1f}% | "
                f"W:{result['wins']:,} L:{result['losses']:,} T:{result['timeouts']:,}"
            )
        total_wins += result["wins"]
        total_losses += result["losses"]
        total_timeouts += result["timeouts"]
        all_features.extend(result["features"])

    print(f"\n  전체: {len(all_features):,}건 | W:{total_wins:,} L:{total_losses:,} T:{total_timeouts:,}")

    # 패턴 분석
    analyze_patterns(all_features)

    print(f"\n  완료!\n")


if __name__ == "__main__":
    asyncio.run(main())
