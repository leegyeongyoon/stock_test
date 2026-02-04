"""Trading Engine - 메인 엔진 루프"""

import asyncio
from datetime import datetime
from typing import Optional, Union

import structlog
from sqlalchemy import select

from src.config import get_settings
from src.models.database import OrderModel, async_session
from src.data.candle_manager import get_candle_manager
from src.data.symbol_manager import init_symbol_manager, get_symbol_manager
from src.engine.command_queue import Command, CommandQueue, CommandType
from src.exchange.upbit import UpbitExchange
# Binance exchanges removed - Upbit only
from src.features.feature_engine import FeatureEngine
from src.models.schemas import OrderSide, OrderType, StrategyType, TradingMode
from src.position import PositionStateMachine
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
# Core Carry 전략 비활성화 (Upbit 현물 전용)
# from src.strategies.core_carry import CoreCarryStrategy
from src.strategies.core_safety import get_core_safety_guard
from src.strategies.satellite import Regime, SatelliteStrategy
from src.strategies.attack_breakout import AttackBreakoutStrategy, get_attack_strategy
from src.strategies.pullback_strategy import PullbackStrategy, get_pullback_strategy
from src.strategies.ignition import IgnitionStrategy, get_ignition_strategy, SurgeDetector, get_surge_detector
from src.monitoring.slack import SlackNotifier, AlertLevel, SlackMessage
from src.portfolio.position_ledger import PositionLedger, FillEvent
from src.risk.exposure_manager import (
    ExposureManager,
    ExposureConfig,
    init_exposure_manager,
    get_exposure_manager,
)

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

        # 포지션 상태 머신 (Satellite 전략용)
        self.position_state_machine = PositionStateMachine(
            feature_engine=self.feature_engine,
            candle_manager=self.candle_manager,
            risk_engine=self.risk_engine,
        )

        # Risk Overlay (MDD 5% 방어)
        self.risk_overlay = get_risk_overlay(
            candle_manager=self.candle_manager,
            feature_engine=self.feature_engine,
        )
        self.exec_health = get_exec_health_monitor()
        self.core_safety = get_core_safety_guard()

        # 전략 (Upbit 현물 전용)
        self.core_strategy = None  # Core Carry 비활성화 (선물 헤지 불가)
        self.satellite_strategy = SatelliteStrategy()
        self.attack_strategy = get_attack_strategy()  # Attack 전략 (급등 추격 - 비권장)
        self.pullback_strategy = get_pullback_strategy()  # Pullback 전략 (눌림목 매수)
        self.ignition_strategy = get_ignition_strategy()  # v4.0 Ignition 전략 (전조 패턴 + 점화)
        self.surge_detector = get_surge_detector()  # 급등 시작 실시간 감지
        self.pullback_strategy.set_mode(settings.pullback_mode)  # 설정에서 모드 로드

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
        self._btc_regime: str = Regime.NEUTRAL

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

        # v5.2: 모니터링 캐시 (Attack Score Top 후보)
        self._attack_score_cache: dict[str, dict] = {}  # symbol -> score result
        self._attack_score_cache_time: Optional[datetime] = None

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

    def _prefilter_symbols_for_pullback(self, market_data: dict) -> list[str]:
        """Pullback 전략용 심볼 Pre-filtering (호가창 조회 대상 축소)"""
        candidates = []
        for sym, md in market_data.items():
            price_change = abs(md.get("price_change_pct", 0))
            volume_24h = md.get("volume_24h", 0)

            # 조건: 거래대금 > 1억 AND 변화율 0.5~15%
            if volume_24h > 100_000_000 and 0.5 < price_change < 15.0:
                candidates.append(sym)

        # 최대 20개로 제한 (Rate Limit 방지)
        return candidates[:20]

    def _reset_filter_stats_if_new_day(self) -> None:
        """새로운 날짜면 필터 통계 리셋"""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._filter_stats_date != today:
            self._filter_stats = {
                "anti_chase": 0,
                "exposure_limit": 0,
                "overheat": 0,
                "total": 0,
            }
            self._filter_stats_date = today

    def increment_filter_stat(self, filter_type: str) -> None:
        """필터링 통계 증가"""
        self._reset_filter_stats_if_new_day()
        if filter_type in self._filter_stats:
            self._filter_stats[filter_type] += 1
        self._filter_stats["total"] += 1

    def get_filter_stats(self) -> dict:
        """필터링 통계 조회"""
        self._reset_filter_stats_if_new_day()
        return self._filter_stats.copy()

    async def update_attack_score_cache(self) -> None:
        """Attack Score 캐시 업데이트 (상위 후보 추적용)"""
        if not self._is_upbit or not self.attack_strategy:
            return

        try:
            # 거래대금 상위 30개만 계산 (성능 최적화)
            sorted_symbols = sorted(
                self._market_data.items(),
                key=lambda x: x[1].get("volume_24h", 0),
                reverse=True
            )[:30]

            cache = {}
            for symbol, md in sorted_symbols:
                try:
                    # market_data에 change_rate 추가
                    market_data_for_score = {
                        **md,
                        "change_rate": md.get("price_change_pct", 0) / 100,
                    }
                    score_result = self.attack_strategy.get_attack_score(market_data_for_score)
                    cache[symbol] = {
                        "symbol": symbol,
                        "score": score_result.total_score,
                        "level": score_result.level,
                        "distance_to_entry": max(0, 80 - score_result.total_score),
                        "change_rate": md.get("price_change_pct", 0) / 100,
                        "volume_24h": md.get("volume_24h", 0),
                        "components": [c.to_dict() for c in score_result.components] if score_result.components else [],
                    }
                except Exception as e:
                    logger.debug(f"Failed to calculate attack score for {symbol}: {e}")
                    continue

            self._attack_score_cache = cache
            self._attack_score_cache_time = datetime.utcnow()

        except Exception as e:
            logger.warning("Failed to update attack score cache", error=str(e))

    def get_top_attack_candidates(self, limit: int = 5, min_score: int = 50) -> list[dict]:
        """상위 Attack 후보 반환 (점수 높은 순)"""
        if not self._attack_score_cache:
            return []

        # 점수로 정렬하여 상위 N개 반환
        sorted_candidates = sorted(
            self._attack_score_cache.values(),
            key=lambda x: x["score"],
            reverse=True
        )

        # min_score 이상만 필터링
        filtered = [c for c in sorted_candidates if c["score"] >= min_score]

        return filtered[:limit]

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

        # 초기 1분봉 데이터 로드 (SurgeDetector용)
        await self._load_initial_candles(qualified_symbols)

        # Upbit 기존 포지션 동기화 (서버 재시작 시 포지션 추적 복구)
        await self._sync_satellite_positions()

        # 메인 루프 시작
        self._running = True
        self._candle_update_counter = 0  # 캔들 업데이트 카운터
        self._main_task = asyncio.create_task(self._main_loop())

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

                # 2.5. 1분봉 데이터 갱신 (60초마다)
                self._candle_update_counter = getattr(self, "_candle_update_counter", 0) + 1
                if self._candle_update_counter >= 60:
                    self._candle_update_counter = 0
                    await self._refresh_candles()

                    # v4.2: KMVI 업데이트 (1분마다)
                    try:
                        symbols = self.symbol_manager.get_qualified_symbols()
                        self.risk_overlay.update_kmvi(symbols)
                    except Exception as e:
                        logger.warning("KMVI update failed", error=str(e))

                # v5.2: Attack Score 캐시 업데이트 (10초마다)
                self._attack_cache_counter = getattr(self, "_attack_cache_counter", 0) + 1
                if self._attack_cache_counter >= 10:
                    self._attack_cache_counter = 0
                    await self.update_attack_score_cache()

                # 3. 전략 실행 (NORMAL 모드에서만)
                can_open = self.risk_engine.can_open_position
                if not can_open:
                    logger.debug(f"Strategy execution skipped: can_open_position={can_open}, mode={self.risk_engine.mode}")
                if can_open:
                    await self._execute_strategies()

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
            # TODO: 포지션 정리 로직
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
                # 5분봉 로드 (v5.0: Candle Surge Bonus)
                await self._load_candles_for_symbol(symbol, "5", 50)
                loaded_count_5m += 1
            except Exception as e:
                print(f"[DEBUG] Failed to load 5m candles for {symbol}: {e}")
                logger.warning(f"Failed to load 5m candles for {symbol}: {e}")
            await asyncio.sleep(0.05)  # Rate limit 방지

        print(f"[DEBUG] Initial candle loading complete: 1m={loaded_count_1m}, 5m={loaded_count_5m}/{len(target_symbols)}")
        logger.info(
            "Initial 1m/5m candles loaded",
            symbols_count=len(target_symbols),
            loaded_1m=loaded_count_1m,
            loaded_5m=loaded_count_5m,
        )

    async def _refresh_candles(self) -> None:
        """1분봉/5분봉 데이터 갱신 (60초마다 호출)"""
        watch_symbols = self.symbol_manager.get_qualified_symbols()[:30]

        for symbol in watch_symbols:
            try:
                # 1분봉 갱신
                await self._load_candles_for_symbol(symbol, "1", 20)
            except Exception:
                pass
            await asyncio.sleep(0.03)  # Rate limit 방지

            try:
                # 5분봉 갱신 (v5.0: Candle Surge Bonus)
                await self._load_candles_for_symbol(symbol, "5", 12)
            except Exception:
                pass
            await asyncio.sleep(0.03)  # Rate limit 방지

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
            dt = datetime.fromisoformat(candle_time.replace("Z", "+00:00"))
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
        v5.0: 분봉 급등 데이터 계산

        Returns:
            dict with change_1m, rvol_1m, change_5m, rvol_5m
        """
        result = {
            "change_1m": 0.0,
            "rvol_1m": 1.0,
            "change_5m": 0.0,
            "rvol_5m": 1.0,
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
                }

            # BTC 레짐 계산 (Upbit: 가격 변화율 기반)
            btc_data = self._market_data.get("KRW-BTC", {})
            if btc_data:
                btc_change = btc_data.get("price_change_pct", 0)

                # 가격 변화율 기반 레짐 판단
                if btc_change > 3.0:  # +3% 이상 -> 상승장
                    self._btc_regime = Regime.BULLISH
                elif btc_change < -3.0:  # -3% 이하 -> 하락장
                    self._btc_regime = Regime.BEARISH
                elif abs(btc_change) > 5.0:  # 변동성 큼
                    self._btc_regime = Regime.VOLATILE
                else:
                    self._btc_regime = Regime.NEUTRAL

                self.satellite_strategy.update_btc_regime(self._btc_regime)

        except Exception as e:
            logger.error("Failed to update Upbit market data", error=str(e))

    async def _update_market_data_binance(self, watch_symbols: list[str]) -> None:
        """Binance 시장 데이터 업데이트 (기존 로직)"""
        # 일괄 조회 (개별 조회 대비 API 호출 90% 절감)
        perp_tickers = await self.perp_exchange.get_all_tickers()
        perp_books = await self.perp_exchange.get_all_book_tickers()
        funding_rates = await self.perp_exchange.get_all_funding_rates()

        # Spot도 일괄 조회 (FUTURES_ONLY 모드가 아닌 경우)
        spot_tickers = {}
        spot_books = {}
        if not settings.futures_only_mode:
            spot_tickers = await self.spot_exchange.get_all_tickers()
            spot_books = await self.spot_exchange.get_all_book_tickers()

        # 심볼별 데이터 업데이트
        for symbol in watch_symbols:
            perp_ticker = perp_tickers.get(symbol, {})
            perp_book = perp_books.get(symbol, {})
            spot_ticker = spot_tickers.get(symbol, {})
            spot_book = spot_books.get(symbol, {})
            funding_rate = funding_rates.get(symbol, 0)

            perp_last = perp_ticker.get("last", 0)
            spot_last = spot_ticker.get("last", perp_last)  # Spot 없으면 Perp 사용

            if perp_last > 0:
                # 24h 고점/저점 (돌파 기준)
                high_24h = perp_ticker.get("highPrice", perp_last * 1.005)
                low_24h = perp_ticker.get("lowPrice", perp_last * 0.995)

                # RVOL 계산: 가격 변동률 기반 추정
                price_change_pct = abs(perp_ticker.get("priceChangePercent", 0))
                estimated_rvol = 1.0 + (price_change_pct / 1.5)

                # VWAP 근사: (고점+저점+종가) / 3
                vwap_approx = (high_24h + low_24h + perp_last) / 3

                # ClosePos 계산
                price_range = high_24h - low_24h
                close_pos = (perp_last - low_24h) / price_range if price_range > 0 else 0.5

                self._market_data[symbol] = {
                    "symbol": symbol,
                    "spot_price": spot_last,
                    "perp_price": perp_last,
                    "spot_bid": spot_book.get("bid", spot_last),
                    "spot_ask": spot_book.get("ask", spot_last),
                    "perp_bid": perp_book.get("bid", perp_last),
                    "perp_ask": perp_book.get("ask", perp_last),
                    "funding_rate": funding_rate,
                    "price": perp_last,
                    "volume_24h": perp_ticker.get("quoteVolume", 0),
                    "high_20": high_24h,
                    "low_20": low_24h,
                    "highest_12_5m": high_24h,
                    "lowest_12_5m": low_24h,
                    "rvol": estimated_rvol,
                    "vwap": vwap_approx,
                    "close_pos": close_pos,
                    "price_change_pct": price_change_pct,
                    "timestamp": datetime.utcnow(),
                }

                # Core Safety Guard에 펀딩 레이트 업데이트
                self.core_safety.update_funding_rate(symbol, funding_rate)

        # BTC 레짐 계산 (Binance: 펀딩비 기반)
        btc_data = self._market_data.get("BTCUSDT", {})
        if btc_data:
            btc_funding = btc_data.get("funding_rate", 0)

            if btc_funding > 0.0003:
                self._btc_regime = Regime.BULLISH
            elif btc_funding < -0.0001:
                self._btc_regime = Regime.BEARISH
            else:
                self._btc_regime = Regime.NEUTRAL

            self.satellite_strategy.update_btc_regime(self._btc_regime)

    async def _execute_strategies(self) -> None:
        """전략 실행 (우선순위 체인 적용)"""
        try:
            # === 우선순위 1-3: Risk Overlay 평가 ===
            # 현재 자산 조회
            if self._is_upbit:
                # Upbit: KRW 잔고 + 보유 자산 가치
                krw_balance = await self.exchange.get_balance("KRW")
                all_balances = await self.exchange.get_all_balances()
                current_equity = krw_balance.total if krw_balance else 0
                # 보유 자산 가치 추가
                for bal in all_balances:
                    if bal.asset != "KRW" and bal.total > 0:
                        symbol = f"KRW-{bal.asset}"
                        market_data = self._market_data.get(symbol, {})
                        price = market_data.get("price", 0)
                        if price > 0:
                            current_equity += bal.total * price
            else:
                perp_balance = await self.perp_exchange.get_balance("USDT")
                spot_balance = await self.spot_exchange.get_balance("USDT")
                current_equity = (
                    (perp_balance.total if perp_balance else 0)
                    + (spot_balance.total if spot_balance else 0)
                )

            # Risk Overlay 평가
            risk_decision = self.risk_overlay.evaluate(current_equity)

            # User Mode 자동 다운그레이드 체크
            self.mode_manager.update_risk_state()

            # HALT 모드면 신규 진입 완전 차단
            if risk_decision.mode == RiskMode.HALT:
                logger.warning(
                    "Strategy execution blocked: HALT mode",
                    reason=risk_decision.primary_reason,
                )
                return

            for symbol, market_data in self._market_data.items():
                # Core 전략 비활성화 (Upbit 현물 전용 - 선물 헤지 불가)
                # if risk_decision.core_allowed and self.core_strategy:
                #     core_ok, core_reason = self.core_safety.can_open_core(symbol)
                #     if core_ok:
                #         core_signal = await self.core_strategy.generate_signal(market_data)
                #         if core_signal:
                #             await self._execute_signal(core_signal, market_data, risk_decision)
                pass  # Core 전략 스킵

                # Satellite 전략 (모멘텀) - Risk Overlay 체크
                if risk_decision.satellite_allowed:
                    sat_signal = await self.satellite_strategy.generate_signal(market_data)
                    if sat_signal:
                        await self._execute_signal(sat_signal, market_data, risk_decision)

                # Attack 전략 (급등주 공격) - Upbit 전용
                if self._is_upbit and self.attack_strategy and risk_decision.satellite_allowed:
                    # Attack 전략에 리스크 상태 업데이트
                    dd_tier = getattr(self.risk_overlay, '_dd_tier', 0)
                    daily_loss = getattr(self.risk_overlay, '_daily_loss_pct', 0)
                    btc_regime = self._btc_regime if isinstance(self._btc_regime, str) else self._btc_regime.value

                    self.attack_strategy.update_risk_state(
                        dd_tier=dd_tier,
                        daily_loss=daily_loss,
                        regime=btc_regime,
                        is_volatile=(self._btc_regime == Regime.VOLATILE),
                    )

                    # market_data에 equity 추가
                    market_data_with_equity = {
                        **market_data,
                        "equity": current_equity,
                        "change_rate": market_data.get("price_change_pct", 0) / 100,  # % to ratio
                    }

                    attack_signal = await self.attack_strategy.generate_signal(market_data_with_equity)
                    if attack_signal:
                        await self._execute_attack_signal(attack_signal, market_data, risk_decision)

            # === Pullback 전략 (눌림목 매수) - Upbit 전용 ===
            # Note: Pullback은 Satellite와 독립적으로 운영됨
            if self._is_upbit and self.pullback_strategy and self.pullback_strategy.is_enabled():
                pullback_allowed = risk_decision.mode not in [RiskMode.HALT]
                if pullback_allowed:
                    # Pre-filtering: 호가창 조회 대상 축소 (Rate Limit 방지)
                    candidates = self._prefilter_symbols_for_pullback(self._market_data)
                    logger.debug(f"Pullback candidates: {len(candidates)} symbols (pre-filtered)")

                    # market_data에 호가 정보 추가 (캐시 사용)
                    enhanced_market_data = {}
                    for sym in candidates:
                        md = self._market_data.get(sym, {})
                        # 캐시된 호가창 조회 (Rate Limit 방지)
                        orderbook = await self._get_orderbook_cached(sym)
                        if orderbook:
                            bid_volume = sum(b[1] for b in orderbook.get("bids", [])[:5])
                            ask_volume = sum(a[1] for a in orderbook.get("asks", [])[:5])
                        else:
                            bid_volume = 0
                            ask_volume = 0

                        enhanced_market_data[sym] = {
                            **md,
                            "bid_volume": bid_volume,
                            "ask_volume": ask_volume,
                            "highest_24h": md.get("high_20", md.get("price", 0) * 1.01),
                            "lowest_24h": md.get("low_20", md.get("price", 0) * 0.99),
                            "change_rate": md.get("price_change_pct", 0) / 100,
                            "change_1h": md.get("price_change_pct", 0) / 100 / 24,  # 근사
                            "change_5m": 0,  # TODO: 5분 변화율 추가
                            "sma_20": md.get("vwap", 0),  # VWAP을 SMA 근사로 사용
                        }

                    # 눌림목 시그널 스캔 (pre-filtered 심볼만)
                    pullback_signals = await self.pullback_strategy.scan_for_signals(
                        symbols=candidates,
                        market_data_map=enhanced_market_data,
                    )

                    # 시그널 실행
                    for signal in pullback_signals:
                        await self._execute_pullback_signal(signal, enhanced_market_data.get(signal.symbol, {}), risk_decision, current_equity)

            # === v4.0 Ignition 전략 (전조 패턴 + 점화) - Upbit 전용 ===
            # Note: Ignition은 Satellite와 독립적으로 운영됨 (satellite_enabled와 무관)
            logger.info(f"[IGNITION CHECK] is_upbit={self._is_upbit}, strategy={self.ignition_strategy is not None}, mode={settings.ignition_mode}")
            if self._is_upbit and self.ignition_strategy and settings.ignition_mode != "OFF":
                # Ignition은 SAFE/HALT 모드에서만 차단, satellite_enabled와 무관
                ignition_allowed = risk_decision.mode not in [RiskMode.HALT]
                logger.info(f"[IGNITION] ignition_allowed={ignition_allowed}, risk_mode={risk_decision.mode}")
                if ignition_allowed:
                    try:
                        # BTC 24시간 변화율 설정 (상대강도 계산용)
                        btc_data = self._market_data.get("KRW-BTC", {})
                        btc_change_24h = btc_data.get("price_change_pct", 0) / 100
                        self.ignition_strategy.set_btc_change(btc_change_24h)
                        self.ignition_strategy.set_account_balance(current_equity)
                        self.ignition_strategy.set_mode(settings.ignition_mode)

                        # Setup 스캔 (항상 실행)
                        logger.info("[IGNITION] Starting setup scan...")
                        qualified_symbols = self.symbol_manager.get_qualified_symbols()
                        logger.info(f"[IGNITION] Qualified symbols: {len(qualified_symbols)}")
                        new_candidates = await self.ignition_strategy.scan_setups(
                            symbols=qualified_symbols,
                            market_data_map=self._market_data,
                        )
                        if new_candidates:
                            logger.info(
                                "Ignition setup candidates found",
                                count=len(new_candidates),
                                symbols=[c.symbol for c in new_candidates],
                            )

                        # Watchlist 종목들 점화 체크 (실시간)
                        watchlist = self.ignition_strategy.get_watchlist()
                        for candidate in watchlist:
                            symbol = candidate.symbol
                            md = self._market_data.get(symbol, {})
                            if not md:
                                continue

                            # 점화 신호 체크
                            ignition_signal = self.ignition_strategy.check_ignition(symbol, md)
                            if ignition_signal:
                                # 호가 정보 조회
                                orderbook = await self._get_orderbook_cached(symbol)
                                bid_price = orderbook.get("bids", [[0]])[0][0] if orderbook else md.get("price", 0)
                                ask_price = orderbook.get("asks", [[0]])[0][0] if orderbook else md.get("price", 0)
                                current_price = md.get("price", 0)

                                # 진입 시도 (Anti-Chase Gate 포함)
                                position = self.ignition_strategy.try_entry(
                                    signal=ignition_signal,
                                    current_price=current_price,
                                    bid_price=bid_price,
                                    ask_price=ask_price,
                                )

                                if position:
                                    # 실제 주문 실행
                                    await self._execute_ignition_entry(position, md, risk_decision)

                        # 기존 포지션 청산 체크
                        for pos in self.ignition_strategy.get_all_positions():
                            symbol = pos.symbol
                            md = self._market_data.get(symbol, {})
                            current_price = md.get("price", 0)

                            exit_result = self.ignition_strategy.check_exits(symbol, current_price)
                            if exit_result:
                                exit_reason, exit_pct = exit_result
                                await self._execute_ignition_exit(pos, exit_reason, exit_pct, md)

                        # 정리
                        self.ignition_strategy.cleanup()

                    except Exception as e:
                        logger.error("Ignition strategy error", error=str(e))

            # === Surge Detector (급등 시작 실시간 감지) - Upbit 전용 ===
            # Note: Surge Detector는 Satellite와 독립적으로 운영됨
            if self._is_upbit and self.surge_detector and settings.ignition_mode != "OFF":
                surge_allowed = risk_decision.mode not in [RiskMode.HALT]
                if surge_allowed:
                    try:
                        # 전체 심볼 스캔하여 급등 시작 감지
                        qualified_symbols = self.symbol_manager.get_qualified_symbols()
                        surge_signals = self.surge_detector.scan_all_symbols(
                            symbols=qualified_symbols,
                            market_data_map=self._market_data,
                        )

                        # 급등 신호 처리
                        for surge in surge_signals:
                            await self._execute_surge_entry(surge, self._market_data.get(surge.symbol, {}), risk_decision, current_equity)

                        # 기존 Surge 포지션 청산 체크
                        for pos in self.surge_detector.get_positions():
                            symbol = pos.symbol
                            md = self._market_data.get(symbol, {})
                            current_price = md.get("price", 0)

                            exit_result = self.surge_detector.check_exit(symbol, current_price)
                            if exit_result:
                                exit_reason, exit_pct = exit_result
                                await self._execute_surge_exit(symbol, pos, exit_reason, exit_pct, current_price)

                        # 만료 신호 정리
                        self.surge_detector.clear_expired()

                    except Exception as e:
                        logger.error("Surge detector error", error=str(e))

        except Exception as e:
            logger.error("Strategy execution error", error=str(e))

    async def _execute_signal(self, signal, market_data: dict, risk_decision=None) -> None:
        """시그널 실행 - 실제 주문 발행 (Risk Overlay 적용)"""
        try:
            # 이벤트 기록
            self.add_event(
                level="INFO",
                event_type="STRATEGY",
                message=f"Signal: {signal.strategy.value} {signal.signal_type.value} {signal.symbol}",
                details={
                    "side": signal.side.value,
                    "reason": signal.reason,
                    "confidence": signal.confidence,
                },
            )

            logger.info(
                "Signal generated",
                strategy=signal.strategy.value,
                signal_type=signal.signal_type.value,
                symbol=signal.symbol,
                side=signal.side.value,
                reason=signal.reason,
            )

            # 잔고 확인
            if self._is_upbit:
                # Upbit: KRW 잔고 사용
                krw_balance = await self.exchange.get_balance("KRW")
                available_capital = krw_balance.free if krw_balance else 0
            else:
                perp_balance = await self.perp_exchange.get_balance("USDT")
                perp_free = perp_balance.free if perp_balance else 0

                # FUTURES_ONLY 모드면 Futures 잔고만 사용
                if settings.futures_only_mode:
                    available_capital = perp_free
                else:
                    spot_balance = await self.spot_exchange.get_balance("USDT")
                    spot_free = spot_balance.free if spot_balance else 0
                    available_capital = spot_free + perp_free

            # Risk Overlay 사이징 배수 적용
            if risk_decision:
                available_capital *= risk_decision.sizing_multiplier
                if risk_decision.sizing_multiplier < 1.0:
                    logger.info(
                        "Capital adjusted by risk overlay",
                        original=available_capital / risk_decision.sizing_multiplier,
                        adjusted=available_capital,
                        multiplier=risk_decision.sizing_multiplier,
                    )

            # 최소 자본 체크
            min_capital = 10000 if self._is_upbit else 100  # Upbit: 1만원, Binance: $100
            if available_capital < min_capital:
                logger.warning("Insufficient capital", available=available_capital, min_required=min_capital)
                return

            # Core 전략 잔고 체크 (Upbit은 KRW, Binance는 Spot USDT)
            if signal.strategy == StrategyType.CORE:
                if self._is_upbit:
                    # Upbit: Core는 Defensive Core (현금 비중 관리)
                    # 별도의 잔고 체크 없음 - available_capital이 KRW 잔고
                    pass
                elif not settings.futures_only_mode:
                    spot_balance = await self.spot_exchange.get_balance("USDT")
                    spot_free = spot_balance.free if spot_balance else 0

                    spot_price = market_data.get("spot_price", 0)
                    if spot_price <= 0:
                        logger.warning("Invalid spot price", symbol=signal.symbol)
                        return

                    estimated_order_value = (available_capital * 0.1) * 1.1

                    if spot_free < estimated_order_value:
                        logger.warning(
                            "Insufficient Spot balance for Core strategy",
                            symbol=signal.symbol,
                            spot_free=spot_free,
                            estimated_needed=estimated_order_value,
                        )
                        self.add_event(
                            level="WARNING",
                            event_type="RISK",
                            message=f"Skipped Core entry: insufficient Spot balance ({spot_free:.2f} USDT)",
                            details={
                                "symbol": signal.symbol,
                                "spot_balance": spot_free,
                                "needed": estimated_order_value,
                            },
                        )
                        return

            # 포지션 사이즈 계산
            symbol = signal.symbol
            current_price = market_data.get("perp_price") or market_data.get("price", 0)

            if signal.strategy == StrategyType.CORE:
                # Core 전략 비활성화 - Satellite 로직 사용
                quantity = self.satellite_strategy.get_position_size(signal, available_capital)
            else:
                quantity = self.satellite_strategy.get_position_size(signal, available_capital)

            # 최소 notional 보장
            if self._is_upbit:
                # Upbit: 최소 5000 KRW
                MIN_NOTIONAL = 5500  # 5000 + 여유분
            else:
                # Binance Futures: 최소 100 USDT
                MIN_NOTIONAL = 105

            if current_price > 0:
                min_quantity = MIN_NOTIONAL / current_price
                if quantity < min_quantity:
                    logger.info(
                        "Adjusting quantity to meet min notional",
                        original=quantity,
                        min_required=min_quantity,
                        min_notional=MIN_NOTIONAL,
                    )
                    quantity = min_quantity

            if quantity <= 0:
                logger.warning("Position size too small", quantity=quantity)
                return

            # 자본 대비 최대 한도 체크 (User Mode 설정 적용)
            mode_config = self.mode_manager.get_config()
            max_position_pct = mode_config.max_position_pct  # SAFE:3%, BALANCED:5%, AGGRESSIVE:8%
            max_notional = available_capital * max_position_pct
            max_quantity = max_notional / current_price if current_price > 0 else 0
            if quantity > max_quantity and max_quantity > 0:
                logger.info(
                    "Capping quantity to max exposure (User Mode)",
                    original=quantity,
                    capped=max_quantity,
                    mode=self.mode_manager.effective_mode.value,
                    max_pct=max_position_pct,
                )
                quantity = max_quantity

            # Notional 최종 체크
            notional = quantity * current_price
            min_notional_final = 5000 if self._is_upbit else 100
            if notional < min_notional_final:
                logger.warning(
                    "Notional too small after adjustments",
                    notional=notional,
                    quantity=quantity,
                    min_required=min_notional_final,
                )
                return

            # 심볼별 최소 수량 및 정밀도 처리
            pre_round_qty = quantity
            quantity = self._round_quantity(symbol, quantity)

            logger.info(
                "Quantity after rounding",
                symbol=symbol,
                pre_round=pre_round_qty,
                post_round=quantity,
                current_price=current_price,
                notional=quantity * current_price if current_price > 0 else 0,
            )

            if quantity <= 0:
                logger.warning("Quantity is zero after rounding", symbol=symbol)
                return

            # PAPER 모드 체크 - 실제 거래 스킵
            if not self.mode_manager.should_execute_trades():
                logger.info(
                    "PAPER mode - skipping actual trade execution",
                    symbol=symbol,
                    strategy=signal.strategy.value,
                    side=signal.side.value,
                    quantity=quantity,
                )
                self.add_event(
                    level="INFO",
                    event_type="PAPER",
                    message=f"[PAPER] Would have {signal.side.value}: {symbol}",
                    details={
                        "strategy": signal.strategy.value,
                        "quantity": quantity,
                        "price": current_price,
                        "notional": quantity * current_price,
                    },
                )
                return

            # P0: ExposureManager 노출 체크 (Satellite/Core 포함)
            if signal.strategy != StrategyType.ATTACK:  # Attack은 별도 처리
                order_amount = quantity * current_price

                # 잔고 조회 (Upbit)
                if self._is_upbit:
                    krw_bal = await self.exchange.get_balance("KRW")
                    total_equity = krw_bal.total if krw_bal else 0
                    available_cash = krw_bal.free if krw_bal else 0
                else:
                    total_equity = available_capital
                    available_cash = available_capital

                exposure_check = await self.exposure_manager.can_open_position(
                    symbol=symbol,
                    strategy_id=signal.strategy.value,
                    order_amount=order_amount,
                    total_equity=total_equity,
                    available_cash=available_cash,
                )

                if not exposure_check.allowed:
                    logger.warning(
                        "Entry blocked by ExposureManager",
                        symbol=symbol,
                        strategy=signal.strategy.value,
                        reason=exposure_check.reason,
                        current_positions=exposure_check.current_positions,
                    )
                    self.add_event(
                        level="WARNING",
                        event_type="FILTER",
                        message=f"{symbol} 진입 차단: {exposure_check.reason}",
                        details={
                            "symbol": symbol,
                            "strategy": signal.strategy.value,
                            "filter_type": "EXPOSURE_LIMIT",
                            "order_amount": order_amount,
                        },
                    )
                    self.increment_filter_stat("exposure_limit")
                    return

                # 조정된 금액으로 수량 재계산
                if exposure_check.adjusted_amount < order_amount:
                    logger.info(
                        "Order amount adjusted by ExposureManager",
                        original=order_amount,
                        adjusted=exposure_check.adjusted_amount,
                        reason=exposure_check.reason,
                    )
                    quantity = exposure_check.adjusted_amount / current_price
                    quantity = self._round_quantity(symbol, quantity)
                    if quantity <= 0:
                        logger.warning("Quantity zero after exposure adjustment")
                        return

            # Core 전략
            if signal.strategy == StrategyType.CORE:
                if self._is_upbit:
                    # Upbit: Defensive Core (현물 매수/매도)
                    await self._execute_upbit_spot_entry(signal, market_data, quantity)
                else:
                    # Binance: 현물 매수 + 선물 매도 (캐시앤캐리)
                    await self._execute_core_entry(signal, market_data, quantity)
            # Satellite 전략
            else:
                if self._is_upbit:
                    # Upbit: 현물 롱 only
                    if signal.side == OrderSide.BUY:
                        await self._execute_upbit_spot_entry(signal, market_data, quantity)
                    else:
                        logger.info("Upbit does not support short selling, skipping SELL signal")
                else:
                    # Binance: 선물 롱/숏
                    await self._execute_satellite_entry(signal, market_data, quantity)

        except Exception as e:
            logger.error("Signal execution error", error=str(e))
            self.add_event(
                level="ERROR",
                event_type="ORDER",
                message=f"Order execution failed: {signal.symbol}",
                details={"error": str(e)},
            )

    async def _execute_core_entry(self, signal, market_data: dict, quantity: float) -> None:
        """Core 전략 진입

        futures_only_mode:
        - True (테스트): Futures Long + Futures Short (같은 심볼, 헤지 테스트용)
        - False (Live): Spot Buy + Futures Short (실제 캐시앤캐리)
        """
        symbol = signal.symbol

        # Futures-only 모드 체크
        if settings.futures_only_mode:
            await self._execute_core_entry_futures_only(signal, market_data, quantity)
        else:
            await self._execute_core_entry_spot_perp(signal, market_data, quantity)

    async def _execute_core_entry_futures_only(self, signal, market_data: dict, quantity: float) -> None:
        """Core 전략 진입 - Futures Only 모드 (테스트용)

        Spot 대신 Futures Long을 사용하여 헤지 로직 테스트
        실제로는 Long + Short = 포지션 상쇄되지만, 로직 검증용
        """
        symbol = signal.symbol
        perp_qty = self._round_quantity_for_perp(symbol, quantity)

        if perp_qty <= 0:
            logger.warning("Perp quantity too small", symbol=symbol)
            return

        logger.info(
            "Executing Core entry (FUTURES_ONLY mode)",
            symbol=symbol,
            quantity=perp_qty,
        )

        # 1. Futures Long (Spot 대체)
        long_result = await self.perp_exchange.place_order(
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=perp_qty,
        )

        if not long_result.success:
            logger.error("Futures Long failed", symbol=symbol, error=long_result.error)
            self.add_event(
                level="ERROR",
                event_type="ORDER",
                message=f"Futures Long failed (FUTURES_ONLY): {symbol}",
                details={"error": long_result.error},
            )
            return

        logger.info(
            "Futures Long filled",
            symbol=symbol,
            filled_qty=long_result.filled_qty,
            avg_price=long_result.avg_price,
        )

        # Long 진입 주문 기록
        self.add_order(
            symbol=symbol,
            strategy="CORE",
            side="BUY",
            order_type="MARKET",
            quantity=perp_qty,
            price=long_result.avg_price,
            status="FILLED" if long_result.filled_qty > 0 else "REJECTED",
            filled_qty=long_result.filled_qty,
            avg_fill_price=long_result.avg_price,
            exchange_order_id=long_result.exchange_order_id,
        )

        # 2. Futures Short (헤지)
        short_result = await self.perp_exchange.place_order(
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=perp_qty,
        )

        if not short_result.success:
            logger.error(
                "Futures Short hedge failed - SAFE MODE",
                symbol=symbol,
                error=short_result.error,
            )
            self.add_event(
                level="CRITICAL",
                event_type="RISK",
                message=f"Hedge failed! Long opened but Short failed: {symbol}",
                details={
                    "long_filled": long_result.filled_qty,
                    "error": short_result.error,
                },
            )
            await self.risk_engine.pause("Hedge failure in FUTURES_ONLY mode")
            return

        logger.info(
            "Futures Short hedge completed",
            symbol=symbol,
            filled_qty=short_result.filled_qty,
            avg_price=short_result.avg_price,
        )

        # Short 헤지 주문 기록
        self.add_order(
            symbol=symbol,
            strategy="CORE",
            side="SELL",
            order_type="MARKET",
            quantity=perp_qty,
            price=short_result.avg_price,
            status="FILLED" if short_result.filled_qty > 0 else "REJECTED",
            filled_qty=short_result.filled_qty,
            avg_fill_price=short_result.avg_price,
            exchange_order_id=short_result.exchange_order_id,
        )

        self.add_event(
            level="INFO",
            event_type="ORDER",
            message=f"Core entry completed (FUTURES_ONLY): {symbol}",
            details={
                "long_qty": long_result.filled_qty,
                "long_price": long_result.avg_price,
                "short_qty": short_result.filled_qty,
                "short_price": short_result.avg_price,
                "mode": "FUTURES_ONLY",
            },
        )

    async def _execute_core_entry_spot_perp(self, signal, market_data: dict, quantity: float) -> None:
        """Core 전략 진입 - Spot + Perp 모드 (Live용)"""
        symbol = signal.symbol

        logger.info(
            "Executing Core entry (SPOT+PERP mode)",
            symbol=symbol,
            quantity=quantity,
        )

        # 1. 현물 시장가 매수
        spot_result = await self.spot_exchange.place_order(
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=quantity,
        )

        if not spot_result.success:
            logger.error("Spot order failed", symbol=symbol, error=spot_result.error)
            self.add_event(
                level="ERROR",
                event_type="ORDER",
                message=f"Spot buy failed: {symbol}",
                details={"error": spot_result.error},
            )
            return

        logger.info(
            "Spot order filled",
            symbol=symbol,
            filled_qty=spot_result.filled_qty,
            avg_price=spot_result.avg_price,
        )

        # Spot 매수 진입 주문 기록
        self.add_order(
            symbol=symbol,
            strategy="CORE",
            side="BUY",
            order_type="MARKET",
            quantity=quantity,
            price=spot_result.avg_price,
            status="FILLED" if spot_result.filled_qty > 0 else "REJECTED",
            filled_qty=spot_result.filled_qty,
            avg_fill_price=spot_result.avg_price,
            exchange_order_id=spot_result.exchange_order_id,
        )

        # 2. 선물 시장가 매도 (숏)
        perp_qty = self._round_quantity_for_perp(symbol, spot_result.filled_qty)

        if perp_qty <= 0:
            logger.error("Perp quantity too small after rounding", original=spot_result.filled_qty)
            await self.risk_engine.pause("Hedge failed - perp quantity too small")
            return

        perp_result = await self.perp_exchange.place_order(
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=perp_qty,
        )

        if not perp_result.success:
            logger.error(
                "Perp hedge failed - SAFE MODE TRIGGERED",
                symbol=symbol,
                error=perp_result.error,
            )
            self.add_event(
                level="CRITICAL",
                event_type="RISK",
                message=f"Hedge failed! Spot bought but perp sell failed: {symbol}",
                details={
                    "spot_filled": spot_result.filled_qty,
                    "error": perp_result.error,
                },
            )
            await self.risk_engine.pause("Hedge failure - spot bought without perp hedge")
            return

        logger.info(
            "Perp hedge completed",
            symbol=symbol,
            filled_qty=perp_result.filled_qty,
            avg_price=perp_result.avg_price,
        )

        # Perp 매도 헤지 주문 기록
        self.add_order(
            symbol=symbol,
            strategy="CORE",
            side="SELL",
            order_type="MARKET",
            quantity=perp_qty,
            price=perp_result.avg_price,
            status="FILLED" if perp_result.filled_qty > 0 else "REJECTED",
            filled_qty=perp_result.filled_qty,
            avg_fill_price=perp_result.avg_price,
            exchange_order_id=perp_result.exchange_order_id,
        )

        self.add_event(
            level="INFO",
            event_type="ORDER",
            message=f"Core entry completed: {symbol}",
            details={
                "spot_qty": spot_result.filled_qty,
                "spot_price": spot_result.avg_price,
                "perp_qty": perp_result.filled_qty,
                "perp_price": perp_result.avg_price,
            },
        )

    async def _execute_satellite_entry(self, signal, market_data: dict, quantity: float) -> None:
        """Satellite 전략 진입 - 선물만"""
        symbol = signal.symbol
        side = signal.side

        logger.info(
            "Executing Satellite entry",
            symbol=symbol,
            side=side.value,
            quantity=quantity,
        )

        # 선물 시장가 주문
        result = await self.perp_exchange.place_order(
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
        )

        if not result.success:
            logger.error("Satellite order failed", symbol=symbol, error=result.error)
            self.add_event(
                level="ERROR",
                event_type="ORDER",
                message=f"Satellite {side.value} failed: {symbol}",
                details={"error": result.error},
            )
            return

        logger.info(
            "Satellite order filled",
            symbol=symbol,
            side=side.value,
            filled_qty=result.filled_qty,
            avg_price=result.avg_price,
        )

        # Satellite 진입 주문 기록
        self.add_order(
            symbol=symbol,
            strategy="SATELLITE",
            side=side.value,
            order_type="MARKET",
            quantity=quantity,
            price=result.avg_price,
            status="FILLED" if result.filled_qty > 0 else "REJECTED",
            filled_qty=result.filled_qty,
            avg_fill_price=result.avg_price,
            exchange_order_id=result.exchange_order_id,
        )

        # Position State Machine에 등록
        atr_5m = self.candle_manager.calc_atr(symbol, "5m", 14) or 0
        if atr_5m > 0:
            self.position_state_machine.create_position(
                symbol=symbol,
                side="LONG" if side == OrderSide.BUY else "SHORT",
                entry_price=result.avg_price,
                quantity=result.filled_qty,
                atr_5m=atr_5m,
            )
            logger.info(
                "Position registered with PSM",
                symbol=symbol,
                side="LONG" if side == OrderSide.BUY else "SHORT",
                atr=atr_5m,
            )

        self.add_event(
            level="INFO",
            event_type="ORDER",
            message=f"Satellite {side.value} completed: {symbol}",
            details={
                "quantity": result.filled_qty,
                "price": result.avg_price,
            },
        )

    async def _execute_upbit_spot_entry(self, signal, market_data: dict, quantity: float) -> None:
        """Upbit 현물 진입 (Core/Satellite 공용)"""
        symbol = signal.symbol
        side = signal.side

        # Upbit은 롱 only - 매도는 청산용으로만
        if side == OrderSide.SELL:
            logger.info("Upbit SELL order - treating as position close")

        logger.info(
            "Executing Upbit spot entry",
            symbol=symbol,
            side=side.value,
            quantity=quantity,
            strategy=signal.strategy.value,
        )

        # 시장가 주문 (Upbit은 매수 시 금액 기준)
        if side == OrderSide.BUY:
            # 매수: 금액 기준으로 주문
            current_price = market_data.get("price", 0)
            order_amount = quantity * current_price  # KRW 금액
            result = await self.exchange.place_order(
                symbol=symbol,
                side=side,
                order_type=OrderType.MARKET,
                quantity=order_amount,  # KRW 금액
            )
        else:
            # 매도: 수량 기준으로 주문
            result = await self.exchange.place_order(
                symbol=symbol,
                side=side,
                order_type=OrderType.MARKET,
                quantity=quantity,
            )

        if not result.success:
            logger.error("Upbit order failed", symbol=symbol, error=result.error)
            self.add_event(
                level="ERROR",
                event_type="ORDER",
                message=f"Upbit {side.value} failed: {symbol}",
                details={"error": result.error},
            )
            return

        logger.info(
            "Upbit order filled",
            symbol=symbol,
            side=side.value,
            filled_qty=result.filled_qty,
            avg_price=result.avg_price,
        )

        # 주문 기록
        self.add_order(
            symbol=symbol,
            strategy=signal.strategy.value,
            side=side.value,
            order_type="MARKET",
            quantity=quantity,
            price=result.avg_price,
            status="FILLED" if result.filled_qty > 0 else "REJECTED",
            filled_qty=result.filled_qty,
            avg_fill_price=result.avg_price,
            exchange_order_id=result.exchange_order_id,
        )

        # P0: 단일 진실 원장에 체결 기록
        if result.filled_qty > 0:
            current_price = market_data.get("price", result.avg_price)
            atr_5m = current_price * 0.01  # 1% as temp ATR
            initial_stop = current_price - (atr_5m * 2.0) if side == OrderSide.BUY else None

            fill_event = FillEvent(
                order_id=result.order_id or str(result.exchange_order_id),
                exchange_order_id=result.exchange_order_id or "",
                position_id=None,  # 신규 포지션
                symbol=symbol,
                strategy_id=signal.strategy.value,
                side="BUY" if side == OrderSide.BUY else "SELL",
                filled_quantity=result.filled_qty,
                fill_price=result.avg_price,
                fee=result.commission or 0,
                fee_asset="KRW",
                timestamp=datetime.utcnow(),
                requested_price=current_price,
                spread_bps_at_fill=0,  # TODO: 호가창에서 계산
                initial_stop_price=initial_stop,
            )

            try:
                if side == OrderSide.BUY:
                    await self.position_ledger.on_buy_fill(fill_event)
                else:
                    await self.position_ledger.on_sell_fill(fill_event)
                logger.info(
                    "Fill recorded to PositionLedger",
                    symbol=symbol,
                    side=side.value,
                    qty=result.filled_qty,
                )
            except Exception as e:
                logger.error("Failed to record fill to ledger", error=str(e))

        # Satellite의 경우 Position State Machine에 등록
        if signal.strategy == StrategyType.SATELLITE and side == OrderSide.BUY:
            # Upbit용 ATR 계산 (캔들 데이터 필요)
            atr_5m = 0  # TODO: Upbit 캔들 기반 ATR 계산
            if result.avg_price > 0:
                # 가격의 1%를 임시 ATR로 사용
                atr_5m = result.avg_price * 0.01

            if atr_5m > 0:
                self.position_state_machine.create_position(
                    symbol=symbol,
                    side="LONG",
                    entry_price=result.avg_price,
                    quantity=result.filled_qty,
                    atr_5m=atr_5m,
                )
                logger.info(
                    "Upbit position registered with PSM",
                    symbol=symbol,
                    side="LONG",
                    atr=atr_5m,
                )

        self.add_event(
            level="INFO",
            event_type="ORDER",
            message=f"Upbit {signal.strategy.value} {side.value} completed: {symbol}",
            details={
                "quantity": result.filled_qty,
                "price": result.avg_price,
            },
        )

    async def _execute_attack_signal(self, signal, market_data: dict, risk_decision=None) -> None:
        """Attack 시그널 실행 (Upbit 전용)"""
        try:
            symbol = signal.symbol
            current_price = market_data.get("price", 0)
            metadata = signal.metadata or {}

            logger.info(
                "Executing Attack signal",
                symbol=symbol,
                level=metadata.get("attack_level"),
                score=metadata.get("attack_score"),
                tranche=metadata.get("tranche"),
                quantity=signal.quantity,
            )

            # 이벤트 기록
            self.add_event(
                level="INFO",
                event_type="ATTACK",
                message=f"Attack entry: {symbol} L{metadata.get('attack_level', 0)}",
                details={
                    "score": metadata.get("attack_score"),
                    "tranche": metadata.get("tranche"),
                    "quantity": signal.quantity,
                    "stop_price": metadata.get("stop_price"),
                },
            )

            # 매수 금액 계산 (Upbit은 금액 기준)
            order_amount = signal.quantity * current_price

            # 최소 주문 금액 체크
            if order_amount < 5000:
                logger.warning("Attack order too small", amount=order_amount)
                return

            # P2: ExposureManager 노출 체크
            balance = await self.exchange.get_balance()
            total_equity = balance.total if balance else 0
            available_cash = balance.free if balance else 0

            exposure_check = await self.exposure_manager.can_open_position(
                symbol=symbol,
                strategy_id="ATTACK",
                order_amount=order_amount,
                total_equity=total_equity,
                available_cash=available_cash,
            )

            if not exposure_check.allowed:
                logger.warning(
                    "Attack entry blocked by ExposureManager",
                    symbol=symbol,
                    reason=exposure_check.reason,
                    current_exposure=exposure_check.current_exposure,
                )
                self.add_event(
                    level="WARNING",
                    event_type="FILTER",
                    message=f"{symbol} Attack 차단: {exposure_check.reason}",
                    details={
                        "symbol": symbol,
                        "strategy": "ATTACK",
                        "filter_type": "EXPOSURE_LIMIT",
                        "order_amount": order_amount,
                    },
                )
                self.increment_filter_stat("exposure_limit")
                return

            # 조정된 금액 사용
            if exposure_check.adjusted_amount < order_amount:
                logger.info(
                    "Attack order amount adjusted",
                    original=order_amount,
                    adjusted=exposure_check.adjusted_amount,
                    reason=exposure_check.reason,
                )
                order_amount = exposure_check.adjusted_amount

            # 시장가 매수
            result = await self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=order_amount,  # KRW 금액
            )

            if not result.success:
                logger.error("Attack order failed", symbol=symbol, error=result.error)
                self.add_event(
                    level="ERROR",
                    event_type="ATTACK",
                    message=f"Attack buy failed: {symbol}",
                    details={"error": result.error},
                )
                return

            # 체결 수량/가격이 0이면 잔고에서 확인
            filled_qty = result.filled_qty
            avg_price = result.avg_price

            if filled_qty <= 0 or avg_price <= 0:
                # 주문은 성공했지만 체결 정보가 없으면 잔고에서 확인
                try:
                    asset = symbol.replace("KRW-", "")
                    balance = await self.exchange.get_balance(asset)
                    if balance and balance.total > 0:
                        ticker = await self.exchange.get_ticker(symbol)
                        filled_qty = balance.total
                        avg_price = ticker.get("trade_price", 0) if ticker else 0
                        logger.info(
                            "Attack order filled (from balance)",
                            symbol=symbol,
                            filled_qty=filled_qty,
                            avg_price=avg_price,
                        )
                except Exception as e:
                    logger.warning(f"Failed to get balance for {symbol}: {e}")

            logger.info(
                "Attack order filled",
                symbol=symbol,
                filled_qty=filled_qty,
                avg_price=avg_price,
            )

            # 체결되지 않았으면 포지션 추적 안함
            if filled_qty <= 0 or avg_price <= 0:
                logger.warning("Attack order not filled, skipping position tracking", symbol=symbol)
                return

            # 주문 기록
            self.add_order(
                symbol=symbol,
                strategy="ATTACK",
                side="BUY",
                order_type="MARKET",
                quantity=signal.quantity,
                price=avg_price,
                status="FILLED",
                filled_qty=filled_qty,
                avg_fill_price=avg_price,
                exchange_order_id=result.exchange_order_id,
            )

            # Attack 포지션 추적 시작
            if metadata.get("tranche") == 1:
                # 1차 트랜치: 새 포지션 등록
                self.attack_strategy.track_position(
                    symbol=symbol,
                    entry_price=avg_price,
                    quantity=filled_qty,
                    stop_price=metadata.get("stop_price", 0),
                    stop_distance_pct=metadata.get("stop_distance_pct", 0.01),
                    attack_level=metadata.get("attack_level", 0),
                    attack_score=metadata.get("attack_score", 0),
                    total_target_quantity=metadata.get("target_total_quantity", 0),
                )
            else:
                # 2차/3차 트랜치: 기존 포지션 업데이트
                self.attack_strategy.update_position(
                    symbol=symbol,
                    added_quantity=filled_qty,
                    tranche=metadata.get("tranche", 0),
                )

            self.add_event(
                level="INFO",
                event_type="ATTACK",
                message=f"Attack tranche {metadata.get('tranche', 1)} completed: {symbol}",
                details={
                    "quantity": filled_qty,
                    "price": avg_price,
                    "level": metadata.get("attack_level"),
                },
            )

            # Slack 알림 (Attack 매수)
            if self.slack_notifier.is_enabled:
                notional = filled_qty * avg_price
                try:
                    krw_balance = await self.exchange.get_balance("KRW")
                    remaining_krw = krw_balance.available if krw_balance else 0
                except Exception:
                    remaining_krw = 0

                await self.slack_notifier.send(SlackMessage(
                    text=f"""
