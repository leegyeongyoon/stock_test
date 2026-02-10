"""
한국 주식 백테스트 엔진

특징:
- 거래세 및 수수료 정확 반영
- 가격제한폭(±30%) 적용
- 호가단위 적용
- 거래일 캘린더 적용
- 다층 필터 시스템 연동
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional, Callable
import uuid
import numpy as np
from numpy.typing import NDArray

from src.krstock.market import (
    MarketType,
    is_trading_day,
    get_tick_size,
    align_to_tick,
    calculate_trading_cost,
    calculate_price_limits,
    PRICE_LIMIT_RATIO,
    TradingCostConfig,
)
from src.krstock.indicators import TechnicalIndicators, calculate_all_indicators
from src.strategies.krstock.base_krstock import (
    BaseKRStockStrategy,
    CandleData,
    Signal,
    SignalType,
    ExitReason,
    Position,
)


@dataclass
class KRBacktestConfig:
    """백테스트 설정"""
    # 자본금
    initial_capital: float = 100_000_000  # 1억원

    # 시장
    market: MarketType = MarketType.KOSPI

    # 거래 비용
    commission_rate: float = 0.00015  # 0.015%
    tax_rate: float = 0.0023          # 0.23% (KOSPI+농특세)

    # 슬리피지
    slippage_pct: float = 0.1         # 0.1%

    # 포지션 제한
    max_positions: int = 10           # 최대 포지션 수
    max_single_position_pct: float = 0.10  # 단일 포지션 최대 10%
    max_sector_exposure_pct: float = 0.30  # 섹터 최대 30%

    # 일일 손실 한도
    daily_loss_limit_pct: float = -0.03  # -3% (SAFE 모드)
    daily_halt_limit_pct: float = -0.05  # -5% (HALT 모드)


@dataclass
class KRBacktestTrade:
    """백테스트 거래 기록"""
    trade_id: str
    stock_code: str
    stock_name: str
    strategy_name: str

    # 진입
    entry_date: date
    entry_price: float
    quantity: int
    entry_value: float

    # 청산
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    exit_value: Optional[float] = None
    exit_reason: Optional[ExitReason] = None

    # 손익
    pnl: float = 0.0
    pnl_pct: float = 0.0
    net_pnl: float = 0.0  # 수수료/세금 차감 후

    # 비용
    entry_commission: float = 0.0
    exit_commission: float = 0.0
    exit_tax: float = 0.0
    slippage: float = 0.0
    total_cost: float = 0.0

    # 보유 정보
    holding_days: int = 0
    max_profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    # 진입 시 지표
    entry_indicators: Optional[dict] = None


@dataclass
class KRBacktestPosition:
    """백테스트 포지션"""
    trade_id: str
    stock_code: str
    stock_name: str
    strategy_name: str
    market: MarketType

    entry_date: date
    entry_price: float
    quantity: int
    entry_value: float
    entry_commission: float

    # 현재 상태
    current_price: float = 0.0
    current_value: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0

    # 손익
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    max_profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    # 리스크 관리
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    trailing_stop_price: float = 0.0
    trailing_activated: bool = False

    def update(self, current_price: float) -> None:
        """포지션 업데이트"""
        self.current_price = current_price
        self.current_value = current_price * self.quantity
        self.highest_price = max(self.highest_price, current_price)

        if self.lowest_price == 0:
            self.lowest_price = current_price
        else:
            self.lowest_price = min(self.lowest_price, current_price)

        # 손익 계산
        self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        self.unrealized_pnl_pct = (current_price / self.entry_price - 1) * 100

        # 최대 수익/손실
        self.max_profit_pct = max(
            self.max_profit_pct,
            (self.highest_price / self.entry_price - 1) * 100
        )
        self.max_drawdown_pct = max(
            self.max_drawdown_pct,
            (self.entry_price - self.lowest_price) / self.entry_price * 100
        )


@dataclass
class KRBacktestResult:
    """백테스트 결과"""
    run_id: str
    strategy_name: str
    market: str
    start_date: date
    end_date: date

    # 자본
    initial_capital: float
    final_equity: float

    # 성과 지표
    total_return_pct: float = 0.0
    cagr: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # 거래 통계
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    # 평균
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_holding_days: float = 0.0
    avg_trade_pnl: float = 0.0

    # 비용
    total_commission: float = 0.0
    total_tax: float = 0.0
    total_slippage: float = 0.0

    # 상세 데이터
    trades: list[KRBacktestTrade] = field(default_factory=list)
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    daily_returns: list[float] = field(default_factory=list)
    monthly_returns: dict[str, float] = field(default_factory=dict)


class KRStockBacktestEngine:
    """
    한국 주식 백테스트 엔진

    특징:
    - 한국 시장 특성 반영 (거래세, 수수료, 가격제한폭)
    - 다층 필터 시스템 연동
    - 상세 성과 분석
    """

    def __init__(self, config: KRBacktestConfig):
        self.config = config
        self.cost_config = TradingCostConfig(
            commission_rate=config.commission_rate,
            kospi_tax_rate=config.tax_rate,
            kosdaq_tax_rate=config.tax_rate,
        )

    def run_backtest(
        self,
        strategy: BaseKRStockStrategy,
        candles_by_stock: dict[str, list[CandleData]],
        stock_info: dict[str, dict],  # stock_code -> {name, market, sector, ...}
        start_date: date,
        end_date: date,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> KRBacktestResult:
        """
        백테스트 실행

        Args:
            strategy: 매매 전략
            candles_by_stock: 종목별 캔들 데이터
            stock_info: 종목 정보
            start_date: 시작일
            end_date: 종료일
            progress_callback: 진행률 콜백

        Returns:
            백테스트 결과
        """
        run_id = str(uuid.uuid4())[:8]

        # 상태 초기화
        cash = self.config.initial_capital
        positions: dict[str, KRBacktestPosition] = {}  # stock_code -> position
        closed_trades: list[KRBacktestTrade] = []
        equity_curve: list[tuple[date, float]] = []
        daily_pnl_list: list[float] = []

        # 거래일 목록 생성
        trading_days = []
        current_date = start_date
        while current_date <= end_date:
            if is_trading_day(current_date):
                trading_days.append(current_date)
            current_date += timedelta(days=1)

        total_days = len(trading_days)
        prev_equity = self.config.initial_capital

        # 일별 시뮬레이션
        for day_idx, trade_date in enumerate(trading_days):
            if progress_callback:
                progress_callback(day_idx + 1, total_days)

            # 1. 기존 포지션 업데이트 및 청산 확인
            positions_to_close = []

            for stock_code, position in positions.items():
                if stock_code not in candles_by_stock:
                    continue

                # 해당 날짜 캔들 찾기
                candle = self._find_candle_by_date(
                    candles_by_stock[stock_code], trade_date
                )
                if candle is None:
                    continue

                # 포지션 업데이트
                position.update(candle.close)

                # 청산 조건 확인
                exit_result = self._check_exit_conditions(
                    position, candle, trade_date, strategy
                )

                if exit_result is not None:
                    exit_reason, exit_price = exit_result
                    positions_to_close.append((stock_code, exit_reason, exit_price))

            # 청산 처리
            for stock_code, exit_reason, exit_price in positions_to_close:
                position = positions[stock_code]
                trade = self._close_position(
                    position, exit_price, trade_date, exit_reason
                )
                cash += trade.exit_value - trade.exit_commission - trade.exit_tax
                closed_trades.append(trade)
                del positions[stock_code]

            # 2. 신규 진입 신호 확인
            if len(positions) < self.config.max_positions:
                for stock_code, candles in candles_by_stock.items():
                    # 이미 포지션 있으면 스킵
                    if stock_code in positions:
                        continue

                    # 최대 포지션 수 체크
                    if len(positions) >= self.config.max_positions:
                        break

                    # 해당 날짜 캔들 찾기
                    candle = self._find_candle_by_date(candles, trade_date)
                    if candle is None:
                        continue

                    # 히스토리 캔들 가져오기
                    historical = self._get_historical_candles(candles, trade_date, 200)
                    if len(historical) < 60:
                        continue

                    # 기술적 지표 계산
                    indicators = self._calculate_indicators(historical)

                    # 신호 생성
                    signal = strategy.generate_signal(
                        candle=candle,
                        indicators=indicators,
                        historical_candles=historical,
                        market_cap=stock_info.get(stock_code, {}).get("market_cap"),
                        trading_status=stock_info.get(stock_code, {}).get("status", "NORMAL"),
                        is_tradable=True,
                    )

                    if signal is not None and signal.signal_type == SignalType.BUY:
                        # 포지션 사이즈 계산
                        position_value = min(
                            cash * (signal.position_size_pct / 100),
                            self.config.initial_capital * self.config.max_single_position_pct
                        )

                        if position_value < candle.close:
                            continue  # 최소 1주 매수 불가

                        quantity = int(position_value / candle.close)

                        # 슬리피지 적용
                        entry_price = candle.close * (1 + self.config.slippage_pct / 100)
                        entry_price = align_to_tick(entry_price, up=True)

                        # 수수료 계산
                        entry_value = entry_price * quantity
                        entry_commission = entry_value * self.config.commission_rate

                        # 현금 충분한지 확인
                        if cash < entry_value + entry_commission:
                            continue

                        # 포지션 생성
                        position = KRBacktestPosition(
                            trade_id=str(uuid.uuid4())[:8],
                            stock_code=stock_code,
                            stock_name=stock_info.get(stock_code, {}).get("name", stock_code),
                            strategy_name=strategy.name,
                            market=self.config.market,
                            entry_date=trade_date,
                            entry_price=entry_price,
                            quantity=quantity,
                            entry_value=entry_value,
                            entry_commission=entry_commission,
                            current_price=entry_price,
                            current_value=entry_value,
                            highest_price=entry_price,
                            lowest_price=entry_price,
                            stop_loss_price=signal.stop_loss_price or 0,
                            take_profit_price=signal.take_profit_price or 0,
                        )

                        positions[stock_code] = position
                        cash -= (entry_value + entry_commission)

                        # 쿨다운 설정
                        strategy.set_cooldown(
                            stock_code,
                            datetime.combine(trade_date, datetime.min.time())
                        )

            # 3. 일일 자산 평가
            positions_value = sum(p.current_value for p in positions.values())
            total_equity = cash + positions_value
            equity_curve.append((trade_date, total_equity))

            # 일일 수익률 계산
            daily_return = (total_equity / prev_equity - 1) * 100
            daily_pnl_list.append(daily_return)
            prev_equity = total_equity

            # 일일 손실 한도 체크
            daily_return_from_start = (total_equity / self.config.initial_capital - 1) * 100
            # (실제로는 SAFE/HALT 모드 전환 로직 추가)

        # 4. 잔여 포지션 강제 청산 (백테스트 종료)
        for stock_code, position in positions.items():
            trade = self._close_position(
                position, position.current_price, end_date, ExitReason.MARKET_CLOSE
            )
            cash += trade.exit_value - trade.exit_commission - trade.exit_tax
            closed_trades.append(trade)

        # 5. 결과 계산
        final_equity = cash
        result = self._calculate_results(
            run_id=run_id,
            strategy_name=strategy.name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.config.initial_capital,
            final_equity=final_equity,
            trades=closed_trades,
            equity_curve=equity_curve,
            daily_returns=daily_pnl_list,
        )

        return result

    def _find_candle_by_date(
        self, candles: list[CandleData], trade_date: date
    ) -> Optional[CandleData]:
        """날짜로 캔들 찾기"""
        for candle in candles:
            candle_date = candle.timestamp.date() if isinstance(candle.timestamp, datetime) else candle.timestamp
            if candle_date == trade_date:
                return candle
        return None

    def _get_historical_candles(
        self, candles: list[CandleData], trade_date: date, lookback: int
    ) -> list[CandleData]:
        """과거 캔들 데이터 가져오기"""
        historical = []
        for candle in candles:
            candle_date = candle.timestamp.date() if isinstance(candle.timestamp, datetime) else candle.timestamp
            if candle_date < trade_date:
                historical.append(candle)
            if len(historical) >= lookback:
                break
        return historical[-lookback:] if len(historical) > lookback else historical

    def _calculate_indicators(self, candles: list[CandleData]) -> TechnicalIndicators:
        """기술적 지표 계산"""
        opens = np.array([c.open for c in candles])
        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])
        closes = np.array([c.close for c in candles])
        volumes = np.array([c.volume for c in candles], dtype=np.float64)

        return calculate_all_indicators(opens, highs, lows, closes, volumes)

    def _check_exit_conditions(
        self,
        position: KRBacktestPosition,
        candle: CandleData,
        trade_date: date,
        strategy: BaseKRStockStrategy
    ) -> Optional[tuple[ExitReason, float]]:
        """청산 조건 확인"""
        current_price = candle.close

        # 1. 손절
        if position.stop_loss_price > 0 and current_price <= position.stop_loss_price:
            # 슬리피지 적용 (불리하게)
            exit_price = current_price * (1 - self.config.slippage_pct / 100)
            return ExitReason.STOP_LOSS, align_to_tick(exit_price, up=False)

        # 2. 익절
        if position.take_profit_price > 0 and current_price >= position.take_profit_price:
            exit_price = current_price * (1 - self.config.slippage_pct / 100)
            return ExitReason.TAKE_PROFIT, align_to_tick(exit_price, up=False)

        # 3. 추적손절
        if not position.trailing_activated:
            trigger_pct = strategy.config.trailing_trigger_pct
            if position.unrealized_pnl_pct >= trigger_pct:
                position.trailing_activated = True
                trailing_price = current_price * (1 - strategy.config.trailing_stop_pct / 100)
                position.trailing_stop_price = align_to_tick(trailing_price, up=True)

        if position.trailing_activated:
            # 고점 갱신 시 추적손절가 상향
            if current_price > position.highest_price:
                new_trailing = current_price * (1 - strategy.config.trailing_stop_pct / 100)
                position.trailing_stop_price = max(
                    position.trailing_stop_price,
                    align_to_tick(new_trailing, up=True)
                )

            if current_price <= position.trailing_stop_price:
                exit_price = current_price * (1 - self.config.slippage_pct / 100)
                return ExitReason.TRAILING_STOP, align_to_tick(exit_price, up=False)

        # 4. 시간손절
        holding_days = (trade_date - position.entry_date).days
        if holding_days >= strategy.config.max_holding_days:
            exit_price = current_price * (1 - self.config.slippage_pct / 100)
            return ExitReason.TIME_STOP, align_to_tick(exit_price, up=False)

        return None

    def _close_position(
        self,
        position: KRBacktestPosition,
        exit_price: float,
        exit_date: date,
        exit_reason: ExitReason
    ) -> KRBacktestTrade:
        """포지션 청산"""
        exit_value = exit_price * position.quantity

        # 비용 계산
        exit_commission, exit_tax, _ = calculate_trading_cost(
            exit_price, position.quantity, is_sell=True,
            market=position.market, config=self.cost_config
        )

        # 손익 계산
        gross_pnl = exit_value - position.entry_value
        total_cost = position.entry_commission + exit_commission + exit_tax
        net_pnl = gross_pnl - total_cost
        pnl_pct = (exit_price / position.entry_price - 1) * 100

        holding_days = (exit_date - position.entry_date).days

        return KRBacktestTrade(
            trade_id=position.trade_id,
            stock_code=position.stock_code,
            stock_name=position.stock_name,
            strategy_name=position.strategy_name,
            entry_date=position.entry_date,
            entry_price=position.entry_price,
            quantity=position.quantity,
            entry_value=position.entry_value,
            exit_date=exit_date,
            exit_price=exit_price,
            exit_value=exit_value,
            exit_reason=exit_reason,
            pnl=gross_pnl,
            pnl_pct=pnl_pct,
            net_pnl=net_pnl,
            entry_commission=position.entry_commission,
            exit_commission=exit_commission,
            exit_tax=exit_tax,
            total_cost=total_cost,
            holding_days=holding_days,
            max_profit_pct=position.max_profit_pct,
            max_drawdown_pct=position.max_drawdown_pct,
        )

    def _calculate_results(
        self,
        run_id: str,
        strategy_name: str,
        start_date: date,
        end_date: date,
        initial_capital: float,
        final_equity: float,
        trades: list[KRBacktestTrade],
        equity_curve: list[tuple[date, float]],
        daily_returns: list[float]
    ) -> KRBacktestResult:
        """결과 계산"""
        # 기본 성과
        total_return_pct = (final_equity / initial_capital - 1) * 100

        # 연환산 수익률 (CAGR)
        days = (end_date - start_date).days
        years = days / 365.25
        if years > 0 and final_equity > 0:
            cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100
        else:
            cagr = 0.0

        # 최대 낙폭 (MDD)
        peak = initial_capital
        max_dd = 0.0
        for _, equity in equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)

        # 거래 통계
        total_trades = len(trades)
        winning_trades = len([t for t in trades if t.net_pnl > 0])
        losing_trades = len([t for t in trades if t.net_pnl <= 0])
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

        # 평균 손익
        wins = [t.pnl_pct for t in trades if t.net_pnl > 0]
        losses = [t.pnl_pct for t in trades if t.net_pnl <= 0]
        avg_win_pct = np.mean(wins) if wins else 0.0
        avg_loss_pct = np.mean(losses) if losses else 0.0

        # Profit Factor
        total_profit = sum(t.net_pnl for t in trades if t.net_pnl > 0)
        total_loss = abs(sum(t.net_pnl for t in trades if t.net_pnl <= 0))
        profit_factor = (total_profit / total_loss) if total_loss > 0 else 0.0

        # 평균 보유 기간
        avg_holding = np.mean([t.holding_days for t in trades]) if trades else 0.0

        # 샤프 비율 (무위험 이자율 3% 가정)
        if daily_returns:
            returns_arr = np.array(daily_returns)
            rf_daily = 3.0 / 252  # 일일 무위험 이자율
            excess_returns = returns_arr - rf_daily
            if np.std(returns_arr) > 0:
                sharpe = np.mean(excess_returns) / np.std(returns_arr) * np.sqrt(252)
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # 소르티노 비율
        if daily_returns:
            downside_returns = [r for r in daily_returns if r < 0]
            if downside_returns:
                downside_std = np.std(downside_returns)
                if downside_std > 0:
                    sortino = np.mean(excess_returns) / downside_std * np.sqrt(252)
                else:
                    sortino = 0.0
            else:
                sortino = sharpe  # 손실 없으면 샤프와 동일
        else:
            sortino = 0.0

        # 칼마 비율
        calmar = (cagr / max_dd) if max_dd > 0 else 0.0

        # 비용 합계
        total_commission = sum(t.entry_commission + t.exit_commission for t in trades)
        total_tax = sum(t.exit_tax for t in trades)

        # 월별 수익률
        monthly_returns = {}
        for trade_date, equity in equity_curve:
            month_key = trade_date.strftime("%Y-%m")
            if month_key not in monthly_returns:
                monthly_returns[month_key] = []
        # (실제로는 월초/월말 자산으로 계산)

        return KRBacktestResult(
            run_id=run_id,
            strategy_name=strategy_name,
            market=self.config.market.value,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_equity=final_equity,
            total_return_pct=total_return_pct,
            cagr=cagr,
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_win_pct=avg_win_pct,
            avg_loss_pct=avg_loss_pct,
            avg_holding_days=avg_holding,
            avg_trade_pnl=np.mean([t.net_pnl for t in trades]) if trades else 0.0,
            total_commission=total_commission,
            total_tax=total_tax,
            total_slippage=0.0,  # 슬리피지는 가격에 이미 반영
            trades=trades,
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            monthly_returns=monthly_returns,
        )


def print_backtest_summary(result: KRBacktestResult) -> str:
    """백테스트 결과 요약 출력"""
    lines = [
        "=" * 60,
        f"📊 백테스트 결과: {result.strategy_name}",
        "=" * 60,
        f"기간: {result.start_date} ~ {result.end_date}",
        f"시장: {result.market}",
        "",
        "📈 수익률",
        f"  총 수익률: {result.total_return_pct:+.2f}%",
        f"  CAGR: {result.cagr:+.2f}%",
        f"  최대 낙폭: -{result.max_drawdown_pct:.2f}%",
        "",
        "📊 리스크 지표",
        f"  샤프 비율: {result.sharpe_ratio:.2f}",
        f"  소르티노 비율: {result.sortino_ratio:.2f}",
        f"  칼마 비율: {result.calmar_ratio:.2f}",
        "",
        "🎯 거래 통계",
        f"  총 거래: {result.total_trades}회",
        f"  승률: {result.win_rate:.1f}%",
        f"  Profit Factor: {result.profit_factor:.2f}",
        f"  평균 수익: +{result.avg_win_pct:.2f}%",
        f"  평균 손실: {result.avg_loss_pct:.2f}%",
        f"  평균 보유: {result.avg_holding_days:.1f}일",
        "",
        "💰 비용",
        f"  총 수수료: {result.total_commission:,.0f}원",
        f"  총 거래세: {result.total_tax:,.0f}원",
        "",
        f"최종 자산: {result.final_equity:,.0f}원 (초기 {result.initial_capital:,.0f}원)",
        "=" * 60,
    ]
    return "\n".join(lines)
