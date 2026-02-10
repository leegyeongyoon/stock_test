"""v3.2 전략 OpenAI 심층 최적화

기존 v3.1 대비 개선:
- 소스코드 전체 (시그널 생성기 + 청산 로직) 제공
- 전체 거래 내역 (심볼, 시간, 진입가, 청산가, 수익률, 청산사유 등)
- 심볼별 성과 분석 (승률, 수익률, 거래수)
- 시간대별 성과 분석
- 데이터 역분석 결과 요약
- 손실 거래 상세 분석

실행: cd server && python -m scripts.run_v3_openai_optimize
"""

import asyncio
import json
import os
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from openai import AsyncOpenAI

from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.models.schemas import BacktestConfig
from src.backtesting.strategies.strategy_adapters import get_signal_generator, get_exit_checker
from src.models.database import async_session

OPENAI_MODEL = "gpt-4o"
MAX_ROUNDS = 3

SYMBOLS = [
    "KRW-ETH", "KRW-IP", "KRW-DOGE", "KRW-USDT", "KRW-SHIB",
    "KRW-VIRTUAL", "KRW-TRX", "KRW-AVNT", "KRW-ETC", "KRW-PEPE",
    "KRW-SAND", "KRW-NEAR", "KRW-BCH", "KRW-MOVE", "KRW-0G",
    "KRW-PENGU", "KRW-HBAR", "KRW-FLOW", "KRW-POL", "KRW-AXS",
    "KRW-LAYER", "KRW-SEI", "KRW-WLD", "KRW-WLFI", "KRW-KAITO",
    "KRW-CTC", "KRW-ME", "KRW-ARB", "KRW-BOUNTY", "KRW-BORA",
    "KRW-FIL", "KRW-WET", "KRW-UNI", "KRW-APT", "KRW-CRO",
    "KRW-TRUMP", "KRW-NOM", "KRW-ANIME", "KRW-A", "KRW-BONK",
    "KRW-WAVES", "KRW-SONIC", "KRW-ATOM", "KRW-DOOD", "KRW-BLUR",
    "KRW-AXL", "KRW-BLAST", "KRW-NEO", "KRW-AERGO", "KRW-ALGO",
    "KRW-MOCA", "KRW-TRUST", "KRW-AAVE", "KRW-CELO", "KRW-AKT",
    "KRW-EGLD", "KRW-LSK", "KRW-WCT", "KRW-W", "KRW-ERA",
    "KRW-IN", "KRW-CARV", "KRW-AGLD", "KRW-PLUME", "KRW-ID",
    "KRW-G", "KRW-NXPC", "KRW-JTO", "KRW-DKA", "KRW-IOTA",
    "KRW-2Z", "KRW-F", "KRW-FF", "KRW-BAT", "KRW-PENDLE",
    "KRW-BIO", "KRW-ARK", "KRW-BEAM", "KRW-CVC", "KRW-ARDR",
    "KRW-JUP", "KRW-THETA", "KRW-WAXP", "KRW-USDC", "KRW-AQT",
    "KRW-CYBER", "KRW-JST", "KRW-TREE", "KRW-WAL", "KRW-IOST",
    "KRW-API3", "KRW-NEWT", "KRW-RVN", "KRW-AWE", "KRW-IQ",
    "KRW-LA", "KRW-LPT", "KRW-HP", "KRW-TOKAMAK", "KRW-T",
    "KRW-ANKR", "KRW-ARKM", "KRW-YGG", "KRW-PUNDIX", "KRW-POWR",
    "KRW-AHT", "KRW-HUNT", "KRW-ALT", "KRW-AERO", "KRW-USDE",
    "KRW-USD1", "KRW-COW", "KRW-BTC",
]

# ─── 소스코드 (OpenAI에 전달) ───

EXIT_LOGIC_CODE = """
def create_pullback_exit_checker(stop_loss_pct, take_profit_pct, trailing_trigger_pct, trailing_stop_pct, time_stop_minutes):
    def check_exit(position, current_price, history_provider):
        entry_price = position.entry_price

        # 1. 타임스탑: 보유시간 >= time_stop_minutes면 청산
        if time_stop_minutes > 0:
            holding_minutes = (current_time - position.entry_time).total_seconds() / 60
            if holding_minutes >= time_stop_minutes:
                return "time_stop"

        # 2. 캔들 내 저가로 SL 체크 (순간 터치)
        low_pnl_pct = (candle.low - entry_price) / entry_price
        if low_pnl_pct <= stop_loss_pct:
            return "stop_loss"

        # 3. 캔들 내 고가로 TP 체크 (순간 터치)
        high_pnl_pct = (candle.high - entry_price) / entry_price
        if high_pnl_pct >= take_profit_pct:
            return "take_profit"

        # 4. 트레일링: max_profit >= trailing_trigger 이후, 고점대비 trailing_stop 하락시
        if position.max_profit_pct >= trailing_trigger_pct:
            drop_from_peak = position.max_profit_pct - current_pnl_pct
            if drop_from_peak >= trailing_stop_pct:
                return "trailing_stop"

        return None  # 유지
    return check_exit
"""

