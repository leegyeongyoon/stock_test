"""백테스트 엔진

과거 캔들 데이터로 전략 성능을 시뮬레이션
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from src.backtesting.analytics.metrics import BacktestMetrics
from src.backtesting.data.data_loader import BacktestCandle, BacktestDataLoader, HistoryProvider
from src.backtesting.models.schemas import (
    BacktestConfig,
    BacktestResult,
    BacktestTrade,
    BacktestMetricsSchema,
    EquityPoint,
)

logger = structlog.get_logger()


@dataclass
class SimulatedPosition:
    """시뮬레이션 포지션"""

    trade_id: str
    symbol: str
    strategy: str
    entry_time: datetime
    entry_price: float
    quantity: float
    entry_value: float

    # 손익 추적
    max_profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    # 진입 시점 지표
    entry_indicators: dict = field(default_factory=dict)


@dataclass
class SimulatedAccount:
    """시뮬레이션 계좌"""

    initial_capital: float
    cash: float
    positions: dict[str, SimulatedPosition] = field(default_factory=dict)
    closed_trades: list[BacktestTrade] = field(default_factory=list)
    commission_rate: float = 0.0005  # 0.05%
    slippage_bps: float = 5  # 5bps

    @property
    def equity(self) -> float:
        """현재 자산 가치"""
        position_value = sum(
            p.quantity * p.entry_price for p in self.positions.values()
        )
        return self.cash + position_value

    def can_buy(self, amount: float) -> bool:
        """매수 가능 여부"""
        return self.cash >= amount

    def apply_slippage(self, price: float, is_buy: bool) -> float:
        """슬리피지 적용"""
        slippage = price * (self.slippage_bps / 10000)
        if is_buy:
            return price + slippage  # 매수 시 더 높게
        return price - slippage  # 매도 시 더 낮게

    def apply_commission(self, value: float) -> float:
        """수수료 계산"""
        return value * self.commission_rate


class BacktestEngine:
    """
    백테스트 엔진

    Features:
    - 전략 시그널 시뮬레이션
    - 슬리피지/수수료 반영
    - 포지션 관리
    - 성능 지표 계산
    """

    def __init__(
        self,
        config: BacktestConfig,
        data_loader: BacktestDataLoader,
    ) -> None:
        self._config = config
        self._data_loader = data_loader
        self._history_provider = HistoryProvider()
        self._account: Optional[SimulatedAccount] = None
        self._current_time: Optional[datetime] = None
        self._equity_points: list[EquityPoint] = []

    async def run(
        self,
        signal_generator: callable,
        exit_checker: Optional[callable] = None,
        progress_callback: Optional[callable] = None,
    ) -> BacktestResult:
        """
        백테스트 실행

        Args:
            signal_generator: 시그널 생성 함수
                (timestamp, history_provider, symbols) -> list[dict]
                반환: [{"symbol": "KRW-BTC", "action": "buy", "score": 85, ...}]

            exit_checker: 청산 조건 체크 함수 (선택)
                (position, current_price, history_provider) -> Optional[str]
                반환: 청산 사유 또는 None

            progress_callback: 진행 상황 콜백
                (current_idx, total_count) -> None

        Returns:
            백테스트 결과
        """
        run_id = str(uuid.uuid4())[:8]

        logger.info(
            "Starting backtest",
            run_id=run_id,
            strategy=self._config.strategy,
            symbols=self._config.symbols,
            start_date=self._config.start_date.isoformat(),
            end_date=self._config.end_date.isoformat(),
        )

        # 계좌 초기화
        self._account = SimulatedAccount(
            initial_capital=self._config.initial_capital,
            cash=self._config.initial_capital,
            commission_rate=self._config.commission_rate,
            slippage_bps=self._config.slippage_bps,
        )

        # 데이터 로드
        symbols = self._config.symbols
        if not symbols:
            # 심볼 지정 없으면 데이터 있는 심볼 사용
            logger.warning("No symbols specified, backtest may be limited")
            return self._empty_result(run_id)

        all_candles = await self._data_loader.load_multi_symbol(
            symbols,
            self._config.interval,
            self._config.start_date,
            self._config.end_date,
        )

        # HistoryProvider에 데이터 로드
        for symbol, candles in all_candles.items():
            self._history_provider.load_data(symbol, self._config.interval, candles)

        # 타임스탬프 수집
        timestamps = set()
        for candles in all_candles.values():
            for c in candles:
                timestamps.add(c.open_time)

        sorted_timestamps = sorted(timestamps)
        total_count = len(sorted_timestamps)

        if total_count == 0:
            logger.warning("No data available for backtest")
            return self._empty_result(run_id)

        # 초기 에쿼티 포인트
        self._equity_points = [
            EquityPoint(
                timestamp=self._config.start_date,
                equity=self._config.initial_capital,
                drawdown_pct=0,
            )
        ]

        # 시뮬레이션 루프
        for idx, timestamp in enumerate(sorted_timestamps):
            self._current_time = timestamp
            self._history_provider.set_time(timestamp)

            # 현재 가격 맵
            current_prices = {}
            for symbol, candles in all_candles.items():
                for c in candles:
                    if c.open_time == timestamp:
                        current_prices[symbol] = c.close
                        break

            # 1. 기존 포지션 청산 체크
            if exit_checker:
                await self._check_exits(exit_checker, current_prices)

            # 2. 새 시그널 생성
            signals = signal_generator(
                timestamp,
                self._history_provider,
                symbols,
            )

            # 3. 시그널 처리
            for signal in signals:
                await self._process_signal(signal, current_prices)

            # 4. 에쿼티 기록
            self._record_equity(timestamp)

            # 5. 진행 상황
            if progress_callback and idx % 100 == 0:
                progress_callback(idx, total_count)

        # 마지막 가격으로 미청산 포지션 청산
        await self._close_all_positions(sorted_timestamps[-1], all_candles)

        # 결과 계산
        return self._build_result(run_id)

    async def _check_exits(
        self,
        exit_checker: callable,
        current_prices: dict[str, float],
    ) -> None:
        """청산 조건 체크"""
        positions_to_close = []

        for symbol, position in self._account.positions.items():
            price = current_prices.get(symbol)
            if price is None:
                continue

            # 손익 업데이트
            pnl_pct = (price - position.entry_price) / position.entry_price
            position.max_profit_pct = max(position.max_profit_pct, pnl_pct)
            position.max_drawdown_pct = min(position.max_drawdown_pct, pnl_pct)

            # 청산 체크
            exit_reason = exit_checker(position, price, self._history_provider)
            if exit_reason:
                positions_to_close.append((symbol, exit_reason, price))

        # 청산 실행
        for symbol, reason, price in positions_to_close:
            await self._close_position(symbol, price, reason)

    async def _process_signal(
        self,
        signal: dict,
        current_prices: dict[str, float],
    ) -> None:
        """시그널 처리"""
        symbol = signal.get("symbol")
        action = signal.get("action")

        if not symbol or not action:
            return

        price = current_prices.get(symbol)
        if price is None:
            return

        if action == "buy":
            await self._open_position(symbol, price, signal)
        elif action == "sell":
            reason = signal.get("reason", "signal_sell")
            await self._close_position(symbol, price, reason)

    async def _open_position(
        self,
        symbol: str,
        price: float,
        signal: dict,
    ) -> None:
        """포지션 진입"""
        # 이미 포지션 있으면 스킵
        if symbol in self._account.positions:
            return

        # 포지션 사이즈 계산 (자본의 일정 비율)
        position_pct = signal.get("position_pct", 0.1)  # 기본 10%
        position_value = self._account.cash * position_pct

        if not self._account.can_buy(position_value):
            return

        # 슬리피지 적용
        exec_price = self._account.apply_slippage(price, is_buy=True)
        quantity = position_value / exec_price

        # 수수료
        commission = self._account.apply_commission(position_value)

        # 계좌 업데이트
        self._account.cash -= position_value + commission

        # 포지션 생성
        position = SimulatedPosition(
            trade_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            strategy=self._config.strategy,
            entry_time=self._current_time,
            entry_price=exec_price,
            quantity=quantity,
            entry_value=position_value,
            entry_indicators=signal.get("indicators", {}),
        )

        self._account.positions[symbol] = position

        logger.debug(
            "Opened position",
            symbol=symbol,
            price=exec_price,
            quantity=quantity,
            value=position_value,
        )

    async def _close_position(
        self,
        symbol: str,
        price: float,
        reason: str,
    ) -> None:
        """포지션 청산"""
        position = self._account.positions.get(symbol)
        if not position:
            return

        # 슬리피지 적용
        exec_price = self._account.apply_slippage(price, is_buy=False)
        exit_value = position.quantity * exec_price

        # 수수료
        commission = self._account.apply_commission(exit_value)

        # 손익 계산
        pnl = exit_value - position.entry_value - commission
        pnl_pct = pnl / position.entry_value

        # 보유 시간
        holding_minutes = int(
            (self._current_time - position.entry_time).total_seconds() / 60
        )

        # 거래 기록
        trade = BacktestTrade(
            trade_id=position.trade_id,
            symbol=symbol,
            strategy=position.strategy,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            entry_quantity=position.quantity,
            exit_time=self._current_time,
            exit_price=exec_price,
            exit_reason=reason,
            pnl=pnl,
            pnl_pct=pnl_pct,
            commission=commission,
            holding_minutes=holding_minutes,
            max_profit_pct=position.max_profit_pct,
            max_drawdown_pct=position.max_drawdown_pct,
            entry_indicators=position.entry_indicators,  # v28: 진입 시점 지표
        )

        self._account.closed_trades.append(trade)

        # 계좌 업데이트
        self._account.cash += exit_value - commission
        del self._account.positions[symbol]

        logger.debug(
            "Closed position",
            symbol=symbol,
            price=exec_price,
            pnl=pnl,
            pnl_pct=f"{pnl_pct*100:.2f}%",
            reason=reason,
        )

    async def _close_all_positions(
        self,
        timestamp: datetime,
        all_candles: dict[str, list[BacktestCandle]],
    ) -> None:
        """모든 포지션 청산 (백테스트 종료 시)"""
        self._current_time = timestamp

        for symbol in list(self._account.positions.keys()):
            # 마지막 가격 찾기
            candles = all_candles.get(symbol, [])
            if candles:
                price = candles[-1].close
                await self._close_position(symbol, price, "backtest_end")

    def _record_equity(self, timestamp: datetime) -> None:
        """에쿼티 기록"""
        equity = self._account.equity
        peak = max(p.equity for p in self._equity_points) if self._equity_points else equity

        dd_pct = (peak - equity) / peak * 100 if peak > 0 and equity < peak else 0

        self._equity_points.append(
            EquityPoint(
                timestamp=timestamp,
                equity=equity,
                drawdown_pct=dd_pct,
            )
        )

    def _build_result(self, run_id: str) -> BacktestResult:
        """결과 빌드"""
        trades = self._account.closed_trades

        # 메트릭스 계산
        metrics_calc = BacktestMetrics(
            trades,
            self._config.initial_capital,
            self._config.start_date,
            self._config.end_date,
        )
        metrics = metrics_calc.calculate()

        # 에쿼티 커브 변환
        equity_curve = [
            EquityPoint(
                timestamp=p.timestamp,
                equity=p.equity,
                drawdown_pct=p.drawdown_pct,
            )
            for p in self._equity_points
        ]

        return BacktestResult(
            run_id=run_id,
            strategy=self._config.strategy,
            symbols=self._config.symbols,
            interval=self._config.interval,
            start_date=self._config.start_date,
            end_date=self._config.end_date,
            initial_capital=self._config.initial_capital,
            final_equity=self._account.equity,
            metrics=metrics,
            parameters=self._config.parameters,
            trades=trades,
            equity_curve=equity_curve,
            created_at=datetime.utcnow(),
        )

    def _empty_result(self, run_id: str) -> BacktestResult:
        """빈 결과"""
        return BacktestResult(
            run_id=run_id,
            strategy=self._config.strategy,
            symbols=self._config.symbols,
            interval=self._config.interval,
            start_date=self._config.start_date,
            end_date=self._config.end_date,
            initial_capital=self._config.initial_capital,
            final_equity=self._config.initial_capital,
            metrics=BacktestMetricsSchema(
                total_return_pct=0,
                max_drawdown_pct=0,
                total_trades=0,
                win_rate=0,
            ),
            parameters=self._config.parameters,
            trades=[],
            equity_curve=[],
            created_at=datetime.utcnow(),
        )


def create_pullback_exit_checker(
    stop_loss_pct: float = -0.01,
    take_profit_pct: float = 0.015,
    trailing_trigger_pct: float = 0.008,
    trailing_stop_pct: float = 0.004,
):
    """
    PULLBACK 전략용 청산 체커 생성

    Args:
        stop_loss_pct: 손절 (-1%)
        take_profit_pct: 익절 (+1.5%)
        trailing_trigger_pct: 트레일링 시작 (+0.8%)
        trailing_stop_pct: 트레일링 스탑 (0.4%)

    v2: 캔들 내 고가/저가 체크 (스탑로스 정확도 향상)
    """

    def check_exit(
        position: SimulatedPosition,
        current_price: float,
        history_provider: HistoryProvider,
    ) -> Optional[str]:
        entry_price = position.entry_price

        # v2: 캔들 내 가격 변동 체크 (고가/저가로 스탑로스 정확도 향상)
        candles = history_provider.get_candles(position.symbol, "5m", 1)
        if candles:
            current_candle = candles[-1]

            # 저가로 스탑로스 체크 (캔들 내 순간 터치)
            low_pnl_pct = (current_candle.low - entry_price) / entry_price
            if low_pnl_pct <= stop_loss_pct:
                return "stop_loss"

            # 고가로 익절 체크 (캔들 내 순간 터치)
            high_pnl_pct = (current_candle.high - entry_price) / entry_price
            if high_pnl_pct >= take_profit_pct:
                return "take_profit"

        # 종가 기준 체크 (기존 로직 - 폴백)
        pnl_pct = (current_price - entry_price) / entry_price

        # 손절
        if pnl_pct <= stop_loss_pct:
            return "stop_loss"

        # 익절
        if pnl_pct >= take_profit_pct:
            return "take_profit"

        # 트레일링 스탑
        if position.max_profit_pct >= trailing_trigger_pct:
            # 고점 대비 하락
            drop_from_peak = position.max_profit_pct - pnl_pct
            if drop_from_peak >= trailing_stop_pct:
                return "trailing_stop"

        return None

    return check_exit
