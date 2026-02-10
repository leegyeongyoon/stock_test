"""TP/SL 최적 비율 탐색 + 유망 패턴 심층 분석

이전 분석에서 발견한 유망 패턴들에 대해:
1. 다양한 TP/SL 조합으로 최적 비율 탐색
2. 유망 패턴 조건을 미세 조정하여 최적화
3. 수수료 포함 기대수익 양수인 전략만 필터
"""

import asyncio
import math
from collections import defaultdict
from datetime import datetime

from src.models.database import async_session
from sqlalchemy import text


COMMISSION = 0.001  # 왕복 0.1%


async def load_candles_for_symbol(db, symbol: str) -> list[dict]:
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


def calc_rsi(closes, period=14):
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
    return 100 - (100 / (1 + avg_gain / avg_loss))


def calc_sma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_ema(closes, period):
    if len(closes) < period:
        return None
    m = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]:
        ema = (p - ema) * m + ema
    return ema


def analyze_candle_features(candles, idx):
    if idx < 200:
        return None

    window = candles[max(0, idx - 250) : idx + 1]
    closes = [c["close"] for c in window]
    volumes = [c["volume"] for c in window]
    current = candles[idx]
    price = current["close"]
    if price <= 0:
        return None

    sma20 = calc_sma(closes, 20)
    ema50 = calc_ema(closes, 50)
    sma200 = calc_sma(closes, 200)
    if not sma20 or not ema50 or not sma200 or sma200 <= 0:
        return None

    rsi = calc_rsi(closes, 14)
    if rsi is None:
        return None

    avg_vol = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else None
    rvol = current["volume"] / avg_vol if avg_vol and avg_vol > 0 else None
    if rvol is None:
        return None

    # ATR%
    if len(window) >= 15:
        trs = []
        for i in range(-14, 0):
            h, lo, pc = window[i]["high"], window[i]["low"], window[i - 1]["close"]
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        atr = sum(trs) / len(trs)
        atr_pct = atr / price * 100
    else:
        return None

    # 변화율
    change_6 = (price - candles[idx - 6]["close"]) / candles[idx - 6]["close"] if idx >= 6 and candles[idx - 6]["close"] > 0 else 0
    change_12 = (price - candles[idx - 12]["close"]) / candles[idx - 12]["close"] if idx >= 12 and candles[idx - 12]["close"] > 0 else 0

    # 캔들
    body = current["close"] - current["open"]
    candle_range = current["high"] - current["low"]
    lower_wick = min(current["open"], current["close"]) - current["low"]
    is_bullish = body > 0
    body_ratio = abs(body) / candle_range if candle_range > 0 else 0

    # 직전 3봉 음봉
    bearish_3 = 0
    if idx >= 3:
        for j in range(1, 4):
            if candles[idx - j]["close"] < candles[idx - j]["open"]:
                bearish_3 += 1

    price_vs_sma20 = (price - sma20) / sma20

    # BB
    if len(closes) >= 20:
        mean20 = sum(closes[-20:]) / 20
        std20 = math.sqrt(sum((x - mean20) ** 2 for x in closes[-20:]) / 20)
    else:
        std20 = 0
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    bb_position = (price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5

    return {
        "rsi": rsi, "rvol": rvol, "atr_pct": atr_pct,
        "change_6": change_6, "change_12": change_12,
        "is_bullish": is_bullish, "body_ratio": body_ratio,
        "bearish_3": bearish_3, "price_vs_sma20": price_vs_sma20,
        "bb_position": bb_position, "price_vs_sma200": (price - sma200) / sma200,
    }


def check_forward(candles, idx, tp_pct, sl_pct, max_candles):
    entry = candles[idx]["close"]
    if entry <= 0:
        return "skip", 0
    tp = entry * (1 + tp_pct)
    sl = entry * (1 + sl_pct)
    end = min(idx + max_candles + 1, len(candles))
    max_ret = 0
    for i in range(idx + 1, end):
        c = candles[i]
        max_ret = max(max_ret, (c["high"] - entry) / entry)
        if c["low"] <= sl:
            return "loss", max_ret
        if c["high"] >= tp:
            return "win", max_ret
    return "timeout", max_ret


async def main():
    print("=" * 120)
    print("  TP/SL 최적 비율 탐색 + 유망 패턴 심층 분석")
    print("=" * 120)

    # 심볼 로드
    async with async_session() as db:
        r = await db.execute(text("""
            SELECT symbol FROM backtest_candles
            WHERE interval = '5m'
            GROUP BY symbol HAVING COUNT(*) >= 5000
            ORDER BY COUNT(*) DESC LIMIT 30
        """))
        symbols = [row[0] for row in r.fetchall()]
    print(f"  심볼: {len(symbols)}개\n")

    # 전 심볼 캔들 + 피처 계산
    all_entries = []  # (features, candles_ref, idx)

    for si, symbol in enumerate(symbols):
        async with async_session() as db:
            candles = await load_candles_for_symbol(db, symbol)
        if len(candles) < 300:
            continue
        for idx in range(200, len(candles) - 36):  # 36봉(3시간) forward 여유
            features = analyze_candle_features(candles, idx)
            if features:
                all_entries.append((features, candles, idx))
        print(f"  [{si+1}/{len(symbols)}] {symbol}: {len(candles)}봉 처리")

    print(f"\n  총 분석 대상: {len(all_entries):,}건\n")

    # === 1. 전체 TP/SL 비율별 승률 (최적 비율 탐색) ===
    print("=" * 100)
    print("  [1] TP/SL 비율별 전체 승률 (샘플 10,000건)")
    print("=" * 100)

    # 속도 위해 샘플링
    import random
    sample_indices = random.sample(range(len(all_entries)), min(10000, len(all_entries)))

    tp_sl_combos = [
        # (tp%, sl%, forward_candles)
        (0.010, -0.008, 12),  # +1.0% / -0.8% / 60분
        (0.010, -0.010, 12),  # +1.0% / -1.0% / 60분
        (0.012, -0.008, 12),  # +1.2% / -0.8% / 60분
        (0.012, -0.010, 18),  # +1.2% / -1.0% / 90분
        (0.015, -0.010, 18),  # +1.5% / -1.0% / 90분
        (0.015, -0.012, 18),  # +1.5% / -1.2% / 90분
        (0.015, -0.015, 24),  # +1.5% / -1.5% / 120분
        (0.020, -0.010, 24),  # +2.0% / -1.0% / 120분
        (0.020, -0.015, 24),  # +2.0% / -1.5% / 120분
        (0.020, -0.020, 36),  # +2.0% / -2.0% / 180분
        (0.025, -0.015, 36),  # +2.5% / -1.5% / 180분
        (0.030, -0.020, 36),  # +3.0% / -2.0% / 180분
    ]

    print(f"  {'TP':>6s} | {'SL':>6s} | {'시간':>5s} | {'전체WR':>7s} | {'BE_WR':>7s} | {'기대수익':>10s} | 비고")
    print(f"  {'-'*80}")

    for tp, sl, fwd in tp_sl_combos:
        wins, losses, timeouts = 0, 0, 0
        for si in sample_indices:
            feat, candles, idx = all_entries[si]
            result, _ = check_forward(candles, idx, tp, sl, fwd)
            if result == "win":
                wins += 1
            elif result == "loss":
                losses += 1
            else:
                timeouts += 1
        total = wins + losses + timeouts
        wr = wins / total * 100
        be_wr = (abs(sl) + COMMISSION) / (tp + abs(sl)) * 100
        expected = (wins * tp + losses * sl) / total - COMMISSION
        tag = " ★" if expected > 0 else ""
        print(
            f"  {tp*100:+5.1f}% | {sl*100:+5.1f}% | {fwd*5:>3d}분 | "
            f"{wr:6.1f}% | {be_wr:6.1f}% | {expected*100:+8.4f}%{tag}"
        )

    # === 2. 유망 패턴별 최적 TP/SL ===
    print(f"\n{'=' * 100}")
    print("  [2] 유망 패턴별 최적 TP/SL 탐색")
    print(f"{'=' * 100}")

    patterns = {
        "A: ATR>1.5% + RSI<30 + RVOL>2 + 양봉": lambda f: f["atr_pct"] > 1.5 and f["rsi"] < 30 and f["rvol"] > 2 and f["is_bullish"],
        "B: 30분-2%하락 + RVOL>2 + 양봉 + RSI<40": lambda f: f["change_6"] < -0.02 and f["rvol"] > 2 and f["is_bullish"] and f["rsi"] < 40,
        "C: SMA20 -2%아래 + RVOL>2 + 양봉": lambda f: f["price_vs_sma20"] < -0.02 and f["rvol"] > 2 and f["is_bullish"],
        "D: 30분-2%하락 + RSI<30 + 양봉": lambda f: f["change_6"] < -0.02 and f["rsi"] < 30 and f["is_bullish"],
        "E: BB<0.1 + RSI<30 + 양봉 + RVOL>2": lambda f: f["bb_position"] < 0.1 and f["rsi"] < 30 and f["is_bullish"] and f["rvol"] > 2,
        "F: 1시간-5%하락 + RVOL>3": lambda f: f["change_12"] < -0.05 and f["rvol"] > 3,
        "G: ATR>1.0% + RSI<30 + 양봉": lambda f: f["atr_pct"] > 1.0 and f["rsi"] < 30 and f["is_bullish"],
        "H: ATR>1.0% + RSI<35 + RVOL>1.5 + 양봉": lambda f: f["atr_pct"] > 1.0 and f["rsi"] < 35 and f["rvol"] > 1.5 and f["is_bullish"],
        "I: 30분-1.5%하락 + RVOL>2 + 양봉": lambda f: f["change_6"] < -0.015 and f["rvol"] > 2 and f["is_bullish"],
        "J: ATR>0.8% + 30분-2%하락 + 양봉": lambda f: f["atr_pct"] > 0.8 and f["change_6"] < -0.02 and f["is_bullish"],
        "K: 3봉음봉 + RSI<30 + ATR>1%": lambda f: f["bearish_3"] == 3 and f["rsi"] < 30 and f["atr_pct"] > 1.0,
        "L: change_6<-2% + change_12<-3% + 양봉": lambda f: f["change_6"] < -0.02 and f["change_12"] < -0.03 and f["is_bullish"],
    }

    for pname, pfn in patterns.items():
        matching = [(f, c, i) for f, c, i in all_entries if pfn(f)]
        if len(matching) < 20:
            print(f"\n  {pname}: {len(matching)}건 (부족, 스킵)")
            continue

        print(f"\n  {pname}: {len(matching):,}건")

        best_expected = -999
        best_combo = None

        for tp, sl, fwd in tp_sl_combos:
            wins, losses, timeouts = 0, 0, 0
            for feat, candles, idx in matching:
                result, _ = check_forward(candles, idx, tp, sl, fwd)
                if result == "win":
                    wins += 1
                elif result == "loss":
                    losses += 1
                else:
                    timeouts += 1
            total = wins + losses + timeouts
            wr = wins / total * 100
            expected = (wins * tp + losses * sl) / total - COMMISSION
            tag = ""
            if expected > 0:
                tag = " ★"
                if expected > best_expected:
                    best_expected = expected
                    best_combo = (tp, sl, fwd, wr, expected, total, wins, losses, timeouts)

            print(
                f"    TP{tp*100:+5.1f}% SL{sl*100:+5.1f}% {fwd*5:>3d}분 | "
                f"WR {wr:5.1f}% | W:{wins:>4d} L:{losses:>4d} T:{timeouts:>4d} | "
                f"기대 {expected*100:+7.4f}%{tag}"
            )

        if best_combo:
            tp, sl, fwd, wr, exp, total, w, l, t = best_combo
            print(
                f"  → 최적: TP{tp*100:+5.1f}% SL{sl*100:+5.1f}% {fwd*5}분 | "
                f"WR {wr:.1f}% | {total}건 | 기대수익 {exp*100:+.4f}%/trade ★★★"
            )

    # === 3. 최종 요약 ===
    print(f"\n{'=' * 100}")
    print("  [3] 전체 결과 요약")
    print(f"{'=' * 100}")
    print("  30일간 30개 심볼 5분봉 데이터 기반 역분석 완료")
    print("  결론은 위 ★★★ 표시된 최적 패턴+TP/SL 조합을 전략으로 구현")
    print()


if __name__ == "__main__":
    asyncio.run(main())
