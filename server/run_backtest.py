#!/usr/bin/env python3
"""백테스트 실행 스크립트 (v13 + 시장 추세 감지)

개선된 전략들을 테스트합니다:
- PULLBACK: 반등 확인 게이팅 추가
- DIP_SCALPER: 반등 확인 + 아래꼬리 체크 추가
- ATTACK: 추격 방지 게이트 추가
- REBOUND: 기존 (베이스라인)
- SHORT_BREAKDOWN: 하락 돌파 숏 (NEW)
- SHORT_RALLY_FADE: 급등 후 하락 숏 (NEW)

시장 추세 감지:
- BULLISH: 롱 전략 활성화 (PULLBACK, REBOUND, DIP_SCALPER, ATTACK)
- BEARISH: 숏 전략 활성화 (SHORT_BREAKDOWN, SHORT_RALLY_FADE)
- SIDEWAYS: 보수적 운영 (PULLBACK만)

사용법:
    python run_backtest.py                    # 전체 백테스트
    python run_backtest.py --regime           # 시장 추세 감지 테스트
    python run_backtest.py --adaptive         # 적응형 백테스트 (추세 기반)
"""

import asyncio
import httpx
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import json


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

    def load_data(self, symbol: str, interval: str, candles: list[BacktestCandle]):
        if symbol not in self._data:
            self._data[symbol] = {}
        self._data[symbol][interval] = candles

    def set_time(self, timestamp: datetime):
        self._current_time = timestamp

    def get_candles(self, symbol: str, interval: str, count: int) -> list[BacktestCandle]:
        if symbol not in self._data or interval not in self._data[symbol]:
            return []

        candles = self._data[symbol][interval]
        if not self._current_time:
            return candles[-count:] if len(candles) >= count else candles

        # 현재 시간 이전의 캔들만 반환
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
    """Upbit에서 캔들 데이터 가져오기 (여러 번 호출하여 더 많은 데이터)"""
    all_candles = []
    url = f"https://api.upbit.com/v1/candles/minutes/{interval}"

    async with httpx.AsyncClient() as client:
        to_param = None
        fetches = count // 200 + 1  # 최대 몇 번 호출할지

        for _ in range(min(fetches, 3)):  # 최대 3번 (600개)
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
            await asyncio.sleep(0.15)  # Rate limit

    return all_candles


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
    for c in reversed(raw_candles):  # 시간순 정렬
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


