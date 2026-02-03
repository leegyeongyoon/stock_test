"""Risk Management 모듈"""

from src.risk.modes import ModeManager
from src.risk.risk_engine import RiskEngine
from src.risk.attack_gate import AttackGate, AttackGateResult, get_attack_gate

# v4.2 신규
from src.risk.kmvi import (
    KMVICalculator,
    KMVIState,
    KMVITier,
    get_kmvi_calculator,
)
from src.risk.pump_setup_score import (
    PumpSetupScoreCalculator,
    PumpSetupScore,
    get_pump_setup_calculator,
)
from src.risk.stop_watchdog import (
    StopWatchdog,
    StopEvent,
    StopType,
    WatchedPosition,
    get_stop_watchdog,
    create_stop_watchdog,
)
from src.risk.risk_overlay import (
    RiskOverlay,
    RiskDecision,
    RiskMode,
    get_risk_overlay,
)

__all__ = [
    "RiskEngine",
    "ModeManager",
    # Attack Gate
    "AttackGate",
    "AttackGateResult",
    "get_attack_gate",
    # v4.2 KMVI
    "KMVICalculator",
    "KMVIState",
    "KMVITier",
    "get_kmvi_calculator",
    # v4.2 Pump Setup Score
    "PumpSetupScoreCalculator",
    "PumpSetupScore",
    "get_pump_setup_calculator",
    # v4.2 Stop Watchdog
    "StopWatchdog",
    "StopEvent",
    "StopType",
    "WatchedPosition",
    "get_stop_watchdog",
    "create_stop_watchdog",
    # v4.2 RiskOverlay
    "RiskOverlay",
    "RiskDecision",
    "RiskMode",
    "get_risk_overlay",
]
