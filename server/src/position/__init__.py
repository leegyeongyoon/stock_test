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

__all__ = [
    "PositionState",
    "PositionStateMachine",
    "ManagedPosition",
    "ScoreBreakdown",
    "OrderIntent",
]
