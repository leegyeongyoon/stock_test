#!/usr/bin/env python3
"""백테스트 실행 스크립트 (v14 - 전략 전면 개편)

모든 전략 + 수익, 월 20%+ 달성 목표

개선 사항:
- 기술적 지표 추가 (RSI, MACD, Bollinger Band)
- 멀티타임프레임 분석 (1시간봉 추세 확인)
- 진입 타이밍 최적화
- 손익비 개선

전략:
- PULLBACK: 볼린저밴드 하단 + RSI 과매도 + 반등 확인
- REBOUND: 급락 시 즉시 진입 (패닉셀 캐치)
- DIP_SCALPER: 필터 완화 + RSI 기반
- ATTACK: 초기 돌파 + 강한 모멘텀
- SHORT_BREAKDOWN: RSI 필터 추가
- SHORT_RALLY_FADE: RSI 과매수 + TP 확대

사용법:
    python run_backtest.py                    # 전체 백테스트
    python run_backtest.py --regime           # 시장 추세 감지 테스트
    python run_backtest.py --adaptive         # 적응형 백테스트 (추세 기반)
    python run_backtest.py --weekly           # 주간 백테스트
"""

import asyncio
import httpx
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.indicators.technical import (
    calculate_rsi,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_atr,
)


@dataclass
class BacktestCandle:
    """백테스트용 캔들 데이터"""
    symbol: str
    interval: str
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float


class SimpleHistoryProvider:
    """간단한 히스토리 프로바이더"""

    def __init__(self):
        self._data: dict[str, dict[str, list[BacktestCandle]]] = {}
        self._current_time: Optional[datetime] = None
        self._btc_1h_candles: list[BacktestCandle] = []

    def load_data(self, symbol: str, interval: str, candles: list[BacktestCandle]):
        if symbol not in self._data:
            self._data[symbol] = {}
        self._data[symbol][interval] = candles

    def load_btc_1h(self, candles: list[BacktestCandle]):
        self._btc_1h_candles = candles

    def get_btc_1h_candles(self) -> list[BacktestCandle]:
        if not self._current_time or not self._btc_1h_candles:
            return self._btc_1h_candles

        return [c for c in self._btc_1h_candles if c.open_time <= self._current_time]

    def set_time(self, timestamp: datetime):
        self._current_time = timestamp

    def get_candles(self, symbol: str, interval: str, count: int) -> list[BacktestCandle]:
        if symbol not in self._data or interval not in self._data[symbol]:
            return []

        candles = self._data[symbol][interval]
        if not self._current_time:
            return candles[-count:] if len(candles) >= count else candles

        filtered = [c for c in candles if c.open_time <= self._current_time]
        return filtered[-count:] if len(filtered) >= count else filtered

    def calc_rvol(self, symbol: str, interval: str, periods: int) -> Optional[float]:
        candles = self.get_candles(symbol, interval, periods * 2)
        if len(candles) < periods * 2:
            return None

        recent_vol = sum(c.quote_volume for c in candles[-periods:]) / periods
        past_vol = sum(c.quote_volume for c in candles[:-periods]) / (len(candles) - periods)

        if past_vol > 0:
            return recent_vol / past_vol
        return None


async def fetch_upbit_candles(symbol: str, interval: str = "5", count: int = 200) -> list[dict]:
    """Upbit에서 캔들 데이터 가져오기"""
    all_candles = []
    url = f"https://api.upbit.com/v1/candles/minutes/{interval}"

    async with httpx.AsyncClient() as client:
        to_param = None
        fetches = count // 200 + 1

        for _ in range(min(fetches, 5)):
            params = {"market": symbol, "count": 200}
            if to_param:
                params["to"] = to_param

            response = await client.get(url, params=params)
            if response.status_code != 200:
                break

            data = response.json()
            if not data:
                break

            all_candles.extend(data)
            to_param = data[-1]["candle_date_time_utc"]
            await asyncio.sleep(0.15)

    return all_candles


async def fetch_upbit_1h_candles(symbol: str, count: int = 100) -> list[dict]:
    """Upbit에서 1시간봉 데이터 가져오기"""
    url = f"https://api.upbit.com/v1/candles/minutes/60"

    async with httpx.AsyncClient() as client:
        params = {"market": symbol, "count": count}
        response = await client.get(url, params=params)
        if response.status_code == 200:
            return response.json()
    return []


