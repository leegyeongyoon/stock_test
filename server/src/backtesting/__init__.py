"""백테스팅 시스템

과거 캔들 데이터로 전략 성능을 테스트하고 파라미터를 최적화하는 시스템

사용법:
    1. 캔들 데이터 수집:
        POST /api/backtest/fetch-candles
        {"symbol": "KRW-BTC", "interval": "5m", "days": 30}

    2. 백테스트 실행:
        POST /api/backtest/run
        {"strategy": "PULLBACK", "symbols": ["KRW-BTC"], ...}

    3. 파라미터 최적화:
        POST /api/backtest/optimize
        {"strategy": "PULLBACK", "param_grid": {...}, ...}
"""

from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.analytics.metrics import BacktestMetrics
from src.backtesting.data.candle_fetcher import HistoricalCandleFetcher
from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.optimization.optimizer import ParameterOptimizer

__all__ = [
    "BacktestEngine",
    "BacktestMetrics",
    "HistoricalCandleFetcher",
    "BacktestDataLoader",
    "ParameterOptimizer",
]