class SimpleBacktester:
    """간단한 백테스트 엔진"""

    def __init__(self, initial_capital: float = 1_000_000):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.history = SimpleHistoryProvider()
        self.btc_trend_up = False  # BTC 추세 필터

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
        print(f"Exit Params: SL={exit_params['stop_loss_pct']*100:.1f}%, TP={exit_params['take_profit_pct']*100:.1f}%")
        print(f"{'='*60}")

        # 캔들 데이터 수집
        all_candles = {}
        for i, symbol in enumerate(symbols):
            raw = await fetch_upbit_candles(symbol, "5", 200)
            if raw:
                candles = parse_candles(symbol, "5m", raw)
                all_candles[symbol] = candles
                self.history.load_data(symbol, "5m", candles)

            if (i + 1) % 20 == 0:
                print(f"  Loaded {i+1}/{len(symbols)} symbols...")
                await asyncio.sleep(0.5)  # Rate limit

        print(f"  Total symbols with data: {len(all_candles)}")

        # 모든 타임스탬프 수집
        timestamps = set()
        for candles in all_candles.values():
            for c in candles:
                timestamps.add(c.open_time)

        sorted_timestamps = sorted(timestamps)
        print(f"  Timespan: {sorted_timestamps[0]} ~ {sorted_timestamps[-1]}")
        print(f"  Total candles: {len(sorted_timestamps)}")

        # 시뮬레이션
        for timestamp in sorted_timestamps:
            self.history.set_time(timestamp)

            # 현재 가격
            current_prices = {}
            for symbol, candles in all_candles.items():
                for c in candles:
                    if c.open_time == timestamp:
                        current_prices[symbol] = c.close
                        break

            # 1. 청산 체크
            self._check_exits(current_prices, exit_params, timestamp)

            # 2. 시그널 생성
            active_symbols = [s for s in all_candles.keys() if s not in self.positions]
            signals = signal_generator(timestamp, self.history, active_symbols)

            # 3. 진입
            for signal in signals[:5]:  # 최대 5개
                self._open_position(signal, current_prices, timestamp)

        # 마지막 청산
        for symbol in list(self.positions.keys()):
            if symbol in current_prices:
                self._close_position(symbol, current_prices[symbol], "backtest_end", timestamp)

        # 결과 계산
        return self._calculate_results(strategy)

    def _check_exits(self, prices: dict, params: dict, timestamp: datetime):
        for symbol, pos in list(self.positions.items()):
            price = prices.get(symbol)
            if not price:
                continue

            # 롱/숏에 따라 손익 계산 방향 다름
            if pos.get("is_short", False):
                # 숏: 가격 하락이 이익
                pnl_pct = (pos["entry_price"] - price) / pos["entry_price"]
            else:
                # 롱: 가격 상승이 이익
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]

            if pnl_pct <= params["stop_loss_pct"]:
                self._close_position(symbol, price, "stop_loss", timestamp)
            elif pnl_pct >= params["take_profit_pct"]:
                self._close_position(symbol, price, "take_profit", timestamp)

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
        }

    def _close_position(self, symbol: str, price: float, reason: str, timestamp: datetime):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return

        if pos.get("is_short", False):
            # 숏: 가격 하락이 이익
            pnl_pct = (pos["entry_price"] - price) / pos["entry_price"]
            pnl = pos["value"] * pnl_pct
            exit_value = pos["value"] + pnl
        else:
            # 롱: 가격 상승이 이익
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
            return {"strategy": strategy, "trades": 0, "win_rate": 0, "total_return": 0, "profit_factor": 0, "avg_win": 0, "avg_loss": 0}

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]

        total_pnl = sum(t["pnl"] for t in self.trades)
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

        final_equity = self.cash + sum(p["value"] for p in self.positions.values())
        total_return = (final_equity - self.initial_capital) / self.initial_capital * 100

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
            "profit_factor": abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else 0,
        }

        # 종료 사유별 통계
        reasons = {}
        for t in self.trades:
            r = t["reason"]
            if r not in reasons:
                reasons[r] = {"count": 0, "pnl": 0}
            reasons[r]["count"] += 1
            reasons[r]["pnl"] += t["pnl"]
        result["exit_reasons"] = reasons

        return result


# === Signal Generators (from strategy_adapters.py) ===

def create_pullback_generator(params: dict = None):
    """PULLBACK 시그널 생성기 (v5: 승률 50%+ 목표)"""
    params = params or {}
    min_pullback_pct = params.get("min_pullback_pct", 0.04)  # 4% 눌림 (더 깊은 눌림)
    max_pullback_pct = params.get("max_pullback_pct", 0.10)  # 10% 눌림 (과매도 제외)
    min_range_pct = params.get("min_range_pct", 0.06)  # 6% 변동폭
    max_rvol = params.get("max_rvol", 1.5)  # 1.5x RVOL (안정적)
    min_score = params.get("min_score", 75)  # 75점 (엄격)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 45)  # 45분

    def generate(timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            # Cooldown
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 50)
            if len(candles) < 30:
                continue

            price = candles[-1].close
            highs = [c.high for c in candles]
            lows = [c.low for c in candles]
            highest = max(highs)
            lowest = min(lows)

            range_pct = (highest - lowest) / lowest if lowest > 0 else 0
            if range_pct < min_range_pct:
                continue

            pullback_pct = (highest - price) / highest if highest > 0 else 0
            if pullback_pct < min_pullback_pct or pullback_pct > max_pullback_pct:
                continue

            rvol = history.calc_rvol(symbol, "5m", 20)
            if rvol and rvol > max_rvol:
                continue

            # 🔑 반등 확인 게이팅 (v5: 강화 - 승률 50%+)
            current_candle = candles[-1]
            if current_candle.close <= current_candle.open:
                continue  # 음봉이면 진입 안함

            # 강한 양봉 확인 (바디 비율 50% 이상)
            candle_range = current_candle.high - current_candle.low
            if candle_range > 0:
                body_ratio = (current_candle.close - current_candle.open) / candle_range
                if body_ratio < 0.5:
                    continue  # 약한 양봉 제외

            # 최근 3봉 중 2봉 이상 양봉이어야 함
            recent = candles[-3:]
            bullish = sum(1 for c in recent if c.close > c.open)
            if bullish < 2:
                continue  # 반등 모멘텀 부족

            # 점수 계산
            score = 50
            if 0.04 <= pullback_pct <= 0.08:
                score += 25
            if rvol and rvol <= 1.0:
                score += 20
            if bullish >= 3:
                score += 15
            elif bullish >= 2:
                score += 10

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "buy",
                    "strategy": "PULLBACK",
                    "score": score,
                    "position_pct": 0.15,  # 15% 포지션
                })

        return signals

    return generate


