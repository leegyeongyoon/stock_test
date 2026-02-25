"""v4 라이브 전략 모듈 (AI 최적화 결과)

AI Discovery Engine에서 생성/검증된 전략을 라이브 엔진용으로 변환.
V3BaseStrategy 상속, scan/check_exit 인터페이스 구현.

사용법:
1. run_ai_optimizer 실행 → ai_optimization_results.json 생성
2. 최적 전략의 파라미터와 코드를 이 파일에 반영
3. engine/core.py에서 v4 전략 로드

현재: AI 최적화 결과 반영 전 (placeholder)
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

from src.strategies.v3_live import V3BaseStrategy, V3Position, V3Signal

logger = structlog.get_logger()


class V4StrategyLoader:
    """AI 생성 전략 로더

    reports/analysis/ai_optimization_results.json에서 전략 정보 로드.
    generated/ 디렉토리의 전략 코드를 동적 로드.
    """

    RESULTS_PATH = Path("reports/analysis/ai_optimization_results.json")
    GENERATED_DIR = Path("src/strategies/generated")

    @classmethod
    def load_results(cls) -> Optional[dict]:
        """최적화 결과 로드"""
        if not cls.RESULTS_PATH.exists():
            logger.warning("No AI optimization results found", path=str(cls.RESULTS_PATH))
            return None

        with open(cls.RESULTS_PATH) as f:
            return json.load(f)

    @classmethod
    def get_selected_strategies(cls) -> list[dict]:
        """선별된 전략 목록"""
        results = cls.load_results()
        if not results:
            return []
        return results.get("selected_strategies", [])

    @classmethod
    def get_ensemble(cls) -> list[dict]:
        """앙상블 멤버 목록"""
        results = cls.load_results()
        if not results:
            return []
        return results.get("ensemble", {}).get("members", [])


class V4DynamicStrategy(V3BaseStrategy):
    """AI 생성 전략의 라이브 래퍼

    BacktestEngine용 signal_generator를 라이브 scan() 인터페이스로 변환.
    """

    def __init__(
        self,
        name: str,
        signal_func: callable,
        params: dict,
        cooldown_minutes: int = 60,
    ):
        super().__init__(name, cooldown_minutes=cooldown_minutes)
        self._signal_func = signal_func
        self._params = params

        # exit 파라미터
        self.SL_PCT = params.get("stop_loss_pct", -0.02)
        self.TP_PCT = params.get("take_profit_pct", 0.02)
        self.TRAIL_TRIGGER = params.get("trailing_trigger_pct", 0.02)
        self.TRAIL_STOP = params.get("trailing_stop_pct", 0.01)
        self.TIME_STOP_MIN = params.get("time_stop_minutes", 180)
        self.POSITION_PCT = params.get("position_pct", 0.10)

    def scan(
        self,
        symbol: str,
        market_data: dict,
        candle_manager,
        now: datetime,
    ) -> Optional[V3Signal]:
        """라이브 스캔 (candle_manager → HistoryProvider 어댑터)"""
        if not self._check_cooldown(symbol, now):
            return None

        price = market_data.get("price", 0)
        if price <= 0:
            return None

        # candle_manager를 간이 HistoryProvider처럼 사용
        adapter = _CandleManagerAdapter(candle_manager)

        try:
            signals = self._signal_func(now, adapter, [symbol])
        except Exception as e:
            logger.error("V4 signal generation failed", strategy=self.name, symbol=symbol, error=str(e))
            return None

        if not signals:
            return None

        # 첫 번째 시그널 변환
        sig = signals[0]
        score = sig.get("score", 60)

        self.record_entry(symbol, now)

        return V3Signal(
            symbol=symbol,
            strategy=self.name,
            score=score,
            entry_price=price,
            position_pct=self.POSITION_PCT,
            indicators=sig.get("indicators", {}),
        )


class _CandleManagerAdapter:
    """CandleManager → HistoryProvider 어댑터

    라이브 candle_manager의 인터페이스를 백테스트 HistoryProvider처럼 변환.
    """

    def __init__(self, candle_manager):
        self._cm = candle_manager

    def get_candles(self, symbol: str, interval: str, count: int = 50):
        if self._cm:
            return self._cm.get_candles(symbol, interval, count)
        return []

    def get_closes(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        if self._cm:
            return self._cm.get_closes(symbol, interval, count)
        return []

    def get_highs(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        if self._cm:
            return self._cm.get_highs(symbol, interval, count)
        return []

    def get_lows(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        if self._cm:
            return self._cm.get_lows(symbol, interval, count)
        return []

    def get_volumes(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        if self._cm:
            return self._cm.get_volumes(symbol, interval, count)
        return []

    def get_current_price(self, symbol: str, interval: str = "5m") -> Optional[float]:
        if self._cm:
            closes = self._cm.get_closes(symbol, interval, 1)
            return closes[-1] if closes else None
        return None

    def calc_sma(self, symbol: str, interval: str, period: int) -> Optional[float]:
        closes = self.get_closes(symbol, interval, period)
        if len(closes) < period:
            return None
        return sum(closes[-period:]) / period

    def calc_ema(self, symbol: str, interval: str, period: int) -> Optional[float]:
        closes = self.get_closes(symbol, interval, period + 10)
        if len(closes) < period:
            return None
        multiplier = 2 / (period + 1)
        ema = closes[0]
        for close in closes[1:]:
            ema = (close - ema) * multiplier + ema
        return ema

    def calc_rvol(self, symbol: str, interval: str, period: int = 20) -> Optional[float]:
        volumes = self.get_volumes(symbol, interval, period + 1)
        if len(volumes) < period + 1:
            return None
        current = volumes[-1]
        avg = sum(volumes[:-1]) / len(volumes[:-1])
        return current / avg if avg > 0 else None

    def calc_atr(self, symbol: str, interval: str, period: int = 14) -> Optional[float]:
        candles = self.get_candles(symbol, interval, period + 1)
        if len(candles) < period + 1:
            return None
        true_ranges = []
        for i in range(1, len(candles)):
            c = candles[i]
            pc = candles[i - 1]
            h = c.high if hasattr(c, "high") else c.get("high", 0)
            lo = c.low if hasattr(c, "low") else c.get("low", 0)
            prev_close = pc.close if hasattr(pc, "close") else pc.get("close", 0)
            tr = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
            true_ranges.append(tr)
        return sum(true_ranges[-period:]) / min(len(true_ranges), period) if true_ranges else None


# === V4 전략 로드 ===

_v4_strategies: Optional[list[V3BaseStrategy]] = None


def get_v4_strategies() -> list[V3BaseStrategy]:
    """v4 전략 인스턴스 목록 반환 (싱글톤)

    AI 최적화 결과가 없으면 빈 리스트 반환.
    결과가 있으면 동적으로 전략 로드.
    """
    global _v4_strategies
    if _v4_strategies is not None:
        return _v4_strategies

    _v4_strategies = []

    results = V4StrategyLoader.load_results()
    if not results:
        logger.info("No v4 strategies available (run ai_optimizer first)")
        return _v4_strategies

    selected = results.get("selected_strategies", [])

    for strategy_info in selected[:6]:  # 최대 6개
        name = strategy_info.get("name", "Unknown")
        params = strategy_info.get("parameters", {})

        # generated/ 디렉토리에서 코드 로드 시도
        filename = f"{name.lower().replace(' ', '_')}.py"
        filepath = V4StrategyLoader.GENERATED_DIR / filename

        if filepath.exists():
            try:
                # 동적 코드 로드
                namespace = {}
                with open(filepath) as f:
                    code = f.read()
                exec(compile(code, str(filepath), "exec"), namespace)

                # 시그널 생성기 찾기
                signal_cls = None
                for key, val in namespace.items():
                    if isinstance(val, type) and hasattr(val, "__call__"):
                        signal_cls = val
                        break

                if signal_cls:
                    instance = signal_cls(params)
                    strategy = V4DynamicStrategy(
                        name=name,
                        signal_func=instance,
                        params=params,
                        cooldown_minutes=params.get("cooldown_minutes", 60),
                    )
                    _v4_strategies.append(strategy)
                    logger.info("Loaded v4 strategy", name=name, type=strategy_info.get("type"))

            except Exception as e:
                logger.error("Failed to load v4 strategy", name=name, error=str(e))

    logger.info("V4 strategies loaded", count=len(_v4_strategies))
    return _v4_strategies
