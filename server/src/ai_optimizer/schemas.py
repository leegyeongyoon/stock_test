"""AI Optimizer 데이터 모델 정의"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class StrategyTypeAI(str, Enum):
    """AI 최적화 대상 전략 유형"""
    MOMENTUM = "momentum"
    BREAKOUT = "breakout"
    TREND = "trend"
    MEAN_REVERSION = "mean_reversion"
    VOLUME = "volume"
    MULTI_TIMEFRAME = "multi_timeframe"
    BTC_CORRELATION = "btc_correlation"
    REGIME_ADAPTIVE = "regime_adaptive"


class PhaseType(str, Enum):
    """최적화 단계"""
    DISCOVERY = "discovery"
    GENERATION = "generation"
    OPTIMIZATION = "optimization"
    VALIDATION = "validation"


@dataclass
class StrategyCandidate:
    """AI 생성 전략 후보"""
    candidate_id: str
    name: str
    strategy_type: StrategyTypeAI
    description: str

    # AI 생성 코드
    signal_code: str = ""
    exit_code: str = ""

    # 파라미터
    parameters: dict = field(default_factory=dict)
    param_count: int = 0

    # 백테스트 결과
    return_pct: float = 0.0
    sharpe: float = 0.0
    max_dd: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_holding_min: float = 0.0

    # Walk-forward 결과
    wf_oos_return: Optional[float] = None
    wf_oos_sharpe: Optional[float] = None
    wf_is_oos_ratio: Optional[float] = None
    wf_passed: bool = False

    # 메타
    phase: PhaseType = PhaseType.DISCOVERY
    created_round: int = 0
    last_modified_round: int = 0
    is_active: bool = True
    compile_errors: list[str] = field(default_factory=list)


@dataclass
class BacktestSummary:
    """압축된 백테스트 결과 (프롬프트용)"""
    return_pct: float = 0.0
    sharpe: float = 0.0
    max_dd: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_holding_min: float = 0.0
    max_consecutive_losses: int = 0

    # 청산 사유별 요약
    exit_reason_summary: dict = field(default_factory=dict)

    # 상위 50개 거래 (간략)
    top_trades: list[dict] = field(default_factory=list)

    # 심볼별 성과 요약
    symbol_summary: dict = field(default_factory=dict)

    # 시간대별 요약
    hour_summary: dict = field(default_factory=dict)

    def to_prompt_text(self) -> str:
        """프롬프트용 압축 텍스트 (8-12K 토큰 목표)"""
        lines = [
            f"Return: {self.return_pct:+.2f}% | Sharpe: {self.sharpe:.2f} | "
            f"MDD: {self.max_dd:.2f}% | WR: {self.win_rate:.1f}%",
            f"Trades: {self.total_trades} | PF: {self.profit_factor:.2f} | "
            f"AvgWin: {self.avg_win_pct:+.2f}% | AvgLoss: {self.avg_loss_pct:+.2f}%",
            f"AvgHold: {self.avg_holding_min:.0f}min | MaxConsLoss: {self.max_consecutive_losses}",
        ]

        if self.exit_reason_summary:
            lines.append("\nExit reasons:")
            for reason, info in self.exit_reason_summary.items():
                lines.append(
                    f"  {reason}: {info['count']}건 "
                    f"(W{info.get('wins', 0)}/L{info.get('losses', 0)}, "
                    f"pnl {info.get('total_pnl_pct', 0):+.2f}%)"
                )

        if self.top_trades:
            lines.append(f"\nTop {len(self.top_trades)} trades:")
            for t in self.top_trades[:25]:
                lines.append(
                    f"  {t.get('symbol', ''):12s} {t.get('pnl_pct', 0):+5.2f}% "
                    f"{t.get('exit_reason', ''):12s} {t.get('holding_min', 0):>4.0f}min"
                )

        if self.symbol_summary:
            lines.append(f"\nSymbol performance ({len(self.symbol_summary)} symbols):")
            # 손실 심볼 우선
            sorted_syms = sorted(
                self.symbol_summary.items(),
                key=lambda x: x[1].get("total_pnl_pct", 0),
            )
            for sym, s in sorted_syms[:15]:
                lines.append(
                    f"  {sym}: {s.get('trades', 0)}건 "
                    f"WR {s.get('win_rate', 0):.0f}% "
                    f"pnl {s.get('total_pnl_pct', 0):+.2f}%"
                )
            if len(sorted_syms) > 15:
                lines.append(f"  ... +{len(sorted_syms) - 15} more")

        return "\n".join(lines)


@dataclass
class RoundResult:
    """라운드별 결과"""
    round_num: int
    phase: PhaseType
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # OpenAI 응답
    openai_analysis: str = ""
    openai_changes: str = ""
    openai_reasoning: str = ""

    # 생성/수정된 후보들
    candidates_created: list[str] = field(default_factory=list)
    candidates_modified: list[str] = field(default_factory=list)

    # 백테스트 결과
    best_return_pct: float = 0.0
    best_candidate_id: str = ""

    # 프롬프트 크기
    prompt_tokens: int = 0
    response_tokens: int = 0

    # 에러
    errors: list[str] = field(default_factory=list)


@dataclass
class OptimizationSession:
    """전체 세션 상태"""
    session_id: str
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None

    # 설정
    total_rounds: int = 15
    symbols: list[str] = field(default_factory=list)
    interval: str = "5m"
    initial_capital: float = 1_000_000

    # 후보 전략들
    candidates: dict[str, StrategyCandidate] = field(default_factory=dict)

    # 라운드 히스토리
    rounds: list[RoundResult] = field(default_factory=list)

    # 현재 상태
    current_round: int = 0
    current_phase: PhaseType = PhaseType.DISCOVERY

    # 최적 전략
    best_candidates: list[str] = field(default_factory=list)

    # 통계
    total_backtests_run: int = 0
    total_openai_calls: int = 0
    total_cost_usd: float = 0.0

    def get_phase(self, round_num: int) -> PhaseType:
        """라운드 번호에 따른 단계 결정"""
        if round_num <= 3:
            return PhaseType.DISCOVERY
        if round_num <= 8:
            return PhaseType.GENERATION
        if round_num <= 12:
            return PhaseType.OPTIMIZATION
        return PhaseType.VALIDATION

    def get_active_candidates(self) -> list[StrategyCandidate]:
        """활성 후보 목록"""
        return [c for c in self.candidates.values() if c.is_active]

    def get_top_candidates(self, n: int = 5) -> list[StrategyCandidate]:
        """상위 N개 후보 (수익률 기준)"""
        active = self.get_active_candidates()
        return sorted(active, key=lambda c: c.return_pct, reverse=True)[:n]
