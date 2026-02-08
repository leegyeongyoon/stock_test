"""백테스팅 Pydantic 스키마"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class BacktestConfig(BaseModel):
    """백테스트 실행 설정"""

    strategy: str = Field(description="전략명 (PULLBACK, REBOUND, DIP_SCALPER 등)")
    symbols: list[str] = Field(default=[], description="테스트할 심볼 리스트 (빈 배열이면 전체)")
    start_date: datetime = Field(description="시작 일시")
    end_date: datetime = Field(description="종료 일시")
    interval: str = Field(default="5m", description="캔들 간격 (1m, 5m, 15m, 1h)")
    initial_capital: float = Field(default=1000000, description="초기 자본 (KRW)")
    commission_rate: float = Field(default=0.0005, description="수수료율 (0.05% = 0.0005)")
    slippage_bps: float = Field(default=5, description="슬리피지 (bps)")

    # 전략 파라미터 오버라이드 (선택)
    parameters: dict = Field(default={}, description="전략 파라미터 오버라이드")


class BacktestTrade(BaseModel):
    """백테스트 거래 내역"""

    trade_id: str
    symbol: str
    strategy: str
    entry_time: datetime
    entry_price: float
    entry_quantity: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    commission: float = 0.0
    holding_minutes: Optional[int] = None
    max_profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0


class BacktestMetricsSchema(BaseModel):
    """백테스트 성능 지표"""

    # 수익률
    total_return_pct: float = Field(description="총 수익률 (%)")
    annualized_return_pct: float = Field(default=0.0, description="연환산 수익률 (%)")

    # 리스크
    max_drawdown_pct: float = Field(description="최대 낙폭 (%)")
    max_drawdown_duration_days: int = Field(default=0, description="최대 낙폭 지속 기간 (일)")
    sharpe_ratio: float = Field(default=0.0, description="샤프 비율")
    sortino_ratio: float = Field(default=0.0, description="소르티노 비율")
    calmar_ratio: float = Field(default=0.0, description="칼마 비율")

    # 거래 통계
    total_trades: int = Field(description="총 거래 수")
    winning_trades: int = Field(default=0, description="승리 거래 수")
    losing_trades: int = Field(default=0, description="패배 거래 수")
    win_rate: float = Field(description="승률 (%)")
    profit_factor: float = Field(default=0.0, description="수익 팩터")
    avg_win_pct: float = Field(default=0.0, description="평균 이익 (%)")
    avg_loss_pct: float = Field(default=0.0, description="평균 손실 (%)")
    avg_holding_minutes: float = Field(default=0.0, description="평균 보유 시간 (분)")

    # 최대/최소
    best_trade_pct: float = Field(default=0.0, description="최고 수익 거래 (%)")
    worst_trade_pct: float = Field(default=0.0, description="최악 손실 거래 (%)")
    max_consecutive_wins: int = Field(default=0, description="최대 연속 승리")
    max_consecutive_losses: int = Field(default=0, description="최대 연속 패배")


class EquityPoint(BaseModel):
    """에쿼티 커브 포인트"""

    timestamp: datetime
    equity: float
    drawdown_pct: float = 0.0


class BacktestResult(BaseModel):
    """백테스트 결과"""

    run_id: str
    strategy: str
    symbols: list[str]
    interval: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_equity: float

    # 핵심 지표
    metrics: BacktestMetricsSchema

    # 파라미터
    parameters: dict = {}

    # 거래 내역
    trades: list[BacktestTrade] = []

    # 에쿼티 커브
    equity_curve: list[EquityPoint] = []

    # 전략별/시간대별 분석 (선택)
    analysis_by_hour: Optional[dict] = None
    analysis_by_symbol: Optional[dict] = None

    created_at: datetime


class OptimizationConfig(BaseModel):
    """파라미터 최적화 설정"""

    strategy: str
    symbols: list[str] = []
    start_date: datetime
    end_date: datetime
    interval: str = "5m"
    initial_capital: float = 1000000

    # 최적화할 파라미터 범위
    param_grid: dict = Field(
        description="파라미터 그리드 (예: {'stop_loss_pct': [-0.01, -0.015, -0.02]})"
    )

    # 최적화 방법
    method: str = Field(default="grid", description="최적화 방법 (grid, random)")
    max_iterations: int = Field(default=100, description="최대 반복 (random 방식)")

    # 목표 지표
    optimize_target: str = Field(default="sharpe_ratio", description="최적화 대상 지표")


class OptimizationResultItem(BaseModel):
    """최적화 결과 단일 항목"""

    parameters: dict
    metrics: BacktestMetricsSchema
    score: float  # 최적화 대상 지표 값


class OptimizationResult(BaseModel):
    """파라미터 최적화 결과"""

    strategy: str
    optimize_target: str
    total_combinations: int
    tested_combinations: int

    # 최적 파라미터
    best_parameters: dict
    best_metrics: BacktestMetricsSchema
    best_score: float

    # 상위 N개 결과
    top_results: list[OptimizationResultItem] = []

    # 전체 결과 (히트맵용)
    all_results: list[OptimizationResultItem] = []

    created_at: datetime