SIGNAL_CODES = {
    "VOLATILE_OVERSOLD_BOUNCE": """
class VolatileOversoldBounceSignalGenerator:
    # 데이터 근거: ATR>1% + RSI<35 + RVOL>1.5 + 양봉 → WR 53.8%, 기대수익 +0.25%/trade
    # v3.1 튜닝: RSI 35→30, RVOL 1.5→2.0 → WR 74.3%, 수익률 +4.90%

    def _evaluate(self, timestamp, history, symbol):
        candles = history.get_candles(symbol, "5m", 220)
        if len(candles) < 200: return None

        current = candles[-1]
        price = current.close

        # 1. 양봉 필수 (close > open)
        if current.close <= current.open: return None

        # 2. RSI(14) < max_rsi
        rsi = calc_rsi(closes, 14)
        if rsi is None or rsi >= self.max_rsi: return None

        # 3. ATR(14)/price*100 >= min_atr_pct
        atr_pct = calc_atr_pct(candles, 14, price)
        if atr_pct is None or atr_pct < self.min_atr_pct: return None

        # 4. RVOL(20봉 평균 대비) >= min_rvol
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol: return None

        # 5. 쿨다운: 같은 심볼 cooldown_minutes 이내 재진입 불가

        score = 60 + min(atr_pct, 3)*5 + (35-rsi)*0.5 + min(rvol, 5)*2
        return signal
""",
    "CRASH_RECOVERY": """
class CrashRecoverySignalGenerator:
    # 데이터 근거: 30분-2%하락 + RVOL>2 + 양봉 + RSI<40 → WR 48.6%, 기대수익 +0.30%/trade
    # v3.1 튜닝: RSI 40→35, RVOL 2.0→2.5 → WR 58.0%, 수익률 +4.19%

    def _evaluate(self, timestamp, history, symbol):
        candles = history.get_candles(symbol, "5m", 30)
        if len(candles) < 8: return None

        current = candles[-1]
        price = current.close

        # 1. 양봉 필수
        if current.close <= current.open: return None

        # 2. 30분(6봉) -min_drop_pct% 이상 하락
        price_6_ago = candles[-7].close
        change_6 = (price - price_6_ago) / price_6_ago
        if change_6 > -self.min_drop_pct: return None

        # 3. RSI(14) < max_rsi (RSI 없으면 통과)
        rsi = calc_rsi(closes, 14)
        if rsi is not None and rsi >= self.max_rsi: return None

        # 4. RVOL(20봉 평균 대비) >= min_rvol
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol: return None

        # 5. 쿨다운

        score = 60 + drop_strength*20 + min(rvol-1, 5)*3
        return signal
""",
    "TRIPLE_BEARISH_REVERSAL": """
class TripleBearishReversalSignalGenerator:
    # 데이터 근거: 3봉연속음봉 + RSI<30 + ATR>1% → WR 52.4%, 기대수익 +0.17%/trade
    # 양봉 조건 없음 (진입 시점이 음봉 마지막일 수 있음)

    def _evaluate(self, timestamp, history, symbol):
        candles = history.get_candles(symbol, "5m", 220)
        if len(candles) < 20: return None

        current = candles[-1]
        price = current.close

        # 1. 직전 3봉 연속 음봉 (close < open)
        bearish_count = sum(1 for j in range(1,4) if candles[-1-j].close < candles[-1-j].open)
        if bearish_count < self.min_bearish: return None

        # 2. RSI(14) < max_rsi
        rsi = calc_rsi(closes, 14)
        if rsi is None or rsi >= self.max_rsi: return None

        # 3. ATR(14)/price*100 >= min_atr_pct
        atr_pct = calc_atr_pct(candles, 14, price)
        if atr_pct is None or atr_pct < self.min_atr_pct: return None

        # 4. 쿨다운
        # 주의: 양봉 조건 없음!

        score = 60 + bearish_count*5 + (30-rsi)*0.5 + min(atr_pct, 3)*5
        return signal
""",
}

# ─── 데이터 역분석 결과 요약 ───