def create_rebound_generator(params: dict = None):
    """REBOUND 시그널 생성기 (v7: 승률 50%+ & 거래량 완화)"""
    params = params or {}
    min_drop_pct = params.get("min_drop_pct", 0.015)  # 1.5% 급락 (완화)
    min_rvol = params.get("min_rvol", 2.0)  # 2.0x RVOL (완화)
    min_score = params.get("min_score", 65)  # 65점 (완화)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 10)  # 10분 (완화)

    def generate(timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 20)
            if len(candles) < 5:
                continue

            recent = candles[-3:]
            price = recent[-1].close
            prev_price = recent[0].open
            change_pct = (price - prev_price) / prev_price if prev_price > 0 else 0

            if change_pct > -min_drop_pct:
                continue

            rvol = history.calc_rvol(symbol, "5m", 20)
            if not rvol or rvol < min_rvol:
                continue

            # 반등 확인 (v7: 유지 - 승률 핵심)
            current = recent[-1]
            if current.close <= current.open:
                continue

            # 양봉 확인 (바디 비율 40% 이상으로 완화)
            candle_range = current.high - current.low
            if candle_range > 0:
                body_ratio = (current.close - current.open) / candle_range
                if body_ratio < 0.4:
                    continue

            score = 50 + abs(change_pct) * 500 + min(rvol, 5) * 5

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "buy",
                    "strategy": "REBOUND",
                    "score": score,
                    "position_pct": 0.2,  # 20% 포지션 (높은 확신)
                })

        return signals

    return generate


def create_dip_scalper_generator(params: dict = None):
    """DIP_SCALPER 시그널 생성기 (v7: 승률 50%+ & 진입 조건 완화)"""
    params = params or {}
    min_dip_pct = params.get("min_dip_pct", 0.015)  # 1.5% 급락 (완화)
    min_rvol = params.get("min_rvol", 1.5)  # 1.5x RVOL (완화)
    min_score = params.get("min_score", 55)  # 55점 (완화)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 10)  # 10분 (완화)

    def generate(timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 10)
            if len(candles) < 3:
                continue

            price = candles[-1].close
            prev_price = candles[-2].close
            change_pct = (price - prev_price) / prev_price if prev_price > 0 else 0

            if change_pct > -min_dip_pct:
                continue

            rvol = history.calc_rvol(symbol, "5m", 10)
            if not rvol or rvol < min_rvol:
                continue

            # v7: 반등 확인 (유지 - 승률 핵심)
            current_candle = candles[-1]

            # 양봉 확인
            if current_candle.close <= current_candle.open:
                continue

            # 저가 대비 반등 (0.5% 이상으로 완화)
            bounce_from_low = (current_candle.close - current_candle.low) / current_candle.low if current_candle.low > 0 else 0
            if bounce_from_low < 0.005:
                continue

            # 양봉 (바디 비율 30% 이상으로 완화)
            candle_range = current_candle.high - current_candle.low
            if candle_range > 0:
                body_ratio = (current_candle.close - current_candle.open) / candle_range
                if body_ratio < 0.3:
                    continue

            score = 50 + abs(change_pct) * 500 + min(rvol, 5) * 5

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "buy",
                    "strategy": "DIP_SCALPER",
                    "score": score,
                    "position_pct": 0.1,  # 10% 포지션
                })

        return signals

    return generate