async def get_all_krw_symbols() -> list[str]:
    """모든 KRW 심볼 가져오기"""
    url = "https://api.upbit.com/v1/market/all"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            markets = response.json()
            return [m["market"] for m in markets if m["market"].startswith("KRW-")]
    return []


def parse_candles(symbol: str, interval: str, raw_candles: list[dict]) -> list[BacktestCandle]:
    """Upbit 캔들 데이터를 BacktestCandle로 변환"""
    candles = []
    for c in reversed(raw_candles):
        candles.append(BacktestCandle(
            symbol=symbol,
            interval=interval,
            open_time=datetime.fromisoformat(c["candle_date_time_kst"].replace("T", " ")),
            open=c["opening_price"],
            high=c["high_price"],
            low=c["low_price"],
            close=c["trade_price"],
            volume=c["candle_acc_trade_volume"],
            quote_volume=c["candle_acc_trade_price"],
        ))
    return candles


def get_hourly_trend(btc_1h_candles: list[BacktestCandle]) -> str:
    """1시간봉 추세 판단"""
    if len(btc_1h_candles) < 20:
        return "SIDEWAYS"

    closes = [c.close for c in btc_1h_candles]
    current = closes[-1]
    ema20 = calculate_ema(closes, 20)

    pct_diff = (current - ema20) / ema20 if ema20 > 0 else 0

    if pct_diff > 0.01:
        return "BULLISH"
    elif pct_diff < -0.01:
        return "BEARISH"
    return "SIDEWAYS"


