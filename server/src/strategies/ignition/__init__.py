"""
v4.0 Pre-Breakout Setup + Ignition 전략

핵심 철학:
- "급등 추격"이 아닌 "사전 스캐닝 + 초반 점화 진입"
- Setup Engine: 15m/1h 기반 전조 패턴으로 후보 선별
- Ignition Engine: 1m 기반 점화 초반에만 진입
- Anti-Chase Gate: 과추격 방지 (시간/가격/호가 필터)
- Profit-add Only: 맞을 때만 증액

v4.0 2-Engine Architecture:
- IgnitionEngineV2: 초반 1파 (마이크로 풀백 진입)
- RetestEngine: 리테스트 2파 (VWAP/돌파레벨 지지 확인)
- RiskSizer: 리스크 기반 사이징 (position = equity × risk / stop_distance)
- IgnitionDDGate: DD Tier 기반 엔진 제어
- LiquidityTimeFilter: 시간대별 유동성 조정 (업비트 9시 초기화 대응)
- Pre-Ignition Patterns: 압축/건조 감지

v4.1 Surge Filters (구조적 위험만 차단):
- VolOverheatGuard: 상한 컷 (8x 초과시 차단)
- HotYesterdayPolicy: 전일 급등주 조건 강화 (금지 X)
- UpbitMicrostructureFilter: 호가 구조 필터 (시간 필터 대체)
- StructureAntiChase: 구조 기반 추격 판단 (5분%컷 대체)

모듈 구성:
- setup_score: 5대 Setup 패턴 점수화 (S1~S5)
- setup_engine: Watchlist 관리
- ignition_engine: 점화 감지 (v1)
- ignition_strategy: 통합 전략 클래스 (v1)
- ignition_strategy_v2: 2-Engine 통합 전략 (v4.0)
- engines/: 2-Engine 아키텍처 (IgnitionEngineV2, RetestEngine)
- sizing/: 리스크 기반 사이징 (RiskSizer, AttackLevelConfig)
- patterns/: Pre-Ignition 패턴 (변동성 압축, 거래량 건조)
- filters/: Surge 필터 6종
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
from src.strategies.ignition.filters import (
    VolOverheatGuard,
    VolOverheatResult,
    get_vol_overheat_guard,
    HotYesterdayPolicy,
    HotYesterdayAdjustment,
    get_hot_yesterday_policy,
    UpbitMicrostructureFilter,
    MicrostructureResult,
    get_upbit_microstructure_filter,
    StructureAntiChase,
    StructureAntiChaseResult,
    EntryType,
    get_structure_anti_chase,
    LiquidityTimeFilter,
    LiquidityTimeAdjustment,
    get_liquidity_time_filter,
    IgnitionDDGate,
    DDGateResult,
    get_ignition_dd_gate,
)
from src.strategies.ignition.ignition_strategy_v2 import (
    IgnitionStrategyV2,
    PositionV2,
    get_ignition_strategy_v2,
)
from src.strategies.ignition.engines import (
    IgnitionEngineV2,
    RetestEngine,
    EngineSignal,
    get_ignition_engine_v2,
    get_retest_engine,
)
from src.strategies.ignition.sizing import (
    RiskSizer,
    AttackLevel,
    get_risk_sizer,
)
from src.strategies.ignition.patterns import (
    VolatilitySqueezeDetector,
    VolumeDryupDetector,
    SqueezeResult,
    DryupResult,
    get_volatility_squeeze_detector,
    get_volume_dryup_detector,
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
    # Filters (v4.1)
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
    # v4.0 2-Engine Architecture
    "IgnitionStrategyV2",
    "PositionV2",
    "get_ignition_strategy_v2",
    "IgnitionEngineV2",
    "RetestEngine",
    "EngineSignal",
    "get_ignition_engine_v2",
    "get_retest_engine",
    # Sizing
    "RiskSizer",
    "AttackLevel",
    "get_risk_sizer",
    # Patterns
    "VolatilitySqueezeDetector",
    "VolumeDryupDetector",
    "SqueezeResult",
    "DryupResult",
    "get_volatility_squeeze_detector",
    "get_volume_dryup_detector",
]
