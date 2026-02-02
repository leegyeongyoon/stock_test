"""Position State Machine Module

4-state position management system for Satellite strategy:
- WEAK: Defensive mode
- NORMAL: Standard rules
- STRONG: Trend continuation
- EXTREME: Climax/overheated
"""

from src.position.schemas import (
    ManagedPosition,
    OrderIntent,
    PositionState,
    ScoreBreakdown,
)
from src.position.state_machine import PositionStateMachine
from src.position.attack_position_policy import (
    AttackPositionPolicy,
    AttackPositionSizing,
    StopCalculation,
    StopType,
    get_attack_position_policy,
)

__all__ = [
    "PositionState",
    "PositionStateMachine",
    "ManagedPosition",
    "ScoreBreakdown",
    "OrderIntent",
    # Attack Position Policy
    "AttackPositionPolicy",
    "AttackPositionSizing",
    "StopCalculation",
    "StopType",
    "get_attack_position_policy",
]