DATA_ANALYSIS_SUMMARY = """
## 데이터 역분석 결과 (30일 5분봉 245,656건)

### 핵심 발견
- 전체 베이스라인 승률: TP+2% SL-2% 기준 11.8% (매우 낮음)
- 수수료: 왕복 0.1% (매수 0.05% + 매도 0.05%)
- 슬리피지: 5bps (0.05%)
- TP/SL 1:1 (±2%) + 180분이 대부분 패턴에서 최적 (R:R 높이면 승률 급락)
- ATR% > 1%가 수익의 가장 강력한 예측자
- 트레일링 스탑 활성화(trailing_trigger < TP)는 수익을 깎음 → 비활성화가 최적

### 패턴별 최적 TP/SL (역분석 검증)
| 패턴 | 최적 TP/SL | WR | 기대수익/건 |
|------|-----------|-----|-----------|
| ATR>1% + RSI<35 + RVOL>1.5 + 양봉 | TP+2% SL-2% 180min | 53.8% | +0.25% |
| 30분-2%하락 + RVOL>2 + 양봉 + RSI<40 | TP+2% SL-1.5% 120min | 48.6% | +0.30% |
| SMA20 -2%아래 + RVOL>2 + 양봉 | TP+2% SL-2% 180min | 56.0% | +0.34% |
| 3봉연속음봉 + RSI<30 + ATR>1% | TP+2% SL-2% 180min | 52.4% | +0.17% |
| ATR>1.5% + RSI<30 + RVOL>2 + 양봉 | TP+2% SL-2% 180min | 74.4% | +0.89% (소표본 39건) |

### 중요 교훈
- SL을 타이트하게(-1.5% → -1.2%) 하면 5분봉 노이즈에 걸려서 승률↓ 수익률↓
- SL을 넓게(-2.5%) 하면 손실 거래당 금액이 커져서 수익률↓
- TP를 높게(+3%) 하면 도달률이 급격히 떨어져서 time_stop 청산↑
- 쿨다운이 너무 짧으면(15분) 같은 하락파에서 반복 진입 → 연쇄 손실
- RVOL 필터가 가장 효과적인 노이즈 제거기
"""

# ─── 전략 정의 ───

STRATEGIES = {
    "VOLATILE_OVERSOLD_BOUNCE": {
        "params": {
            "stop_loss_pct": -0.020,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.020,
            "trailing_stop_pct": 0.010,
            "time_stop_minutes": 180,
            "min_atr_pct": 1.0,
            "max_rsi": 30,
            "min_rvol": 2.0,
            "cooldown_minutes": 60,
        },
        "param_descriptions": {
            "stop_loss_pct": "손절 비율 (음수, -0.020 = -2.0%). 캔들 저가가 이 수준 이하면 즉시 청산",
            "take_profit_pct": "익절 비율 (+0.020 = +2.0%). 캔들 고가가 이 수준 이상이면 즉시 청산",
            "trailing_trigger_pct": "트레일링 시작 수익률. TP와 같으면(0.020) 트레일링 비활성화. TP보다 낮으면 활성화",
            "trailing_stop_pct": "트레일링 손절폭. 고점 대비 이 만큼 하락시 청산",
            "time_stop_minutes": "최대 보유시간(분). TP/SL 도달 못하면 이 시간 후 시장가 청산",
            "min_atr_pct": "최소 ATR% (변동성 필터). ATR(14)/price*100이 이 값 이상이어야 진입",
            "max_rsi": "최대 RSI. RSI(14)가 이 값 미만이어야 진입 (과매도 필터)",
            "min_rvol": "최소 상대거래량. 최근 20봉 평균 거래량 대비 배수. 높을수록 거래량 폭발 요구",
            "cooldown_minutes": "같은 심볼 재진입 대기시간(분). 짧으면 같은 하락파 반복진입 위험",
        },
    },
    "CRASH_RECOVERY": {
        "params": {
            "stop_loss_pct": -0.015,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.020,
            "trailing_stop_pct": 0.010,
            "time_stop_minutes": 120,
            "min_drop_pct": 0.02,
            "max_rsi": 35,
            "min_rvol": 2.5,
            "cooldown_minutes": 45,
        },
        "param_descriptions": {
            "stop_loss_pct": "손절 비율 (음수, -0.015 = -1.5%)",
            "take_profit_pct": "익절 비율 (+0.020 = +2.0%)",
            "trailing_trigger_pct": "트레일링 시작 수익률 (TP와 같으면 비활성화)",
            "trailing_stop_pct": "트레일링 손절폭",
            "time_stop_minutes": "최대 보유시간(분)",
            "min_drop_pct": "최소 30분 하락폭 (0.02 = 2%). 6봉전 대비 현재가 변화율이 이 값 이상 하락해야 진입",
            "max_rsi": "최대 RSI (과매도 필터)",
            "min_rvol": "최소 상대거래량 배수",
            "cooldown_minutes": "재진입 대기시간(분)",
        },
    },
    "TRIPLE_BEARISH_REVERSAL": {
        "params": {
            "stop_loss_pct": -0.020,
            "take_profit_pct": 0.020,
            "trailing_trigger_pct": 0.020,
            "trailing_stop_pct": 0.010,
            "time_stop_minutes": 180,
            "max_rsi": 30,
            "min_atr_pct": 1.0,
            "min_bearish": 3,
            "cooldown_minutes": 30,
        },
        "param_descriptions": {
            "stop_loss_pct": "손절 비율 (음수, -0.020 = -2.0%)",
            "take_profit_pct": "익절 비율 (+0.020 = +2.0%)",
            "trailing_trigger_pct": "트레일링 시작 수익률 (TP와 같으면 비활성화)",
            "trailing_stop_pct": "트레일링 손절폭",
            "time_stop_minutes": "최대 보유시간(분)",
            "max_rsi": "최대 RSI (과매도 필터). 양봉 조건 없음 주의",
            "min_atr_pct": "최소 ATR%",
            "min_bearish": "직전 3봉 중 최소 음봉 수 (3이면 전부 음봉)",
            "cooldown_minutes": "재진입 대기시간(분)",
        },
    },
}