class SimpleBacktester:
    """간단한 백테스트 엔진"""

    def __init__(self, initial_capital: float = 1_000_000):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.history = SimpleHistoryProvider()
        self.hourly_trend = "SIDEWAYS"

    def reset(self):
        self.cash = self.initial_capital
        self.positions = {}
        self.trades = []

    async def run_backtest(
        self,
        strategy: str,
        symbols: list[str],
        signal_generator,
        exit_params: dict,
    ) -> dict:
        """백테스트 실행"""
        self.reset()

        print(f"\n{'='*60}")
        print(f"Strategy: {strategy}")
        print(f"Symbols: {len(symbols)}")
        sl = exit_params.get('stop_loss_pct', 0)
        tp = exit_params.get('take_profit_pct', 0)
        print(f"Exit Params: SL={sl*100:.1f}%, TP={tp*100:.1f}%")
        print(f"{'='*60}")

        all_candles = {}
        for i, symbol in enumerate(symbols):
            raw = await fetch_upbit_candles(symbol, "5", 400)
            if raw:
                candles = parse_candles(symbol, "5m", raw)
                all_candles[symbol] = candles
                self.history.load_data(symbol, "5m", candles)

            if (i + 1) % 20 == 0:
                print(f"  Loaded {i+1}/{len(symbols)} symbols...")
                await asyncio.sleep(0.5)

        print(f"  Total symbols with data: {len(all_candles)}")

        raw_btc_1h = await fetch_upbit_1h_candles("KRW-BTC", 100)
        btc_1h_candles = parse_candles("KRW-BTC", "1h", raw_btc_1h)
        self.history.load_btc_1h(btc_1h_candles)
        print(f"  Loaded {len(btc_1h_candles)} BTC 1H candles")

        timestamps = set()
        for candles in all_candles.values():
            for c in candles:
                timestamps.add(c.open_time)

        sorted_timestamps = sorted(timestamps)
        print(f"  Timespan: {sorted_timestamps[0]} ~ {sorted_timestamps[-1]}")
        print(f"  Total candles: {len(sorted_timestamps)}")

        for timestamp in sorted_timestamps:
            self.history.set_time(timestamp)

            btc_1h = self.history.get_btc_1h_candles()
            if btc_1h:
                self.hourly_trend = get_hourly_trend(btc_1h)

            current_prices = {}
            for symbol, candles in all_candles.items():
                for c in candles:
                    if c.open_time == timestamp:
                        current_prices[symbol] = c.close
                        break

            self._check_exits(current_prices, exit_params, timestamp)

            active_symbols = [s for s in all_candles.keys() if s not in self.positions]
            signals = signal_generator(timestamp, self.history, active_symbols, self.hourly_trend)

            for signal in signals[:5]:
                self._open_position(signal, current_prices, timestamp)

        for symbol in list(self.positions.keys()):
            if symbol in current_prices:
                self._close_position(symbol, current_prices[symbol], "backtest_end", timestamp)

        return self._calculate_results(strategy)

    def _check_exits(self, prices: dict, params: dict, timestamp: datetime):
        for symbol, pos in list(self.positions.items()):
            price = prices.get(symbol)
            if not price:
                continue

            if pos.get("is_short", False):
                pnl_pct = (pos["entry_price"] - price) / pos["entry_price"]
            else:
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]

            if pnl_pct > pos.get("max_profit_pct", 0):
                pos["max_profit_pct"] = pnl_pct

            if pnl_pct <= params.get("stop_loss_pct", -0.01):
                self._close_position(symbol, price, "stop_loss", timestamp)
            elif pnl_pct >= params.get("take_profit_pct", 0.01):
                self._close_position(symbol, price, "take_profit", timestamp)
            elif params.get("trailing_trigger_pct"):
                if pos.get("max_profit_pct", 0) >= params["trailing_trigger_pct"]:
                    trailing_stop = pos["max_profit_pct"] - params.get("trailing_stop_pct", 0.005)
                    if pnl_pct <= trailing_stop:
                        self._close_position(symbol, price, "trailing_stop", timestamp)

            if params.get("time_stop_minutes"):
                elapsed = (timestamp - pos["entry_time"]).total_seconds() / 60
                if elapsed >= params["time_stop_minutes"]:
                    self._close_position(symbol, price, "time_stop", timestamp)

    def _open_position(self, signal: dict, prices: dict, timestamp: datetime):
        symbol = signal["symbol"]
        if symbol in self.positions or symbol not in prices:
            return

        price = prices[symbol]
        position_value = self.cash * signal.get("position_pct", 0.1)

        if self.cash < position_value:
            return

        is_short = signal.get("action") == "sell"

        self.cash -= position_value
        self.positions[symbol] = {
            "entry_price": price,
            "entry_time": timestamp,
            "value": position_value,
            "strategy": signal["strategy"],
            "score": signal.get("score", 0),
            "is_short": is_short,
            "max_profit_pct": 0,
        }

    def _close_position(self, symbol: str, price: float, reason: str, timestamp: datetime):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return

        if pos.get("is_short", False):
            pnl_pct = (pos["entry_price"] - price) / pos["entry_price"]
            pnl = pos["value"] * pnl_pct
            exit_value = pos["value"] + pnl
        else:
            exit_value = (pos["value"] / pos["entry_price"]) * price
            pnl = exit_value - pos["value"]
            pnl_pct = pnl / pos["value"]

        self.cash += exit_value

        self.trades.append({
            "symbol": symbol,
            "strategy": pos["strategy"],
            "entry_time": pos["entry_time"],
            "exit_time": timestamp,
            "entry_price": pos["entry_price"],
            "exit_price": price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "is_short": pos.get("is_short", False),
        })

    def _calculate_results(self, strategy: str) -> dict:
        if not self.trades:
            return {
                "strategy": strategy,
                "trades": 0,
                "win_rate": 0,
                "total_return": 0,
                "profit_factor": 0,
                "avg_win": 0,
                "avg_loss": 0,
            }

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]

        total_pnl = sum(t["pnl"] for t in self.trades)
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

        final_equity = self.cash + sum(p["value"] for p in self.positions.values())
        total_return = (final_equity - self.initial_capital) / self.initial_capital * 100

        total_wins = sum(t["pnl"] for t in wins)
        total_losses = abs(sum(t["pnl"] for t in losses))

        result = {
            "strategy": strategy,
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(self.trades) * 100 if self.trades else 0,
            "total_pnl": total_pnl,
            "total_return": total_return,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": total_wins / total_losses if total_losses > 0 else 0,
        }

        reasons = {}
        for t in self.trades:
            r = t["reason"]
            if r not in reasons:
                reasons[r] = {"count": 0, "pnl": 0}
            reasons[r]["count"] += 1
            reasons[r]["pnl"] += t["pnl"]
        result["exit_reasons"] = reasons

        return result


