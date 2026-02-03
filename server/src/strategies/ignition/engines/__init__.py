"""
v4.0 2-Engine Architecture

Ignition Engine: 초반 1파 (마이크로 풀백 진입)
Retest Engine: 리테스트 2파 (VWAP/돌파레벨 지지 확인)

둘 다 먹으려면 진입 타입 분리 + 거리 기반 Anti-Chase + 리스크 기반 사이징
"""

from src.strategies.ignition.engines.base_engine import (
    BaseEngine,
    EngineSignal,
    EngineType,
)
from src.strategies.ignition.engines.ignition_engine_v2 import (
    IgnitionEngineV2,
    IgnitionSignalV2,
    get_ignition_engine_v2,
)
from src.strategies.ignition.engines.retest_engine import (
    RetestEngine,
    RetestSignal,
    get_retest_engine,
)

__all__ = [
    "BaseEngine",
    "EngineSignal",
    "EngineType",
    "IgnitionEngineV2",
    "IgnitionSignalV2",
    "get_ignition_engine_v2",
    "RetestEngine",
    "RetestSignal",
    "get_retest_engine",
]
