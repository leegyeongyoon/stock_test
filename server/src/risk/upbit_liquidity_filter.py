"""UpbitLiquidityFilter - 업비트 유동성 필터 강화

업비트 특성 반영:
- 24h 거래대금: 기존 1억 → 50억 상향
- 스프레드: 기존 12bps → 10bps 하향
- 평균 스프레드 체크 추가 (최근 1시간)
- 오더북 깊이 체크 강화

진입 필터 통과 기준:
1. 24h 거래대금 >= min_volume_24h
2. 현재 스프레드 <= max_spread_bps
3. 평균 스프레드 (1시간) <= max_avg_spread_bps
4. 오더북 깊이 >= 주문금액 * depth_multiplier
5. 예상 슬리피지 <= max_slippage_pct
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

logger = structlog.get_logger()


@dataclass
class UpbitLiquidityConfig:
    """업비트 유동성 필터 설정"""

    # 24h 거래대금 (KRW) - 강화됨
    min_volume_24h_default: float = 5_000_000_000  # 50억 (기존 1억 → 50억)
    min_volume_24h_ignition: float = 3_000_000_000  # Ignition/Surge는 30억
    min_volume_24h_attack: float = 2_000_000_000  # Attack은 20억

    # 스프레드 (bps) - 강화됨
    max_spread_bps_default: float = 10.0  # 10bps (기존 12bps)
    max_spread_bps_aggressive: float = 15.0  # 공격적 전략 15bps

    # 평균 스프레드 (최근 1시간)
    avg_spread_lookback_min: int = 60
    max_avg_spread_bps: float = 8.0  # 평균 8bps 초과 시 차단

    # 오더북 깊이 - 강화됨
    depth_multiplier: float = 8.0  # 주문 금액의 8배 (기존 6배 → 8배)
    depth_range_pct: float = 0.005  # 0.5% 범위 내 물량

    # 슬리피지 - 강화됨
    max_slippage_pct: float = 0.002  # 0.2% (기존 0.25% → 0.2%)

    # 최소 틱 수 (호가 단계)
    min_tick_count: int = 5  # 최소 5단계 호가 필요


@dataclass
class LiquidityCheckResult:
    """유동성 체크 결과"""

    passed: bool
    reason: str
    spread_bps: float = 0.0
    avg_spread_bps: float = 0.0
    depth_ratio: float = 0.0  # 오더북 깊이 / 주문금액
    slippage_pct: float = 0.0
    volume_24h: float = 0.0
    details: dict = field(default_factory=dict)


class UpbitLiquidityFilter:
    """
    업비트 유동성 필터

    책임:
    1. 24h 거래대금 체크
    2. 현재/평균 스프레드 체크
    3. 오더북 깊이 체크
    4. 예상 슬리피지 체크
    """

    def __init__(self, config: Optional[UpbitLiquidityConfig] = None) -> None:
        self._config = config or UpbitLiquidityConfig()

        # 스프레드 히스토리: symbol -> [(timestamp, spread_bps)]
        self._spread_history: dict[str, list[tuple[datetime, float]]] = {}

    @property
    def config(self) -> UpbitLiquidityConfig:
        """현재 설정"""
        return self._config

    def record_spread(self, symbol: str, spread_bps: float) -> None:
        """스프레드 기록 (평균 계산용)"""
        now = datetime.utcnow()

        if symbol not in self._spread_history:
            self._spread_history[symbol] = []

        self._spread_history[symbol].append((now, spread_bps))

        # 2시간 이전 데이터 정리
        cutoff = now - timedelta(hours=2)
        self._spread_history[symbol] = [
            (t, s) for t, s in self._spread_history[symbol] if t > cutoff
        ]

    def get_avg_spread(self, symbol: str, lookback_min: int = 60) -> float:
        """평균 스프레드 조회 (bps)"""
        if symbol not in self._spread_history:
            return 0.0

        now = datetime.utcnow()
        cutoff = now - timedelta(minutes=lookback_min)

        recent = [s for t, s in self._spread_history[symbol] if t > cutoff]
        if not recent:
            return 0.0

        return sum(recent) / len(recent)

    async def check(
        self,
        symbol: str,
        strategy_id: str,
        order_size_krw: float,
        current_price: float,
        orderbook: Optional[dict] = None,
        volume_24h: float = 0,
    ) -> LiquidityCheckResult:
        """
        유동성 종합 체크

        Args:
            symbol: 심볼
            strategy_id: 전략 ID
            order_size_krw: 주문 금액 (KRW)
            current_price: 현재가
            orderbook: 호가창 데이터 {bids: [...], asks: [...]}
            volume_24h: 24시간 거래대금 (KRW)

        Returns:
            LiquidityCheckResult
        """
        cfg = self._config

        # 1. 24h 거래대금 체크
        min_volume = self._get_min_volume(strategy_id)
        if volume_24h < min_volume:
            return LiquidityCheckResult(
                passed=False,
                reason=f"Volume too low: {volume_24h/1e9:.2f}B < {min_volume/1e9:.2f}B",
                volume_24h=volume_24h,
                details={"check": "volume_24h", "value": volume_24h, "threshold": min_volume},
            )

        # 호가창 없으면 거래대금만 체크
        if not orderbook:
            logger.warning(f"No orderbook data for {symbol}, skipping depth checks")
            return LiquidityCheckResult(
                passed=True,
                reason="Volume OK, no orderbook data",
                volume_24h=volume_24h,
            )

        # 2. 현재 스프레드 체크
        spread_bps = self._calc_spread_bps(orderbook, current_price)
        self.record_spread(symbol, spread_bps)

        max_spread = self._get_max_spread(strategy_id)
        if spread_bps > max_spread:
            return LiquidityCheckResult(
                passed=False,
                reason=f"Spread too wide: {spread_bps:.1f}bps > {max_spread:.1f}bps",
                spread_bps=spread_bps,
                volume_24h=volume_24h,
                details={"check": "spread", "value": spread_bps, "threshold": max_spread},
            )

        # 3. 평균 스프레드 체크
        avg_spread = self.get_avg_spread(symbol, cfg.avg_spread_lookback_min)
        if avg_spread > cfg.max_avg_spread_bps:
            return LiquidityCheckResult(
                passed=False,
                reason=f"Avg spread too wide: {avg_spread:.1f}bps > {cfg.max_avg_spread_bps:.1f}bps",
                spread_bps=spread_bps,
                avg_spread_bps=avg_spread,
                volume_24h=volume_24h,
                details={"check": "avg_spread", "value": avg_spread, "threshold": cfg.max_avg_spread_bps},
            )

        # 4. 오더북 깊이 체크
        depth_krw = self._calc_depth(orderbook, current_price)
        required_depth = order_size_krw * cfg.depth_multiplier
        depth_ratio = depth_krw / order_size_krw if order_size_krw > 0 else 0

        if depth_krw < required_depth:
            return LiquidityCheckResult(
                passed=False,
                reason=f"Depth too shallow: {depth_krw/1e6:.1f}M < {required_depth/1e6:.1f}M (ratio: {depth_ratio:.1f}x)",
                spread_bps=spread_bps,
                avg_spread_bps=avg_spread,
                depth_ratio=depth_ratio,
                volume_24h=volume_24h,
                details={"check": "depth", "value": depth_krw, "threshold": required_depth},
            )

        # 5. 예상 슬리피지 체크
        slippage_pct = self._estimate_slippage(orderbook, order_size_krw, current_price)
        if slippage_pct > cfg.max_slippage_pct:
            return LiquidityCheckResult(
                passed=False,
                reason=f"Slippage too high: {slippage_pct*100:.2f}% > {cfg.max_slippage_pct*100:.2f}%",
                spread_bps=spread_bps,
                avg_spread_bps=avg_spread,
                depth_ratio=depth_ratio,
                slippage_pct=slippage_pct,
                volume_24h=volume_24h,
                details={"check": "slippage", "value": slippage_pct, "threshold": cfg.max_slippage_pct},
            )

        # 6. 호가 단계 수 체크
        tick_count = len(orderbook.get("asks", []))
        if tick_count < cfg.min_tick_count:
            return LiquidityCheckResult(
                passed=False,
                reason=f"Too few order levels: {tick_count} < {cfg.min_tick_count}",
                spread_bps=spread_bps,
                avg_spread_bps=avg_spread,
                depth_ratio=depth_ratio,
                slippage_pct=slippage_pct,
                volume_24h=volume_24h,
                details={"check": "tick_count", "value": tick_count, "threshold": cfg.min_tick_count},
            )

        # 모든 체크 통과
        return LiquidityCheckResult(
            passed=True,
            reason="All liquidity checks passed",
            spread_bps=spread_bps,
            avg_spread_bps=avg_spread,
            depth_ratio=depth_ratio,
            slippage_pct=slippage_pct,
            volume_24h=volume_24h,
            details={
                "spread_bps": spread_bps,
                "avg_spread_bps": avg_spread,
                "depth_ratio": depth_ratio,
                "slippage_pct": slippage_pct,
                "volume_24h_b": volume_24h / 1e9,
            },
        )

    def _get_min_volume(self, strategy_id: str) -> float:
        """전략별 최소 거래대금"""
        cfg = self._config
        return cfg.min_volume_24h_default

    def _get_max_spread(self, strategy_id: str) -> float:
        """전략별 최대 스프레드"""
        cfg = self._config
        return cfg.max_spread_bps_default

    def _calc_spread_bps(self, orderbook: dict, current_price: float) -> float:
        """스프레드 계산 (bps)"""
        asks = orderbook.get("asks", [])
        bids = orderbook.get("bids", [])

        if not asks or not bids:
            return 0.0

        # 최우선 호가
        best_ask = asks[0].get("price", 0) if isinstance(asks[0], dict) else asks[0][0]
        best_bid = bids[0].get("price", 0) if isinstance(bids[0], dict) else bids[0][0]

        if best_bid <= 0:
            return 0.0

        spread = (best_ask - best_bid) / best_bid * 10000
        return spread

    def _calc_depth(self, orderbook: dict, current_price: float) -> float:
        """오더북 깊이 계산 (KRW, ±0.5% 범위)"""
        asks = orderbook.get("asks", [])
        bids = orderbook.get("bids", [])
        cfg = self._config

        upper_bound = current_price * (1 + cfg.depth_range_pct)
        lower_bound = current_price * (1 - cfg.depth_range_pct)

        depth = 0.0

        # 매도 호가 (asks)
        for ask in asks:
            if isinstance(ask, dict):
                price = ask.get("price", 0)
                size = ask.get("size", 0)
            else:
                price, size = ask[0], ask[1]

            if price <= upper_bound:
                depth += price * size

        # 매수 호가 (bids)
        for bid in bids:
            if isinstance(bid, dict):
                price = bid.get("price", 0)
                size = bid.get("size", 0)
            else:
                price, size = bid[0], bid[1]

            if price >= lower_bound:
                depth += price * size

        return depth

    def _estimate_slippage(
        self,
        orderbook: dict,
        order_size_krw: float,
        current_price: float,
    ) -> float:
        """예상 슬리피지 계산 (%)"""
        asks = orderbook.get("asks", [])

        if not asks:
            return 0.0

        remaining = order_size_krw
        total_cost = 0.0
        total_qty = 0.0

        for ask in asks:
            if isinstance(ask, dict):
                price = ask.get("price", 0)
                size = ask.get("size", 0)
            else:
                price, size = ask[0], ask[1]

            available_krw = price * size

            if remaining <= available_krw:
                # 이 호가에서 전량 체결
                qty = remaining / price
                total_cost += remaining
                total_qty += qty
                break
            else:
                # 이 호가 전량 소진
                total_cost += available_krw
                total_qty += size
                remaining -= available_krw

        if total_qty <= 0:
            return 1.0  # 체결 불가

        avg_fill_price = total_cost / total_qty
        slippage = (avg_fill_price - current_price) / current_price

        return max(0, slippage)

    def get_summary(self, symbol: str) -> dict:
        """심볼별 유동성 현황 요약"""
        avg_spread = self.get_avg_spread(symbol)
        spread_history = self._spread_history.get(symbol, [])

        recent_spreads = [s for _, s in spread_history[-20:]]
        max_spread = max(recent_spreads) if recent_spreads else 0
        min_spread = min(recent_spreads) if recent_spreads else 0

        return {
            "symbol": symbol,
            "avg_spread_bps": avg_spread,
            "max_spread_bps": max_spread,
            "min_spread_bps": min_spread,
            "sample_count": len(spread_history),
        }


# 싱글톤 인스턴스
_liquidity_filter: Optional[UpbitLiquidityFilter] = None


def get_upbit_liquidity_filter() -> UpbitLiquidityFilter:
    """UpbitLiquidityFilter 싱글톤 조회"""
    global _liquidity_filter
    if _liquidity_filter is None:
        _liquidity_filter = UpbitLiquidityFilter()
    return _liquidity_filter


def init_upbit_liquidity_filter(
    config: Optional[UpbitLiquidityConfig] = None,
) -> UpbitLiquidityFilter:
    """UpbitLiquidityFilter 초기화"""
    global _liquidity_filter
    _liquidity_filter = UpbitLiquidityFilter(config=config)
    logger.info(
        "UpbitLiquidityFilter initialized",
        min_volume_default=_liquidity_filter.config.min_volume_24h_default / 1e9,
        max_spread_default=_liquidity_filter.config.max_spread_bps_default,
    )
    return _liquidity_filter