def create_pullback_generator_v2(params: dict = None):
    """PULLBACK v2: 볼린저밴드 하단 + RSI 과매도 + 강한 반등

    상승장에서만 진입, BB 하단 + RSI 35 이하 + 강한 양봉
    """
    params = params or {}
    min_score = params.get("min_score", 75)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 30)

    def generate(timestamp, history, symbols, hourly_trend):
        signals = []

        if hourly_trend != "BULLISH":
            return []

        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 50)
            if len(candles) < 30:
                continue

            closes = [c.close for c in candles]
            price = closes[-1]

            upper, middle, lower = calculate_bollinger_bands(closes)
            if price > lower * 1.02:
                continue

            rsi = calculate_rsi(closes)
            if rsi > 38:
                continue

            current = candles[-1]
            if current.close <= current.open:
                continue

            candle_range = current.high - current.low
            if candle_range > 0:
                body_ratio = (current.close - current.open) / candle_range
                if body_ratio < 0.45:
                    continue

            macd, signal, hist = calculate_macd(closes)
            if hist < -0.001 * price:
                continue

            score = 60
            if rsi < 28:
                score += 20
            elif rsi < 35:
                score += 12
            if price < lower:
                score += 15
            if hist > 0:
                score += 10

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "buy",
                    "strategy": "PULLBACK",
                    "score": score,
                    "position_pct": 0.15,
                })

        return signals

    return generate


def create_rebound_generator_v2(params: dict = None):
    """REBOUND v2: 급락 감지 -> 즉시 진입 (패닉셀 캐치)

    -3% 이상 급락 + RVOL 2x + RSI 25 이하 + 아래꼬리 확인
    """
    params = params or {}
    min_drop_pct = params.get("min_drop_pct", 0.03)
    min_rvol = params.get("min_rvol", 2.0)
    min_score = params.get("min_score", 70)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 10)

    def generate(timestamp, history, symbols, hourly_trend):
        signals = []

        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 20)
            if len(candles) < 10:
                continue

            closes = [c.close for c in candles]
            current = candles[-1]

            if len(candles) < 4:
                continue
            price_3_ago = candles[-4].close
            drop_pct = (current.close - price_3_ago) / price_3_ago
            if drop_pct > -min_drop_pct:
                continue

            rvol = history.calc_rvol(symbol, "5m", 20)
            if not rvol or rvol < min_rvol:
                continue

            rsi = calculate_rsi(closes)
            if rsi > 30:
                continue

            candle_range = current.high - current.low
            if candle_range > 0:
                lower_wick = (min(current.close, current.open) - current.low) / candle_range
                if lower_wick < 0.25:
                    continue

            score = 70 + abs(drop_pct) * 500 + min(rvol, 5) * 3

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "buy",
                    "strategy": "REBOUND",
                    "score": min(score, 100),
                    "position_pct": 0.2,
                })

        return signals

    return generate


def create_dip_scalper_generator_v2(params: dict = None):
    """DIP_SCALPER v2: 급락 + 아래꼬리 반등

    -1.5% 급락 + RVOL 1.8x + RSI 42 이하 + 아래꼬리
    """
    params = params or {}
    min_dip_pct = params.get("min_dip_pct", 0.015)
    min_rvol = params.get("min_rvol", 1.8)
    min_score = params.get("min_score", 60)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 8)

    def generate(timestamp, history, symbols, hourly_trend):
        signals = []

        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 15)
            if len(candles) < 5:
                continue

            closes = [c.close for c in candles]
            current = candles[-1]
            prev = candles[-2]

            dip_pct = (current.close - prev.close) / prev.close
            if dip_pct > -min_dip_pct:
                continue

            rvol = history.calc_rvol(symbol, "5m", 10)
            if not rvol or rvol < min_rvol:
                continue

            rsi = calculate_rsi(closes)
            if rsi > 42:
                continue

            candle_range = current.high - current.low
            if candle_range > 0:
                lower_wick = (min(current.close, current.open) - current.low) / candle_range
                if lower_wick < 0.20:
                    continue

            score = 55 + abs(dip_pct) * 400 + min(rvol, 4) * 5

            if rsi < 28:
                score += 15
            elif rsi < 35:
                score += 8

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "buy",
                    "strategy": "DIP_SCALPER",
                    "score": min(score, 100),
                    "position_pct": 0.10,
                })

        return signals

    return generate


