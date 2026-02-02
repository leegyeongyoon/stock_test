"""
v4.0 Pre-Breakout Setup + Ignition 전략

핵심 철학:
- "급등 추격"이 아닌 "사전 스캐닝 + 초반 점화 진입"
- Setup Engine: 15m/1h 기반 전조 패턴으로 후보 선별
- Ignition Engine: 1m 기반 점화 초반에만 진입
- Anti-Chase Gate: 과추격 방지 (시간/가격/호가 필터)
- Profit-add Only: 맞을 때만 증액

모듈 구성:
- setup_score: 5대 Setup 패턴 점수화 (S1~S5)
- setup_engine: Watchlist 관리
- ignition_engine: 점화 감지
- anti_chase_gate: 과추격 방지
- ignition_position_policy: Profit-add 사이징
- ignition_strategy: 통합 전략 클래스
"""

from src.strategies.ignition.setup_score import (
    SetupScoreCalculator,
    SetupScoreResult,
    SetupScoreComponent,
    get_setup_score_calculator,
)
from src.strategies.ignition.setup_engine import (
    SetupEngine,
    SetupCandidate,
    get_setup_engine,
)
from src.strategies.ignition.ignition_engine import (
    IgnitionEngine,
    IgnitionSignal,
    get_ignition_engine,
)
from src.strategies.ignition.anti_chase_gate import (
    AntiChaseGate,
    AntiChaseResult,
    get_anti_chase_gate,
)
from src.strategies.ignition.ignition_position_policy import (
    IgnitionPositionPolicy,
    IgnitionPositionSizing,
    get_ignition_position_policy,
)
from src.strategies.ignition.ignition_strategy import (
    IgnitionStrategy,
    IgnitionPosition,
    get_ignition_strategy,
)
from src.strategies.ignition.surge_detector import (
    SurgeDetector,
    SurgeSignal,
    get_surge_detector,
)

__all__ = [
    "SetupScoreCalculator",
    "SetupScoreResult",
    "SetupScoreComponent",
    "get_setup_score_calculator",
    "SetupEngine",
    "SetupCandidate",
    "get_setup_engine",
    "IgnitionEngine",
    "IgnitionSignal",
    "get_ignition_engine",
    "AntiChaseGate",
    "AntiChaseResult",
    "get_anti_chase_gate",
    "IgnitionPositionPolicy",
    "IgnitionPositionSizing",
    "get_ignition_position_policy",
    "IgnitionStrategy",
    "IgnitionPosition",
    "get_ignition_strategy",
    "SurgeDetector",
    "SurgeSignal",
    "get_surge_detector",
]
