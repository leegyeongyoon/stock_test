"""
SurgeDetector 필터 6종 (v4.0)

구조적 위험만 차단하는 "공격적" 접근:
- VolOverheatGuard: 너무 늦은 진입 차단 (상한 컷)
- HotYesterdayPolicy: 전일 급등주 조건 강화 (금지 X, 파라미터 조정)
- UpbitMicrostructureFilter: 얇은 호가 차단 (시간 필터 대체)
- StructureAntiChase: 구조 기반 추격 판단 (5분%컷 대체)
- LiquidityTimeFilter: 시간대별 유동성 조정 (업비트 9시 초기화 대응)
- IgnitionDDGate: DD Tier 기반 엔진 제어
"""

from src.strategies.ignition.filters.vol_overheat_guard import (
    VolOverheatGuard,
    VolOverheatResult,
    get_vol_overheat_guard,
)
from src.strategies.ignition.filters.hot_yesterday_policy import (
    HotYesterdayPolicy,
    HotYesterdayAdjustment,
    get_hot_yesterday_policy,
)
from src.strategies.ignition.filters.upbit_microstructure_filter import (
    UpbitMicrostructureFilter,
    MicrostructureResult,
    get_upbit_microstructure_filter,
)
from src.strategies.ignition.filters.structure_anti_chase import (
    StructureAntiChase,
    StructureAntiChaseResult,
    EntryType,
    get_structure_anti_chase,
)
from src.strategies.ignition.filters.liquidity_time_filter import (
    LiquidityTimeFilter,
    LiquidityTimeAdjustment,
    get_liquidity_time_filter,
)
from src.strategies.ignition.filters.ignition_dd_gate import (
    IgnitionDDGate,
    DDGateResult,
    get_ignition_dd_gate,
)

__all__ = [
    "VolOverheatGuard",
    "VolOverheatResult",
    "get_vol_overheat_guard",
    "HotYesterdayPolicy",
    "HotYesterdayAdjustment",
    "get_hot_yesterday_policy",
    "UpbitMicrostructureFilter",
    "MicrostructureResult",
    "get_upbit_microstructure_filter",
    "StructureAntiChase",
    "StructureAntiChaseResult",
    "EntryType",
    "get_structure_anti_chase",
    "LiquidityTimeFilter",
    "LiquidityTimeAdjustment",
    "get_liquidity_time_filter",
    "IgnitionDDGate",
    "DDGateResult",
    "get_ignition_dd_gate",
]