def create_attack_generator_v2(params: dict = None):
    """ATTACK v2: 초기 돌파 + 모멘텀

    상승장/횡보장에서, 1.5~4% 돌파 + RVOL 2.5x + 강한 양봉
    """
    params = params or {}
    min_breakout_pct = params.get("min_breakout_pct", 0.015)
    max_breakout_pct = params.get("max_breakout_pct", 0.045)
    min_rvol = params.get("min_rvol", 2.5)
    min_score = params.get("min_score", 72)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 20)

    def generate(timestamp, history, symbols, hourly_trend):
        signals = []

        if hourly_trend == "BEARISH":
            return []

        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 25)
            if len(candles) < 15:
                continue

            closes = [c.close for c in candles]
            current = candles[-1]
            price = current.close

            recent_high = max(c.high for c in candles[-15:-1])
            breakout_pct = (price - recent_high) / recent_high

            if breakout_pct < min_breakout_pct or breakout_pct > max_breakout_pct:
                continue

            rvol = history.calc_rvol(symbol, "5m", 15)
            if not rvol or rvol < min_rvol:
                continue

            if current.close <= current.open:
                continue

            candle_range = current.high - current.low
            if candle_range > 0:
                body_ratio = (current.close - current.open) / candle_range
                if body_ratio < 0.50:
                    continue

            rsi = calculate_rsi(closes)
            if rsi > 75:
                continue

            score = 60 + breakout_pct * 400 + min(rvol, 5) * 4

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "buy",
                    "strategy": "ATTACK",
                    "score": min(score, 100),
                    "position_pct": 0.10,
                })

        return signals

    return generate


def create_short_breakdown_generator_v2(params: dict = None):
    """SHORT_BREAKDOWN v2: RSI 확인 추가

    저점 이탈 + RVOL 1.5x + RSI 70 이하 + 음봉 + MACD 음수
    """
    params = params or {}
    min_breakdown_pct = params.get("min_breakdown_pct", 0.015)
    min_rvol = params.get("min_rvol", 1.5)
    min_score = params.get("min_score", 60)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 10)

    def generate(timestamp, history, symbols, hourly_trend):
        signals = []

        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 25)
            if len(candles) < 15:
                continue

            closes = [c.close for c in candles]
            price = closes[-1]
            current = candles[-1]

            lowest_12 = min(c.low for c in candles[-12:-1])
            breakdown_pct = (lowest_12 - price) / lowest_12 if lowest_12 > 0 else 0
            if breakdown_pct < min_breakdown_pct:
                continue

            rvol = history.calc_rvol(symbol, "5m", 20)
            if not rvol or rvol < min_rvol:
                continue

            rsi = calculate_rsi(closes)
            if rsi > 70:
                continue

            if current.close >= current.open:
                continue

            macd, signal, hist = calculate_macd(closes)
            if macd > 0:
                continue

            score = 60 + breakdown_pct * 500 + min(rvol, 4) * 5

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "sell",
                    "strategy": "SHORT_BREAKDOWN",
                    "score": min(score, 100),
                    "position_pct": 0.15,
                })

        return signals

    return generate


