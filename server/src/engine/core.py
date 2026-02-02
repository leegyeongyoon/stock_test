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

        # Slack 알림
        self.slack_notifier = SlackNotifier()
        if self.slack_notifier.is_enabled:
            logger.info("Slack notifier enabled")

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

        # DB에서 주문 히스토리 로드
        await self._load_orders_from_db()

        # 메인 루프 시작
        self._running = True
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

                # 3. 전략 실행 (NORMAL 모드에서만)
                if self.risk_engine.can_open_position:
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
            if self._is_upbit and self.pullback_strategy and self.pullback_strategy.is_enabled():
                if risk_decision.satellite_allowed:
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
            if self._is_upbit and self.ignition_strategy and settings.ignition_mode != "OFF":
                if risk_decision.satellite_allowed:
                    try:
                        # BTC 24시간 변화율 설정 (상대강도 계산용)
                        btc_data = self._market_data.get("KRW-BTC", {})
                        btc_change_24h = btc_data.get("price_change_pct", 0) / 100
                        self.ignition_strategy.set_btc_change(btc_change_24h)
                        self.ignition_strategy.set_account_balance(current_equity)
                        self.ignition_strategy.set_mode(settings.ignition_mode)

                        # 15분마다 Setup 스캔
                        import time
                        current_minute = int(time.time() / 60)
                        if current_minute % 15 == 0:
                            qualified_symbols = self.symbol_manager.get_qualified_symbols()
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
            if self._is_upbit and self.surge_detector and settings.ignition_mode != "OFF":
                if risk_decision.satellite_allowed:
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

            # Slack 알림
            if self.slack_notifier.is_enabled:
                notional = filled_qty * avg_price
                await self.slack_notifier.send(SlackMessage(
                    text=f"""
:chart_with_upwards_trend: *Pullback 매수 체결*
> 심볼: `{symbol}`
> 점수: {signal.score}점 (L{signal.level})
> 수량: {filled_qty:.4f}
> 가격: ₩{avg_price:,.0f}
> 금액: ₩{notional:,.0f}
> 손절가: ₩{signal.stop_loss:,.0f}
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

            # Slack 알림
            if self.slack_notifier.is_enabled:
                notional = filled_qty * avg_price
                await self.slack_notifier.send(SlackMessage(
                    text=f"""
:fire: *Ignition 매수 체결*
> 심볼: `{symbol}`
> 모드: {sizing.mode}
> 수량: {filled_qty:.4f}
> 가격: ₩{avg_price:,.0f}
> 금액: ₩{notional:,.0f}
> 손절가: ₩{sizing.stop_loss:,.0f}
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

            # Slack 알림
            if self.slack_notifier.is_enabled:
                pnl_emoji = ":moneybag:" if position.realized_pnl > 0 else ":money_with_wings:"
                await self.slack_notifier.send(SlackMessage(
                    text=f"""
{pnl_emoji} *Ignition 청산*
> 심볼: `{symbol}`
> 사유: {exit_reason.value}
> 수량: {exit_qty:.4f}
> 가격: ₩{result.avg_price or current_price:,.0f}
> 손익: ₩{position.realized_pnl:,.0f}
                    """.strip(),
                    level=AlertLevel.INFO if position.realized_pnl >= 0 else AlertLevel.WARNING,
                ))

        except Exception as e:
            logger.error("Ignition exit execution error", error=str(e))

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

            # Slack 알림
            if self.slack_notifier.is_enabled:
                notional = filled_qty * avg_price
                await self.slack_notifier.send(SlackMessage(
                    text=f"""
:rocket: *급등 감지 매수*
> 심볼: `{symbol}`
> 1분 변화: +{surge.change_1m_pct:.1f}%
> 5분 변화: +{surge.change_5m_pct:.1f}%
> 거래량: {surge.volume_ratio:.1f}x
> 수량: {filled_qty:.4f}
> 가격: ₩{avg_price:,.0f}
> 금액: ₩{notional:,.0f}
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

            # Slack 알림
            if self.slack_notifier.is_enabled:
                pnl_emoji = ":moneybag:" if pnl > 0 else ":money_with_wings:"
                await self.slack_notifier.send(SlackMessage(
                    text=f"""
{pnl_emoji} *급등 청산*
> 심볼: `{symbol}`
> 사유: {exit_reason}
> 수량: {exit_qty:.4f}
> 진입가: ₩{position.entry_price:,.0f}
> 청산가: ₩{result.avg_price or current_price:,.0f}
> 손익: ₩{pnl:,.0f}
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

                            # Slack 알림 (청산)
                            if self.slack_notifier.is_enabled:
                                pnl_emoji = ":moneybag:" if realized_pnl >= 0 else ":money_with_wings:"
                                pnl_sign = "+" if realized_pnl >= 0 else ""
                                pnl_pct = (realized_pnl / (entry_price * result.filled_qty)) * 100 if entry_price > 0 else 0
                                await self.slack_notifier.send(SlackMessage(
                                    text=f"""
{pnl_emoji} *Pullback 청산*
> 심볼: `{symbol}`
> 사유: {reason}
> 수량: {result.filled_qty:.4f}
> 진입가: ₩{entry_price:,.0f}
> 청산가: ₩{result.avg_price:,.0f}
> 손익: {pnl_sign}₩{realized_pnl:,.0f} ({pnl_sign}{pnl_pct:.2f}%)
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
                    pos_dict = {
                        "symbol": symbol,
                        "side": "BUY",  # Upbit은 롱만
                        "quantity": balance.total,
                        "avg_price": current_price,  # 진입가 추적 필요
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
                            self.add_event(
                                level="INFO",
                                event_type="ORDER",
                                message=f"Upbit position closed: {symbol}",
                                details={
                                    "reason": sat_exit.reason,
                                    "quantity": result.filled_qty,
                                    "price": result.avg_price,
                                },
                            )

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

    async def _update_cached_state_upbit(self) -> None:
        """Upbit 상태 캐시 업데이트"""
        # 잔고 조회
        krw_balance = await self.exchange.get_balance("KRW")
        all_balances = await self.exchange.get_all_balances()

        krw_total = krw_balance.total if krw_balance else 0
        current_equity = krw_total

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

                # PSM에서 진입가 조회
                psm_pos = self.position_state_machine.get_position(symbol)
                entry_price = psm_pos.entry_price if psm_pos else current_price
                position_pnl = (current_price - entry_price) * balance.total if psm_pos else 0
                unrealized_pnl += position_pnl

                self._cached_positions.append({
                    "symbol": symbol,
                    "strategy": "SATELLITE",  # Upbit은 Satellite 위주
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
