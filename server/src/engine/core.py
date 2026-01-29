"""Trading Engine - 메인 엔진 루프"""

import asyncio
from datetime import datetime
from typing import Optional

import structlog

from src.config import get_settings
from src.engine.command_queue import Command, CommandQueue, CommandType
from src.exchange.binance_perp import BinancePerpExchange
from src.exchange.binance_spot import BinanceSpotExchange
from src.models.schemas import OrderSide, OrderType, StrategyType, TradingMode
from src.risk.risk_engine import RiskEngine
from src.strategies.core_carry import CoreCarryStrategy
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
        """전략 실행"""
        try:
            for symbol, market_data in self._market_data.items():
                # Core 전략 (캐시앤캐리)
                core_signal = await self.core_strategy.generate_signal(market_data)
                if core_signal:
                    await self._execute_signal(core_signal, market_data)

                # Satellite 전략 (모멘텀)
                sat_signal = await self.satellite_strategy.generate_signal(market_data)
                if sat_signal:
                    await self._execute_signal(sat_signal, market_data)

        except Exception as e:
            logger.error("Strategy execution error", error=str(e))

    async def _execute_signal(self, signal, market_data: dict) -> None:
        """시그널 실행 - 실제 주문 발행"""
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
            spot_balance = await self.spot_exchange.get_balance("USDT")
            perp_balance = await self.perp_exchange.get_balance("USDT")

            available_capital = (spot_balance.free if spot_balance else 0) + \
                              (perp_balance.free if perp_balance else 0)

            if available_capital < 100:  # 최소 $100 필요
                logger.warning("Insufficient capital", available=available_capital)
                return

            # 포지션 사이즈 계산
            if signal.strategy == StrategyType.CORE:
                quantity = self.core_strategy.get_position_size(signal, available_capital)
            else:
                quantity = self.satellite_strategy.get_position_size(signal, available_capital)

            if quantity <= 0:
                logger.warning("Position size too small", quantity=quantity)
                return

            # 심볼별 최소 수량 및 정밀도 처리
            symbol = signal.symbol
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
        """Core 전략 진입 - 현물 매수 + 선물 매도"""
        symbol = signal.symbol

        logger.info(
            "Executing Core entry",
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
        # 선물은 정밀도가 다름 - 정수 단위로 변환
        perp_qty = self._round_quantity_for_perp(symbol, spot_result.filled_qty)

        if perp_qty <= 0:
            logger.error("Perp quantity too small after rounding", original=spot_result.filled_qty)
            # 현물만 매수된 상태 - SAFE 모드
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
            # 헤지 실패 시 SAFE 모드로 전환
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

                pos_dict = {
                    "symbol": symbol,
                    "side": position.side,
                    "quantity": position.quantity,
                    "avg_price": position.entry_price,
                    "opened_at": datetime.utcnow(),  # TODO: 실제 진입 시간
                }

                # Core 청산 조건 체크
                core_exit = await self.core_strategy.should_exit(pos_dict, market_data)
                if core_exit:
                    self.add_event(
                        level="WARNING",
                        event_type="STRATEGY",
                        message=f"Core exit signal: {symbol}",
                        details={"reason": core_exit.reason},
                    )

                # Satellite 청산 조건 체크
                sat_exit = await self.satellite_strategy.should_exit(pos_dict, market_data)
                if sat_exit:
                    self.add_event(
                        level="WARNING",
                        event_type="STRATEGY",
                        message=f"Satellite exit signal: {symbol}",
                        details={"reason": sat_exit.reason},
                    )

        except Exception as e:
            logger.error("Position management error", error=str(e))

    async def _update_cached_state(self) -> None:
        """상태 캐시 업데이트"""
        try:
            # 잔고 조회
            spot_balance = await self.spot_exchange.get_balance("USDT")
            perp_balance = await self.perp_exchange.get_balance("USDT")

            spot_total = spot_balance.total if spot_balance else 0
            perp_total = perp_balance.total if perp_balance else 0

            # 포지션 조회
            perp_positions = await self.perp_exchange.get_positions()

            # Summary 업데이트
            self._cached_summary = {
                "equity": spot_total + perp_total,
                "pnl_today": 0.0,  # TODO: 계산
                "pnl_today_pct": 0.0,
                "drawdown": 0.0,
                "exposure": sum(p.quantity * p.entry_price for p in perp_positions),
                "cash": spot_total + (perp_balance.free if perp_balance else 0),
                "margin_used": perp_total - (perp_balance.free if perp_balance else 0),
                "mode": self.mode.value,
                "is_paper": settings.is_paper_mode,
                "updated_at": datetime.utcnow().isoformat(),
            }

            # 포지션 캐시
            self._cached_positions = [
                {
                    "symbol": p.symbol,
                    "strategy": "CORE",  # TODO: 전략 매핑
                    "side": p.side,
                    "quantity": p.quantity,
                    "avg_price": p.entry_price,
                    "current_price": p.entry_price,  # TODO: 현재가
                    "unrealized_pnl": p.unrealized_pnl,
                    "realized_pnl": 0.0,
                    "notional": p.quantity * p.entry_price,
                    "leverage": p.leverage,
                }
                for p in perp_positions
            ]

        except Exception as e:
            logger.error("Failed to update cached state", error=str(e))

    def get_summary(self) -> dict:
        """요약 정보 조회"""
        return self._cached_summary

    def get_positions(self) -> list:
        """포지션 목록 조회"""
        return self._cached_positions

    def get_events(self, limit: int = 100) -> list:
        """이벤트 목록 조회"""
        return self._cached_events[-limit:]

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
