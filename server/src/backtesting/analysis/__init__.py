"""
v28 데이터 기반 전략 분석 시스템

이 모듈은 백테스트 거래 데이터를 분석하여 수익 패턴을 발견하고
전략 파라미터를 최적화하기 위한 도구를 제공합니다.

모듈 구성:
- trade_analyzer: 승/패 거래 패턴 분석
- indicator_distribution: 지표 분포 분석
- optimal_sl_tp: 최적 SL/TP 도출
- pattern_extractor: 패턴 추출기
- openai_analyzer: OpenAI 연동 분석 (선택)
"""

from .trade_analyzer import TradePatternAnalyzer
from .indicator_distribution import IndicatorDistributionAnalyzer
from .optimal_sl_tp import SLTPAnalyzer
from .pattern_extractor import PatternExtractor

# OpenAI 분석기는 선택적 import (openai 패키지 필요)
try:
    from .openai_analyzer import OpenAIAnalyzer
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False
    OpenAIAnalyzer = None  # type: ignore

__all__ = [
    "TradePatternAnalyzer",
    "IndicatorDistributionAnalyzer",
    "SLTPAnalyzer",
    "PatternExtractor",
    "OpenAIAnalyzer",
]
