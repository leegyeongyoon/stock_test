"""Prometheus 메트릭 수집"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

import structlog

logger = structlog.get_logger()


class MetricsCollector:
    """
    Prometheus 메트릭 수집

    /metrics 엔드포인트에서 노출
    """

    def __init__(self) -> None:
        # Gauges - 현재 상태
        self.equity = Gauge(
            "trading_equity_usd",
            "Current equity in USD",
        )
        self.pnl_today = Gauge(
            "trading_pnl_today_usd",
            "Today's PnL in USD",
        )
        self.drawdown = Gauge(
            "trading_drawdown_pct",
            "Current drawdown percentage",
        )
        self.positions_count = Gauge(
            "trading_positions_count",
            "Number of open positions",
            ["strategy"],
        )
        self.mode = Gauge(
            "trading_mode",
            "Current trading mode (0=NORMAL, 1=SAFE, 2=HALT)",
        )

        # Counters - 누적 카운트
        self.orders_total = Counter(
            "trading_orders_total",
            "Total number of orders",
            ["strategy", "side", "status"],
        )
        self.trades_total = Counter(
            "trading_trades_total",
            "Total number of trades",
            ["strategy", "side"],
        )
        self.events_total = Counter(
            "trading_events_total",
            "Total number of events",
            ["level", "type"],
        )

        # Histograms - 분포
        self.order_latency = Histogram(
            "trading_order_latency_seconds",
            "Order execution latency",
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        )
        self.trade_pnl = Histogram(
            "trading_trade_pnl_pct",
            "Trade PnL percentage",
            ["strategy"],
            buckets=[-0.05, -0.02, -0.01, 0, 0.01, 0.02, 0.05],
        )

    def update_equity(self, value: float) -> None:
        """자산 업데이트"""
        self.equity.set(value)

    def update_pnl(self, value: float) -> None:
        """PnL 업데이트"""
        self.pnl_today.set(value)

    def update_drawdown(self, value: float) -> None:
        """드로다운 업데이트"""
        self.drawdown.set(value)

    def update_positions(self, strategy: str, count: int) -> None:
        """포지션 수 업데이트"""
        self.positions_count.labels(strategy=strategy).set(count)

    def update_mode(self, mode: str) -> None:
        """모드 업데이트"""
        mode_map = {"NORMAL": 0, "SAFE": 1, "HALT": 2}
        self.mode.set(mode_map.get(mode, 0))

    def record_order(self, strategy: str, side: str, status: str) -> None:
        """주문 기록"""
        self.orders_total.labels(
            strategy=strategy,
            side=side,
            status=status,
        ).inc()

    def record_trade(self, strategy: str, side: str, pnl_pct: float = 0) -> None:
        """체결 기록"""
        self.trades_total.labels(strategy=strategy, side=side).inc()
        self.trade_pnl.labels(strategy=strategy).observe(pnl_pct)

    def record_event(self, level: str, event_type: str) -> None:
        """이벤트 기록"""
        self.events_total.labels(level=level, type=event_type).inc()

    def record_order_latency(self, latency_seconds: float) -> None:
        """주문 레이턴시 기록"""
        self.order_latency.observe(latency_seconds)

    def get_metrics(self) -> bytes:
        """Prometheus 형식 메트릭 반환"""
        return generate_latest()

    def get_content_type(self) -> str:
        """Content-Type 헤더"""
        return CONTENT_TYPE_LATEST


# 전역 인스턴스
metrics = MetricsCollector()