def create_attack_generator(params: dict = None):
    """ATTACK 시그널 생성기 (v7: 승률 50%+ & 거래량 완화)"""
    params = params or {}
    min_breakout_pct = params.get("min_breakout_pct", 0.02)  # 2% 돌파 (완화)
    min_rvol = params.get("min_rvol", 2.5)  # 2.5x RVOL (완화)
    min_score = params.get("min_score", 70)  # 70점 (완화)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 20)  # 20분 (완화)

    def generate(timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 20)
            if len(candles) < 12:
                continue

            price = candles[-1].close
            recent_12 = candles[-12:-1]
            highest_12 = max(c.high for c in recent_12)

            breakout_pct = (price - highest_12) / highest_12 if highest_12 > 0 else 0
            if breakout_pct < min_breakout_pct:
                continue

            rvol = history.calc_rvol(symbol, "5m", 20)
            if not rvol or rvol < min_rvol:
                continue

            # 🔑 추격 방지 게이트 (완화)
            change_5m = (price - candles[-2].close) / candles[-2].close if candles[-2].close > 0 else 0
            current_candle = candles[-1]
            change_intra = (price - current_candle.open) / current_candle.open if current_candle.open > 0 else 0

            if change_5m > 0.06 and change_intra > 0.03:  # 6%+3% (완화)
                continue  # 과열 추격 방지

            # 🔑 반등 확인: 양봉
            if current_candle.close <= current_candle.open:
                continue

            score = 50 + breakout_pct * 500 + min(rvol, 5) * 5

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "buy",
                    "strategy": "ATTACK",
                    "score": score,
                    "position_pct": 0.15,  # 15% 포지션
                })

        return signals

    return generate


def create_short_breakdown_generator(params: dict = None):
    """SHORT_BREAKDOWN 시그널 생성기 - 하락 돌파 숏 (v2: 더 많은 기회)"""
    params = params or {}
    min_breakdown_pct = params.get("min_breakdown_pct", 0.015)  # 1.5% 돌파 (완화)
    min_rvol = params.get("min_rvol", 1.5)   # 1.5x RVOL (완화)
    min_score = params.get("min_score", 55)  # 55점 (완화)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 10)  # 10분 (완화)

    def generate(timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 20)
            if len(candles) < 12:
                continue

            price = candles[-1].close

            # 최근 12봉 저점
            recent_12 = candles[-12:-1]
            lowest_12 = min(c.low for c in recent_12)

            # 저점 이탈 체크
            breakdown_pct = (lowest_12 - price) / lowest_12 if lowest_12 > 0 else 0
            if breakdown_pct < min_breakdown_pct:
                continue

            # RVOL 체크
            rvol = history.calc_rvol(symbol, "5m", 20)
            if not rvol or rvol < min_rvol:
                continue

            # 하락 확인: 현재 캔들이 음봉
            current_candle = candles[-1]
            if current_candle.close >= current_candle.open:
                continue

            score = 50 + breakdown_pct * 500 + min(rvol, 5) * 5

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "sell",
                    "strategy": "SHORT_BREAKDOWN",
                    "score": score,
                    "position_pct": 0.15,
                })

        return signals

    return generate