:crossed_swords: *Attack 매수 (L{metadata.get('attack_level', 1)})*
> 심볼: `{symbol}`
> 점수: {metadata.get('attack_score', 0):.0f}점
> 트랜치: {metadata.get('tranche', 1)}/3
> 수량: {filled_qty:.4f}
> 가격: ₩{avg_price:,.0f}
> 매수금액: ₩{notional:,.0f}
> 손절가: ₩{metadata.get('stop_price', 0):,.0f}
---
> 💰 잔여 현금: ₩{remaining_krw:,.0f}
                    """.strip(),
                    level=AlertLevel.INFO,
                ))

        except Exception as e:
            logger.error("Attack signal execution error", error=str(e))
            self.add_event(
                level="ERROR",
                event_type="ATTACK",
                message=f"Attack execution failed: {signal.symbol}",
                details={"error": str(e)},
            )

    async def _execute_pullback_signal(self, signal, market_data: dict, risk_decision, current_equity: float) -> None:
        """Pullback 시그널 실행 (Upbit 전용 - 눌림목 매수)"""
        try:
            symbol = signal.symbol
            current_price = market_data.get("price", 0)

            logger.info(
                "Executing Pullback signal",
                symbol=symbol,
                level=signal.level,
                score=signal.score,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
            )

            # 이벤트 기록
            self.add_event(
                level="INFO",
                event_type="PULLBACK",
                message=f"Pullback entry: {symbol} L{signal.level}",
                details={
                    "score": signal.score,
                    "target_allocation": signal.target_allocation,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                },
            )

            # 매수 금액 계산 (자산의 target_allocation %)
            order_amount = current_equity * signal.target_allocation

            # 최소 주문 금액만 체크 (최대 제한 없음 - 사용자 요청)
            MIN_ORDER = 5000  # 최소 5천원

            if order_amount < MIN_ORDER:
                logger.debug("Pullback order too small", amount=order_amount, min=MIN_ORDER)
                return

            # MAX_ORDER 제한 제거 - 잔고 전체 사용 가능

            logger.info(
                "Pullback order amount calculated",
                symbol=symbol,
                equity=current_equity,
                allocation=signal.target_allocation,
                order_amount=order_amount,
            )

            # PAPER 모드 체크
            if not self.mode_manager.should_execute_trades():
                logger.info(
                    "PAPER mode - skipping Pullback trade execution",
                    symbol=symbol,
                    amount=order_amount,
                )
                self.add_event(
                    level="INFO",
                    event_type="PAPER",
                    message=f"[PAPER] Would have bought {symbol} (Pullback)",
                    details={
                        "amount": order_amount,
                        "price": current_price,
                        "level": signal.level,
                    },
                )
                return

            # 시장가 매수
            result = await self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=order_amount,  # KRW 금액
            )

            if not result.success:
                logger.error("Pullback order failed", symbol=symbol, error=result.error)
                self.add_event(
                    level="ERROR",
                    event_type="PULLBACK",
                    message=f"Pullback buy failed: {symbol}",
                    details={"error": result.error},
                )
                return

            # 체결 수량/가격 확인
            filled_qty = result.filled_qty
            avg_price = result.avg_price

            if filled_qty <= 0 or avg_price <= 0:
                # 잔고에서 확인
                try:
                    asset = symbol.replace("KRW-", "")
                    balance = await self.exchange.get_balance(asset)
                    if balance and balance.total > 0:
                        ticker = await self.exchange.get_ticker(symbol)
                        filled_qty = balance.total
                        avg_price = ticker.get("trade_price", 0) if ticker else 0
                        logger.info(
                            "Pullback order filled (from balance)",
                            symbol=symbol,
                            filled_qty=filled_qty,
                            avg_price=avg_price,
                        )
                except Exception as e:
                    logger.warning(f"Failed to get balance for {symbol}: {e}")

            logger.info(
                "Pullback order filled",
                symbol=symbol,
                filled_qty=filled_qty,
                avg_price=avg_price,
            )

            if filled_qty <= 0 or avg_price <= 0:
                logger.warning("Pullback order not filled", symbol=symbol)
                return

            # 주문 기록
            self.add_order(
                symbol=symbol,
                strategy="PULLBACK",
                side="BUY",
                order_type="MARKET",
                quantity=filled_qty,
                price=avg_price,
                status="FILLED",
                filled_qty=filled_qty,
                avg_fill_price=avg_price,
                exchange_order_id=result.exchange_order_id,
            )

            # Pullback 포지션 추적
            self.pullback_strategy.track_position(
                symbol=symbol,
                entry_price=avg_price,
                quantity=filled_qty,
                stop_loss=signal.stop_loss,
            )

            self.add_event(
                level="INFO",
                event_type="PULLBACK",
                message=f"Pullback entry completed: {symbol}",
                details={
                    "quantity": filled_qty,
                    "price": avg_price,
                    "level": signal.level,
                    "score": signal.score,
                },
            )

            # Slack 알림 (잔고 정보 포함)
            if self.slack_notifier.is_enabled:
                notional = filled_qty * avg_price
                # 잔고 조회
                try:
                    krw_balance = await self.exchange.get_balance("KRW")
                    remaining_krw = krw_balance.available if krw_balance else 0
                except Exception:
                    remaining_krw = 0

                await self.slack_notifier.send(SlackMessage(
                    text=f"""