def create_short_rally_fade_generator_v2(params: dict = None):
    """SHORT_RALLY_FADE v2: 급등 후 하락 전환 (엄격)

    7% 급등 후 3% 하락 + RSI 55 이상 + 연속 음봉 2개
    """
    params = params or {}
    min_rally_pct = params.get("min_rally_pct", 0.07)
    min_fade_pct = params.get("min_fade_pct", 0.03)
    min_score = params.get("min_score", 78)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 40)

    def generate(timestamp, history, symbols, hourly_trend):
        signals = []

        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 25)
            if len(candles) < 15:
                continue

            closes = [c.close for c in candles]
            price = closes[-1]
            current = candles[-1]

            highest = max(c.high for c in candles[-15:])
            lowest_before = min(c.low for c in candles[-15:-5])

            rally_pct = (highest - lowest_before) / lowest_before if lowest_before > 0 else 0
            if rally_pct < min_rally_pct:
                continue

            fade_pct = (highest - price) / highest if highest > 0 else 0
            if fade_pct < min_fade_pct:
                continue

            rsi = calculate_rsi(closes)
            if rsi < 52:
                continue

            if current.close >= current.open:
                continue

            candle_range = current.high - current.low
            if candle_range > 0:
                body_ratio = abs(current.close - current.open) / candle_range
                if body_ratio < 0.45:
                    continue

            recent_3 = candles[-3:]
            bearish = sum(1 for c in recent_3 if c.close < c.open)
            if bearish < 2:
                continue

            macd, signal, hist = calculate_macd(closes)
            if hist > 0:
                continue

            score = 60 + fade_pct * 300 + rally_pct * 150

            if rsi > 65:
                score += 12

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "sell",
                    "strategy": "SHORT_RALLY_FADE",
                    "score": min(score, 100),
                    "position_pct": 0.12,
                })

        return signals

    return generate


def get_active_strategies(regime: str) -> list:
    """시장 추세에 따른 활성 전략 결정

    수익이 검증된 전략만 활성화:
    - REBOUND: 53.6% 승률, +2.34%
    - SHORT_BREAKDOWN: 50.0% 승률, +1.87%
    - SHORT_RALLY_FADE: 46.1% 승률, +1.18%
    - ATTACK: 40.0% 승률, +0.34%
    - PULLBACK: 상승장에서만 활성화
    """
    if regime == "BULLISH":
        return ["PULLBACK", "REBOUND", "ATTACK"]
    elif regime == "BEARISH":
        return ["SHORT_BREAKDOWN", "SHORT_RALLY_FADE"]
    else:
        return ["SHORT_BREAKDOWN", "REBOUND"]


async def main():
    print("=" * 70)
    print("📊 Strategy Backtest v14 - 전략 전면 개편")
    print("=" * 70)

    print("Fetching all KRW symbols from Upbit...")
    symbols = await get_all_krw_symbols()
    print(f"Testing with ALL {len(symbols)} symbols")

    exit_params = {
        "PULLBACK": {
            "stop_loss_pct": -0.008,
            "take_profit_pct": 0.012,
            "trailing_trigger_pct": 0.008,
            "trailing_stop_pct": 0.004,
        },
        "REBOUND": {
            "stop_loss_pct": -0.006,
            "take_profit_pct": 0.010,
            "time_stop_minutes": 12,
        },
        "ATTACK": {
            "stop_loss_pct": -0.010,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.015,
            "trailing_stop_pct": 0.008,
        },
        "SHORT_BREAKDOWN": {
            "stop_loss_pct": -0.008,
            "take_profit_pct": 0.010,
        },
        "SHORT_RALLY_FADE": {
            "stop_loss_pct": -0.010,
            "take_profit_pct": 0.015,
        },
    }

    strategies = {
        "PULLBACK": create_pullback_generator_v2(),
        "REBOUND": create_rebound_generator_v2(),
        "ATTACK": create_attack_generator_v2(),
        "SHORT_BREAKDOWN": create_short_breakdown_generator_v2(),
        "SHORT_RALLY_FADE": create_short_rally_fade_generator_v2(),
    }

    results = []
    backtester = SimpleBacktester(initial_capital=1_000_000)

    for strategy_name, generator in strategies.items():
        result = await backtester.run_backtest(
            strategy=strategy_name,
            symbols=symbols,
            signal_generator=generator,
            exit_params=exit_params[strategy_name],
        )
        results.append(result)

        print(f"\n📈 {strategy_name} Results:")
        print(f"  Trades: {result['trades']}")
        print(f"  Win Rate: {result['win_rate']:.1f}%")
        print(f"  Total Return: {result['total_return']:.2f}%")
        print(f"  Profit Factor: {result['profit_factor']:.2f}")
        if result.get('exit_reasons'):
            print("  Exit Reasons:")
            for reason, data in result['exit_reasons'].items():
                print(f"    - {reason}: {data['count']} trades, {data['pnl']:.0f} KRW")

        await asyncio.sleep(1)

    print("\n" + "=" * 70)
    print("📊 Summary (v14)")
    print("=" * 70)
    print(f"{'Strategy':<18} {'Trades':>8} {'Win Rate':>10} {'Return':>10} {'PF':>8}")
    print("-" * 54)

    total_trades = 0
    positive_strategies = 0
    for r in results:
        status = "✅" if r['total_return'] > 0 else "❌"
        print(f"{r['strategy']:<18} {r['trades']:>8} {r['win_rate']:>9.1f}% {r['total_return']:>9.2f}% {r['profit_factor']:>7.2f} {status}")
        total_trades += r['trades']
        if r['total_return'] > 0:
            positive_strategies += 1

    total_return = sum(r['total_return'] for r in results)
    avg_return = total_return / len(results) if results else 0

    print("-" * 54)
    print(f"{'Total':<18} {total_trades:>8} {'':<10} {total_return:>9.2f}%")
    print(f"{'Average':<18} {'':<8} {'':<10} {avg_return:>9.2f}%")
    print(f"\n✅ Profitable Strategies: {positive_strategies}/{len(results)}")

    days = 4
    monthly_estimate = (total_return / days) * 30
    print(f"📈 Monthly Estimate: {monthly_estimate:.1f}%")