def create_short_rally_fade_generator(params: dict = None):
    """SHORT_RALLY_FADE 시그널 생성기 (v5: 승률 50%+ 목표)"""
    params = params or {}
    min_rally_pct = params.get("min_rally_pct", 0.06)  # 6% 급등 후 (더 큰 급등)
    min_fade_pct = params.get("min_fade_pct", 0.025)   # 2.5% 이상 하락
    min_score = params.get("min_score", 80)            # 80점 (엄격)
    last_entry = {}
    cooldown_minutes = params.get("cooldown_minutes", 45)  # 45분

    def generate(timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            if symbol in last_entry:
                elapsed = (timestamp - last_entry[symbol]).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            candles = history.get_candles(symbol, "5m", 20)
            if len(candles) < 10:
                continue

            price = candles[-1].close

            # 최근 10봉 고점과 그 전 저점
            highest = max(c.high for c in candles[-10:])
            lowest_before = min(c.low for c in candles[-10:-3])

            # 급등폭 체크
            rally_pct = (highest - lowest_before) / lowest_before if lowest_before > 0 else 0
            if rally_pct < min_rally_pct:
                continue

            # 고점 대비 하락폭
            fade_pct = (highest - price) / highest if highest > 0 else 0
            if fade_pct < min_fade_pct:
                continue

            # 하락 확인 (v5: 강화 - 승률 50%+)
            # 최근 3봉 중 2봉 이상 음봉
            recent_3 = candles[-3:]
            bearish = sum(1 for c in recent_3 if c.close < c.open)
            if bearish < 2:
                continue

            # 현재 봉이 강한 음봉 (바디 비율 50% 이상)
            current = candles[-1]
            if current.close >= current.open:
                continue  # 양봉이면 제외
            candle_range = current.high - current.low
            if candle_range > 0:
                body_ratio = abs(current.close - current.open) / candle_range
                if body_ratio < 0.5:
                    continue  # 약한 음봉 제외

            score = 50 + fade_pct * 300 + rally_pct * 200

            if score >= min_score:
                last_entry[symbol] = timestamp
                signals.append({
                    "symbol": symbol,
                    "action": "sell",
                    "strategy": "SHORT_RALLY_FADE",
                    "score": score,
                    "position_pct": 0.12,  # 12% 포지션
                })

        return signals

    return generate


# === Main ===

async def main():
    print("=" * 70)
    print("📊 Strategy Improvement Backtest")
    print("=" * 70)

    # Upbit에서 모든 KRW 심볼 가져오기
    print("Fetching all KRW symbols from Upbit...")
    symbols = await get_all_krw_symbols()
    print(f"Testing with ALL {len(symbols)} symbols")

    # 청산 파라미터 (v13: v11 기반 + 미세 조정)
    # v11에서 PULLBACK 53%, REBOUND 52% 승률 달성
    # TP 1.2%로 조정하여 승률 유지하면서 수익성 개선
    exit_params = {
        "PULLBACK": {"stop_loss_pct": -0.015, "take_profit_pct": 0.012},      # TP 1.2%
        "REBOUND": {"stop_loss_pct": -0.015, "take_profit_pct": 0.012},       # TP 1.2%
        "DIP_SCALPER": {"stop_loss_pct": -0.012, "take_profit_pct": 0.01},    # TP 1.0%
        "ATTACK": {"stop_loss_pct": -0.018, "take_profit_pct": 0.014},        # TP 1.4%
        "SHORT_BREAKDOWN": {"stop_loss_pct": -0.01, "take_profit_pct": 0.01},    # 1:1 유지
        "SHORT_RALLY_FADE": {"stop_loss_pct": -0.012, "take_profit_pct": 0.01},  # SL 1.2%
    }

    # 전략 생성기 (롱 + 숏)
    strategies = {
        "PULLBACK": create_pullback_generator(),
        "REBOUND": create_rebound_generator(),
        "DIP_SCALPER": create_dip_scalper_generator(),
        "ATTACK": create_attack_generator(),
        "SHORT_BREAKDOWN": create_short_breakdown_generator(),
        "SHORT_RALLY_FADE": create_short_rally_fade_generator(),
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

        # 결과 출력
        print(f"\n📈 {strategy_name} Results:")
        print(f"  Trades: {result['trades']}")
        print(f"  Win Rate: {result['win_rate']:.1f}%")
        print(f"  Total Return: {result['total_return']:.2f}%")
        print(f"  Profit Factor: {result['profit_factor']:.2f}")
        if result.get('exit_reasons'):
            print("  Exit Reasons:")
            for reason, data in result['exit_reasons'].items():
                print(f"    - {reason}: {data['count']} trades, {data['pnl']:.0f} KRW")

        await asyncio.sleep(1)  # Rate limit between strategies

    # 요약
    print("\n" + "=" * 70)
    print("📊 Summary")
    print("=" * 70)
    print(f"{'Strategy':<15} {'Trades':>8} {'Win Rate':>10} {'Return':>10} {'PF':>8}")
    print("-" * 51)
    for r in results:
        print(f"{r['strategy']:<15} {r['trades']:>8} {r['win_rate']:>9.1f}% {r['total_return']:>9.2f}% {r['profit_factor']:>8.2f}")

    total_return = sum(r['total_return'] for r in results) / len(results)
    print("-" * 51)
    print(f"{'Average':<15} {'':<8} {'':<10} {total_return:>9.2f}%")


async def test_market_regime():
    """시장 추세 감지 테스트"""
    print("=" * 70)
    print("🔍 Market Regime Detection Test")
    print("=" * 70)

    # BTC 캔들 데이터 가져오기
    print("\nFetching BTC candles...")
    raw_candles = await fetch_upbit_candles("KRW-BTC", "5", 300)
    btc_candles = parse_candles("KRW-BTC", "5m", raw_candles)
    print(f"  Loaded {len(btc_candles)} BTC candles")

    if len(btc_candles) < 50:
        print("  Not enough data for regime detection")
        return

    # 시장 추세 감지
    from src.strategies.market_regime import MarketRegimeDetector, AdaptiveStrategyManager

    detector = MarketRegimeDetector()
    manager = AdaptiveStrategyManager(detector)

    result = manager.update(btc_candles)

    print(f"\n📊 Market Regime Analysis:")
    print(f"  Regime: {result['regime'].upper()}")
    print(f"  Confidence: {result['confidence']*100:.1f}%")
    print(f"  BTC 4h Change: {result['btc_change_4h']*100:.2f}%")
    print(f"  EMA Trend: {result['ema_trend']}")
    print(f"\n  Active Strategies: {', '.join(result['active_strategies']) or 'None'}")

    status = manager.get_status()
    print(f"\n📈 Full Status:")
    print(f"  BTC 1h: {status['btc_change_1h']}")
    print(f"  BTC 4h: {status['btc_change_4h']}")
    print(f"  BTC 24h: {status['btc_change_24h']}")
    print(f"  Volume Trend: {status['volume_trend']}")


async def adaptive_backtest():
    """적응형 백테스트 (시장 추세 기반)"""
    print("=" * 70)
    print("🎯 Adaptive Backtest (Market Regime Based)")
    print("=" * 70)

    # BTC 캔들 데이터로 시장 추세 판단
    print("\nAnalyzing market regime...")
    raw_btc = await fetch_upbit_candles("KRW-BTC", "5", 300)
    btc_candles = parse_candles("KRW-BTC", "5m", raw_btc)

    from src.strategies.market_regime import MarketRegimeDetector, AdaptiveStrategyManager

    detector = MarketRegimeDetector()
    manager = AdaptiveStrategyManager(detector)
    result = manager.update(btc_candles)

    print(f"\n  Market Regime: {result['regime'].upper()}")
    print(f"  Confidence: {result['confidence']*100:.1f}%")
    print(f"  Recommended Strategies: {', '.join(result['active_strategies'])}")

    # 활성화된 전략만 백테스트
    active_strategies = result['active_strategies']
    if not active_strategies:
        print("\n  No strategies recommended for current market condition.")
        return

    print(f"\n  Running backtest for: {', '.join(active_strategies)}")

    # 모든 KRW 심볼 가져오기
    symbols = await get_all_krw_symbols()
    print(f"  Testing with {len(symbols)} symbols")

    # 전략 생성기 매핑
    all_generators = {
        "PULLBACK": create_pullback_generator(),
        "REBOUND": create_rebound_generator(),
        "DIP_SCALPER": create_dip_scalper_generator(),
        "ATTACK": create_attack_generator(),
        "SHORT_BREAKDOWN": create_short_breakdown_generator(),
        "SHORT_RALLY_FADE": create_short_rally_fade_generator(),
    }

    # 활성화된 전략만 필터링
    strategies = {k: v for k, v in all_generators.items() if k in active_strategies}

    results = []
    backtester = SimpleBacktester(initial_capital=1_000_000)

    for strategy_name, generator in strategies.items():
        exit_params = manager.get_exit_params(strategy_name)
        result = await backtester.run_backtest(
            strategy=strategy_name,
            symbols=symbols,
            signal_generator=generator,
            exit_params=exit_params,
        )
        results.append(result)

        print(f"\n📈 {strategy_name} Results:")
        print(f"  Trades: {result['trades']}")
        print(f"  Win Rate: {result['win_rate']:.1f}%")
        print(f"  Total Return: {result['total_return']:.2f}%")
        print(f"  Profit Factor: {result['profit_factor']:.2f}")

        await asyncio.sleep(1)

    # 요약
    if results:
        print("\n" + "=" * 70)
        print("📊 Adaptive Backtest Summary")
        print("=" * 70)
        print(f"{'Strategy':<18} {'Trades':>8} {'Win Rate':>10} {'Return':>10} {'PF':>8}")
        print("-" * 54)
        for r in results:
            print(f"{r['strategy']:<18} {r['trades']:>8} {r['win_rate']:>9.1f}% {r['total_return']:>9.2f}% {r['profit_factor']:>8.2f}")

        total_return = sum(r['total_return'] for r in results) / len(results)
        print("-" * 54)
        print(f"{'Average':<18} {'':<8} {'':<10} {total_return:>9.2f}%")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "--regime":
            asyncio.run(test_market_regime())
        elif sys.argv[1] == "--adaptive":
            asyncio.run(adaptive_backtest())
        else:
            print("Usage:")
            print("  python run_backtest.py           # Full backtest")
            print("  python run_backtest.py --regime  # Test market regime detection")
            print("  python run_backtest.py --adaptive # Adaptive backtest")
    else:
        asyncio.run(main())
