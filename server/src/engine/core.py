"""Trading Engine - 메인 엔진 루프"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional, Union


def _utc_iso() -> str:
    """UTC ISO 문자열 (Z suffix 포함, 프론트엔드 timezone 변환용)"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

import structlog
from sqlalchemy import select

from src.config import get_settings
from src.models.database import OrderModel, PositionModel, async_session
from src.data.candle_manager import get_candle_manager
from src.data.symbol_manager import init_symbol_manager, get_symbol_manager
from src.engine.command_queue import Command, CommandQueue, CommandType
from src.exchange.upbit import UpbitExchange
# Binance exchanges removed - Upbit only
from src.features.feature_engine import FeatureEngine
from src.models.schemas import OrderSide, OrderType, StrategyType, TradingMode
from src.risk.capital_profile import CapitalProfileManager, get_capital_profile_manager
from src.risk.exec_health import get_exec_health_monitor
from src.risk.risk_engine import RiskEngine
from src.risk.risk_overlay import RiskMode, get_risk_overlay
from src.risk.stop_watchdog import (
    StopWatchdog,
    StopEvent,
    StopType,
    WatchedPosition,
    get_stop_watchdog,
)
from src.services.mode_manager import get_mode_manager
from src.services.trade_recorder import trade_recorder
from src.strategies.v3_live import get_v3_strategies, V3Signal, V3Position
from src.monitoring.slack import SlackNotifier, AlertLevel, SlackMessage
from src.portfolio.position_ledger import PositionLedger, FillEvent
from src.risk.exposure_manager import (
    ExposureManager,
    ExposureConfig,
    init_exposure_manager,
    get_exposure_manager,
)
from src.risk.upbit_liquidity_filter import get_upbit_liquidity_filter

logger = structlog.get_logger()
settings = get_settings()