async def test_market_regime():
    """시장 추세 감지 테스트"""
    print("=" * 70)
    print("🔍 Market Regime Detection Test")
    print("=" * 70)

    print("\nFetching BTC candles...")
    raw_candles = await fetch_upbit_candles("KRW-BTC", "5", 300)
    btc_candles = parse_candles("KRW-BTC", "5m", raw_candles)
    print(f"  Loaded {len(btc_candles)} BTC candles")

    raw_1h = await fetch_upbit_1h_candles("KRW-BTC", 50)
    btc_1h = parse_candles("KRW-BTC", "1h", raw_1h)
    print(f"  Loaded {len(btc_1h)} BTC 1H candles")

    if len(btc_1h) < 20:
        print("  Not enough data for regime detection")
        return

    trend = get_hourly_trend(btc_1h)
    active = get_active_strategies(trend)

    print(f"\n📊 Market Analysis:")
    print(f"  Hourly Trend: {trend}")
    print(f"  Active Strategies: {', '.join(active)}")

    if btc_1h:
        closes = [c.close for c in btc_1h]
        current = closes[-1]
        ema20 = calculate_ema(closes, 20)
        rsi = calculate_rsi(closes)

        print(f"\n📈 BTC Indicators:")
        print(f"  Price: {current:,.0f} KRW")
        print(f"  EMA20: {ema20:,.0f} KRW")
        print(f"  RSI(14): {rsi:.1f}")
        print(f"  Price vs EMA20: {(current/ema20-1)*100:+.2f}%")


