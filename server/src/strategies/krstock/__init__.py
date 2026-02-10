"""
한국 주식 매매 전략 모음

10개 전략 - 각각 다층 필터 시스템 통합:
1. Momentum Breakout - 20일 고점 돌파
2. Gap Fade - 갭상승 후 평균회귀
3. VWAP Pullback - VWAP 접근 반등
4. Support Bounce - 60일 저점 반등
5. Volume Surge Scalp - 거래량 급증 스캘핑
6. Sector Rotation - 강세 섹터 선도주
7. Multi-Timeframe - 멀티 타임프레임 정렬
8. Earnings Drift - 실적 발표 후 드리프트
9. Opening Range Breakout - 오프닝 레인지 돌파
10. Oversold Bounce - 과매도 복합 반등
"""

from src.strategies.krstock.base_krstock import (
    BaseKRStockStrategy,
    StrategyConfig,
    CandleData,
    Signal,
    SignalType,
    SignalStrength,
    ExitReason,
    Position,
)

from src.strategies.krstock.momentum_strategies import (
    MomentumBreakoutStrategy,
    GapFadeStrategy,
    create_momentum_breakout_strategy,
    create_gap_fade_strategy,
)

from src.strategies.krstock.mean_reversion_strategies import (
    VWAPPullbackStrategy,
    SupportBounceStrategy,
    create_vwap_pullback_strategy,
    create_support_bounce_strategy,
)

from src.strategies.krstock.volume_strategies import (
    VolumeSurgeScalpStrategy,
    SectorRotationStrategy,
    create_volume_surge_scalp_strategy,
    create_sector_rotation_strategy,
)

from src.strategies.krstock.advanced_strategies import (
    MultiTimeframeStrategy,
    EarningsDriftStrategy,
    OpeningRangeBreakoutStrategy,
    OversoldBounceStrategy,
    create_multi_timeframe_strategy,
    create_earnings_drift_strategy,
    create_opening_range_breakout_strategy,
    create_oversold_bounce_strategy,
)


# 전략 이름 → 전략 클래스 매핑
STRATEGY_MAP: dict[str, type[BaseKRStockStrategy]] = {
    "MOMENTUM_BREAKOUT": MomentumBreakoutStrategy,
    "GAP_FADE": GapFadeStrategy,
    "VWAP_PULLBACK": VWAPPullbackStrategy,
    "SUPPORT_BOUNCE": SupportBounceStrategy,
    "VOLUME_SURGE_SCALP": VolumeSurgeScalpStrategy,
    "SECTOR_ROTATION": SectorRotationStrategy,
    "MULTI_TIMEFRAME": MultiTimeframeStrategy,
    "EARNINGS_DRIFT": EarningsDriftStrategy,
    "OPENING_RANGE_BREAKOUT": OpeningRangeBreakoutStrategy,
    "OVERSOLD_BOUNCE": OversoldBounceStrategy,
}


def create_all_strategies() -> list[BaseKRStockStrategy]:
    """모든 전략 인스턴스 생성"""
    return [
        create_momentum_breakout_strategy(),      # 1
        create_gap_fade_strategy(),               # 2
        create_vwap_pullback_strategy(),          # 3
        create_support_bounce_strategy(),         # 4
        create_volume_surge_scalp_strategy(),     # 5
        create_sector_rotation_strategy(),        # 6
        create_multi_timeframe_strategy(),        # 7
        create_earnings_drift_strategy(),         # 8
        create_opening_range_breakout_strategy(), # 9
        create_oversold_bounce_strategy(),        # 10
    ]


def create_strategy_by_name(name: str) -> BaseKRStockStrategy:
    """전략 이름으로 인스턴스 생성"""
    if name not in STRATEGY_MAP:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(STRATEGY_MAP.keys())}")
    return STRATEGY_MAP[name]()


def get_strategy_info() -> list[dict]:
    """전략 정보 조회"""
    strategies = create_all_strategies()
    return [
        {
            "name": s.name,
            "type": s.config.strategy_type,
            "stop_loss_pct": s.config.stop_loss_pct,
            "take_profit_pct": s.config.take_profit_pct,
            "max_holding_days": s.config.max_holding_days,
            "max_position_pct": s.config.max_position_pct,
            "min_signal_score": s.config.min_signal_score,
        }
        for s in strategies
    ]


__all__ = [
    # Base
    "BaseKRStockStrategy",
    "StrategyConfig",
    "CandleData",
    "Signal",
    "SignalType",
    "SignalStrength",
    "ExitReason",
    "Position",
    # Strategies
    "MomentumBreakoutStrategy",
    "GapFadeStrategy",
    "VWAPPullbackStrategy",
    "SupportBounceStrategy",
    "VolumeSurgeScalpStrategy",
    "SectorRotationStrategy",
    "MultiTimeframeStrategy",
    "EarningsDriftStrategy",
    "OpeningRangeBreakoutStrategy",
    "OversoldBounceStrategy",
    # Factory
    "create_all_strategies",
    "create_strategy_by_name",
    "get_strategy_info",
    "STRATEGY_MAP",
]