:chart_with_upwards_trend: *Pullback 매수 체결*
> 심볼: `{symbol}`
> 점수: {signal.score}점 (L{signal.level})
> 수량: {filled_qty:.4f}
> 가격: ₩{avg_price:,.0f}
> 금액: ₩{notional:,.0f}
> 손절가: ₩{signal.stop_loss:,.0f}
---
> 💰 잔여 현금: ₩{remaining_krw:,.0f}
                    """.strip(),
                    level=AlertLevel.INFO,
                ))

        except Exception as e:
            logger.error("Pullback signal execution error", error=str(e))
            self.add_event(
                level="ERROR",
                event_type="PULLBACK",
                message=f"Pullback execution failed: {signal.symbol}",
                details={"error": str(e)},
            )

    async def _execute_ignition_entry(self, position, market_data: dict, risk_decision) -> None:
        """Ignition 진입 실행 (v4.0 전조 패턴 + 점화)"""
        try:
            symbol = position.symbol
            sizing = position.sizings[-1]  # 최신 사이징
            current_price = market_data.get("price", 0)

            logger.info(
                "Executing Ignition entry",
                symbol=symbol,
                mode=sizing.mode,
                quantity=sizing.quantity,
                entry_price=sizing.entry_price,
                stop_loss=sizing.stop_loss,
            )

            # 이벤트 기록
            self.add_event(
                level="INFO",
                event_type="IGNITION",
                message=f"Ignition entry: {symbol} ({sizing.mode})",
                details={
                    "phase": sizing.phase.value,
                    "risk_pct": sizing.risk_pct,
                    "position_amount": sizing.position_amount,
                    "stop_loss": sizing.stop_loss,
                },
            )

            # 매수 금액
            order_amount = sizing.position_amount

            # 최소 주문 금액 체크
            MIN_ORDER = 5000
            if order_amount < MIN_ORDER:
                logger.debug("Ignition order too small", amount=order_amount, min=MIN_ORDER)
                return

            # PAPER 모드 체크
            if not self.mode_manager.should_execute_trades():
                logger.info(
                    "PAPER mode - skipping Ignition trade execution",
                    symbol=symbol,
                    amount=order_amount,
                )
                self.add_event(
                    level="INFO",
                    event_type="PAPER",
                    message=f"[PAPER] Would have bought {symbol} (Ignition)",
                    details={
                        "amount": order_amount,
                        "price": current_price,
                        "mode": sizing.mode,
                    },
                )
                return

            # 시장가 매수
            result = await self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=order_amount,  # KRW 금액
            )

            if not result.success:
                logger.error("Ignition order failed", symbol=symbol, error=result.error)
                self.add_event(
                    level="ERROR",
                    event_type="IGNITION",
                    message=f"Ignition buy failed: {symbol}",
                    details={"error": result.error},
                )
                return

            filled_qty = result.filled_qty
            avg_price = result.avg_price

            # 체결 정보 확인
            if filled_qty <= 0 or avg_price <= 0:
                asset = symbol.replace("KRW-", "")
                balance = await self.exchange.get_balance(asset)
                if balance and balance.total > 0:
                    ticker = await self.exchange.get_ticker(symbol)
                    filled_qty = balance.total
                    avg_price = ticker.get("trade_price", 0) if ticker else current_price

            logger.info(
                "Ignition entry filled",
                symbol=symbol,
                quantity=filled_qty,
                price=avg_price,
            )

            self.add_event(
                level="INFO",
                event_type="IGNITION",
                message=f"Ignition entry completed: {symbol}",
                details={
                    "quantity": filled_qty,
                    "price": avg_price,
                    "mode": sizing.mode,
                    "stop_loss": sizing.stop_loss,
                },
            )

            # Slack 알림 (잔고 정보 포함)
            if self.slack_notifier.is_enabled:
                notional = filled_qty * avg_price
                # 잔고 조회
                try:
                    krw_balance = await self.exchange.get_balance("KRW")
                    remaining_krw = krw_balance.available if krw_balance else 0
                except Exception:
                    remaining_krw = 0

                await self.slack_notifier.send(SlackMessage(
                    text=f"""