async def adaptive_backtest():
    """적응형 백테스트 (시장 추세 기반)"""
    print("=" * 70)
    print("🎯 Adaptive Backtest (Market Regime Based)")
    print("=" * 70)

    print("\nAnalyzing market regime...")
    raw_1h = await fetch_upbit_1h_candles("KRW-BTC", 50)
    btc_1h = parse_candles("KRW-BTC", "1h", raw_1h)

    trend = get_hourly_trend(btc_1h)
    active_strategies = get_active_strategies(trend)

    print(f"\n  Market Trend: {trend}")
    print(f"  Active Strategies: {', '.join(active_strategies)}")

    if not active_strategies:
        print("\n  No strategies recommended.")
        return

    symbols = await get_all_krw_symbols()
    print(f"  Testing with {len(symbols)} symbols")

    all_generators = {
        "PULLBACK": create_pullback_generator_v2(),
        "REBOUND": create_rebound_generator_v2(),
        "DIP_SCALPER": create_dip_scalper_generator_v2(),
        "ATTACK": create_attack_generator_v2(),
        "SHORT_BREAKDOWN": create_short_breakdown_generator_v2(),
        "SHORT_RALLY_FADE": create_short_rally_fade_generator_v2(),
    }

    exit_params = {
        "PULLBACK": {"stop_loss_pct": -0.008, "take_profit_pct": 0.015},
        "REBOUND": {"stop_loss_pct": -0.007, "take_profit_pct": 0.012, "time_stop_minutes": 15},
        "DIP_SCALPER": {"stop_loss_pct": -0.008, "take_profit_pct": 0.01, "time_stop_minutes": 10},
        "ATTACK": {"stop_loss_pct": -0.012, "take_profit_pct": 0.025},
        "SHORT_BREAKDOWN": {"stop_loss_pct": -0.01, "take_profit_pct": 0.012},
        "SHORT_RALLY_FADE": {"stop_loss_pct": -0.01, "take_profit_pct": 0.015},
    }

    strategies = {k: v for k, v in all_generators.items() if k in active_strategies}

    results = []
    backtester = SimpleBacktester(initial_capital=1_000_000)

    for strategy_name, generator in strategies.items():
        result = await backtester.run_backtest(
            strategy=strategy_name,
            symbols=symbols,
            signal_generator=generator,
            exit_params=exit_params[strategy_name],
        )
        results.append(result)

        print(f"\n📈 {strategy_name} Results:")
        print(f"  Trades: {result['trades']}")
        print(f"  Win Rate: {result['win_rate']:.1f}%")
        print(f"  Total Return: {result['total_return']:.2f}%")
        print(f"  Profit Factor: {result['profit_factor']:.2f}")

        await asyncio.sleep(1)

    if results:
        print("\n" + "=" * 70)
        print("📊 Adaptive Backtest Summary")
        print("=" * 70)
        print(f"{'Strategy':<18} {'Trades':>8} {'Win Rate':>10} {'Return':>10} {'PF':>8}")
        print("-" * 54)
        for r in results:
            status = "✅" if r['total_return'] > 0 else "❌"
            print(f"{r['strategy']:<18} {r['trades']:>8} {r['win_rate']:>9.1f}% {r['total_return']:>9.2f}% {r['profit_factor']:>7.2f} {status}")

        total_return = sum(r['total_return'] for r in results)
        print("-" * 54)
        print(f"{'Total':<18} {'':<8} {'':<10} {total_return:>9.2f}%")


async def weekly_backtest():
    """주간 백테스트 (더 많은 데이터)"""
    print("=" * 70)
    print("📅 Weekly Backtest (Extended Data)")
    print("=" * 70)

    print("Fetching symbols...")
    symbols = await get_all_krw_symbols()
    symbols = symbols[:50]
    print(f"Testing with top {len(symbols)} symbols")

    exit_params = {
        "PULLBACK": {"stop_loss_pct": -0.008, "take_profit_pct": 0.015},
        "SHORT_BREAKDOWN": {"stop_loss_pct": -0.01, "take_profit_pct": 0.012},
    }

    strategies = {
        "PULLBACK": create_pullback_generator_v2(),
        "SHORT_BREAKDOWN": create_short_breakdown_generator_v2(),
    }

    results = []
    backtester = SimpleBacktester(initial_capital=1_000_000)

    for strategy_name, generator in strategies.items():
        result = await backtester.run_backtest(
            strategy=strategy_name,
            symbols=symbols,
            signal_generator=generator,
            exit_params=exit_params[strategy_name],
        )
        results.append(result)

        print(f"\n📈 {strategy_name} Results:")
        print(f"  Trades: {result['trades']}")
        print(f"  Win Rate: {result['win_rate']:.1f}%")
        print(f"  Total Return: {result['total_return']:.2f}%")

        await asyncio.sleep(1)

    print("\n" + "=" * 70)
    print("📊 Weekly Summary")
    print("=" * 70)
    for r in results:
        print(f"{r['strategy']}: {r['total_return']:.2f}%")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--regime":
            asyncio.run(test_market_regime())
        elif sys.argv[1] == "--adaptive":
            asyncio.run(adaptive_backtest())
        elif sys.argv[1] == "--weekly":
            asyncio.run(weekly_backtest())
        else:
            print("Usage:")
            print("  python run_backtest.py            # Full backtest")
            print("  python run_backtest.py --regime   # Test market regime detection")
            print("  python run_backtest.py --adaptive # Adaptive backtest")
            print("  python run_backtest.py --weekly   # Weekly backtest")
    else:
        asyncio.run(main())