async def run_backtest(strategy: str, params: dict) -> dict:
    """단일 전략 백테스트 + 심층 분석"""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30)

    config = BacktestConfig(
        strategy=strategy, symbols=SYMBOLS,
        start_date=start_date, end_date=end_date,
        interval="5m", initial_capital=1_000_000,
        parameters=params,
    )

    async with async_session() as db:
        loader = BacktestDataLoader(db)
        engine = BacktestEngine(config, loader)
        signal_gen = get_signal_generator(strategy, params)
        exit_chk = get_exit_checker(strategy, params)
        result = await engine.run(signal_gen, exit_chk)

        # ─── 1. 기본 메트릭 ───
        metrics = {
            "return_pct": result.metrics.total_return_pct,
            "win_rate": result.metrics.win_rate,
            "trades": result.metrics.total_trades,
            "pf": result.metrics.profit_factor or 0,
            "sharpe": result.metrics.sharpe_ratio or 0,
            "mdd": result.metrics.max_drawdown_pct,
            "avg_win": result.metrics.avg_win_pct or 0,
            "avg_loss": result.metrics.avg_loss_pct or 0,
        }

        # ─── 2. 청산사유별 분석 ───
        exit_reasons = {}
        for t in result.trades:
            r = t.exit_reason
            if r not in exit_reasons:
                exit_reasons[r] = {"count": 0, "wins": [], "losses": []}
            exit_reasons[r]["count"] += 1
            if t.pnl > 0:
                exit_reasons[r]["wins"].append(t.pnl_pct * 100)
            else:
                exit_reasons[r]["losses"].append(t.pnl_pct * 100)

        reason_analysis = {}
        for r, data in exit_reasons.items():
            reason_analysis[r] = {
                "count": data["count"],
                "win_count": len(data["wins"]),
                "loss_count": len(data["losses"]),
                "avg_win_pct": sum(data["wins"]) / len(data["wins"]) if data["wins"] else 0,
                "avg_loss_pct": sum(data["losses"]) / len(data["losses"]) if data["losses"] else 0,
                "total_pnl_pct": sum(data["wins"]) + sum(data["losses"]),
            }

        # ─── 3. 심볼별 성과 분석 ───
        symbol_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl_sum": 0.0, "pnl_list": []})
        for t in result.trades:
            s = symbol_stats[t.symbol]
            s["trades"] += 1
            if t.pnl > 0:
                s["wins"] += 1
            s["pnl_sum"] += t.pnl_pct * 100
            s["pnl_list"].append(t.pnl_pct * 100)

        symbol_analysis = {}
        for sym, s in sorted(symbol_stats.items(), key=lambda x: x[1]["pnl_sum"]):
            symbol_analysis[sym] = {
                "trades": s["trades"],
                "win_rate": (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0,
                "total_pnl_pct": round(s["pnl_sum"], 2),
                "avg_pnl_pct": round(s["pnl_sum"] / s["trades"], 2) if s["trades"] > 0 else 0,
            }

        # ─── 4. 시간대별 성과 ───
        hour_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl_sum": 0.0})
        for t in result.trades:
            h = t.entry_time.hour
            hour_stats[h]["trades"] += 1
            if t.pnl > 0:
                hour_stats[h]["wins"] += 1
            hour_stats[h]["pnl_sum"] += t.pnl_pct * 100

        hour_analysis = {}
        for h in sorted(hour_stats.keys()):
            s = hour_stats[h]
            hour_analysis[f"{h:02d}시"] = {
                "trades": s["trades"],
                "win_rate": round(s["wins"] / s["trades"] * 100, 1) if s["trades"] > 0 else 0,
                "total_pnl_pct": round(s["pnl_sum"], 2),
            }

        # ─── 5. 전체 거래 내역 (OpenAI 전달용) ───
        all_trades = []
        for t in result.trades:
            all_trades.append({
                "symbol": t.symbol,
                "entry_time": t.entry_time.strftime("%m/%d %H:%M"),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl_pct": round(t.pnl_pct * 100, 2),
                "exit_reason": t.exit_reason,
                "holding_min": t.holding_minutes,
                "max_profit_pct": round(t.max_profit_pct * 100, 2),
                "max_dd_pct": round(t.max_drawdown_pct * 100, 2),
                "indicators": t.entry_indicators,
            })

        # ─── 6. 손실 거래 상세 ───
        loss_trades = sorted(
            [t for t in all_trades if t["pnl_pct"] < 0],
            key=lambda x: x["pnl_pct"]
        )

        # ─── 7. 수익 거래 상세 ───
        win_trades = sorted(
            [t for t in all_trades if t["pnl_pct"] > 0],
            key=lambda x: -x["pnl_pct"]
        )

        # ─── 8. 연속 손실 분석 ───
        max_consecutive_loss = 0
        current_streak = 0
        streak_details = []
        for t in all_trades:
            if t["pnl_pct"] < 0:
                current_streak += 1
                if current_streak > max_consecutive_loss:
                    max_consecutive_loss = current_streak
            else:
                if current_streak >= 3:
                    streak_details.append(f"{current_streak}연패")
                current_streak = 0

        metrics["exit_reasons"] = exit_reasons
        metrics["reason_analysis"] = reason_analysis
        metrics["symbol_analysis"] = symbol_analysis
        metrics["hour_analysis"] = hour_analysis
        metrics["all_trades"] = all_trades
        metrics["loss_trades"] = loss_trades
        metrics["win_trades"] = win_trades
        metrics["max_consecutive_loss"] = max_consecutive_loss
        metrics["streak_details"] = streak_details

        return metrics


def build_prompt(strategy_name: str, strategy_info: dict, params: dict,
                 result: dict, round_num: int, history: list) -> str:
    """심층 OpenAI 프롬프트 생성"""

    # ─── 이전 라운드 기록 ───
    history_text = ""
    if history:
        history_text = "\n## 이전 최적화 라운드 기록\n"
        for h in history:
            history_text += (
                f"- 라운드 {h['round']}: 수익률 {h['return_pct']:+.2f}%, "
                f"승률 {h['win_rate']:.1f}%, 거래 {h['trades']}건, PF {h['pf']:.2f}\n"
            )
            if h.get("changes"):
                history_text += f"  변경: {h['changes']}\n"
            if h.get("reverted"):
                history_text += f"  → 실패하여 원복됨. 원인: {h['revert_reason']}\n"
            if h.get("applied"):
                history_text += f"  → 성공! 적용됨\n"

    # ─── 청산사유 분석 ───
    reason_text = ""
    for r, info in result.get("reason_analysis", {}).items():
        reason_text += (
            f"  {r}: {info['count']}건 "
            f"(승{info['win_count']} 패{info['loss_count']}, "
            f"평균수익 {info['avg_win_pct']:+.2f}%, 평균손실 {info['avg_loss_pct']:+.2f}%, "
            f"합산 {info['total_pnl_pct']:+.2f}%)\n"
        )

    # ─── 심볼별 분석 (손실 심볼 위주) ───
    sym_text = "### 심볼별 성과 (전체)\n"
    sym_data = result.get("symbol_analysis", {})
    losing_syms = {k: v for k, v in sym_data.items() if v["total_pnl_pct"] < 0}
    winning_syms = {k: v for k, v in sym_data.items() if v["total_pnl_pct"] >= 0}

    sym_text += f"수익 심볼: {len(winning_syms)}개 | 손실 심볼: {len(losing_syms)}개\n\n"
    sym_text += "손실 심볼 상세:\n"
    for sym, s in sorted(losing_syms.items(), key=lambda x: x[1]["total_pnl_pct"]):
        sym_text += f"  {sym}: {s['trades']}건, 승률 {s['win_rate']:.0f}%, 합산 {s['total_pnl_pct']:+.2f}%, 평균 {s['avg_pnl_pct']:+.2f}%\n"
    sym_text += "\n수익 심볼 상세:\n"
    for sym, s in sorted(winning_syms.items(), key=lambda x: -x[1]["total_pnl_pct"]):
        sym_text += f"  {sym}: {s['trades']}건, 승률 {s['win_rate']:.0f}%, 합산 {s['total_pnl_pct']:+.2f}%, 평균 {s['avg_pnl_pct']:+.2f}%\n"

    # ─── 시간대별 분석 ───
    hour_text = "### 시간대별 성과\n"
    for h, s in result.get("hour_analysis", {}).items():
        status = "+" if s["total_pnl_pct"] > 0 else ""
        hour_text += f"  {h}: {s['trades']}건, 승률 {s['win_rate']:.0f}%, 합산 {status}{s['total_pnl_pct']:.2f}%\n"

    # ─── 전체 거래 내역 ───
    trades_text = "### 전체 거래 내역\n"
    trades_text += "심볼 | 진입시간 | 진입가 | 청산가 | 수익률 | 사유 | 보유(분) | 최대수익 | 최대DD | 진입지표\n"
    trades_text += "-" * 120 + "\n"
    for t in result.get("all_trades", []):
        indicators_str = ""
        if t.get("indicators"):
            ind = t["indicators"]
            parts = []
            for k, v in ind.items():
                if isinstance(v, float):
                    parts.append(f"{k}={v:.2f}")
                else:
                    parts.append(f"{k}={v}")
            indicators_str = " ".join(parts)
        trades_text += (
            f"{t['symbol']:12s} | {t['entry_time']} | {t['entry_price']:>12.4f} | "
            f"{t['exit_price']:>12.4f} | {t['pnl_pct']:+6.2f}% | {t['exit_reason']:13s} | "
            f"{t['holding_min']:>4.0f}분 | {t['max_profit_pct']:+5.2f}% | {t['max_dd_pct']:+5.2f}% | "
            f"{indicators_str}\n"
        )

    # ─── 손실 패턴 분석 ───
    loss_pattern_text = "### 손실 거래 패턴 분석\n"
    loss_trades = result.get("loss_trades", [])
    if loss_trades:
        loss_pattern_text += f"총 손실거래: {len(loss_trades)}건\n"
        sl_losses = [t for t in loss_trades if t["exit_reason"] == "stop_loss"]
        ts_losses = [t for t in loss_trades if t["exit_reason"] == "time_stop"]
        trl_losses = [t for t in loss_trades if t["exit_reason"] == "trailing_stop"]

        if sl_losses:
            avg_max_profit = sum(t["max_profit_pct"] for t in sl_losses) / len(sl_losses)
            loss_pattern_text += (
                f"\nstop_loss 손실: {len(sl_losses)}건\n"
                f"  → 이 거래들의 평균 최대수익(손절 전 고점): {avg_max_profit:+.2f}%\n"
                f"  → 고점 경험 후 손절된 비율: {sum(1 for t in sl_losses if t['max_profit_pct'] > 0.5)/len(sl_losses)*100:.0f}%\n"
            )
        if ts_losses:
            avg_pnl = sum(t["pnl_pct"] for t in ts_losses) / len(ts_losses)
            loss_pattern_text += (
                f"\ntime_stop 손실: {len(ts_losses)}건, 평균손실: {avg_pnl:+.2f}%\n"
            )
        if trl_losses:
            avg_pnl = sum(t["pnl_pct"] for t in trl_losses) / len(trl_losses)
            loss_pattern_text += (
                f"\ntrailing_stop 손실: {len(trl_losses)}건, 평균손실: {avg_pnl:+.2f}%\n"
            )

        loss_pattern_text += f"\n최대 연속 손실: {result.get('max_consecutive_loss', 0)}연패\n"
        if result.get("streak_details"):
            loss_pattern_text += f"3연패 이상: {', '.join(result['streak_details'])}\n"

    # ─── 프롬프트 조립 ───
    prompt = f"""# 전략 최적화: {strategy_name} (라운드 {round_num}/{MAX_ROUNDS})

{DATA_ANALYSIS_SUMMARY}

## 청산 로직 소스코드
```python
{EXIT_LOGIC_CODE}
```

## 시그널 생성기 소스코드
```python
{SIGNAL_CODES[strategy_name]}
```

## 현재 파라미터
```json
{json.dumps(params, indent=2, ensure_ascii=False)}
```

## 파라미터 설명
{json.dumps(strategy_info['param_descriptions'], indent=2, ensure_ascii=False)}

## 백테스트 결과 (30일, {len(SYMBOLS)}심볼, 5분봉)
- 수익률: {result['return_pct']:+.2f}%
- 승률: {result['win_rate']:.1f}%
- 거래수: {result['trades']}건
- PF: {result['pf']:.2f}
- Sharpe: {result['sharpe']:.2f}
- MDD: {result['mdd']:.2f}%
- 평균수익: {result['avg_win']:+.2f}% / 평균손실: {result['avg_loss']:+.2f}%
- 수수료: 왕복 0.1% (0.05% × 2) + 슬리피지 5bps

## 청산 사유별 분석
{reason_text}

{sym_text}

{hour_text}

{loss_pattern_text}

{trades_text}
{history_text}

## 요청
위의 **소스코드, 전체 거래 내역, 심볼별/시간대별 분석, 데이터 역분석 결과**를 종합적으로 분석하세요.

단순 파라미터 수치 조정뿐 아니라, 다음을 고려하세요:
1. 특정 심볼의 반복 손실 → 필터링 또는 조건 강화
2. 특정 시간대 손실 집중 → 시간 필터
3. 손절 전 고점이 높았던 거래 → 트레일링 활성화 검토
4. 진입 지표(RSI, ATR, RVOL)의 분포와 승패 관계
5. 연속 손실 패턴 → 쿨다운 조정
6. 새로운 진입 조건 추가 제안 (코드 변경이 필요한 경우 구체적 로직 포함)

**중요**: 파라미터만 수정할 경우 params에만 값을 넣고, 시그널 로직 변경이 필요하면 code_changes에 구체적으로 기술하세요.

반드시 아래 JSON 형식으로만 응답하세요:
```json
{{
  "analysis": "데이터 기반 상세 분석 (3-5문장)",
  "changes": "변경 사항 요약 (1문장)",
  "reasoning": "변경 이유와 기대 효과 (2-3문장)",
  "params": {{
    // 수정된 전체 파라미터 (기존과 동일한 키 구조)
  }},
  "code_changes": "시그널 로직 변경이 필요한 경우 구체적 pseudo-code. 파라미터만 수정이면 null"
}}
```"""

    return prompt


async def ask_openai(client: AsyncOpenAI, prompt: str) -> dict:
    """OpenAI에 질의하고 JSON 응답 파싱"""
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 암호화폐 5분봉 단타 트레이딩 전략 최적화 전문가입니다. "
                    "업비트(Upbit) 거래소 데이터 기반 백테스트 결과를 분석하고, "
                    "실제 소스코드와 전체 거래 내역을 보고 구조적 개선을 합니다. "
                    "이론적 일반론이 아닌, 데이터에서 보이는 구체적 패턴 기반으로만 제안합니다. "
                    "반드시 JSON 형식으로만 응답하세요."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=4000,
    )

    text = response.choices[0].message.content.strip()

    # JSON 추출
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    return json.loads(text)