:fire: *Ignition 매수 체결*
> 심볼: `{symbol}`
> 모드: {sizing.mode}
> 수량: {filled_qty:.4f}
> 가격: ₩{avg_price:,.0f}
> 금액: ₩{notional:,.0f}
> 손절가: ₩{sizing.stop_loss:,.0f}
---
> 💰 잔여 현금: ₩{remaining_krw:,.0f}
                    """.strip(),
                    level=AlertLevel.INFO,
                ))

        except Exception as e:
            logger.error("Ignition entry execution error", error=str(e))
            self.add_event(
                level="ERROR",
                event_type="IGNITION",
                message=f"Ignition entry failed: {position.symbol}",
                details={"error": str(e)},
            )

    async def _execute_ignition_exit(self, position, exit_reason, exit_pct: float, market_data: dict) -> None:
        """Ignition 청산 실행"""
        try:
            symbol = position.symbol
            current_price = market_data.get("price", 0)
            exit_qty = position.total_quantity * exit_pct

            logger.info(
                "Executing Ignition exit",
                symbol=symbol,
                reason=exit_reason.value,
                exit_pct=exit_pct,
                exit_qty=exit_qty,
            )

            # 이벤트 기록
            self.add_event(
                level="INFO",
                event_type="IGNITION",
                message=f"Ignition exit: {symbol} ({exit_reason.value})",
                details={
                    "exit_pct": exit_pct,
                    "exit_qty": exit_qty,
                    "current_r": position.current_r,
                },
            )

            # PAPER 모드 체크
            if not self.mode_manager.should_execute_trades():
                logger.info(
                    "PAPER mode - skipping Ignition exit execution",
                    symbol=symbol,
                    exit_qty=exit_qty,
                )
                return

            # 시장가 매도
            result = await self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=exit_qty,
            )

            if not result.success:
                logger.error("Ignition exit failed", symbol=symbol, error=result.error)
                return

            # 포지션 업데이트
            self.ignition_strategy.close_position(
                symbol=symbol,
                reason=exit_reason,
                exit_price=result.avg_price or current_price,
                exit_quantity=exit_qty if exit_pct < 1.0 else None,
            )

            self.add_event(
                level="INFO",
                event_type="IGNITION",
                message=f"Ignition exit completed: {symbol}",
                details={
                    "reason": exit_reason.value,
                    "quantity": exit_qty,
                    "price": result.avg_price or current_price,
                    "pnl": position.realized_pnl,
                },
            )

            # Slack 알림 (잔고 정보 포함)
            if self.slack_notifier.is_enabled:
                pnl_emoji = ":moneybag:" if position.realized_pnl > 0 else ":money_with_wings:"
                pnl_pct = ((result.avg_price or current_price) / position.entry_price - 1) * 100 if position.entry_price > 0 else 0
                # 잔고 조회
                try:
                    krw_balance = await self.exchange.get_balance("KRW")
                    remaining_krw = krw_balance.available if krw_balance else 0
                except Exception:
                    remaining_krw = 0

                await self.slack_notifier.send(SlackMessage(
                    text=f"""
{pnl_emoji} *Ignition 청산*
> 심볼: `{symbol}`
> 사유: {exit_reason.value}
> 수량: {exit_qty:.4f}
> 가격: ₩{result.avg_price or current_price:,.0f}
> 손익: ₩{position.realized_pnl:,.0f} ({pnl_pct:+.2f}%)
---
> 💰 잔여 현금: ₩{remaining_krw:,.0f}
                    """.strip(),
                    level=AlertLevel.INFO if position.realized_pnl >= 0 else AlertLevel.WARNING,
                ))

        except Exception as e:
            logger.error("Ignition exit execution error", error=str(e))

    async def _liquidate_satellites_for_surge(
        self,
        required_amount: float,
        available_krw: float,
    ) -> float:
        """
        Surge/Ignition 진입을 위해 Satellite 포지션 청산하여 자본 확보

        우선순위:
        1. 수익 포지션 (이익 큰 것부터)
        2. 소폭 손실 포지션 (-2% 이내)

        Args:
            required_amount: 필요한 총 금액
            available_krw: 현재 가용 현금

        Returns:
            청산 후 예상 확보 금액
        """
        shortage = required_amount - available_krw
        if shortage <= 0:
            return available_krw  # 이미 충분함

        # 청산 대상 선정
        liquidation_targets = self.satellite_strategy.get_positions_for_liquidation(
            market_data=self._market_data,
            required_amount=shortage * 1.05,  # 5% 버퍼
            max_loss_pct=-0.02,  # 최대 -2% 손실까지만 청산
        )

        if not liquidation_targets:
            logger.warning(
                "No Satellite positions available for liquidation",
                shortage=f"₩{shortage:,.0f}",
            )
            return available_krw

        # 청산 실행
        freed_capital = 0.0
        for symbol, quantity, current_price, pnl_pct in liquidation_targets:
            try:
                logger.info(
                    "Liquidating Satellite for Surge capital",
                    symbol=symbol,
                    quantity=quantity,
                    pnl_pct=f"{pnl_pct:.2%}",
                    estimated_value=f"₩{quantity * current_price:,.0f}",
                )

                # PAPER 모드 체크
                if not self.mode_manager.should_execute_trades():
                    logger.info(
                        "PAPER mode - simulating Satellite liquidation",
                        symbol=symbol,
                    )
                    freed_capital += quantity * current_price
                    self.satellite_strategy.close_position(symbol)
                    continue

                # 시장가 매도
                result = await self.exchange.place_order(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=quantity,
                )

                if result.success:
                    entry_price = self.satellite_strategy._active_positions.get(
                        symbol, type("obj", (), {"entry_price": current_price})()
                    ).entry_price
                    realized_pnl = (result.avg_price - entry_price) * result.filled_qty

                    # 포지션 추적 종료
                    self.satellite_strategy.close_position(symbol)
                    freed_capital += result.filled_qty * result.avg_price

                    # 이벤트 기록
                    self.add_event(
                        level="INFO",
                        event_type="SATELLITE_EXIT",
                        message=f"Satellite liquidated for Surge: {symbol}",
                        details={
                            "reason": "SURGE_CAPITAL_REALLOCATION",
                            "quantity": result.filled_qty,
                            "price": result.avg_price,
                            "pnl_pct": pnl_pct,
                            "realized_pnl": realized_pnl,
                        },
                    )

                    # 주문 기록
                    self.add_order(
                        symbol=symbol,
                        strategy="SATELLITE",
                        side="SELL",
                        order_type="MARKET",
                        quantity=result.filled_qty,
                        price=result.avg_price,
                        status="FILLED",
                        exchange_order_id=getattr(result, "order_id", None),
                        realized_pnl=realized_pnl,
                    )

                    logger.info(
                        "Satellite liquidated successfully",
                        symbol=symbol,
                        filled_qty=result.filled_qty,
                        price=result.avg_price,
                        realized_pnl=f"₩{realized_pnl:,.0f}",
                    )

                    # Slack 알림
                    if self.slack_notifier.is_enabled:
                        pnl_emoji = "💰" if realized_pnl >= 0 else "📉"
                        await self.slack_notifier.send(SlackMessage(
                            text=f"""
{pnl_emoji} *Satellite 청산 (Surge 자본 재배분)*
> 심볼: `{symbol}`
> 수량: {result.filled_qty:.4f}
> 가격: ₩{result.avg_price:,.0f}
> 수익률: {pnl_pct:.1%}
> 손익: ₩{realized_pnl:,.0f}
                            """.strip(),
                            level=AlertLevel.INFO if realized_pnl >= 0 else AlertLevel.WARNING,
                        ))

                else:
                    logger.error(
                        "Satellite liquidation failed",
                        symbol=symbol,
                        error=result.error,
                    )

            except Exception as e:
                logger.error(
                    "Satellite liquidation error",
                    symbol=symbol,
                    error=str(e),
                )

        logger.info(
            "Satellite liquidation completed",
            freed_capital=f"₩{freed_capital:,.0f}",
            total_available=f"₩{available_krw + freed_capital:,.0f}",
        )

        return available_krw + freed_capital

    async def _execute_surge_entry(self, surge, market_data: dict, risk_decision, current_equity: float) -> None:
        """급등 시작 진입 실행"""
        try:
            symbol = surge.symbol
            current_price = surge.current_price

            logger.info(
                "Executing Surge entry",
                symbol=symbol,
                change_1m=f"{surge.change_1m_pct:.2f}%",
                change_5m=f"{surge.change_5m_pct:.2f}%",
                volume_ratio=f"{surge.volume_ratio:.1f}x",
            )

            # 이벤트 기록
            self.add_event(
                level="INFO",
                event_type="SURGE",
                message=f"Surge detected: {symbol} +{surge.change_1m_pct:.1f}% (1m)",
                details={
                    "change_1m_pct": surge.change_1m_pct,
                    "change_5m_pct": surge.change_5m_pct,
                    "volume_ratio": surge.volume_ratio,
                    "stop_loss": surge.stop_loss,
                },
            )

            # 매수 금액 계산 (자산의 20%)
            order_amount = current_equity * 0.20

            # 최소 주문 금액 체크
            MIN_ORDER = 5000
            if order_amount < MIN_ORDER:
                logger.debug("Surge order too small", amount=order_amount, min=MIN_ORDER)
                return

            # === 자본 확인 및 Satellite 청산 ===
            # 가용 현금 확인
            krw_balance = await self.exchange.get_balance("KRW")
            available_krw = krw_balance.free if krw_balance else 0

            if available_krw < order_amount:
                logger.info(
                    "Insufficient capital for Surge - checking Satellite positions",
                    required=f"₩{order_amount:,.0f}",
                    available=f"₩{available_krw:,.0f}",
                    shortage=f"₩{order_amount - available_krw:,.0f}",
                )

                # Satellite 포지션 청산하여 자본 확보
                available_krw = await self._liquidate_satellites_for_surge(
                    required_amount=order_amount,
                    available_krw=available_krw,
                )

                # 청산 후에도 자본 부족하면 가능한 만큼만 진입
                if available_krw < order_amount:
                    if available_krw < MIN_ORDER:
                        logger.warning(
                            "Cannot proceed with Surge - insufficient capital after liquidation",
                            available=f"₩{available_krw:,.0f}",
                            required=f"₩{order_amount:,.0f}",
                        )
                        self.add_event(
                            level="WARNING",
                            event_type="SURGE",
                            message=f"Surge skipped - insufficient capital: {symbol}",
                            details={
                                "required": order_amount,
                                "available": available_krw,
                            },
                        )
                        return

                    # 가능한 만큼만 진입
                    old_amount = order_amount
                    order_amount = available_krw * 0.95  # 5% 여유
                    logger.info(
                        "Adjusted Surge order amount due to capital constraints",
                        original=f"₩{old_amount:,.0f}",
                        adjusted=f"₩{order_amount:,.0f}",
                    )

            # PAPER 모드 체크
            if not self.mode_manager.should_execute_trades():
                logger.info(
                    "PAPER mode - skipping Surge trade execution",
                    symbol=symbol,
                    amount=order_amount,
                )
                self.add_event(
                    level="INFO",
                    event_type="PAPER",
                    message=f"[PAPER] Would have bought {symbol} (Surge)",
                    details={
                        "amount": order_amount,
                        "price": current_price,
                        "change_1m": surge.change_1m_pct,
                    },
                )
                return

            # 시장가 매수
            result = await self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=order_amount,  # KRW 금액
            )

            if not result.success:
                logger.error("Surge order failed", symbol=symbol, error=result.error)
                self.add_event(
                    level="ERROR",
                    event_type="SURGE",
                    message=f"Surge buy failed: {symbol}",
                    details={"error": result.error},
                )
                return

            filled_qty = result.filled_qty
            avg_price = result.avg_price

            # 체결 정보 확인
            if filled_qty <= 0 or avg_price <= 0:
                asset = symbol.replace("KRW-", "")
                balance = await self.exchange.get_balance(asset)
                if balance and balance.total > 0:
                    ticker = await self.exchange.get_ticker(symbol)
                    filled_qty = balance.total
                    avg_price = ticker.get("trade_price", 0) if ticker else current_price

            logger.info(
                "Surge entry filled",
                symbol=symbol,
                quantity=filled_qty,
                price=avg_price,
            )

            self.add_event(
                level="INFO",
                event_type="SURGE",
                message=f"Surge entry completed: {symbol}",
                details={
                    "quantity": filled_qty,
                    "price": avg_price,
                    "change_1m": surge.change_1m_pct,
                    "volume_ratio": surge.volume_ratio,
                },
            )

            # 포지션 추적
            self.surge_detector.track_position(
                symbol=symbol,
                entry_price=avg_price,
                quantity=filled_qty,
                stop_loss=surge.stop_loss,
                take_profit=surge.take_profit_1,
            )

            # Slack 알림 (잔고 정보 포함)
            if self.slack_notifier.is_enabled:
                notional = filled_qty * avg_price
                # 잔고 조회
                try:
                    krw_balance = await self.exchange.get_balance("KRW")
                    remaining_krw = krw_balance.available if krw_balance else 0
                except:
                    remaining_krw = 0

                await self.slack_notifier.send(SlackMessage(
                    text=f"""
