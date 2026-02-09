"""백테스트 API 엔드포인트"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import select

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


# === 백테스트 결과 조회 API ===


class SavedBacktestResult(BaseModel):
    """저장된 백테스트 결과 (요약)"""
    id: int
    run_id: str
    strategy: str
    symbols: str  # JSON string
    interval: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate: float
    total_trades: int
    profit_factor: float
    created_at: datetime


@router.get("/results", response_model=list[SavedBacktestResult])
async def get_backtest_results(
    limit: int = Query(default=20, description="최대 결과 수"),
    strategy: Optional[str] = Query(default=None, description="전략 필터"),
    db: AsyncSession = Depends(get_db),
):
    """
    저장된 백테스트 결과 목록 조회
    """
    from src.backtesting.models.database import BacktestResultModel

    stmt = select(BacktestResultModel).order_by(BacktestResultModel.created_at.desc())

    if strategy:
        stmt = stmt.where(BacktestResultModel.strategy == strategy)

    stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    results = result.scalars().all()

    return [
        SavedBacktestResult(
            id=r.id,
            run_id=r.run_id,
            strategy=r.strategy,
            symbols=r.symbols,
            interval=r.interval,
            start_date=r.start_date,
            end_date=r.end_date,
            initial_capital=r.initial_capital,
            final_equity=r.final_equity,
            total_return_pct=r.total_return_pct,
            max_drawdown_pct=r.max_drawdown_pct,
            sharpe_ratio=r.sharpe_ratio,
            win_rate=r.win_rate,
            total_trades=r.total_trades,
            profit_factor=r.profit_factor,
            created_at=r.created_at,
        )
        for r in results
    ]


@router.get("/results/{run_id}", response_model=BacktestResult)
async def get_backtest_result_detail(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    백테스트 결과 상세 조회 (거래 내역, 에쿼티 커브 포함)
    """
    from src.backtesting.models.database import BacktestResultModel, BacktestTradeModel

    # 결과 조회
    stmt = select(BacktestResultModel).where(BacktestResultModel.run_id == run_id)
    result = await db.execute(stmt)
    result_model = result.scalar_one_or_none()

    if not result_model:
        raise HTTPException(status_code=404, detail="Backtest result not found")

    # 거래 내역 조회
    stmt = select(BacktestTradeModel).where(BacktestTradeModel.run_id == run_id)
    trades_result = await db.execute(stmt)
    trades = trades_result.scalars().all()

    # BacktestResult 스키마로 변환
    from src.backtesting.models.schemas import (
        BacktestMetricsSchema,
        BacktestTrade,
        EquityPoint,
    )
    import json

    return BacktestResult(
        run_id=result_model.run_id,
        strategy=result_model.strategy,
        symbols=json.loads(result_model.symbols),
        interval=result_model.interval,
        start_date=result_model.start_date,
        end_date=result_model.end_date,
        initial_capital=result_model.initial_capital,
        final_equity=result_model.final_equity,
        metrics=BacktestMetricsSchema(**result_model.metrics_json),
        parameters=result_model.parameters_json or {},
        trades=[
            BacktestTrade(
                trade_id=t.trade_id,
                symbol=t.symbol,
                strategy=t.strategy,
                entry_time=t.entry_time,
                entry_price=t.entry_price,
                entry_quantity=t.entry_quantity,
                exit_time=t.exit_time,
                exit_price=t.exit_price,
                exit_reason=t.exit_reason,
                pnl=t.pnl,
                pnl_pct=t.pnl_pct,
                commission=t.commission,
                holding_minutes=t.holding_minutes,
                max_profit_pct=t.max_profit_pct,
                max_drawdown_pct=t.max_drawdown_pct,
            )
            for t in trades
        ],
        equity_curve=[EquityPoint(**p) for p in (result_model.equity_curve_json or [])],
        created_at=result_model.created_at,
    )


@router.delete("/results/{run_id}")
async def delete_backtest_result(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    백테스트 결과 삭제
    """
    from src.backtesting.models.database import BacktestResultModel, BacktestTradeModel

    # 결과 삭제
    stmt = select(BacktestResultModel).where(BacktestResultModel.run_id == run_id)
    result = await db.execute(stmt)
    result_model = result.scalar_one_or_none()

    if not result_model:
        raise HTTPException(status_code=404, detail="Backtest result not found")

    # 거래 내역 삭제
    stmt = select(BacktestTradeModel).where(BacktestTradeModel.run_id == run_id)
    trades_result = await db.execute(stmt)
    trades = trades_result.scalars().all()

    for trade in trades:
        await db.delete(trade)

    await db.delete(result_model)
    await db.commit()

    return {"success": True, "run_id": run_id}