async def optimize_single_strategy(
    client: AsyncOpenAI,
    strategy_name: str,
    strategy_info: dict,
):
    """단일 전략 3라운드 심층 최적화"""
    print(f"\n{'=' * 100}")
    print(f"  전략: {strategy_name}")
    print(f"{'=' * 100}")

    current_params = deepcopy(strategy_info["params"])
    best_params = deepcopy(current_params)
    best_return = None
    history = []

    for round_num in range(1, MAX_ROUNDS + 1):
        print(f"\n  {'─' * 90}")
        print(f"  라운드 {round_num}/{MAX_ROUNDS}")
        print(f"  {'─' * 90}")

        # 1. 백테스트
        print(f"  [1/4] 백테스트 실행 중...")
        result = await run_backtest(strategy_name, current_params)

        status = "PASS" if result['return_pct'] > 0 else "FAIL"
        print(
            f"  [{status}] 수익률: {result['return_pct']:+.2f}% | "
            f"승률: {result['win_rate']:.1f}% | "
            f"거래: {result['trades']}건 | PF: {result['pf']:.2f} | "
            f"MDD: {result['mdd']:.2f}%"
        )

        reasons = " | ".join(
            f"{k}: {v['count']}" for k, v in sorted(
                result['reason_analysis'].items(), key=lambda x: -x[1]['count']
            )
        )
        print(f"       청산: {reasons}")

        sym_data = result.get("symbol_analysis", {})
        losing_count = sum(1 for v in sym_data.values() if v["total_pnl_pct"] < 0)
        print(f"       심볼: 수익 {len(sym_data)-losing_count}개 / 손실 {losing_count}개")
        print(f"       연속손실: 최대 {result.get('max_consecutive_loss', 0)}연패")

        if best_return is None:
            best_return = result['return_pct']
            print(f"  → 기준 수익률: {best_return:+.2f}%")

        # 2. OpenAI 질의
        print(f"\n  [2/4] OpenAI 심층 분석 중 (거래 {result['trades']}건 전달)...")
        prompt = build_prompt(
            strategy_name, strategy_info, current_params,
            result, round_num, history,
        )
        prompt_size = len(prompt)
        print(f"       프롬프트 크기: {prompt_size:,}자")

        try:
            openai_response = await ask_openai(client, prompt)
        except Exception as e:
            print(f"  [ERROR] OpenAI 응답 실패: {e}")
            history.append({
                "round": round_num,
                "return_pct": result['return_pct'],
                "win_rate": result['win_rate'],
                "trades": result['trades'],
                "pf": result['pf'],
                "changes": f"OpenAI 오류: {str(e)[:100]}",
            })
            continue

        print(f"\n  [3/4] OpenAI 분석 결과:")
        print(f"  분석: {openai_response.get('analysis', 'N/A')}")
        print(f"  변경: {openai_response.get('changes', 'N/A')}")
        print(f"  이유: {openai_response.get('reasoning', 'N/A')}")
        if openai_response.get("code_changes"):
            print(f"  코드변경 제안: {openai_response['code_changes'][:200]}...")

        new_params = openai_response.get("params", {})
        if not new_params:
            print(f"  [SKIP] 파라미터 제시 없음")
            continue

        # 변경 비교
        changes = []
        for k in current_params:
            if k in new_params and new_params[k] != current_params[k]:
                changes.append(f"{k}: {current_params[k]} → {new_params[k]}")
        print(f"  파라미터 변경: {' | '.join(changes) if changes else '변경 없음'}")

        if not changes:
            print(f"  [SKIP] 실질적 변경 없음")
            history.append({
                "round": round_num,
                "return_pct": result['return_pct'],
                "win_rate": result['win_rate'],
                "trades": result['trades'],
                "pf": result['pf'],
                "changes": "변경 없음",
            })
            continue

        # 3. 새 파라미터 백테스트
        print(f"\n  [4/4] 새 파라미터로 백테스트 실행 중...")
        new_result = await run_backtest(strategy_name, new_params)

        new_status = "PASS" if new_result['return_pct'] > 0 else "FAIL"
        print(
            f"  [{new_status}] 수익률: {new_result['return_pct']:+.2f}% | "
            f"승률: {new_result['win_rate']:.1f}% | "
            f"거래: {new_result['trades']}건 | PF: {new_result['pf']:.2f}"
        )

        # 4. 비교
        improved = new_result['return_pct'] > best_return

        if improved:
            diff = new_result['return_pct'] - best_return
            print(f"\n  >>> 개선! {best_return:+.2f}% → {new_result['return_pct']:+.2f}% (+{diff:.2f}%p)")
            best_return = new_result['return_pct']
            best_params = deepcopy(new_params)
            current_params = deepcopy(new_params)
            history.append({
                "round": round_num,
                "return_pct": new_result['return_pct'],
                "win_rate": new_result['win_rate'],
                "trades": new_result['trades'],
                "pf": new_result['pf'],
                "changes": " | ".join(changes),
                "applied": True,
            })
        else:
            revert_reason = (
                f"수익률 {best_return:+.2f}%→{new_result['return_pct']:+.2f}%, "
                f"승률 {result['win_rate']:.1f}%→{new_result['win_rate']:.1f}%, "
                f"거래 {result['trades']}→{new_result['trades']}"
            )
            print(f"\n  >>> 성능 저하 → 원복")
            print(f"      원인: {revert_reason}")
            current_params = deepcopy(best_params)
            history.append({
                "round": round_num,
                "return_pct": new_result['return_pct'],
                "win_rate": new_result['win_rate'],
                "trades": new_result['trades'],
                "pf": new_result['pf'],
                "changes": " | ".join(changes),
                "reverted": True,
                "revert_reason": revert_reason,
            })

    # 최종 결과
    print(f"\n  {'=' * 90}")
    print(f"  [{strategy_name}] 최종 결과")
    print(f"  {'=' * 90}")
    print(f"  최적 수익률: {best_return:+.2f}%")
    print(f"  최적 파라미터:")
    for k, v in best_params.items():
        original = strategy_info["params"].get(k)
        changed = " (변경됨)" if v != original else ""
        print(f"    {k}: {v}{changed}")

    return {
        "strategy": strategy_name,
        "best_return": best_return,
        "best_params": best_params,
        "original_params": strategy_info["params"],
        "history": history,
    }


