"""백테스트 API 엔드포인트"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from src.models.database import AsyncSession, get_db
from src.backtesting.models.schemas import (
    BacktestConfig,
    BacktestResult,
    OptimizationConfig,
    OptimizationResult,
)
from src.backtesting.data.candle_fetcher import HistoricalCandleFetcher
from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine, create_pullback_exit_checker
from src.backtesting.optimization.optimizer import ParameterOptimizer
from src.backtesting.strategies.strategy_adapters import get_signal_generator, get_exit_checker

router = APIRouter(prefix="/backtest", tags=["backtest"])


# === Request/Response 스키마 ===


class CandleFetchRequest(BaseModel):
    """캔들 수집 요청"""

    symbol: str = Field(description="마켓 코드 (KRW-BTC)")
    interval: str = Field(default="5m", description="인터벌 (1m, 5m, 15m, 1h)")
    days: int = Field(default=30, description="수집할 기간 (일)")


class CandleFetchResponse(BaseModel):
    """캔들 수집 응답"""

    symbol: str
    interval: str
    fetched_count: int
    start_date: datetime
    end_date: datetime


class BacktestRunRequest(BaseModel):
    """백테스트 실행 요청"""

    strategy: str = Field(description="전략명 (PULLBACK, REBOUND, DIP_SCALPER)")
    symbols: list[str] = Field(description="테스트할 심볼 리스트")
    start_date: datetime = Field(description="시작 일시")
    end_date: datetime = Field(description="종료 일시")
    interval: str = Field(default="5m", description="캔들 인터벌")
    initial_capital: float = Field(default=1000000, description="초기 자본")

    # 전략 파라미터
    stop_loss_pct: float = Field(default=-0.01, description="손절 비율")
    take_profit_pct: float = Field(default=0.015, description="익절 비율")
    trailing_trigger_pct: float = Field(default=0.008, description="트레일링 시작")
    trailing_stop_pct: float = Field(default=0.004, description="트레일링 스탑")


class OptimizationRunRequest(BaseModel):
    """최적화 실행 요청"""

    strategy: str = Field(description="전략명")
    symbols: list[str] = Field(description="심볼 리스트")
    start_date: datetime
    end_date: datetime
    interval: str = Field(default="5m")
    initial_capital: float = Field(default=1000000)

    # 파라미터 그리드
    param_grid: dict = Field(
        description="최적화할 파라미터 (예: {'stop_loss_pct': [-0.01, -0.015, -0.02]})"
    )

    method: str = Field(default="grid", description="최적화 방법 (grid, random)")
    max_iterations: int = Field(default=100, description="최대 반복 (random)")
    optimize_target: str = Field(default="sharpe_ratio", description="최적화 대상")


class DataRangeResponse(BaseModel):
    """데이터 범위 응답"""

    symbol: str
    interval: str
    oldest_date: Optional[datetime]
    newest_date: Optional[datetime]
    candle_count: int


# === 엔드포인트 ===


@router.post("/fetch-candles", response_model=CandleFetchResponse)
async def fetch_candles(
    request: CandleFetchRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    과거 캔들 데이터 수집

    Upbit API에서 과거 데이터를 가져와 DB에 저장합니다.
    """
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=request.days)

    async with HistoricalCandleFetcher(db) as fetcher:
        count = await fetcher.fetch_and_store(
            request.symbol,
            request.interval,
            start_date,
            end_date,
        )

    return CandleFetchResponse(
        symbol=request.symbol,
        interval=request.interval,
        fetched_count=count,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/data-range", response_model=DataRangeResponse)
async def get_data_range(
    symbol: str = Query(..., description="마켓 코드"),
    interval: str = Query(default="5m", description="인터벌"),
    db: AsyncSession = Depends(get_db),
):
    """저장된 캔들 데이터 범위 조회"""
    async with HistoricalCandleFetcher(db) as fetcher:
        oldest, newest = await fetcher.get_stored_range(symbol, interval)

    loader = BacktestDataLoader(db)

    if oldest and newest:
        count = await loader.get_candle_count(symbol, interval, oldest, newest)
    else:
        count = 0

    return DataRangeResponse(
        symbol=symbol,
        interval=interval,
        oldest_date=oldest,
        newest_date=newest,
        candle_count=count,
    )


@router.post("/run", response_model=BacktestResult)
async def run_backtest(
    request: BacktestRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    백테스트 실행

    과거 데이터로 전략 성능을 시뮬레이션합니다.
    """
    # 데이터 확인
    loader = BacktestDataLoader(db)

    for symbol in request.symbols:
        count = await loader.get_candle_count(
            symbol, request.interval, request.start_date, request.end_date
        )
        if count == 0:
            raise HTTPException(
                status_code=400,
                detail=f"No data for {symbol}. Please fetch candles first.",
            )

    # 백테스트 설정
    config = BacktestConfig(
        strategy=request.strategy,
        symbols=request.symbols,
        start_date=request.start_date,
        end_date=request.end_date,
        interval=request.interval,
        initial_capital=request.initial_capital,
        parameters={
            "stop_loss_pct": request.stop_loss_pct,
            "take_profit_pct": request.take_profit_pct,
            "trailing_trigger_pct": request.trailing_trigger_pct,
            "trailing_stop_pct": request.trailing_stop_pct,
        },
    )

    engine = BacktestEngine(config, loader)

    # 전략별 시그널 생성기
    strategy_params = {
        "stop_loss_pct": request.stop_loss_pct,
        "take_profit_pct": request.take_profit_pct,
        "trailing_trigger_pct": request.trailing_trigger_pct,
        "trailing_stop_pct": request.trailing_stop_pct,
    }

    try:
        signal_generator = get_signal_generator(request.strategy, strategy_params)
        exit_checker = get_exit_checker(request.strategy, strategy_params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await engine.run(signal_generator, exit_checker)

    return result


@router.post("/optimize", response_model=OptimizationResult)
async def run_optimization(
    request: OptimizationRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    파라미터 최적화 실행

    그리드/랜덤 서치로 최적 파라미터를 찾습니다.
    """
    loader = BacktestDataLoader(db)

    # 데이터 확인
    for symbol in request.symbols:
        count = await loader.get_candle_count(
            symbol, request.interval, request.start_date, request.end_date
        )
        if count == 0:
            raise HTTPException(
                status_code=400,
                detail=f"No data for {symbol}. Please fetch candles first.",
            )

    config = OptimizationConfig(
        strategy=request.strategy,
        symbols=request.symbols,
        start_date=request.start_date,
        end_date=request.end_date,
        interval=request.interval,
        initial_capital=request.initial_capital,
        param_grid=request.param_grid,
        method=request.method,
        max_iterations=request.max_iterations,
        optimize_target=request.optimize_target,
    )

    # 시그널 생성기 팩토리 (전략별)
    def signal_generator_factory(params):
        return get_signal_generator(request.strategy, params)

    # 청산 체커 팩토리 (전략별)
    def exit_checker_factory(params):
        return get_exit_checker(request.strategy, params)

    optimizer = ParameterOptimizer(
        config,
        loader,
        signal_generator_factory,
        exit_checker_factory,
    )

    result = await optimizer.optimize()

    return result


@router.get("/symbols")
async def get_available_symbols(
    db: AsyncSession = Depends(get_db),
):
    """
    데이터가 있는 심볼 목록 조회
    """
    from sqlalchemy import select, distinct

    from src.backtesting.models.database import BacktestCandleModel

    stmt = select(distinct(BacktestCandleModel.symbol))
    result = await db.execute(stmt)
    symbols = [row[0] for row in result.fetchall()]

    return {"symbols": symbols}


@router.get("/intervals")
async def get_available_intervals(
    symbol: str = Query(..., description="마켓 코드"),
    db: AsyncSession = Depends(get_db),
):
    """
    심볼별 저장된 인터벌 목록 조회
    """
    from sqlalchemy import select, distinct

    from src.backtesting.models.database import BacktestCandleModel

    stmt = (
        select(distinct(BacktestCandleModel.interval))
        .where(BacktestCandleModel.symbol == symbol)
    )
    result = await db.execute(stmt)
    intervals = [row[0] for row in result.fetchall()]

    return {"symbol": symbol, "intervals": intervals}


class BulkFetchRequest(BaseModel):
    """일괄 캔들 수집 요청"""

    interval: str = Field(default="5m", description="인터벌")
    days: int = Field(default=30, description="수집할 기간 (일)")
    symbols: list[str] = Field(default=[], description="심볼 리스트 (빈 배열이면 모든 KRW 심볼)")


class BulkFetchResponse(BaseModel):
    """일괄 캔들 수집 응답"""

    total_symbols: int
    success_count: int
    failed_symbols: list[str]
    total_candles: int


@router.post("/fetch-all", response_model=BulkFetchResponse)
async def fetch_all_candles(
    request: BulkFetchRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    모든 KRW 심볼 캔들 데이터 일괄 수집

    Upbit의 모든 KRW 마켓 심볼에 대해 과거 데이터를 수집합니다.
    """
    import httpx

    # 심볼 리스트 결정
    if request.symbols:
        symbols = request.symbols
    else:
        # Upbit에서 모든 KRW 심볼 조회
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.upbit.com/v1/market/all")
            markets = response.json()
            symbols = [m["market"] for m in markets if m["market"].startswith("KRW-")]

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=request.days)

    success_count = 0
    failed_symbols = []
    total_candles = 0

    async with HistoricalCandleFetcher(db) as fetcher:
        for symbol in symbols:
            try:
                count = await fetcher.fetch_and_store(
                    symbol,
                    request.interval,
                    start_date,
                    end_date,
                )
                if count > 0:
                    success_count += 1
                    total_candles += count
            except Exception as e:
                failed_symbols.append(symbol)

    return BulkFetchResponse(
        total_symbols=len(symbols),
        success_count=success_count,
        failed_symbols=failed_symbols,
        total_candles=total_candles,
    )
