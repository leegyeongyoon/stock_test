"""FastAPI 앱 진입점"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router, set_engine as set_routes_engine
from src.api.routes_analytics import router as analytics_router
from src.api.routes_monitoring import router as monitoring_router, set_engine as set_monitoring_engine
from src.api.routes_position import router as position_router, set_engine as set_position_engine
from src.api.routes_risk import router as risk_router, set_engine as set_risk_engine
from src.api.routes_trading_history import router as trading_history_router
from src.api.routes_backtest import router as backtest_router
from src.api.websocket import websocket_router, set_engine as set_ws_engine
from src.config import get_settings
from src.engine.core import TradingEngine
from src.models.database import init_db

# 한국 시간대
KST = ZoneInfo("Asia/Seoul")


def kst_timestamper(logger, method_name, event_dict):
    """한국 시간(KST)으로 타임스탬프 추가"""
    event_dict["timestamp"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    return event_dict


# structlog 설정
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        kst_timestamper,  # 한국 시간 사용
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()
settings = get_settings()

# 전역 엔진 인스턴스
engine: TradingEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 수명주기 관리"""
    global engine

    logger.info(
        "Starting application",
        paper_mode=settings.is_paper_mode,
    )

    # DB 초기화
    await init_db()
    logger.info("Database initialized")

    # 엔진 생성 및 시작
    engine = TradingEngine()
    set_routes_engine(engine)
    set_ws_engine(engine)
    set_position_engine(engine)
    set_risk_engine(engine)
    set_monitoring_engine(engine)

    try:
        await engine.start()
        logger.info("Trading engine started")
    except Exception as e:
        logger.error("Failed to start engine", error=str(e), exc_info=True)
        # 엔진 시작 실패해도 API는 동작하도록
        engine.add_event(
            level="CRITICAL",
            event_type="SYSTEM",
            message=f"Engine start failed: {str(e)}",
        )

    yield

    # 종료
    logger.info("Shutting down application")
    if engine:
        await engine.stop()


app = FastAPI(
    title="Binance Trading Engine API",
    description="Binance Spot + USD-M Perp 자동매매 시스템 API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 프로덕션에서는 특정 오리진만 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(monitoring_router, prefix="/api")
app.include_router(position_router, prefix="/api")
app.include_router(risk_router, prefix="/api")
app.include_router(trading_history_router, prefix="/api")
app.include_router(backtest_router, prefix="/api")
app.include_router(websocket_router)


@app.get("/")
async def root():
    """루트 엔드포인트"""
    return {
        "name": "Binance Trading Engine",
        "version": "0.1.0",
        "paper_mode": settings.is_paper_mode,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.app:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_level=settings.log_level.lower(),
    )