:rocket: *급등 감지 매수*
> 심볼: `{symbol}`
> 1분 변화: +{surge.change_1m_pct:.1f}%
> 거래량: {surge.volume_ratio:.1f}x
> 수량: {filled_qty:.4f}
> 가격: ₩{avg_price:,.0f}
> 매수금액: ₩{notional:,.0f}
> 손절가: ₩{surge.stop_loss:,.0f}
---
> 💰 잔여 현금: ₩{remaining_krw:,.0f}
> 📊 총 자산: ₩{current_equity:,.0f}
                    """.strip(),
                    level=AlertLevel.INFO,
                ))

        except Exception as e:
            logger.error("Surge entry execution error", error=str(e))
            self.add_event(
                level="ERROR",
                event_type="SURGE",
                message=f"Surge entry failed: {surge.symbol}",
                details={"error": str(e)},
            )

    async def _execute_surge_exit(self, symbol: str, position, exit_reason: str, exit_pct: float, current_price: float) -> None:
        """Surge 청산 실행"""
        try:
            exit_qty = position.quantity * exit_pct

            logger.info(
                "Executing Surge exit",
                symbol=symbol,
                reason=exit_reason,
                exit_pct=exit_pct,
                exit_qty=exit_qty,
            )

            # 이벤트 기록
            self.add_event(
                level="INFO",
                event_type="SURGE",
                message=f"Surge exit: {symbol} ({exit_reason})",
                details={
                    "exit_pct": exit_pct,
                    "exit_qty": exit_qty,
                    "entry_price": position.entry_price,
                    "current_price": current_price,
                },
            )

            # PAPER 모드 체크
            if not self.mode_manager.should_execute_trades():
                logger.info(
                    "PAPER mode - skipping Surge exit execution",
                    symbol=symbol,
                    exit_qty=exit_qty,
                )
                self.surge_detector.close_position(symbol, partial=(exit_pct < 1.0))
                return

            # 시장가 매도
            result = await self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=exit_qty,
            )

            if not result.success:
                logger.error("Surge exit failed", symbol=symbol, error=result.error)
                return

            # 포지션 업데이트
            self.surge_detector.close_position(symbol, partial=(exit_pct < 1.0))

            # 손익 계산
            pnl = (current_price - position.entry_price) * exit_qty

            self.add_event(
                level="INFO",
                event_type="SURGE",
                message=f"Surge exit completed: {symbol}",
                details={
                    "reason": exit_reason,
                    "quantity": exit_qty,
                    "price": result.avg_price or current_price,
                    "pnl": pnl,
                },
            )

            # Slack 알림 (잔고 정보 포함)
            if self.slack_notifier.is_enabled:
                pnl_emoji = ":moneybag:" if pnl > 0 else ":money_with_wings:"
                pnl_pct = ((current_price / position.entry_price) - 1) * 100
                # 잔고 조회
                try:
                    krw_balance = await self.exchange.get_balance("KRW")
                    remaining_krw = krw_balance.available if krw_balance else 0
                    # 총 자산 계산
                    all_balances = await self.exchange.get_all_balances()
                    total_equity = remaining_krw
                    for bal in all_balances:
                        if bal.asset != "KRW" and bal.total > 0:
                            sym = f"KRW-{bal.asset}"
                            md = self._market_data.get(sym, {})
                            price = md.get("price", 0)
                            if price > 0:
                                total_equity += bal.total * price
                except:
                    remaining_krw = 0
                    total_equity = 0

                await self.slack_notifier.send(SlackMessage(
                    text=f"""
{pnl_emoji} *급등 청산*
> 심볼: `{symbol}`
> 사유: {exit_reason}
> 수량: {exit_qty:.4f}
> 진입가: ₩{position.entry_price:,.0f}
> 청산가: ₩{result.avg_price or current_price:,.0f}
> 손익: ₩{pnl:,.0f} ({pnl_pct:+.2f}%)
---
> 💰 잔여 현금: ₩{remaining_krw:,.0f}
> 📊 총 자산: ₩{total_equity:,.0f}
                    """.strip(),
                    level=AlertLevel.INFO if pnl >= 0 else AlertLevel.WARNING,
                ))

        except Exception as e:
            logger.error("Surge exit execution error", error=str(e))

    def _round_quantity(self, symbol: str, quantity: float) -> float:
        """심볼별 수량 정밀도 처리 (SymbolManager 사용)"""
        return self.symbol_manager.round_quantity(symbol, quantity)

    def _round_quantity_for_perp(self, symbol: str, quantity: float) -> float:
        """선물용 수량 정밀도 처리 (SymbolManager 사용)"""
        return self.symbol_manager.round_quantity(symbol, quantity)

    async def _manage_positions(self) -> None:
        """포지션 관리"""
        try:
            if self._is_upbit:
                # Upbit: 보유 자산으로 포지션 관리
                await self._manage_upbit_positions()
                return

            # Binance: Futures 포지션 조회
            perp_positions = await self.perp_exchange.get_positions()

            for position in perp_positions:
                symbol = position.symbol
                market_data = self._market_data.get(symbol, {})

                if not market_data:
                    continue

                current_price = market_data.get("perp_price", position.entry_price)

                pos_dict = {
                    "symbol": symbol,
                    "side": position.side,
                    "quantity": position.quantity,
                    "avg_price": position.entry_price,
                    "opened_at": datetime.utcnow(),  # TODO: 실제 진입 시간
                }

                # Satellite 포지션인지 확인
                is_satellite = self._is_satellite_position(symbol, position)

                if is_satellite:
                    # Position State Machine으로 관리
                    order_intent = self.position_state_machine.evaluate(symbol, current_price)
                    if order_intent:
                        await self._execute_order_intent(order_intent, position)
                else:
                    # Core Safety Guard 청산 체크 (펀딩 역전 등)
                    should_unwind, unwind_reason = self.core_safety.should_unwind(symbol)
                    if should_unwind:
                        self.add_event(
                            level="CRITICAL",
                            event_type="RISK",
                            message=f"Core safety unwind triggered: {symbol}",
                            details={"reason": unwind_reason},
                        )
                        logger.warning(
                            "Core safety guard triggered unwind",
                            symbol=symbol,
                            reason=unwind_reason,
                        )
                        # 실제 청산 실행
                        await self._execute_position_close(
                            symbol=symbol,
                            position=position,
                            reason=f"Core safety: {unwind_reason}",
                            strategy="CORE",
                        )
                        continue  # 청산 후 다음 포지션으로

                    # Core 전략 비활성화 (Upbit 현물 전용)
                    # core_exit = await self.core_strategy.should_exit(pos_dict, market_data)
                    # if core_exit:
                    #     await self._execute_position_close(...)
                    pass  # Core 전략 스킵

                    # Satellite 청산 조건 체크 (fallback - PSM에 없는 포지션)
                    sat_exit = await self.satellite_strategy.should_exit(pos_dict, market_data)
                    if sat_exit:
                        self.add_event(
                            level="WARNING",
                            event_type="STRATEGY",
                            message=f"Satellite exit signal: {symbol}",
                            details={"reason": sat_exit.reason},
                        )
                        # 실제 청산 실행
                        await self._execute_position_close(
                            symbol=symbol,
                            position=position,
                            reason=sat_exit.reason,
                            strategy="SATELLITE",
                        )

        except Exception as e:
            logger.error("Position management error", error=str(e))

    async def _execute_position_close(
        self,
        symbol: str,
        position,
        reason: str,
        strategy: str = "UNKNOWN",
    ) -> bool:
        """
        포지션 청산 실행

        Args:
            symbol: 심볼
            position: 포지션 객체
            reason: 청산 사유
            strategy: 전략 (CORE/SATELLITE)

        Returns:
            bool: 청산 성공 여부
        """
        # 청산 방향 결정
        close_side = OrderSide.SELL if position.side == "LONG" else OrderSide.BUY
        quantity = abs(position.quantity)

        # 최소 수량 체크
        quantity = self._round_quantity_for_perp(symbol, quantity)
        if quantity <= 0:
            logger.warning("Close quantity too small", symbol=symbol)
            return False

        logger.info(
            "Executing position close",
            symbol=symbol,
            side=close_side.value,
            quantity=quantity,
            reason=reason,
            strategy=strategy,
        )

        # 시장가 청산 주문
        result = await self.perp_exchange.place_order(
            symbol=symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            reduce_only=True,  # 청산 전용
        )

        if result.success:
            logger.info(
                "Position closed successfully",
                symbol=symbol,
                filled_qty=result.filled_qty,
                avg_price=result.avg_price,
            )

            self.add_event(
                level="INFO",
                event_type="ORDER",
                message=f"Position closed: {symbol}",
                details={
                    "strategy": strategy,
                    "reason": reason,
                    "side": close_side.value,
                    "quantity": result.filled_qty,
                    "price": result.avg_price,
                },
            )

            # Core 전략 상태 리셋 (비활성화됨)
            # if strategy == "CORE" and self.core_strategy:
            #     self.core_strategy.reset_carry(symbol)
            pass

            # PnL 계산
            realized_pnl = 0.0
            entry_price = getattr(position, 'entry_price', None) or getattr(position, 'avg_price', 0)
            if entry_price and entry_price > 0 and result.avg_price:
                if position.side == "LONG":
                    realized_pnl = (result.avg_price - entry_price) * result.filled_qty
                else:
                    realized_pnl = (entry_price - result.avg_price) * result.filled_qty

            # 주문 기록 (quantity는 주문 수량, filled_qty는 체결 수량)
            self.add_order(
                symbol=symbol,
                strategy=strategy,
                side=close_side.value,
                order_type="MARKET",
                quantity=quantity,  # 원래 주문 수량
                price=result.avg_price,
                status="FILLED" if result.filled_qty > 0 else "REJECTED",
                filled_qty=result.filled_qty,
                avg_fill_price=result.avg_price,
                realized_pnl=realized_pnl,
            )

            return True
        else:
            logger.error(
                "Position close failed",
                symbol=symbol,
                error=result.error,
            )

            self.add_event(
                level="ERROR",
                event_type="ORDER",
                message=f"Position close failed: {symbol}",
                details={
                    "strategy": strategy,
                    "reason": reason,
                    "error": result.error,
                },
            )

            return False

    def _is_satellite_position(self, symbol: str, position) -> bool:
        """Satellite 전략 포지션인지 확인"""
        # Position State Machine에 등록된 포지션인지 확인
        managed = self.position_state_machine.get_position(symbol)
        return managed is not None

    async def _manage_upbit_positions(self) -> None:
        """Upbit 포지션 관리 (보유 자산 기반)"""
        try:
            # 보유 자산 조회
            balances = await self.exchange.get_all_balances()

            for balance in balances:
                if balance.asset == "KRW" or balance.total <= 0:
                    continue

                symbol = f"KRW-{balance.asset}"
                market_data = self._market_data.get(symbol, {})

                if not market_data:
                    continue

                current_price = market_data.get("price", 0)
                if current_price <= 0:
                    continue

                # Attack 포지션 청산 체크
                if self.attack_strategy:
                    attack_pos = self.attack_strategy._active_positions.get(symbol)
                    if attack_pos:
                        pos_dict = {
                            "symbol": symbol,
                            "side": "BUY",
                            "quantity": balance.total,
                            "avg_price": attack_pos.entry_price,
                            "opened_at": attack_pos.entry_time.isoformat(),
                        }
                        attack_exit = await self.attack_strategy.should_exit(pos_dict, market_data)
                        if attack_exit:
                            logger.info(
                                "Attack exit signal",
                                symbol=symbol,
                                reason=attack_exit.reason,
                            )
                            # 시장가 청산
                            result = await self.exchange.place_order(
                                symbol=symbol,
                                side=OrderSide.SELL,
                                order_type=OrderType.MARKET,
                                quantity=balance.total,
                            )
                            if result.success:
                                # 포지션 추적 종료
                                self.attack_strategy.close_position(symbol)

                                # PnL 계산
                                realized_pnl = (result.avg_price - attack_pos.entry_price) * result.filled_qty

                                self.add_event(
                                    level="INFO",
                                    event_type="ATTACK",
                                    message=f"Attack exit completed: {symbol}",
                                    details={
                                        "reason": attack_exit.reason,
                                        "quantity": result.filled_qty,
                                        "price": result.avg_price,
                                        "pnl": realized_pnl,
                                    },
                                )

                                self.add_order(
                                    symbol=symbol,
                                    strategy="ATTACK",
                                    side="SELL",
                                    order_type="MARKET",
                                    quantity=balance.total,
                                    price=result.avg_price,
                                    status="FILLED",
                                    filled_qty=result.filled_qty,
                                    avg_fill_price=result.avg_price,
                                    realized_pnl=realized_pnl,
                                )

                                # Slack 알림 (Attack 청산)
                                if self.slack_notifier.is_enabled:
                                    pnl_emoji = "💰" if realized_pnl >= 0 else "📉"
                                    pnl_pct = ((result.avg_price / attack_pos.entry_price) - 1) * 100 if attack_pos.entry_price > 0 else 0
                                    try:
                                        krw_balance = await self.exchange.get_balance("KRW")
                                        remaining_krw = krw_balance.available if krw_balance else 0
                                    except Exception:
                                        remaining_krw = 0

                                    await self.slack_notifier.send(SlackMessage(
                                        text=f"""
{pnl_emoji} *Attack 청산*
> 심볼: `{symbol}`
> 사유: {attack_exit.reason}
> 수량: {result.filled_qty:.4f}
> 진입가: ₩{attack_pos.entry_price:,.0f}
> 청산가: ₩{result.avg_price:,.0f}
> 수익률: {pnl_pct:+.2f}%
> 손익: ₩{realized_pnl:,.0f}
---
> 💰 잔여 현금: ₩{remaining_krw:,.0f}
                                        """.strip(),
                                        level=AlertLevel.INFO if realized_pnl >= 0 else AlertLevel.WARNING,
                                    ))

                            continue  # Attack 청산 후 다음 자산으로

                # Pullback 포지션 청산 체크
                if self.pullback_strategy and self.pullback_strategy.is_enabled():
                    pullback_exit = self.pullback_strategy.should_exit(symbol, current_price)
                    if pullback_exit:
                        action = pullback_exit.get("action", "FULL")
                        reason = pullback_exit.get("reason", "Unknown")
                        exit_qty = pullback_exit.get("quantity", balance.total)

                        logger.info(
                            "Pullback exit signal",
                            symbol=symbol,
                            action=action,
                            reason=reason,
                            quantity=exit_qty,
                        )

                        # PAPER 모드 체크
                        if not self.mode_manager.should_execute_trades():
                            logger.info(
                                "PAPER mode - skipping Pullback exit",
                                symbol=symbol,
                                reason=reason,
                            )
                            continue

                        # 시장가 청산
                        result = await self.exchange.place_order(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            order_type=OrderType.MARKET,
                            quantity=exit_qty,
                        )

                        if result.success:
                            # 포지션 업데이트
                            pullback_pos = self.pullback_strategy.get_position(symbol)
                            entry_price = pullback_pos.entry_price if pullback_pos else current_price
                            realized_pnl = (result.avg_price - entry_price) * result.filled_qty

                            if action == "PARTIAL":
                                self.pullback_strategy.close_position(symbol, partial=True, sold_qty=result.filled_qty)
                            else:
                                self.pullback_strategy.close_position(symbol)

                            self.add_event(
                                level="INFO",
                                event_type="PULLBACK",
                                message=f"Pullback exit completed: {symbol}",
                                details={
                                    "reason": reason,
                                    "action": action,
                                    "quantity": result.filled_qty,
                                    "price": result.avg_price,
                                    "pnl": realized_pnl,
                                },
                            )

                            self.add_order(
                                symbol=symbol,
                                strategy="PULLBACK",
                                side="SELL",
                                order_type="MARKET",
                                quantity=exit_qty,
                                price=result.avg_price,
                                status="FILLED",
                                filled_qty=result.filled_qty,
                                avg_fill_price=result.avg_price,
                                realized_pnl=realized_pnl,
                            )

                            # Slack 알림 (청산 - 잔고 정보 포함)
                            if self.slack_notifier.is_enabled:
                                pnl_emoji = ":moneybag:" if realized_pnl >= 0 else ":money_with_wings:"
                                pnl_sign = "+" if realized_pnl >= 0 else ""
                                pnl_pct = (realized_pnl / (entry_price * result.filled_qty)) * 100 if entry_price > 0 else 0
                                # 잔고 조회
                                try:
                                    krw_balance = await self.exchange.get_balance("KRW")
                                    remaining_krw = krw_balance.available if krw_balance else 0
                                except Exception:
                                    remaining_krw = 0

                                await self.slack_notifier.send(SlackMessage(
                                    text=f"""
{pnl_emoji} *Pullback 청산*
> 심볼: `{symbol}`
> 사유: {reason}
> 수량: {result.filled_qty:.4f}
> 진입가: ₩{entry_price:,.0f}
> 청산가: ₩{result.avg_price:,.0f}
> 손익: {pnl_sign}₩{realized_pnl:,.0f} ({pnl_sign}{pnl_pct:.2f}%)
---
> 💰 잔여 현금: ₩{remaining_krw:,.0f}
                                    """.strip(),
                                    level=AlertLevel.INFO if realized_pnl >= 0 else AlertLevel.WARNING,
                                ))

                        if action == "FULL":
                            continue  # Pullback 전량 청산 후 다음 자산으로

                # PSM에 등록된 포지션인지 확인
                psm_position = self.position_state_machine.get_position(symbol)
                if psm_position:
                    # PSM 평가
                    order_intent = self.position_state_machine.evaluate(symbol, current_price)
                    if order_intent:
                        await self._execute_upbit_order_intent(order_intent, balance, current_price)
                else:
                    # Satellite 청산 조건 체크
                    # Upbit API에서 가져온 실제 평균 매수가 사용
                    entry_price = balance.avg_buy_price if balance.avg_buy_price > 0 else current_price
                    pos_dict = {
                        "symbol": symbol,
                        "side": "BUY",  # Upbit은 롱만
                        "quantity": balance.total,
                        "avg_price": entry_price,
                        "opened_at": datetime.utcnow(),
                    }
                    sat_exit = await self.satellite_strategy.should_exit(pos_dict, market_data)
                    if sat_exit:
                        logger.info(
                            "Satellite exit signal (non-PSM)",
                            symbol=symbol,
                            reason=sat_exit.reason,
                        )
                        # 실제 청산 실행
                        result = await self.exchange.place_order(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            order_type=OrderType.MARKET,
                            quantity=balance.total,
                        )
                        if result.success:
                            # 포지션 추적 종료
                            self.satellite_strategy.close_position(symbol)

                            # PnL 계산
                            realized_pnl = (result.avg_price - entry_price) * result.filled_qty

                            self.add_event(
                                level="INFO",
                                event_type="SATELLITE",
                                message=f"Satellite exit: {symbol}",
                                details={
                                    "reason": sat_exit.reason,
                                    "quantity": result.filled_qty,
                                    "entry_price": entry_price,
                                    "exit_price": result.avg_price,
                                    "pnl": realized_pnl,
                                },
                            )

                            # 주문 기록
                            self.add_order(
                                symbol=symbol,
                                strategy="SATELLITE",
                                side="SELL",
                                order_type="MARKET",
                                quantity=balance.total,
                                price=result.avg_price,
                                status="FILLED",
                                filled_qty=result.filled_qty,
                                avg_fill_price=result.avg_price,
                                realized_pnl=realized_pnl,
                            )

                            logger.info(
                                "Satellite position closed",
                                symbol=symbol,
                                reason=sat_exit.reason,
                                pnl=f"₩{realized_pnl:,.0f}",
                            )

                            # Slack 알림 (Satellite 청산)
                            if self.slack_notifier.is_enabled:
                                pnl_emoji = "💰" if realized_pnl >= 0 else "📉"
                                pnl_pct = ((result.avg_price / entry_price) - 1) * 100 if entry_price > 0 else 0
                                try:
                                    krw_balance = await self.exchange.get_balance("KRW")
                                    remaining_krw = krw_balance.available if krw_balance else 0
                                except Exception:
                                    remaining_krw = 0

                                await self.slack_notifier.send(SlackMessage(
                                    text=f"""
{pnl_emoji} *Satellite 청산*
> 심볼: `{symbol}`
> 사유: {sat_exit.reason}
> 수량: {result.filled_qty:.4f}
> 진입가: ₩{entry_price:,.0f}
> 청산가: ₩{result.avg_price:,.0f}
> 수익률: {pnl_pct:+.2f}%
> 손익: ₩{realized_pnl:,.0f}
---
> 💰 잔여 현금: ₩{remaining_krw:,.0f}
                                    """.strip(),
                                    level=AlertLevel.INFO if realized_pnl >= 0 else AlertLevel.WARNING,
                                ))

        except Exception as e:
            logger.error("Upbit position management error", error=str(e))

    async def _execute_upbit_order_intent(self, intent, balance, current_price: float) -> None:
        """Upbit용 OrderIntent 실행"""
        from src.position.schemas import OrderIntent

        if not isinstance(intent, OrderIntent):
            return

        symbol = intent.symbol

        # 전량 청산
        if intent.exit_now:
            logger.info(
                "Executing Upbit full exit",
                symbol=symbol,
                reason=intent.reason,
            )

            result = await self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=balance.total,
            )

            if result.success:
                self.position_state_machine.close_position(symbol)
                self.add_event(
                    level="INFO",
                    event_type="ORDER",
                    message=f"Upbit PSM exit completed: {symbol}",
                    details={
                        "reason": intent.reason,
                        "quantity": result.filled_qty,
                        "price": result.avg_price,
                    },
                )

                self.add_order(
                    symbol=symbol,
                    strategy="SATELLITE",
                    side="SELL",
                    order_type="MARKET",
                    quantity=balance.total,
                    price=result.avg_price,
                    status="FILLED" if result.filled_qty > 0 else "REJECTED",
                    filled_qty=result.filled_qty,
                    avg_fill_price=result.avg_price,
                )
            else:
                logger.error("Upbit PSM exit failed", symbol=symbol, error=result.error)
            return

        # 부분 익절
        for tp in intent.take_profit_orders:
            tp_pct = tp.get("pct", 0)
            if tp_pct <= 0:
                continue

            tp_qty = balance.total * tp_pct

            if tp_qty <= 0:
                continue

            logger.info(
                "Executing Upbit partial TP",
                symbol=symbol,
                pct=tp_pct,
                quantity=tp_qty,
            )

            result = await self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=tp_qty,
            )

            if result.success:
                new_qty = balance.total - result.filled_qty
                self.position_state_machine.update_quantity(symbol, new_qty)

                if tp_pct >= 0.25 and tp_pct <= 0.35:
                    self.position_state_machine.update_tp_status(symbol, tp_30_triggered=True)
                elif tp_pct >= 0.55 and tp_pct <= 0.65:
                    self.position_state_machine.update_tp_status(symbol, tp_60_triggered=True)

                self.add_event(
                    level="INFO",
                    event_type="ORDER",
                    message=f"Upbit partial TP: {symbol} ({tp_pct*100:.0f}%)",
                    details={
                        "quantity": result.filled_qty,
                        "price": result.avg_price,
                        "remaining": new_qty,
                    },
                )

                self.add_order(
                    symbol=symbol,
                    strategy="SATELLITE",
                    side="SELL",
                    order_type="MARKET",
                    quantity=tp_qty,
                    price=result.avg_price,
                    status="FILLED" if result.filled_qty > 0 else "REJECTED",
                    filled_qty=result.filled_qty,
                    avg_fill_price=result.avg_price,
                )
            else:
                logger.error("Upbit partial TP failed", symbol=symbol, error=result.error)

    async def _execute_order_intent(self, intent, position) -> None:
        """OrderIntent 실행"""
        from src.position.schemas import OrderIntent

        if not isinstance(intent, OrderIntent):
            return

        symbol = intent.symbol
        close_side = OrderSide.SELL if position.side == "LONG" else OrderSide.BUY

        # 전량 청산
        if intent.exit_now:
            logger.info(
                "Executing full exit",
                symbol=symbol,
                reason=intent.reason,
            )

            result = await self.perp_exchange.place_order(
                symbol=symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                quantity=position.quantity,
                reduce_only=True,
            )

            if result.success:
                self.position_state_machine.close_position(symbol)
                self.add_event(
                    level="INFO",
                    event_type="ORDER",
                    message=f"PSM exit completed: {symbol}",
                    details={
                        "reason": intent.reason,
                        "quantity": result.filled_qty,
                        "price": result.avg_price,
                    },
                )

                # PnL 계산
                realized_pnl = 0.0
                if position.entry_price and position.entry_price > 0 and result.avg_price:
                    if position.side == "LONG":
                        realized_pnl = (result.avg_price - position.entry_price) * result.filled_qty
                    else:
                        realized_pnl = (position.entry_price - result.avg_price) * result.filled_qty

                # DB 기록 (quantity는 주문 수량, filled_qty는 체결 수량)
                order_id = self.add_order(
                    symbol=symbol,
                    strategy="SATELLITE",
                    side=close_side.value,
                    order_type="MARKET",
                    quantity=position.quantity,  # 원래 주문 수량
                    price=result.avg_price,
                    status="FILLED" if result.filled_qty > 0 else "REJECTED",
                    filled_qty=result.filled_qty,
                    avg_fill_price=result.avg_price,
                    realized_pnl=realized_pnl,
                )
            else:
                logger.error("PSM exit failed", symbol=symbol, error=result.error)
            return

        # 부분 익절
        for tp in intent.take_profit_orders:
            tp_pct = tp.get("pct", 0)
            if tp_pct <= 0:
                continue

            tp_qty = position.quantity * tp_pct
            tp_qty = self._round_quantity_for_perp(symbol, tp_qty)

            if tp_qty <= 0:
                continue

            logger.info(
                "Executing partial TP",
                symbol=symbol,
                pct=tp_pct,
                quantity=tp_qty,
                reason=intent.reason,
            )

            result = await self.perp_exchange.place_order(
                symbol=symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                quantity=tp_qty,
                reduce_only=True,
            )

            if result.success:
                # 수량 업데이트
                new_qty = position.quantity - result.filled_qty
                self.position_state_machine.update_quantity(symbol, new_qty)

                # TP 트리거 상태 업데이트
                if tp_pct >= 0.25 and tp_pct <= 0.35:
                    self.position_state_machine.update_tp_status(symbol, tp_30_triggered=True)
                elif tp_pct >= 0.55 and tp_pct <= 0.65:
                    self.position_state_machine.update_tp_status(symbol, tp_60_triggered=True)

                self.add_event(
                    level="INFO",
                    event_type="ORDER",
                    message=f"PSM partial TP: {symbol} ({tp_pct*100:.0f}%)",
                    details={
                        "reason": intent.reason,
                        "quantity": result.filled_qty,
                        "price": result.avg_price,
                        "remaining": new_qty,
                    },
                )

                # PnL 계산
                realized_pnl = 0.0
                if position.entry_price and position.entry_price > 0 and result.avg_price:
                    if position.side == "LONG":
                        realized_pnl = (result.avg_price - position.entry_price) * result.filled_qty
                    else:
                        realized_pnl = (position.entry_price - result.avg_price) * result.filled_qty

                # DB 기록 (quantity는 주문 수량, filled_qty는 체결 수량)
                self.add_order(
                    symbol=symbol,
                    strategy="SATELLITE",
                    side=close_side.value,
                    order_type="MARKET",
                    quantity=tp_qty,  # 원래 주문 수량
                    price=result.avg_price,
                    status="FILLED" if result.filled_qty > 0 else "REJECTED",
                    filled_qty=result.filled_qty,
                    avg_fill_price=result.avg_price,
                    realized_pnl=realized_pnl,
                )
            else:
                logger.error("PSM partial TP failed", symbol=symbol, error=result.error)

        # 스탑 가격 업데이트 (TODO: 실제 스탑 주문 수정)
        if intent.desired_stop_price != position.entry_price:
            # 현재는 로깅만 (실제 스탑 주문 수정은 별도 구현 필요)
            logger.debug(
                "Stop price update suggested",
                symbol=symbol,
                new_stop=intent.desired_stop_price,
            )

    async def _update_cached_state(self) -> None:
        """상태 캐시 업데이트"""
        try:
            if self._is_upbit:
                await self._update_cached_state_upbit()
            else:
                await self._update_cached_state_binance()

        except Exception as e:
            logger.error("Failed to update cached state", error=str(e))

    async def _get_symbol_strategy_map(self) -> dict[str, str]:
        """PositionLedger에서 심볼별 전략 매핑 조회"""
        strategy_map: dict[str, str] = {}
        strategy_priority = {
            "ATTACK": 1,
            "IGNITION": 2,
            "SURGE": 3,
            "PULLBACK": 4,
            "SATELLITE": 5,
            "CORE": 6,
        }

        try:
            open_positions = await self.position_ledger.get_open_positions()
            for pos in open_positions:
                symbol = pos.symbol
                strategy = pos.strategy_id
                existing = strategy_map.get(symbol)

                if existing is None:
                    strategy_map[symbol] = strategy
                elif strategy_priority.get(strategy, 99) < strategy_priority.get(existing, 99):
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

                # 실제 전략 조회 (Ledger에 없으면 SATELLITE 기본값)
                strategy = symbol_strategy_map.get(symbol, "SATELLITE")

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
            "updated_at": datetime.utcnow().isoformat(),
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

    async def _update_cached_state_binance(self) -> None:
        """Binance 상태 캐시 업데이트 (기존 로직)"""
        # 잔고 조회
        spot_balance = await self.spot_exchange.get_balance("USDT")
        perp_balance = await self.perp_exchange.get_balance("USDT")

        spot_total = spot_balance.total if spot_balance else 0
        perp_total = perp_balance.total if perp_balance else 0
        current_equity = spot_total + perp_total

        # 포지션 조회
        perp_positions = await self.perp_exchange.get_positions()

        # 오늘 시작 자산 설정 (첫 조회 시)
        if self._starting_equity is None:
            self._starting_equity = current_equity

        # PnL 계산
        pnl_today = current_equity - self._starting_equity
        pnl_today_pct = pnl_today / self._starting_equity if self._starting_equity > 0 else 0

        # 미실현 손익 합계
        unrealized_pnl = sum(p.unrealized_pnl for p in perp_positions)

        # Summary 업데이트
        self._cached_summary = {
            "equity": current_equity,
            "pnl_today": pnl_today,
            "pnl_today_pct": pnl_today_pct,
            "drawdown": 0.0,
            "exposure": sum(p.quantity * p.entry_price for p in perp_positions),
            "cash": spot_total + (perp_balance.free if perp_balance else 0),
            "margin_used": perp_total - (perp_balance.free if perp_balance else 0),
            "mode": self.mode.value,
            "is_paper": settings.is_paper_mode,
            "updated_at": datetime.utcnow().isoformat(),
        }

        # 포지션 캐시
        self._cached_positions = []
        for p in perp_positions:
            # 현재가는 market_data에서 가져오기
            market_data = self._market_data.get(p.symbol, {})
            current_price = market_data.get("perp_price", p.entry_price)

            self._cached_positions.append({
                "symbol": p.symbol,
                "strategy": "CORE",
                "side": p.side,
                "quantity": p.quantity,
                "avg_price": p.entry_price,
                "current_price": current_price,
                "unrealized_pnl": p.unrealized_pnl,
                "realized_pnl": 0.0,
                "notional": p.quantity * current_price,
                "leverage": p.leverage,
            })

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

    async def _sync_satellite_positions(self) -> None:
        """서버 시작 시 Upbit 기존 포지션을 Satellite 전략에 동기화"""
        if not self._is_upbit:
            return

        try:
            # 1. 시장 데이터 먼저 업데이트 (현재가 필요)
            watch_symbols = self.symbol_manager.get_qualified_symbols()
            await self._update_market_data_upbit(watch_symbols)

            # 2. Upbit 잔고 조회
            all_balances = await self.exchange.get_all_balances()

            # 3. Satellite 전략에 포지션 동기화
            synced = self.satellite_strategy.sync_positions_from_balances(
                balances=all_balances,
                market_data=self._market_data,
            )

            logger.info(
                "Satellite positions synced from Upbit",
                synced_count=synced,
                total_balances=len(all_balances),
            )

        except Exception as e:
            logger.error("Failed to sync satellite positions", error=str(e))

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
                        "created_at": order.created_at.isoformat(),
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
            "created_at": datetime.utcnow().isoformat(),
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
                pnl_field = "core_pnl" if strategy == "CORE" else "satellite_pnl"
                await trade_recorder.update_daily_stats(
                    trades_count=1,
                    **{pnl_field: 0.0},  # TODO: 실제 PnL
                )
        except Exception as e:
            logger.error("Failed to record order to DB", error=str(e))

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
            "timestamp": datetime.utcnow().isoformat(),
            "level": level,
            "event_type": event_type,
            "message": message,
            "details": details,
        }
        self._cached_events.append(event)

        # 최대 1000개 유지
        if len(self._cached_events) > 1000:
            self._cached_events = self._cached_events[-1000:]