class TradingEngine:
    """
    Trading Engine - 메인 엔진

    책임:
    1. 거래소 연결 관리
    2. 시장 데이터 수집
    3. 전략 실행
    4. 명령 처리
    5. 상태 관리
    """

    def __init__(self) -> None:
        # Upbit 전용 (Binance 제거됨)
        self._is_upbit = True

        # Upbit 단일 거래소
        self.exchange = UpbitExchange(
            api_key=settings.upbit_api_key,
            secret=settings.upbit_secret,
            testnet=False,  # Upbit은 testnet 없음
        )
        # 레거시 호환성을 위한 alias
        self.spot_exchange = self.exchange
        self.perp_exchange = None  # Upbit은 선물 없음

        # 동적 심볼 관리자
        self.symbol_manager = init_symbol_manager(
            perp_exchange=None,
            spot_exchange=self.exchange,
            upbit_exchange=self.exchange,
        )

        # 핵심 컴포넌트
        self.risk_engine = RiskEngine()
        self.command_queue = CommandQueue()

        # 캔들 매니저 및 피처 엔진
        self.candle_manager = get_candle_manager()
        self.feature_engine = FeatureEngine(candle_manager=self.candle_manager)

        # Risk Overlay (MDD 5% 방어)
        self.risk_overlay = get_risk_overlay(
            candle_manager=self.candle_manager,
            feature_engine=self.feature_engine,
        )
        self.exec_health = get_exec_health_monitor()

        # v3 전략 (3개: VOB, CR, TBR)
        self.v3_strategies = get_v3_strategies()
        self.v3_enabled = os.environ.get("V3_ENABLED", "true").lower() == "true"
        self.v3_max_positions = int(os.environ.get("V3_MAX_POSITIONS", "6"))

        # User Mode Manager
        self.mode_manager = get_mode_manager()

        # Capital Profile (Growth/Preserve 2단계 시스템)
        self.capital_profile = get_capital_profile_manager()

        # v4.2: Stop Watchdog (독립 손절 모니터링)
        self.stop_watchdog = get_stop_watchdog()
        self.stop_watchdog.set_exchange(self.exchange)
        self.stop_watchdog.set_callback(self._on_stop_triggered)

        # Slack 알림
        self.slack_notifier = SlackNotifier()
        if self.slack_notifier.is_enabled:
            logger.info("Slack notifier enabled")

        # P0: 단일 진실 원장 (PositionLedger)
        self.position_ledger = PositionLedger()
        logger.info("PositionLedger initialized")

        # P2: 노출 한도 관리 (ExposureManager)
        exposure_config = ExposureConfig(
            max_positions=5,
            max_total_exposure_pct=0.70,  # 70%
            max_symbol_exposure_pct=0.15,  # 15%
            max_strategy_exposure_pct=0.40,  # 40%
            min_cash_reserve_pct=0.20,  # 20%
            max_single_order_pct=0.10,  # 10%
        )
        self.exposure_manager = init_exposure_manager(
            ledger=self.position_ledger,
            config=exposure_config,
        )

        # 상태
        self._running = False
        self._last_heartbeat: Optional[datetime] = None
        self._main_task: Optional[asyncio.Task] = None

        # 시장 데이터 캐시
        self._market_data: dict[str, dict] = {}
        self._btc_regime: str = "NEUTRAL"

        # 캐시된 상태 (API 조회용)
        self._cached_summary: dict = {}
        self._cached_positions: list = []
        self._cached_events: list = []
        self._cached_orders: list = []  # 주문 히스토리

        # DB 기록용 카운터
        self._snapshot_counter: int = 0
        self._starting_equity: Optional[float] = None  # 오늘 시작 자산

        # 호가창 캐시 (Rate Limit 방지)
        self._orderbook_cache: dict[str, tuple[dict, float]] = {}  # symbol -> (data, timestamp)
        self._orderbook_cache_ttl: float = 30.0  # 30초 캐시

        # v5.2: 필터링 통계 (오늘 기준)
        self._filter_stats: dict[str, int] = {
            "anti_chase": 0,
            "exposure_limit": 0,
            "overheat": 0,
            "total": 0,
        }
        self._filter_stats_date: Optional[str] = None  # YYYY-MM-DD

    @property
    def is_running(self) -> bool:
        """엔진 실행 중 여부"""
        return self._running

    @property
    def mode(self) -> TradingMode:
        """현재 모드"""
        return self.risk_engine.mode

    @property
    def last_heartbeat(self) -> Optional[datetime]:
        """마지막 heartbeat"""
        return self._last_heartbeat

    async def _get_orderbook_cached(self, symbol: str) -> Optional[dict]:
        """캐시된 호가창 조회 (Rate Limit 방지)"""
        import time
        now = time.time()

        # 캐시 확인
        if symbol in self._orderbook_cache:
            cached_data, cached_time = self._orderbook_cache[symbol]
            if now - cached_time < self._orderbook_cache_ttl:
                return cached_data

        # 캐시 미스 → API 호출
        try:
            orderbook = await self.exchange.get_orderbook(symbol)
            if orderbook:
                self._orderbook_cache[symbol] = (orderbook, now)
            return orderbook
        except Exception as e:
            logger.warning(f"Orderbook fetch failed for {symbol}: {e}")
            return None

    _STABLECOIN_BASES = {"USDT", "USDC", "USD1", "USDE", "DAI", "BUSD", "TUSD", "PYUSD"}

    def _prefilter_symbols_for_v3(self, market_data: dict) -> list[str]:
        """v3 전략용 심볼 Pre-filtering

        필터:
        - 스테이블코인 제외
        - 거래대금 > 10억 (저유동성 잡코인 배제)
        - 가격 > 100원 (극저가 코인 제외)
        - 거래대금 순 정렬 (유동성 높은 것 우선)
        """
        candidates = []
        for sym, md in market_data.items():
            base = sym.split("-")[-1] if "-" in sym else sym
            if base in self._STABLECOIN_BASES:
                continue

            volume_24h = md.get("volume_24h", 0)
            price = md.get("price", 0)

            if volume_24h < 1_000_000_000:
                continue
            if price < 100:
                continue

            candidates.append((sym, volume_24h))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [c[0] for c in candidates[:30]]

    async def start(self) -> None:
        """엔진 시작"""
        if self._running:
            logger.warning("Engine already running")
            return

        logger.info(
            "Starting Trading Engine",
            paper_mode=settings.is_paper_mode,
            exchange_type=settings.exchange_type,
        )

        # Upbit 거래소 연결 (Binance 제거됨)
        exchange_connected = await self.exchange.connect()
        if not exchange_connected:
            logger.error("Failed to connect to Upbit")
            raise RuntimeError("Upbit connection failed")
        logger.info("Connected to Upbit exchange")

        # 데이터베이스에서 오픈 포지션 복원
        await self.position_ledger.restore_from_database()
        logger.info("Position ledger restore complete")

        # 동적 심볼 목록 초기화
        qualified_symbols = await self.symbol_manager.refresh()
        logger.info(
            "Symbol Manager initialized",
            total=self.symbol_manager._total_symbols,
            qualified=len(qualified_symbols),
            symbols=qualified_symbols[:10],  # 상위 10개만 로그
        )

        # Risk Engine 시작
        await self.risk_engine.start()

        # v4.2: Stop Watchdog 시작
        await self.stop_watchdog.start()

        # DB에서 주문 히스토리 로드
        await self._load_orders_from_db()

        # DB에서 오픈 포지션 복구 (서버 재시작 시 전략별 포지션 복구)
        await self._restore_positions_from_db()

        # 메인 루프 시작
        self._running = True
        self._candle_update_counter = 0  # 캔들 업데이트 카운터 (v5.3: 10초마다)
        self._kmvi_update_counter = 0    # KMVI 업데이트 카운터 (60초마다)
        self._candle_refresh_round = 0   # 캔들 갱신 라운드 로빈
        self._pending_order_cleanup_counter = 0  # v5.5: 미체결 주문 정리 카운터
        self._main_task = asyncio.create_task(self._main_loop())

        # v5.5: 초기 캔들 로드를 백그라운드로 이동 (엔진 시작 블로킹 방지)
        asyncio.create_task(self._load_initial_candles_background(qualified_symbols))

        logger.info("Trading Engine started")

    async def stop(self) -> None:
        """엔진 중지"""
        if not self._running:
            return

        logger.info("Stopping Trading Engine")

        self._running = False

        # 메인 루프 중지
        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass

        # Risk Engine 중지
        await self.risk_engine.stop()

        # v4.2: Stop Watchdog 중지
        await self.stop_watchdog.stop()

        # Upbit 거래소 연결 해제
        await self.exchange.disconnect()

        logger.info("Trading Engine stopped")

    async def _main_loop(self) -> None:
        """메인 엔진 루프"""
        while self._running:
            try:
                self._last_heartbeat = datetime.utcnow()

                # 1. 명령 처리
                await self._process_commands()

                # 2. 시장 데이터 업데이트
                await self._update_market_data()

                # 2.5. 1분봉 데이터 갱신 (v5.3: 60초→10초로 단축, 급등 감지 속도 개선)
                self._candle_update_counter = getattr(self, "_candle_update_counter", 0) + 1
                if self._candle_update_counter >= 10:  # 60→10초
                    self._candle_update_counter = 0
                    await self._refresh_candles()

                # v4.2: KMVI 업데이트 (60초마다 - 캔들과 분리)
                self._kmvi_update_counter = getattr(self, "_kmvi_update_counter", 0) + 1
                if self._kmvi_update_counter >= 60:
                    self._kmvi_update_counter = 0
                    try:
                        symbols = self.symbol_manager.get_qualified_symbols()
                        self.risk_overlay.update_kmvi(symbols)
                    except Exception as e:
                        logger.warning("KMVI update failed", error=str(e))

                # v2.3: 미체결 주문 자동 정리 (30초마다 체크, 3분 초과 시 취소)
                self._pending_order_cleanup_counter = getattr(self, "_pending_order_cleanup_counter", 0) + 1
                if self._pending_order_cleanup_counter >= 30:
                    self._pending_order_cleanup_counter = 0
                    await self._cleanup_stale_pending_orders()

                # 3. 전략 실행 (NORMAL 모드에서만)
                can_open = self.risk_engine.can_open_position
                if not can_open:
                    logger.warning("Strategy execution skipped", can_open_position=can_open)
                if can_open:
                    await self._execute_strategies()

                # 3.5. v3 포지션 청산 체크 (모드 무관, 항상 실행)
                await self._manage_v3_exits()

                # 4. 포지션 관리 (SAFE 모드에서도 실행)
                await self._manage_positions()

                # 5. 상태 캐시 업데이트
                await self._update_cached_state()

                # 루프 간격
                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Main loop error", error=str(e))
                await asyncio.sleep(5)

    async def _process_commands(self) -> None:
        """명령 처리"""
        while True:
            command = await self.command_queue.get_nowait()
            if not command:
                break

            try:
                result = await self._execute_command(command)
                await self.command_queue.complete(command.command_id, result=result)
            except Exception as e:
                logger.error(
                    "Command execution failed",
                    command_id=command.command_id,
                    error=str(e),
                )
                await self.command_queue.complete(command.command_id, error=str(e))

    async def _execute_command(self, command: Command) -> dict:
        """명령 실행"""
        logger.info(
            "Executing command",
            type=command.command_type.value,
            params=command.params,
        )

        if command.command_type == CommandType.PAUSE:
            reason = command.params.get("reason", "Manual pause")
            success = await self.risk_engine.pause(reason)
            return {"success": success, "mode": self.mode.value}

        elif command.command_type == CommandType.RESUME:
            reason = command.params.get("reason", "Manual resume")
            success = await self.risk_engine.resume(reason)
            return {"success": success, "mode": self.mode.value}

        elif command.command_type == CommandType.FLATTEN:
            # 모든 포지션 청산
            strategy = command.params.get("strategy")
            symbol = command.params.get("symbol")

            logger.info("Flatten command received", strategy=strategy, symbol=symbol)

            # Upbit: 잔고 기반 청산
            if self._is_upbit:
                all_balances = await self.exchange.get_all_balances()
                closed_count = 0

                for balance in all_balances:
                    if balance.asset == "KRW":
                        continue
                    if balance.total <= 0:
                        continue

                    asset_symbol = f"KRW-{balance.asset}"

                    # symbol 필터
                    if symbol and asset_symbol != symbol:
                        continue

                    # 시장가 매도
                    try:
                        logger.info(f"Flattening {asset_symbol}", quantity=balance.total)
                        result = await self.exchange.place_order(
                            symbol=asset_symbol,
                            side=OrderSide.SELL,
                            order_type=OrderType.MARKET,
                            quantity=balance.total,
                        )

                        if result.success:
                            closed_count += 1
                            logger.info(f"Flattened {asset_symbol}", quantity=balance.total)
                        else:
                            logger.error(f"Failed to flatten {asset_symbol}", error=result.error)
                    except Exception as e:
                        logger.error(f"Flatten error for {asset_symbol}", error=str(e))

                return {"success": True, "message": f"Flattened {closed_count} positions"}

            return {"success": True, "message": "Flatten initiated"}

        elif command.command_type == CommandType.FLATTEN_SYMBOL:
            symbol = command.params.get("symbol")
            # TODO: 특정 심볼 정리 로직
            return {"success": True, "symbol": symbol}

        elif command.command_type == CommandType.RECONCILE:
            # TODO: 수동 Reconcile
            return {"success": True, "message": "Reconcile completed"}

        else:
            return {"success": False, "error": "Unknown command"}

    async def _load_initial_candles(self, symbols: list[str]) -> None:
        """초기 1분봉/5분봉 데이터 로드 (SurgeDetector + Candle Surge Bonus용)"""
        print(f"[DEBUG] _load_initial_candles called with {len(symbols)} symbols")
        logger.info("Loading initial 1m/5m candles for SurgeDetector...")

        # 상위 50개만 로드 (Rate Limit 고려)
        target_symbols = symbols[:50]
        print(f"[DEBUG] Loading candles for {len(target_symbols)} target symbols")

        loaded_count_1m = 0
        loaded_count_3m = 0
        loaded_count_5m = 0
        for symbol in target_symbols:
            try:
                # 1분봉 로드
                await self._load_candles_for_symbol(symbol, "1", 100)
                loaded_count_1m += 1
            except Exception as e:
                print(f"[DEBUG] Failed to load 1m candles for {symbol}: {e}")
                logger.warning(f"Failed to load 1m candles for {symbol}: {e}")
            await asyncio.sleep(0.05)  # Rate limit 방지

            try:
                # 3분봉 로드 (v2.2: Dip Scalper 3분봉 지원)
                await self._load_candles_for_symbol(symbol, "3", 50)
                loaded_count_3m += 1
            except Exception as e:
                print(f"[DEBUG] Failed to load 3m candles for {symbol}: {e}")
                logger.warning(f"Failed to load 3m candles for {symbol}: {e}")
            await asyncio.sleep(0.05)  # Rate limit 방지

            try:
                # 5분봉 로드 (v5.0: Candle Surge Bonus)
                await self._load_candles_for_symbol(symbol, "5", 50)
                loaded_count_5m += 1
            except Exception as e:
                print(f"[DEBUG] Failed to load 5m candles for {symbol}: {e}")
                logger.warning(f"Failed to load 5m candles for {symbol}: {e}")
            await asyncio.sleep(0.05)  # Rate limit 방지

        print(f"[DEBUG] Initial candle loading complete: 1m={loaded_count_1m}, 3m={loaded_count_3m}, 5m={loaded_count_5m}/{len(target_symbols)}")
        logger.info(
            "Initial 1m/3m/5m candles loaded",
            symbols_count=len(target_symbols),
            loaded_1m=loaded_count_1m,
            loaded_3m=loaded_count_3m,
            loaded_5m=loaded_count_5m,
        )

    async def _load_initial_candles_background(self, symbols: list[str]) -> None:
        """
        v5.5: 백그라운드 초기 캔들 로드 (엔진 시작 블로킹 방지)

        메인 루프가 시작된 후 백그라운드에서 캔들을 로드하여
        엔진 시작이 캔들 로딩에 의해 지연되지 않도록 함
        """
        try:
            logger.info("Starting background candle loading...")
            await asyncio.wait_for(
                self._load_initial_candles(symbols),
                timeout=120.0  # 2분 타임아웃
            )
            logger.info("Background candle loading completed successfully")
        except asyncio.TimeoutError:
            logger.warning("Background candle loading timed out (120s)")
        except Exception as e:
            logger.error("Background candle loading failed", error=str(e), exc_info=True)

    async def _refresh_candles(self) -> None:
        """
        v5.3: 1분봉/5분봉 데이터 갱신 (10초마다 호출)

        - 10초마다 상위 15개 심볼 갱신 (Rate Limit 고려)
        - 1분당 약 90개 심볼 갱신 가능
        - 급등 감지 속도 대폭 개선
        """
        # v5.3: 10초마다 호출되므로 15개씩 갱신 (30개→15개)
        all_symbols = self.symbol_manager.get_qualified_symbols()

        # 라운드 로빈으로 전체 심볼 커버
        refresh_round = getattr(self, "_candle_refresh_round", 0)
        start_idx = (refresh_round * 15) % max(len(all_symbols), 1)
        end_idx = min(start_idx + 15, len(all_symbols))
        watch_symbols = all_symbols[start_idx:end_idx]

        # 다음 라운드 준비
        self._candle_refresh_round = refresh_round + 1

        for symbol in watch_symbols:
            try:
                # 1분봉 갱신 (최근 10개만 - 빠른 갱신)
                await self._load_candles_for_symbol(symbol, "1", 10)
            except Exception:
                pass
            await asyncio.sleep(0.02)  # Rate limit 방지

            try:
                # 3분봉 갱신 (v2.2: Dip Scalper 3분봉 지원)
                await self._load_candles_for_symbol(symbol, "3", 6)
            except Exception:
                pass
            await asyncio.sleep(0.02)  # Rate limit 방지

            try:
                # 5분봉 갱신 (v5.0: Candle Surge Bonus)
                await self._load_candles_for_symbol(symbol, "5", 6)
            except Exception:
                pass
            await asyncio.sleep(0.02)  # Rate limit 방지

    async def _load_candles_for_symbol(
        self, symbol: str, interval: str, limit: int
    ) -> None:
        """심볼별 캔들 로드 (Upbit API)"""
        if not self._is_upbit:
            print(f"[DEBUG] Not Upbit, skipping candle load for {symbol}")
            logger.debug("Not Upbit, skipping candle load")
            return

        # Upbit API 호출
        candles = await self.exchange.get_candles(symbol, interval, limit)

        if not candles:
            print(f"[DEBUG] No candles returned for {symbol} {interval}m")
            logger.debug(f"No candles returned for {symbol} {interval}m")
            return

        print(f"[DEBUG] Got {len(candles)} candles for {symbol} {interval}m")
        logger.debug(f"Loaded {len(candles)} candles for {symbol} {interval}m")

        # CandleManager에 저장
        stored_count = 0
        interval_minutes = int(interval)  # "1" -> 1, "5" -> 5
        for candle in candles:
            kline = self._convert_upbit_candle(candle, interval_minutes)
            self.candle_manager.update_candle(
                symbol=symbol,
                interval=f"{interval}m",
                kline=kline,
            )
            stored_count += 1

        # 저장 확인
        check = self.candle_manager.get_candles(symbol, f"{interval}m", 5)
        check_count = len(check) if check else 0
        print(f"[DEBUG] After store: {symbol} {interval}m has {check_count} candles (stored {stored_count})")
        logger.debug(f"After store: {symbol} {interval}m has {check_count} candles")

    def _convert_upbit_candle(self, upbit_candle: dict, interval_minutes: int = 1) -> dict:
        """Upbit 캔들을 CandleManager 형식으로 변환"""
        from datetime import timezone

        candle_time = upbit_candle.get("candle_date_time_utc", "")
        if candle_time:
            # v5.3: Upbit UTC 시간을 정확히 파싱 (timezone-aware)
            # Upbit API는 "2026-02-04T07:26:00" 형식 (Z 없음, UTC 시간)
            dt = datetime.fromisoformat(candle_time)
            # naive datetime을 UTC로 명시
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            timestamp_ms = int(dt.timestamp() * 1000)
        else:
            timestamp_ms = 0

        # Close time = Open time + interval
        close_time_ms = timestamp_ms + (interval_minutes * 60 * 1000)

        return {
            "t": timestamp_ms,  # Open time
            "T": close_time_ms,  # Close time
            "o": upbit_candle.get("opening_price", 0),
            "h": upbit_candle.get("high_price", 0),
            "l": upbit_candle.get("low_price", 0),
            "c": upbit_candle.get("trade_price", 0),
            "v": upbit_candle.get("candle_acc_trade_volume", 0),
            "q": upbit_candle.get("candle_acc_trade_price", 0),
            "n": 0,  # Trade count (Upbit doesn't provide this)
        }

    def _calc_candle_surge_data(self, symbol: str, current_price: float) -> dict:
        """
        분봉 급등 데이터 계산

        Returns:
            dict with change_1m, rvol_1m, change_3m, rvol_3m, change_5m, rvol_5m, change_30m, rvol_30m
        """
        result = {
            "change_1m": 0.0,
            "rvol_1m": 1.0,
            "change_3m": 0.0,
            "rvol_3m": 1.0,
            "change_5m": 0.0,
            "rvol_5m": 1.0,
            "change_30m": 0.0,
            "rvol_30m": 1.0,
        }

        try:
            # 1분봉 데이터 계산
            candles_1m = self.candle_manager.get_candles(symbol, "1m", 21)
            if len(candles_1m) >= 2:
                # change_1m: 현재가 vs 1분 전 종가
                prev_close_1m = candles_1m[-2].close if len(candles_1m) >= 2 else current_price
                if prev_close_1m > 0:
                    result["change_1m"] = (current_price - prev_close_1m) / prev_close_1m

                # rvol_1m: 최근 1분봉 거래량 / 최근 20개 평균
                rvol_1m = self.candle_manager.calc_rvol(symbol, "1m", 20)
                if rvol_1m is not None:
                    result["rvol_1m"] = rvol_1m

            # 3분봉 데이터 계산 (v2.2: Dip Scalper 3분봉 지원)
            candles_3m = self.candle_manager.get_candles(symbol, "3m", 21)
            if len(candles_3m) >= 2:
                # change_3m: 현재가 vs 3분 전 종가
                prev_close_3m = candles_3m[-2].close if len(candles_3m) >= 2 else current_price
                if prev_close_3m > 0:
                    result["change_3m"] = (current_price - prev_close_3m) / prev_close_3m

                # rvol_3m: 최근 3분봉 거래량 / 최근 20개 평균
                rvol_3m = self.candle_manager.calc_rvol(symbol, "3m", 20)
                if rvol_3m is not None:
                    result["rvol_3m"] = rvol_3m

            # 5분봉 데이터 계산
            candles_5m = self.candle_manager.get_candles(symbol, "5m", 13)
            if len(candles_5m) >= 2:
                # change_5m: 현재가 vs 5분 전 종가
                prev_close_5m = candles_5m[-2].close if len(candles_5m) >= 2 else current_price
                if prev_close_5m > 0:
                    result["change_5m"] = (current_price - prev_close_5m) / prev_close_5m

                # rvol_5m: 최근 5분봉 거래량 / 최근 12개 평균
                rvol_5m = self.candle_manager.calc_rvol(symbol, "5m", 12)
                if rvol_5m is not None:
                    result["rvol_5m"] = rvol_5m

            # 30분봉 데이터 계산
            candles_30m = self.candle_manager.get_candles(symbol, "30m", 21)
            if len(candles_30m) >= 2:
                # change_30m: 현재가 vs 30분 전 종가
                prev_close_30m = candles_30m[-2].close if len(candles_30m) >= 2 else current_price
                if prev_close_30m > 0:
                    result["change_30m"] = (current_price - prev_close_30m) / prev_close_30m

                # rvol_30m: 최근 30분봉 거래량 / 최근 20개 평균
                rvol_30m = self.candle_manager.calc_rvol(symbol, "30m", 20)
                if rvol_30m is not None:
                    result["rvol_30m"] = rvol_30m

        except Exception as e:
            logger.debug(f"Failed to calc candle surge data for {symbol}: {e}")

        return result

    async def _update_market_data(self) -> None:
        """시장 데이터 업데이트 (동적 심볼 + 일괄 조회)"""
        try:
            # 심볼 목록 갱신 필요 시 (1시간마다)
            if self.symbol_manager.needs_refresh():
                qualified = await self.symbol_manager.refresh()
                logger.info(
                    "Symbol list refreshed",
                    qualified=len(qualified),
                )

            # 동적 심볼 목록
            watch_symbols = self.symbol_manager.get_qualified_symbols()

            # 보유 포지션 심볼 강제 포함 (유동성 필터와 무관하게 가격 추적)
            position_symbols = set()
            for strat in self.v3_strategies:
                position_symbols.update(strat.get_all_positions().keys())

            for sym in position_symbols:
                if sym not in watch_symbols:
                    watch_symbols.append(sym)

            if not watch_symbols:
                logger.warning("No qualified symbols to monitor")
                return

            # Upbit 데이터 수집 (Binance 제거됨)
            await self._update_market_data_upbit(watch_symbols)

        except Exception as e:
            logger.error("Failed to update market data", error=str(e))

    async def _update_market_data_upbit(self, watch_symbols: list[str]) -> None:
        """Upbit 시장 데이터 업데이트"""
        try:
            # 전체 시세 조회
            tickers = await self.exchange.get_all_tickers(watch_symbols)

            for symbol in watch_symbols:
                ticker = tickers.get(symbol, {})
                if not ticker:
                    continue

                current_price = ticker.get("trade_price", 0)
                if current_price <= 0:
                    continue

                # 24h 고점/저점
                high_24h = ticker.get("high_price", current_price * 1.005)
                low_24h = ticker.get("low_price", current_price * 0.995)

                # 변화율 기반 RVOL 추정
                change_rate = abs(ticker.get("signed_change_rate", 0))
                estimated_rvol = 1.0 + (change_rate * 100 / 1.5)  # 3% 변동 = RVOL 3.0

                # VWAP 근사
                vwap_approx = (high_24h + low_24h + current_price) / 3

                # ClosePos 계산
                price_range = high_24h - low_24h
                close_pos = (current_price - low_24h) / price_range if price_range > 0 else 0.5

                # v5.0: 분봉 급등 데이터 계산
                candle_surge = self._calc_candle_surge_data(symbol, current_price)

                self._market_data[symbol] = {
                    "symbol": symbol,
                    "price": current_price,
                    "spot_price": current_price,  # 현물만 있음
                    "perp_price": current_price,  # 레거시 호환
                    "spot_bid": current_price,
                    "spot_ask": current_price,
                    "perp_bid": current_price,
                    "perp_ask": current_price,
                    "funding_rate": 0,  # Upbit은 펀딩비 없음
                    "volume_24h": ticker.get("acc_trade_price_24h", 0),
                    "high_20": high_24h,
                    "low_20": low_24h,
                    "highest_12_5m": high_24h,
                    "lowest_12_5m": low_24h,
                    "rvol": estimated_rvol,
                    "vwap": vwap_approx,
                    "close_pos": close_pos,
                    "price_change_pct": change_rate * 100,
                    "timestamp": datetime.utcnow(),
                    # v5.0: Candle Surge Bonus 데이터
                    "change_1m": candle_surge["change_1m"],
                    "rvol_1m": candle_surge["rvol_1m"],
                    "change_5m": candle_surge["change_5m"],
                    "rvol_5m": candle_surge["rvol_5m"],
                    # 30분봉 데이터
                    "change_30m": candle_surge["change_30m"],
                    "rvol_30m": candle_surge["rvol_30m"],
                }

            # BTC 레짐 계산 (Upbit: 가격 변화율 기반)
            btc_data = self._market_data.get("KRW-BTC", {})
            if btc_data:
                btc_change = btc_data.get("price_change_pct", 0)

                if btc_change > 3.0:
                    self._btc_regime = "BULLISH"
                elif btc_change < -3.0:
                    self._btc_regime = "BEARISH"
                elif abs(btc_change) > 5.0:
                    self._btc_regime = "VOLATILE"
                else:
                    self._btc_regime = "NEUTRAL"

        except Exception as e:
            logger.error("Failed to update Upbit market data", error=str(e))

    async def _execute_strategies(self) -> None:
        """v3 전략 실행"""
        try:
            if not self.v3_enabled:
                return

            # 현재 자산 조회 (Upbit KRW)
            krw_balance = await self.exchange.get_balance("KRW")
            all_balances = await self.exchange.get_all_balances()
            current_equity = krw_balance.total if krw_balance else 0
            for bal in all_balances:
                if bal.asset != "KRW" and bal.total > 0:
                    symbol = f"KRW-{bal.asset}"
                    market_data = self._market_data.get(symbol, {})
                    price = market_data.get("price", 0)
                    if price > 0:
                        current_equity += bal.total * price

            # Risk Overlay 평가
            risk_decision = self.risk_overlay.evaluate(current_equity)

            # User Mode 자동 다운그레이드 체크
            self.mode_manager.update_risk_state()

            # HALT 모드면 신규 진입 완전 차단
            if risk_decision.mode == RiskMode.HALT:
                return

            # 현재 v3 포지션 수 체크
            total_v3_positions = sum(
                len(s.get_all_positions()) for s in self.v3_strategies
            )
            if total_v3_positions >= self.v3_max_positions:
                return

            # Pre-filter 심볼
            candidates = self._prefilter_symbols_for_v3(self._market_data)
            now = datetime.utcnow()

            # 각 전략 스캔
            all_signals: list[V3Signal] = []
            for strategy in self.v3_strategies:
                for symbol in candidates:
                    md = self._market_data.get(symbol, {})
                    if not md or md.get("price", 0) <= 0:
                        continue
                    # 이미 포지션이 있으면 스킵
                    if strategy.get_position(symbol):
                        continue
                    signal = strategy.scan(symbol, md, self.candle_manager, now)
                    if signal:
                        all_signals.append(signal)

            # 점수 순 정렬
            all_signals.sort(key=lambda s: s.score, reverse=True)

            # 시그널 실행 (최대 포지션 제한)
            for signal in all_signals:
                if total_v3_positions >= self.v3_max_positions:
                    break
                await self._execute_v3_signal(signal, risk_decision, current_equity)
                total_v3_positions += 1

        except Exception as e:
            logger.error("Strategy execution error", error=str(e), exc_info=True)

    async def _execute_v3_signal(self, signal: V3Signal, risk_decision, current_equity: float) -> None:
        """v3 시그널 실행 (매수 주문)"""
        try:
            symbol = signal.symbol
            price = signal.entry_price

            # 포지션 크기 계산
            position_krw = current_equity * signal.position_pct * risk_decision.sizing_multiplier
            quantity = position_krw / price if price > 0 else 0

            # 최소 주문 금액 체크 (Upbit: 5,000 KRW)
            if position_krw < 5000:
                return

            # KRW 잔고 체크
            krw_balance = await self.exchange.get_balance("KRW")
            available = krw_balance.free if krw_balance else 0
            if available < position_krw:
                return

            # PAPER 모드 체크
            if not self.mode_manager.should_execute_trades():
                logger.info(
                    "PAPER mode - skipping v3 entry",
                    symbol=symbol,
                    strategy=signal.strategy,
                    score=signal.score,
                )
                return

            logger.info(
                "Executing v3 signal",
                symbol=symbol,
                strategy=signal.strategy,
                score=f"{signal.score:.1f}",
                amount=f"₩{position_krw:,.0f}",
                indicators=signal.indicators,
            )

            # 시장가 매수
            result = await self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=quantity,
            )

            if result.success:
                fill_price = result.avg_price or price
                fill_qty = result.filled_qty or quantity

                # 전략에서 해당 전략 객체 찾기
                strategy_obj = None
                for s in self.v3_strategies:
                    if s.name == signal.strategy:
                        strategy_obj = s
                        break

                if strategy_obj:
                    strategy_obj.record_entry(symbol, datetime.utcnow())
                    strategy_obj.track_position(V3Position(
                        symbol=symbol,
                        strategy=signal.strategy,
                        entry_price=fill_price,
                        quantity=fill_qty,
                        entry_time=datetime.utcnow(),
                        stop_loss_pct=strategy_obj.SL_PCT,
                        take_profit_pct=strategy_obj.TP_PCT,
                        trail_trigger_pct=strategy_obj.TRAIL_TRIGGER,
                        trail_stop_pct=strategy_obj.TRAIL_STOP,
                        time_stop_minutes=strategy_obj.TIME_STOP_MIN,
                        highest_price=fill_price,
                    ))

                # PositionLedger에 기록
                fill_event = FillEvent(
                    order_id=f"V3-BUY-{symbol}-{int(datetime.utcnow().timestamp())}",
                    exchange_order_id=result.exchange_order_id if hasattr(result, "exchange_order_id") else None,
                    position_id=None,
                    symbol=symbol,
                    strategy_id=signal.strategy,
                    side="BUY",
                    filled_quantity=fill_qty,
                    fill_price=fill_price,
                    fee=0.0,
                    fee_asset="KRW",
                    timestamp=datetime.utcnow(),
                )
                await self.position_ledger.on_buy_fill(fill_event)

                self.add_event(
                    level="INFO",
                    event_type="V3_ENTRY",
                    message=f"v3 entry: {symbol} ({signal.strategy})",
                    details={
                        "strategy": signal.strategy,
                        "score": signal.score,
                        "quantity": fill_qty,
                        "price": fill_price,
                        "amount_krw": fill_qty * fill_price,
                        **signal.indicators,
                    },
                )

                self.add_order(
                    symbol=symbol,
                    strategy=signal.strategy,
                    side="BUY",
                    order_type="MARKET",
                    quantity=fill_qty,
                    price=fill_price,
                    status="FILLED",
                    filled_qty=fill_qty,
                    avg_fill_price=fill_price,
                )

                # Slack 알림
                if self.slack_notifier.is_enabled:
                    await self.slack_notifier.send(SlackMessage(
                        text=f"""
:chart_with_upwards_trend: *v3 진입* ({signal.strategy})
> 심볼: 
> 점수: {signal.score:.1f}
> 수량: {fill_qty:.4f}
> 가격: ₩{fill_price:,.0f}
> 금액: ₩{fill_qty * fill_price:,.0f}
                        """.strip(),
                        level=AlertLevel.INFO,
                    ))

            else:
                logger.warning("v3 entry failed", symbol=symbol, error=result.error)

        except Exception as e:
            logger.error("v3 signal execution error", symbol=signal.symbol, error=str(e))

    async def _manage_v3_exits(self) -> None:
        """v3 포지션 SL/TP/trailing/time_stop 체크"""
        try:
            now = datetime.utcnow()
            for strategy in self.v3_strategies:
                positions = strategy.get_all_positions()
                for symbol, pos in list(positions.items()):
                    md = self._market_data.get(symbol, {})
                    current_price = md.get("price", 0)
                    if current_price <= 0:
                        continue

                    exit_result = strategy.check_exit(symbol, current_price, now)
                    if not exit_result:
                        continue

                    reason = exit_result["reason"]
                    exit_type = exit_result.get("exit_type", "unknown")

                    # PAPER 모드 체크
                    if not self.mode_manager.should_execute_trades():
                        logger.info("PAPER mode - skipping v3 exit", symbol=symbol, reason=reason)
                        continue

                    # Upbit 잔고에서 실제 수량 확인
                    base = symbol.split("-")[-1]
                    balance = await self.exchange.get_balance(base)
                    sell_qty = balance.total if balance and balance.total > 0 else pos.quantity

                    logger.info(
                        "v3 exit signal",
                        symbol=symbol,
                        strategy=pos.strategy,
                        reason=reason,
                        exit_type=exit_type,
                    )

                    result = await self.exchange.place_order(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        quantity=sell_qty,
                    )

                    if result.success:
                        fill_price = result.avg_price or current_price
                        realized_pnl = (fill_price - pos.entry_price) * result.filled_qty

                        # 전략에서 포지션 제거
                        strategy.close_position(symbol)

                        # PositionLedger 청산
                        sell_fill = FillEvent(
                            order_id=f"V3-SELL-{symbol}-{int(now.timestamp())}",
                            exchange_order_id=result.exchange_order_id if hasattr(result, "exchange_order_id") else None,
                            position_id=None,
                            symbol=symbol,
                            strategy_id=pos.strategy,
                            side="SELL",
                            filled_quantity=result.filled_qty,
                            fill_price=fill_price,
                            fee=0.0,
                            fee_asset="KRW",
                            timestamp=now,
                        )
                        await self.position_ledger.on_sell_fill(sell_fill)

                        self.add_event(
                            level="INFO",
                            event_type="V3_EXIT",
                            message=f"v3 exit: {symbol} ({exit_type})",
                            details={
                                "strategy": pos.strategy,
                                "reason": reason,
                                "exit_type": exit_type,
                                "quantity": result.filled_qty,
                                "entry_price": pos.entry_price,
                                "exit_price": fill_price,
                                "pnl": realized_pnl,
                            },
                        )

                        pnl_pct = (fill_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                        self.add_order(
                            symbol=symbol,
                            strategy=pos.strategy,
                            side="SELL",
                            order_type="MARKET",
                            quantity=sell_qty,
                            price=fill_price,
                            status="FILLED",
                            filled_qty=result.filled_qty,
                            avg_fill_price=fill_price,
                            realized_pnl=realized_pnl,
                        )

                        # Slack 알림
                        if self.slack_notifier.is_enabled:
                            pnl_emoji = ":moneybag:" if realized_pnl >= 0 else ":money_with_wings:"
                            pnl_sign = "+" if realized_pnl >= 0 else ""
                            try:
                                krw_balance = await self.exchange.get_balance("KRW")
                                remaining_krw = krw_balance.available if krw_balance else 0
                            except Exception:
                                remaining_krw = 0

                            await self.slack_notifier.send(SlackMessage(
                                text=f"""
{pnl_emoji} *v3 청산* ({exit_type.upper()})
> 전략: {pos.strategy}
> 심볼: 
> 사유: {reason}
> 수량: {result.filled_qty:.4f}
> 진입가: ₩{pos.entry_price:,.0f}
> 청산가: ₩{fill_price:,.0f}
> 손익: {pnl_sign}₩{realized_pnl:,.0f} ({pnl_sign}{pnl_pct*100:.2f}%)
---
> 잔여 현금: ₩{remaining_krw:,.0f}
                                """.strip(),
                                level=AlertLevel.INFO if realized_pnl >= 0 else AlertLevel.WARNING,
                            ))

                    else:
                        logger.warning("v3 exit failed", symbol=symbol, error=result.error)

        except Exception as e:
            logger.error("v3 exit management error", error=str(e))


    def _round_quantity(self, symbol: str, quantity: float) -> float:
        """심볼별 수량 정밀도 처리 (SymbolManager 사용)"""
        return self.symbol_manager.round_quantity(symbol, quantity)

    def _round_quantity_for_perp(self, symbol: str, quantity: float) -> float:
        """선물용 수량 정밀도 처리 (SymbolManager 사용)"""
        return self.symbol_manager.round_quantity(symbol, quantity)

    async def _manage_positions(self) -> None:
        """포지션 관리 (v3 전략 - exits handled by _manage_v3_exits)"""
        pass  # v3 exits are handled by _manage_v3_exits in main loop

    async def _update_cached_state(self) -> None:
        """상태 캐시 업데이트"""
        try:
            await self._update_cached_state_upbit()
        except Exception as e:
            logger.error("Failed to update cached state", error=str(e))

    async def _get_symbol_strategy_map(self) -> dict[str, str]:
        """PositionLedger + v3 전략에서 심볼별 전략 매핑 조회"""
        strategy_map: dict[str, str] = {}

        # v3 전략 active positions 먼저 반영
        for strat in self.v3_strategies:
            for symbol in strat.get_all_positions():
                strategy_map[symbol] = strat.name

        try:
            open_positions = await self.position_ledger.get_open_positions()

            for pos in open_positions:
                symbol = pos.symbol
                strategy = pos.strategy_id

                if symbol not in strategy_map:
                    strategy_map[symbol] = strategy

        except Exception as e:
            logger.warning("Failed to build strategy map from ledger", error=str(e))

        return strategy_map

    async def _update_cached_state_upbit(self) -> None:
        """Upbit 상태 캐시 업데이트"""
        # 잔고 조회
        krw_balance = await self.exchange.get_balance("KRW")
        all_balances = await self.exchange.get_all_balances()

        krw_total = krw_balance.total if krw_balance else 0
        current_equity = krw_total

        # PositionLedger에서 전략 매핑 조회
        symbol_strategy_map = await self._get_symbol_strategy_map()

        # 보유 자산 가치 합산
        positions_value = 0
        self._cached_positions = []
        unrealized_pnl = 0  # Upbit은 진입가 추적 필요

        for balance in all_balances:
            if balance.asset == "KRW" or balance.total <= 0:
                continue

            symbol = f"KRW-{balance.asset}"
            market_data = self._market_data.get(symbol, {})
            current_price = market_data.get("price", 0)

            if current_price > 0:
                position_value = balance.total * current_price
                positions_value += position_value
                current_equity += position_value

                # Upbit API에서 직접 가져온 평균 매수가 사용
                entry_price = balance.avg_buy_price if balance.avg_buy_price > 0 else current_price

                position_pnl = (current_price - entry_price) * balance.total
                unrealized_pnl += position_pnl

                # 실제 전략 조회
                strategy = symbol_strategy_map.get(symbol, "UNKNOWN")

                self._cached_positions.append({
                    "symbol": symbol,
                    "strategy": strategy,  # 실제 진입 전략 사용
                    "side": "BUY",  # 롱만 가능
                    "quantity": balance.total,
                    "avg_price": entry_price,
                    "current_price": current_price,
                    "unrealized_pnl": position_pnl,
                    "realized_pnl": 0.0,
                    "notional": position_value,
                    "leverage": 1.0,  # 현물
                })

        # 오늘 시작 자산 설정
        if self._starting_equity is None:
            self._starting_equity = current_equity

        # PnL 계산
        pnl_today = current_equity - self._starting_equity
        pnl_today_pct = pnl_today / self._starting_equity if self._starting_equity > 0 else 0

        # Summary 업데이트
        self._cached_summary = {
            "equity": current_equity,
            "pnl_today": pnl_today,
            "pnl_today_pct": pnl_today_pct,
            "drawdown": 0.0,
            "exposure": positions_value,
            "cash": krw_total,
            "margin_used": 0,  # 현물은 마진 없음
            "mode": self.mode.value,
            "is_paper": settings.is_paper_mode,
            "updated_at": _utc_iso(),
        }

        # 60초마다 자산 스냅샷 DB 기록
        self._snapshot_counter += 1
        if self._snapshot_counter >= 60:
            self._snapshot_counter = 0
            await self._record_equity_snapshot(
                equity=current_equity,
                unrealized_pnl=unrealized_pnl,
                pnl_today=pnl_today,
            )

    async def _record_equity_snapshot(
        self,
        equity: float,
        unrealized_pnl: float,
        pnl_today: float,
    ) -> None:
        """자산 스냅샷 DB 기록"""
        try:
            await trade_recorder.record_equity_snapshot(
                equity=equity,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=pnl_today - unrealized_pnl,
            )

            # 일일 통계도 업데이트
            await trade_recorder.update_daily_stats(
                starting_equity=self._starting_equity,
                ending_equity=equity,
                pnl=pnl_today,
            )
        except Exception as e:
            logger.error("Failed to record equity snapshot", error=str(e))

    def get_summary(self) -> dict:
        """요약 정보 조회"""
        return self._cached_summary

    def get_positions(self) -> list:
        """포지션 목록 조회"""
        return self._cached_positions

    def get_events(self, limit: int = 100) -> list:
        """이벤트 목록 조회"""
        return self._cached_events[-limit:]

    async def _restore_positions_from_db(self) -> None:
        """서버 시작 시 DB에서 오픈 포지션을 복구하고 v3 전략에 재등록"""
        try:
            # 1. 시장 데이터 먼저 업데이트 (현재가 필요)
            watch_symbols = self.symbol_manager.get_qualified_symbols()
            await self._update_market_data_upbit(watch_symbols)

            # 2. DB에서 열린 포지션 조회
            async with async_session() as session:
                stmt = select(PositionModel).where(PositionModel.is_open == True)
                result = await session.execute(stmt)
                open_positions = result.scalars().all()

            if not open_positions:
                logger.info("No open positions to restore from database")
                return

            # 3. Upbit 현재 잔고 조회
            all_balances = await self.exchange.get_all_balances()
            balance_map = {f"KRW-{bal.asset}": bal for bal in all_balances if bal.asset != "KRW"}

            # v3 전략 이름→인스턴스 매핑
            v3_strategy_map = {s.name: s for s in self.v3_strategies}

            restored_count = 0
            skipped_count = 0

            # 4. 각 포지션을 해당 전략에 복구
            for position in open_positions:
                symbol = position.symbol
                strategy = position.strategy

                # Upbit 잔고 확인
                if symbol not in balance_map:
                    logger.warning(
                        "Position in DB but no balance on Upbit - skipping",
                        symbol=symbol,
                        strategy=strategy.value,
                    )
                    skipped_count += 1
                    continue

                balance = balance_map[symbol]
                if balance.total <= 0:
                    skipped_count += 1
                    continue

                # 현재가 조회
                market_data = self._market_data.get(symbol)
                if not market_data:
                    skipped_count += 1
                    continue

                current_price = market_data.get("price", 0)
                if current_price <= 0:
                    skipped_count += 1
                    continue

                # v3 전략 복구
                try:
                    v3_strat = v3_strategy_map.get(strategy.value)
                    if v3_strat:
                        v3_pos = V3Position(
                            symbol=symbol,
                            strategy=strategy.value,
                            entry_price=position.avg_price,
                            quantity=balance.total,
                            entry_time=position.opened_at or datetime.utcnow(),
                            stop_loss_pct=v3_strat.SL_PCT,
                            take_profit_pct=v3_strat.TP_PCT,
                            trail_trigger_pct=v3_strat.TRAIL_TRIGGER,
                            trail_stop_pct=v3_strat.TRAIL_STOP,
                            time_stop_minutes=v3_strat.TIME_STOP_MIN,
                            highest_price=current_price,
                        )
                        v3_strat.track_position(v3_pos)
                        logger.info(
                            "v3 position restored",
                            symbol=symbol,
                            strategy=strategy.value,
                            quantity=balance.total,
                            entry_price=position.avg_price,
                        )
                    else:
                        logger.warning(
                            "Unknown strategy for position - skipping",
                            symbol=symbol,
                            strategy=strategy.value,
                        )
                        skipped_count += 1
                        continue

                    # 주문 기록 추가 (SYNCED 상태)
                    self.add_order(
                        symbol=symbol,
                        strategy=strategy.value,
                        side="BUY",
                        order_type="MARKET",
                        quantity=balance.total,
                        price=position.avg_price,
                        status="SYNCED",
                    )

                    restored_count += 1

                except Exception as e:
                    logger.error(
                        "Failed to restore position",
                        symbol=symbol,
                        strategy=strategy.value,
                        error=str(e),
                    )
                    skipped_count += 1
                    continue

            logger.info(
                "Position restoration complete",
                restored=restored_count,
                skipped=skipped_count,
                total_in_db=len(open_positions),
            )

        except Exception as e:
            logger.error("Failed to restore positions from database", error=str(e))

    async def _load_orders_from_db(self, limit: int = 500) -> None:
        """서버 시작 시 DB에서 주문 히스토리 로드 (Upbit KRW 마켓만)"""
        try:
            async with async_session() as session:
                stmt = select(OrderModel).order_by(
                    OrderModel.created_at.desc()
                ).limit(limit)
                result = await session.execute(stmt)
                orders = result.scalars().all()

                loaded_count = 0
                skipped_count = 0

                # 역순으로 추가 (최신이 뒤에 오도록)
                for order in reversed(orders):
                    # USDT 심볼 필터링 (바이낸스 레거시 데이터 제외)
                    if "USDT" in order.symbol:
                        skipped_count += 1
                        continue

                    self._cached_orders.append({
                        "order_id": order.order_id,
                        "symbol": order.symbol,
                        "strategy": order.strategy.value,
                        "side": order.side.value,
                        "order_type": order.order_type.value,
                        "quantity": order.quantity,
                        "price": order.price,
                        "status": order.status.value,
                        "filled_quantity": order.filled_quantity,
                        "avg_fill_price": order.avg_fill_price,
                        "created_at": order.created_at.isoformat() + "Z",
                    })
                    loaded_count += 1

                logger.info(
                    "Loaded orders from DB",
                    loaded=loaded_count,
                    skipped_usdt=skipped_count,
                )
        except Exception as e:
            logger.error("Failed to load orders from DB", error=str(e))

    def get_orders(self, limit: int = 100) -> list:
        """주문 목록 조회 (KRW 마켓만)"""
        # USDT 심볼 필터링 (안전장치)
        krw_orders = [o for o in self._cached_orders if "USDT" not in o.get("symbol", "")]
        return krw_orders[-limit:]

    def add_order(
        self,
        symbol: str,
        strategy: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float = None,
        status: str = "NEW",
        filled_qty: float = 0,
        avg_fill_price: float = None,
        exchange_order_id: str = None,
        realized_pnl: float = 0.0,
    ) -> str:
        """주문 추가 (메모리 + DB)"""
        order_id = f"ORD-{len(self._cached_orders) + 1}-{datetime.utcnow().strftime('%H%M%S')}"
        order = {
            "order_id": order_id,
            "symbol": symbol,
            "strategy": strategy,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "price": price,
            "status": status,
            "filled_quantity": filled_qty,
            "avg_fill_price": avg_fill_price,
            "created_at": _utc_iso(),
        }
        self._cached_orders.append(order)

        # 최대 500개 유지
        if len(self._cached_orders) > 500:
            self._cached_orders = self._cached_orders[-500:]

        # DB에도 비동기로 기록 (fire-and-forget)
        asyncio.create_task(self._record_order_to_db(
            order_id=order_id,
            symbol=symbol,
            strategy=strategy,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            exchange_order_id=exchange_order_id,
            realized_pnl=realized_pnl,
        ))

        return order_id

    async def _record_order_to_db(
        self,
        order_id: str,
        symbol: str,
        strategy: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float = None,
        status: str = "NEW",
        filled_qty: float = 0,
        avg_fill_price: float = None,
        exchange_order_id: str = None,
        realized_pnl: float = 0.0,
    ) -> None:
        """주문을 DB에 기록"""
        try:
            await trade_recorder.record_order(
                order_id=order_id,
                symbol=symbol,
                strategy=strategy,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                filled_quantity=filled_qty,
                avg_fill_price=avg_fill_price,
                status=status,
                exchange_order_id=exchange_order_id,
            )

            # 체결된 경우 Trade도 기록 (avg_fill_price가 0이어도 기록)
            if status == "FILLED":
                trade_price = avg_fill_price if avg_fill_price and avg_fill_price > 0 else price or 0.0
                await trade_recorder.record_trade(
                    order_id=order_id,
                    symbol=symbol,
                    strategy=strategy,
                    side=side,
                    quantity=filled_qty if filled_qty and filled_qty > 0 else quantity,
                    price=trade_price,
                    fee=0.0,  # TODO: 실제 수수료 계산
                    realized_pnl=realized_pnl,
                )

                # 일일 통계 업데이트
                await trade_recorder.update_daily_stats(
                    trades_count=1,
                )

                # 포지션 상태 업데이트 (BUY는 포지션 오픈, SELL은 포지션 종료)
                await self._update_position_in_db(
                    symbol=symbol,
                    strategy=strategy,
                    side=side,
                    quantity=filled_qty if filled_qty and filled_qty > 0 else quantity,
                    price=trade_price,
                    realized_pnl=realized_pnl,
                )

        except Exception as e:
            logger.error("Failed to record order to DB", error=str(e))

    async def _update_position_in_db(
        self,
        symbol: str,
        strategy: str,
        side: str,
        quantity: float,
        price: float,
        realized_pnl: float = 0.0,
    ) -> None:
        """포지션 상태를 DB에 업데이트"""
        try:
            async with async_session() as session:
                if side == "BUY":
                    # BUY: 포지션 생성 or 평균가 업데이트
                    stmt = select(PositionModel).where(
                        PositionModel.symbol == symbol,
                        PositionModel.is_open == True,
                    )
                    result = await session.execute(stmt)
                    position = result.scalar_one_or_none()

                    if position:
                        # 기존 포지션에 추가 매수
                        total_quantity = position.quantity + quantity
                        position.avg_price = (
                            (position.avg_price * position.quantity + price * quantity) / total_quantity
                        )
                        position.quantity = total_quantity
                        logger.debug(
                            f"Updated position in DB: {symbol}",
                            avg_price=position.avg_price,
                            quantity=total_quantity,
                        )
                    else:
                        # 신규 포지션 생성
                        position = PositionModel(
                            symbol=symbol,
                            strategy=StrategyType(strategy),
                            side=OrderSide.BUY,
                            quantity=quantity,
                            avg_price=price,
                            realized_pnl=0.0,
                            is_open=True,
                            opened_at=datetime.utcnow(),
                        )
                        session.add(position)
                        logger.debug(f"Created position in DB: {symbol}", avg_price=price, quantity=quantity)

                    await session.commit()

                elif side == "SELL":
                    # SELL: 포지션 종료 (부분 청산은 미지원, 전체 청산으로 간주)
                    stmt = select(PositionModel).where(
                        PositionModel.symbol == symbol,
                        PositionModel.is_open == True,
                    )
                    result = await session.execute(stmt)
                    position = result.scalar_one_or_none()

                    if position:
                        position.is_open = False
                        position.closed_at = datetime.utcnow()
                        position.realized_pnl += realized_pnl
                        await session.commit()
                        logger.debug(
                            f"Closed position in DB: {symbol}",
                            realized_pnl=realized_pnl,
                        )
                    else:
                        logger.warning(f"SELL order but no open position in DB: {symbol}")

        except Exception as e:
            logger.error("Failed to update position in DB", symbol=symbol, error=str(e))

    def update_order(self, order_id: str, status: str, filled_qty: float = None, avg_price: float = None) -> None:
        """주문 상태 업데이트"""
        for order in self._cached_orders:
            if order["order_id"] == order_id:
                order["status"] = status
                if filled_qty is not None:
                    order["filled_quantity"] = filled_qty
                if avg_price is not None:
                    order["avg_fill_price"] = avg_price
                break

    def _on_stop_triggered(self, event: StopEvent) -> None:
        """
        v4.2: Stop Watchdog 콜백 - 손절 발생 시 시장가 청산

        Args:
            event: 손절 이벤트
        """
        logger.warning(
            "Stop triggered by watchdog",
            symbol=event.symbol,
            stop_type=event.stop_type.value,
            trigger_price=event.trigger_price,
            pnl_pct=f"{event.pnl_pct:.2%}",
        )

        # 이벤트 기록
        self.add_event(
            level="WARNING",
            event_type="STOP_WATCHDOG",
            message=f"Stop triggered: {event.symbol} ({event.stop_type.value})",
            details=event.to_dict(),
        )

        # 비동기 청산 실행
        asyncio.create_task(self._execute_stop_exit(event))

    async def _execute_stop_exit(self, event: StopEvent) -> None:
        """Stop Watchdog 손절 청산 실행"""
        try:
            symbol = event.symbol
            quantity = event.quantity

            # 시장가 매도 주문
            order_result = await self.exchange.create_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=quantity,
            )

            if order_result:
                logger.info(
                    "Stop exit order placed",
                    symbol=symbol,
                    quantity=quantity,
                    order_id=order_result.get("order_id"),
                )

                # Slack 알림
                if self.slack_notifier.is_enabled:
                    alert = SlackMessage(
                        level=AlertLevel.WARNING,
                        title=f"Stop Triggered: {symbol}",
                        message=f"Type: {event.stop_type.value}\nPnL: {event.pnl_pct:.2%}\nTrigger: {event.trigger_price:,.0f}",
                    )
                    await self.slack_notifier.send(alert)
            else:
                logger.error("Stop exit order failed", symbol=symbol)

        except Exception as e:
            logger.error("Stop exit execution error", symbol=event.symbol, error=str(e))

    def add_event(self, level: str, event_type: str, message: str, details: dict = None) -> None:
        """이벤트 추가"""
        event = {
            "id": str(len(self._cached_events) + 1),
            "timestamp": _utc_iso(),
            "level": level,
            "event_type": event_type,
            "message": message,
            "details": details,
        }
        self._cached_events.append(event)

        # 최대 1000개 유지
        if len(self._cached_events) > 1000:
            self._cached_events = self._cached_events[-1000:]

    async def _send_slack_notification(self, level: AlertLevel, title: str, message: str) -> None:
        """Slack 알림 전송 헬퍼 메서드"""
        if not self.slack_notifier.is_enabled:
            return

        try:
            await self.slack_notifier.send(SlackMessage(
                level=level,
                title=title,
                message=message,
            ))
        except Exception as e:
            logger.warning(f"Failed to send Slack notification: {e}")
