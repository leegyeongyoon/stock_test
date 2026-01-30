"""Trading Engine - 메인 엔진 루프"""

import asyncio
from datetime import datetime
from typing import Optional

import structlog

from src.config import get_settings
from src.data.candle_manager import get_candle_manager
from src.engine.command_queue import Command, CommandQueue, CommandType
from src.exchange.binance_perp import BinancePerpExchange
from src.exchange.binance_spot import BinanceSpotExchange
from src.features.feature_engine import FeatureEngine
from src.models.schemas import OrderSide, OrderType, StrategyType, TradingMode
from src.position import PositionStateMachine
from src.risk.exec_health import get_exec_health_monitor
from src.risk.risk_engine import RiskEngine
from src.risk.risk_overlay import RiskMode, get_risk_overlay
from src.services.trade_recorder import trade_recorder
from src.strategies.core_carry import CoreCarryStrategy
from src.strategies.core_safety import get_core_safety_guard
from src.strategies.satellite import Regime, SatelliteStrategy

logger = structlog.get_logger()
settings = get_settings()

# 모니터링할 심볼 리스트
WATCH_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


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
        # 거래소 연결 (Spot과 Futures는 별도 API 키 사용)
        self.spot_exchange = BinanceSpotExchange(
            api_key=settings.spot_api_key,
            secret=settings.spot_secret,
            testnet=settings.is_paper_mode,
        )
        self.perp_exchange = BinancePerpExchange(
            api_key=settings.futures_api_key,
            secret=settings.futures_secret,
            testnet=settings.is_paper_mode,
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

        # 전략
        self.core_strategy = CoreCarryStrategy()
        self.satellite_strategy = SatelliteStrategy()

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

    async def start(self) -> None:
        """엔진 시작"""
        if self._running:
            logger.warning("Engine already running")
            return

        logger.info(
            "Starting Trading Engine",
            paper_mode=settings.is_paper_mode,
        )

        # 거래소 연결
        spot_connected = await self.spot_exchange.connect()
        perp_connected = await self.perp_exchange.connect()

        if not spot_connected or not perp_connected:
            logger.error("Failed to connect to exchanges")
            raise RuntimeError("Exchange connection failed")

        # Risk Engine 시작
        await self.risk_engine.start()

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

        # 거래소 연결 해제
        await self.spot_exchange.disconnect()
        await self.perp_exchange.disconnect()

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
        """시장 데이터 업데이트"""
        try:
            for symbol in WATCH_SYMBOLS:
                # Spot 시세
                spot_ticker = await self.spot_exchange.get_ticker(symbol)
                # Futures 시세
                perp_ticker = await self.perp_exchange.get_ticker(symbol)
                # 펀딩비
                funding_rate = await self.perp_exchange.get_funding_rate(symbol)

                if spot_ticker and perp_ticker:
                    self._market_data[symbol] = {
                        "symbol": symbol,
                        "spot_price": spot_ticker.last,
                        "perp_price": perp_ticker.last,
                        "spot_bid": spot_ticker.bid,
                        "spot_ask": spot_ticker.ask,
                        "perp_bid": perp_ticker.bid,
                        "perp_ask": perp_ticker.ask,
                        "funding_rate": funding_rate or 0,
                        "price": perp_ticker.last,  # Satellite용
                        "high_20": perp_ticker.last * 1.005,  # 임시 (실제로는 캔들 데이터 필요)
                        "low_20": perp_ticker.last * 0.995,
                        "rvol": 1.0,  # 임시 (실제로는 거래량 비교 필요)
                        "vwap": perp_ticker.last,  # 임시
                        "timestamp": datetime.utcnow(),
                    }

                    # Core Safety Guard에 펀딩 레이트 업데이트
                    if funding_rate is not None:
                        self.core_safety.update_funding_rate(symbol, funding_rate)

            # BTC 레짐 계산 (간단 버전)
            btc_data = self._market_data.get("BTCUSDT", {})
            if btc_data:
                btc_price = btc_data.get("perp_price", 0)
                btc_funding = btc_data.get("funding_rate", 0)

                # 펀딩비 기반 레짐 판단
                if btc_funding > 0.0003:  # 0.03% 이상 -> 과열 (롱 많음)
                    self._btc_regime = Regime.BULLISH
                elif btc_funding < -0.0001:  # -0.01% 이하 -> 공포
                    self._btc_regime = Regime.BEARISH
                else:
                    self._btc_regime = Regime.NEUTRAL

                self.satellite_strategy.update_btc_regime(self._btc_regime)

        except Exception as e:
            logger.error("Failed to update market data", error=str(e))

    async def _execute_strategies(self) -> None:
        """전략 실행 (우선순위 체인 적용)"""
        try:
            # === 우선순위 1-3: Risk Overlay 평가 ===
            # 현재 자산 조회
            perp_balance = await self.perp_exchange.get_balance("USDT")
            spot_balance = await self.spot_exchange.get_balance("USDT")
            current_equity = (
                (perp_balance.total if perp_balance else 0)
                + (spot_balance.total if spot_balance else 0)
            )

            # Risk Overlay 평가
            risk_decision = self.risk_overlay.evaluate(current_equity)

            # HALT 모드면 신규 진입 완전 차단
            if risk_decision.mode == RiskMode.HALT:
                logger.warning(
                    "Strategy execution blocked: HALT mode",
                    reason=risk_decision.primary_reason,
                )
                return

            for symbol, market_data in self._market_data.items():
                # Core 전략 (캐시앤캐리) - Risk Overlay + Core Safety 체크
                if risk_decision.core_allowed:
                    # Core Safety Guard 체크
                    core_ok, core_reason = self.core_safety.can_open_core(symbol)
                    if not core_ok:
                        logger.debug(
                            "Core entry blocked by safety guard",
                            symbol=symbol,
                            reason=core_reason,
                        )
                    else:
                        core_signal = await self.core_strategy.generate_signal(market_data)
                        if core_signal:
                            await self._execute_signal(core_signal, market_data, risk_decision)

                # Satellite 전략 (모멘텀) - Risk Overlay 체크
                if risk_decision.satellite_allowed:
                    sat_signal = await self.satellite_strategy.generate_signal(market_data)
                    if sat_signal:
                        await self._execute_signal(sat_signal, market_data, risk_decision)

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

            if available_capital < 100:  # 최소 $100 필요
                logger.warning("Insufficient capital", available=available_capital)
                return

            # Core 전략에서 Spot 잔고 체크 (FUTURES_ONLY 모드가 아닐 때만)
            if signal.strategy == StrategyType.CORE and not settings.futures_only_mode:
                spot_balance = await self.spot_exchange.get_balance("USDT")
                spot_free = spot_balance.free if spot_balance else 0

                # Spot 주문을 위한 예상 필요 금액 계산
                spot_price = market_data.get("spot_price", 0)
                if spot_price <= 0:
                    logger.warning("Invalid spot price", symbol=signal.symbol)
                    return

                # 예상 주문 금액 (보수적으로 10% 여유)
                estimated_order_value = (available_capital * 0.1) * 1.1  # 10% 할당 + 10% 버퍼

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
                quantity = self.core_strategy.get_position_size(signal, available_capital)
            else:
                quantity = self.satellite_strategy.get_position_size(signal, available_capital)

            # Binance Futures 최소 notional 100 USDT 보장
            MIN_NOTIONAL_USDT = 105  # 100 + 여유분
            if current_price > 0:
                min_quantity = MIN_NOTIONAL_USDT / current_price
                if quantity < min_quantity:
                    logger.info(
                        "Adjusting quantity to meet min notional",
                        original=quantity,
                        min_required=min_quantity,
                        min_notional=MIN_NOTIONAL_USDT,
                    )
                    quantity = min_quantity

            if quantity <= 0:
                logger.warning("Position size too small", quantity=quantity)
                return

            # 자본 대비 최대 한도 체크 (10% 제한)
            max_notional = available_capital * 0.10
            max_quantity = max_notional / current_price if current_price > 0 else 0
            if quantity > max_quantity and max_quantity > 0:
                logger.info(
                    "Capping quantity to max exposure",
                    original=quantity,
                    capped=max_quantity,
                )
                quantity = max_quantity

            # Notional 최종 체크
            notional = quantity * current_price
            if notional < 100:
                logger.warning(
                    "Notional too small after adjustments",
                    notional=notional,
                    quantity=quantity,
                )
                return

            # 심볼별 최소 수량 및 정밀도 처리
            quantity = self._round_quantity(symbol, quantity)

            if quantity <= 0:
                return

            # Core 전략: 현물 매수 + 선물 매도 (캐시앤캐리)
            if signal.strategy == StrategyType.CORE:
                await self._execute_core_entry(signal, market_data, quantity)
            # Satellite 전략: 선물만 (모멘텀)
            else:
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

    def _round_quantity(self, symbol: str, quantity: float) -> float:
        """심볼별 수량 정밀도 처리 (현물용)"""
        # 심볼별 최소 수량 및 정밀도 (Binance Spot 규칙)
        precision_map = {
            "BTCUSDT": (0.00001, 5),  # 최소 0.00001 BTC
            "ETHUSDT": (0.0001, 4),   # 최소 0.0001 ETH
            "SOLUSDT": (0.01, 2),     # 최소 0.01 SOL
            "BNBUSDT": (0.001, 3),    # 최소 0.001 BNB
            "XRPUSDT": (0.1, 1),      # 최소 0.1 XRP
        }

        min_qty, decimals = precision_map.get(symbol, (0.001, 3))

        # 정밀도 맞추기
        rounded = round(quantity, decimals)

        # 최소 수량 체크
        if rounded < min_qty:
            return 0

        return rounded

    def _round_quantity_for_perp(self, symbol: str, quantity: float) -> float:
        """선물용 수량 정밀도 처리 (Binance Futures는 더 낮은 정밀도)"""
        # 심볼별 최소 수량 및 정밀도 (Binance Futures 규칙)
        precision_map = {
            "BTCUSDT": (0.001, 3),    # 최소 0.001 BTC
            "ETHUSDT": (0.001, 3),    # 최소 0.001 ETH
            "SOLUSDT": (1, 0),        # 최소 1 SOL (정수)
            "BNBUSDT": (0.01, 2),     # 최소 0.01 BNB
            "XRPUSDT": (1, 0),        # 최소 1 XRP (정수)
        }

        min_qty, decimals = precision_map.get(symbol, (0.001, 3))

        # 정밀도 맞추기 (내림)
        if decimals == 0:
            rounded = int(quantity)
        else:
            rounded = round(quantity, decimals)

        # 최소 수량 체크
        if rounded < min_qty:
            return 0

        return rounded

    async def _manage_positions(self) -> None:
        """포지션 관리"""
        try:
            # Futures 포지션 조회
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

                    # Core 청산 조건 체크 (기존 로직)
                    core_exit = await self.core_strategy.should_exit(pos_dict, market_data)
                    if core_exit:
                        self.add_event(
                            level="WARNING",
                            event_type="STRATEGY",
                            message=f"Core exit signal: {symbol}",
                            details={"reason": core_exit.reason},
                        )
                        # 실제 청산 실행
                        await self._execute_position_close(
                            symbol=symbol,
                            position=position,
                            reason=core_exit.reason,
                            strategy="CORE",
                        )
                        continue

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

            # Core 전략 상태 리셋
            if strategy == "CORE":
                self.core_strategy.reset_carry(symbol)

            # 주문 기록
            self.add_order(
                symbol=symbol,
                strategy=strategy,
                side=close_side.value,
                order_type="MARKET",
                quantity=result.filled_qty,
                price=result.avg_price,
                status="FILLED",
                filled_qty=result.filled_qty,
                avg_fill_price=result.avg_price,
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

                # DB 기록
                order_id = self.add_order(
                    symbol=symbol,
                    strategy="SATELLITE",
                    side=close_side.value,
                    order_type="MARKET",
                    quantity=result.filled_qty,
                    price=result.avg_price,
                    status="FILLED",
                    filled_qty=result.filled_qty,
                    avg_fill_price=result.avg_price,
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

                # DB 기록
                self.add_order(
                    symbol=symbol,
                    strategy="SATELLITE",
                    side=close_side.value,
                    order_type="MARKET",
                    quantity=result.filled_qty,
                    price=result.avg_price,
                    status="FILLED",
                    filled_qty=result.filled_qty,
                    avg_fill_price=result.avg_price,
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
                "drawdown": 0.0,  # TODO: 드로우다운 계산
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
                    "strategy": "CORE",  # TODO: 전략 매핑
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

        except Exception as e:
            logger.error("Failed to update cached state", error=str(e))

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

    def get_orders(self, limit: int = 100) -> list:
        """주문 목록 조회"""
        return self._cached_orders[-limit:]

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
                    realized_pnl=0.0,  # TODO: 실제 PnL 계산
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
