"""
v4.0 리스크 기반 사이징 모듈

핵심 공식:
position_value = (equity * risk_per_trade) / effective_stop_distance_pct

비중이 아닌 리스크로 사이징하여 MDD 5% 준수
"""

from src.strategies.ignition.sizing.risk_sizer import (
    RiskSizer,
    RiskSizingResult,
    get_risk_sizer,
)
from src.strategies.ignition.sizing.attack_level_config import (
    AttackLevel,
    AttackLevelConfig,
    get_attack_level_config,
)

__all__ = [
    "RiskSizer",
    "RiskSizingResult",
    "get_risk_sizer",
    "AttackLevel",
    "AttackLevelConfig",
    "get_attack_level_config",
]