async def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        return

    client = AsyncOpenAI(api_key=api_key)

    print("\n" + "=" * 100)
    print("  v3.2 전략 OpenAI 심층 최적화")
    print("  소스코드 + 전체거래내역 + 심볼별/시간대별 분석 제공")
    print("=" * 100)
    print(f"  모델: {OPENAI_MODEL} | 라운드: {MAX_ROUNDS}회 | 30일 백테스트")
    print(f"  심볼: {len(SYMBOLS)}개 | 수수료: 0.05%×2 | 슬리피지: 5bps")
    print("=" * 100)

    all_results = []
    baseline = {"VOLATILE_OVERSOLD_BOUNCE": 4.90, "CRASH_RECOVERY": 4.19, "TRIPLE_BEARISH_REVERSAL": 2.11}

    for strategy_name, strategy_info in STRATEGIES.items():
        result = await optimize_single_strategy(client, strategy_name, strategy_info)
        all_results.append(result)

    # ─── 전체 요약 ───
    print(f"\n\n{'=' * 100}")
    print("  전체 최적화 결과 요약")
    print(f"{'=' * 100}")

    total_before = sum(baseline.values())
    total_after = 0.0

    for r in all_results:
        total_after += r['best_return']
        before = baseline.get(r['strategy'], 0)
        diff = r['best_return'] - before
        improved_rounds = len([h for h in r['history'] if h.get('applied')])

        print(f"\n  {r['strategy']}:")
        print(f"    v3.1: {before:+.2f}% → 최적화 후: {r['best_return']:+.2f}% ({diff:+.2f}%p)")
        print(f"    개선 적용: {improved_rounds}/{MAX_ROUNDS} 라운드")

        # 변경된 파라미터 표시
        changed = []
        for k, v in r['best_params'].items():
            if v != r['original_params'].get(k):
                changed.append(f"{k}: {r['original_params'][k]} → {v}")
        if changed:
            print(f"    변경된 파라미터: {' | '.join(changed)}")
        else:
            print(f"    변경 없음 (기존 파라미터 최적)")

    print(f"\n  {'─' * 90}")
    print(f"  v3.1 합산: {total_before:+.2f}% → 최적화 후: {total_after:+.2f}% ({total_after - total_before:+.2f}%p)")
    print(f"  진행: v27(-258%) → v2.1(-49.65%) → v3.0(-5.47%) → v3.1(+11.20%) → v3.2({total_after:+.2f}%)")

    # 결과 저장
    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "model": OPENAI_MODEL,
        "rounds": MAX_ROUNDS,
        "results": all_results,
        "summary": {
            "before": total_before,
            "after": total_after,
            "diff": total_after - total_before,
        },
    }
    output_path = "reports/analysis/v3_optimization_results.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  결과 저장: {output_path}")
    print(f"\n  완료!\n")


if __name__ == "__main__":
    asyncio.run(main())
