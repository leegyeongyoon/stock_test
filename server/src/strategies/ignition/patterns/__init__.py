"""
Pre-Ignition 패턴 감지 모듈

급등 직전의 공통 특징:
1. 변동성 압축 (Volatility Contraction)
2. 거래량 건조 후 첫 팽창 (Volume Dry-up → First Expansion)
3. 체결 강도 (매수체결 우위)
"""

from src.strategies.ignition.patterns.volatility_squeeze import (
    VolatilitySqueezeDetector,
    SqueezeResult,
    get_volatility_squeeze_detector,
)
from src.strategies.ignition.patterns.volume_dryup import (
    VolumeDryupDetector,
    DryupResult,
    get_volume_dryup_detector,
)

__all__ = [
    "VolatilitySqueezeDetector",
    "SqueezeResult",
    "get_volatility_squeeze_detector",
    "VolumeDryupDetector",
    "DryupResult",
    "get_volume_dryup_detector",
]
