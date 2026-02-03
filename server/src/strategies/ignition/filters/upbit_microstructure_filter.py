"""
UpbitMicrostructureFilter - 호가 구조 필터 (시간 필터 대체)

핵심 로직:
- 자정 시간 필터 대신 실제 호가 구조 체크
- Spread, Depth, Slippage 기반 판단
- 시간 무관하게 유동성이 충분하면 허용

체크 항목:
1. Spread: 12bps 초과시 차단
2. Depth: 주문 사이즈의 6배 미만시 차단
3. Slippage: 0.25% 초과시 차단
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()


@dataclass(frozen=True)
class MicrostructureResult:
    """호가 구조 체크 결과"""

    passed: bool
    spread_bps: float
    depth_ratio: float  # depth / order_size
    slippage_pct: float
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "spread_bps": round(self.spread_bps, 2),
            "depth_ratio": round(self.depth_ratio, 2),
            "slippage_pct": round(self.slippage_pct * 100, 3),
            "reason": self.reason,
        }


class UpbitMicrostructureFilter:
    """
    Upbit 호가 구조 필터

    시간 기반 필터 대체:
    - 자정에도 유동성 충분하면 거래 허용
    - 낮에도 유동성 부족하면 차단
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._blocked_count = 0
        self._passed_count = 0
        self._block_reasons: dict[str, int] = {
            "spread": 0,
            "depth": 0,
            "slippage": 0,
        }

    def check(
        self,
        symbol: str,
        orderbook: dict,
        order_size_krw: float,
        current_price: float,
    ) -> MicrostructureResult:
        """
        호가 구조 체크

        Args:
            symbol: 심볼
            orderbook: 호가창 데이터 {"bids": [...], "asks": [...]}
            order_size_krw: 주문 크기 (KRW)
            current_price: 현재가

        Returns:
            MicrostructureResult
        """
        # 설정값 가져오기 (기본값 사용 가능)
        max_spread_bps = getattr(self._settings, "microstructure_max_spread_bps", 12.0)
        depth_mult = getattr(self._settings, "microstructure_depth_mult", 6.0)
        max_slippage_pct = getattr(
            self._settings, "microstructure_max_slippage_pct", 0.0025
        )

        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if not bids or not asks:
            return MicrostructureResult(
                passed=False,
                spread_bps=0,
                depth_ratio=0,
                slippage_pct=0,
                reason="empty orderbook",
            )

        # 1. 스프레드 체크
        best_bid = bids[0][0] if isinstance(bids[0], (list, tuple)) else bids[0].get("price", 0)
        best_ask = asks[0][0] if isinstance(asks[0], (list, tuple)) else asks[0].get("price", 0)

        if best_bid <= 0 or best_ask <= 0:
            return MicrostructureResult(
                passed=False,
                spread_bps=0,
                depth_ratio=0,
                slippage_pct=0,
                reason="invalid bid/ask prices",
            )

        mid_price = (best_bid + best_ask) / 2
        spread_bps = ((best_ask - best_bid) / mid_price) * 10000

        if spread_bps > max_spread_bps:
            self._blocked_count += 1
            self._block_reasons["spread"] += 1
            logger.debug(
                "Microstructure blocked: spread",
                symbol=symbol,
                spread_bps=f"{spread_bps:.1f}",
                max_bps=max_spread_bps,
            )
            return MicrostructureResult(
                passed=False,
                spread_bps=spread_bps,
                depth_ratio=0,
                slippage_pct=0,
                reason=f"spread {spread_bps:.1f}bps > {max_spread_bps}bps",
            )

        # 2. 호가 깊이 체크 (0.5% 이내)
        depth_krw = self._calculate_depth_within_pct(asks, current_price, pct=0.005)
        required_depth = order_size_krw * depth_mult
        depth_ratio = depth_krw / order_size_krw if order_size_krw > 0 else 0

        if depth_krw < required_depth:
            self._blocked_count += 1
            self._block_reasons["depth"] += 1
            logger.debug(
                "Microstructure blocked: depth",
                symbol=symbol,
                depth_krw=f"{depth_krw:,.0f}",
                required=f"{required_depth:,.0f}",
                depth_ratio=f"{depth_ratio:.1f}x",
            )
            return MicrostructureResult(
                passed=False,
                spread_bps=spread_bps,
                depth_ratio=depth_ratio,
                slippage_pct=0,
                reason=f"depth {depth_ratio:.1f}x < {depth_mult}x required",
            )

        # 3. 슬리피지 예상치
        slippage_pct = self._estimate_slippage(asks, order_size_krw, current_price)

        if slippage_pct > max_slippage_pct:
            self._blocked_count += 1
            self._block_reasons["slippage"] += 1
            logger.debug(
                "Microstructure blocked: slippage",
                symbol=symbol,
                slippage_pct=f"{slippage_pct*100:.2f}%",
                max_pct=f"{max_slippage_pct*100:.2f}%",
            )
            return MicrostructureResult(
                passed=False,
                spread_bps=spread_bps,
                depth_ratio=depth_ratio,
                slippage_pct=slippage_pct,
                reason=f"slippage {slippage_pct*100:.2f}% > {max_slippage_pct*100:.2f}%",
            )

        # 모든 체크 통과
        self._passed_count += 1
        return MicrostructureResult(
            passed=True,
            spread_bps=spread_bps,
            depth_ratio=depth_ratio,
            slippage_pct=slippage_pct,
        )

    def _calculate_depth_within_pct(
        self,
        asks: list,
        current_price: float,
        pct: float = 0.005,
    ) -> float:
        """
        지정 가격 범위 내 호가 깊이 계산 (KRW)

        Args:
            asks: 매도 호가 리스트
            current_price: 현재가
            pct: 가격 범위 (0.005 = 0.5%)

        Returns:
            깊이 (KRW)
        """
        max_price = current_price * (1 + pct)
        total_depth = 0.0

        for ask in asks:
            if isinstance(ask, (list, tuple)):
                price, qty = ask[0], ask[1]
            else:
                price = ask.get("price", 0)
                qty = ask.get("quantity", 0) or ask.get("size", 0)

            if price > max_price:
                break

            total_depth += price * qty

        return total_depth

    def _estimate_slippage(
        self,
        asks: list,
        order_size_krw: float,
        current_price: float,
    ) -> float:
        """
        슬리피지 예상치 계산

        Args:
            asks: 매도 호가 리스트
            order_size_krw: 주문 크기 (KRW)
            current_price: 현재가

        Returns:
            슬리피지 비율 (0.01 = 1%)
        """
        if order_size_krw <= 0 or current_price <= 0:
            return 0

        remaining_krw = order_size_krw
        total_cost = 0.0
        total_qty = 0.0

        for ask in asks:
            if remaining_krw <= 0:
                break

            if isinstance(ask, (list, tuple)):
                price, qty = ask[0], ask[1]
            else:
                price = ask.get("price", 0)
                qty = ask.get("quantity", 0) or ask.get("size", 0)

            if price <= 0 or qty <= 0:
                continue

            ask_value = price * qty

            if ask_value >= remaining_krw:
                # 이 호가에서 남은 금액 체결
                fill_qty = remaining_krw / price
                total_cost += fill_qty * price
                total_qty += fill_qty
                remaining_krw = 0
            else:
                # 이 호가 전량 체결
                total_cost += ask_value
                total_qty += qty
                remaining_krw -= ask_value

        if total_qty <= 0:
            return 1.0  # 체결 불가

        avg_price = total_cost / total_qty
        slippage = (avg_price - current_price) / current_price

        return max(slippage, 0)

    def get_stats(self) -> dict:
        """통계 반환"""
        total = self._blocked_count + self._passed_count
        return {
            "total_checks": total,
            "blocked": self._blocked_count,
            "passed": self._passed_count,
            "block_rate": (
                round(self._blocked_count / total * 100, 1) if total > 0 else 0
            ),
            "block_reasons": dict(self._block_reasons),
        }


# 싱글톤
_upbit_microstructure_filter: Optional[UpbitMicrostructureFilter] = None


def get_upbit_microstructure_filter() -> UpbitMicrostructureFilter:
    """UpbitMicrostructureFilter 싱글톤"""
    global _upbit_microstructure_filter
    if _upbit_microstructure_filter is None:
        _upbit_microstructure_filter = UpbitMicrostructureFilter()
    return _upbit_microstructure_filter
