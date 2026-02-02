"""
Anti-Chase Gate - 과추격 방지

역할:
- 점화 신호 발생 후 과도한 추격 방지
- 3중 필터 적용:
  1. 시간 필터: 신호 발생 후 120초 이내
  2. 가격 필터: Range High 대비 ATR 2배 이내
  3. 호가 필터: 스프레드 50bps 이하
  4. 배분 체크: 상단 꼬리 30% 이상이면 배분 의심

통과 시 진입 허용, 실패 시 진입 차단
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import structlog

from src.config import get_settings
from src.strategies.ignition.ignition_engine import IgnitionSignal

logger = structlog.get_logger()


class AntiChaseReason(str, Enum):
    """과추격 차단 사유"""

    PASSED = "PASSED"  # 통과
    TIME_EXCEEDED = "TIME_EXCEEDED"  # 시간 초과
    PRICE_OVER_EXTENDED = "PRICE_OVER_EXTENDED"  # 가격 과확장
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"  # 스프레드 과다
    DISTRIBUTION_SUSPECTED = "DISTRIBUTION_SUSPECTED"  # 배분 의심
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"  # 신호 만료
    SIGNAL_INVALID = "SIGNAL_INVALID"  # 신호 무효


@dataclass
class AntiChaseResult:
    """과추격 필터 결과"""

    symbol: str
    passed: bool
    reason: AntiChaseReason
    details: dict = field(default_factory=dict)
    checked_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "passed": self.passed,
            "reason": self.reason.value,
            "details": self.details,
            "checked_at": self.checked_at.isoformat(),
        }


class AntiChaseGate:
    """
    Anti-Chase Gate

    역할:
    - 점화 신호에 대한 진입 적합성 검증
    - 과추격 방지를 통한 손실 최소화
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._check_history: list[AntiChaseResult] = []

    def check(
        self,
        signal: IgnitionSignal,
        current_price: float,
        bid_price: float,
        ask_price: float,
        candle_data: Optional[dict] = None,
    ) -> AntiChaseResult:
        """
        과추격 필터 체크

        Args:
            signal: 점화 신호
            current_price: 현재가
            bid_price: 매수 호가
            ask_price: 매도 호가
            candle_data: 현재 캔들 데이터 (배분 체크용)

        Returns:
            AntiChaseResult
        """
        settings = self._settings
        symbol = signal.symbol

        # === 필터 0: 신호 유효성 ===
        if not signal.is_valid:
            return self._make_result(
                symbol=symbol,
                passed=False,
                reason=AntiChaseReason.SIGNAL_INVALID,
                details={"invalidation_reason": signal.invalidation_reason},
            )

        if signal.is_expired:
            return self._make_result(
                symbol=symbol,
                passed=False,
                reason=AntiChaseReason.SIGNAL_EXPIRED,
                details={"expired_at": signal.expires_at.isoformat()},
            )

        # === 필터 1: 시간 필터 ===
        elapsed_sec = (datetime.utcnow() - signal.signal_time).total_seconds()
        max_entry_window = settings.ignition_entry_window_sec

        if elapsed_sec > max_entry_window:
            return self._make_result(
                symbol=symbol,
                passed=False,
                reason=AntiChaseReason.TIME_EXCEEDED,
                details={
                    "elapsed_sec": round(elapsed_sec, 1),
                    "max_window_sec": max_entry_window,
                },
            )

        # === 필터 2: 가격 필터 (과확장) ===
        atr = signal.atr_1h
        range_high = signal.range_high
        max_extension_mult = settings.ignition_over_extension_atr_mult

        if atr > 0:
            extension_atr = (current_price - range_high) / atr
        else:
            extension_atr = 0

        if extension_atr > max_extension_mult:
            return self._make_result(
                symbol=symbol,
                passed=False,
                reason=AntiChaseReason.PRICE_OVER_EXTENDED,
                details={
                    "current_price": round(current_price, 2),
                    "range_high": round(range_high, 2),
                    "extension_atr": round(extension_atr, 2),
                    "max_extension_atr": max_extension_mult,
                },
            )

        # === 필터 3: 스프레드 필터 ===
        if bid_price > 0 and ask_price > 0:
            spread_bps = ((ask_price - bid_price) / bid_price) * 10000
        else:
            spread_bps = 0

        max_spread_bps = settings.ignition_max_spread_bps

        if spread_bps > max_spread_bps:
            return self._make_result(
                symbol=symbol,
                passed=False,
                reason=AntiChaseReason.SPREAD_TOO_WIDE,
                details={
                    "spread_bps": round(spread_bps, 1),
                    "max_spread_bps": max_spread_bps,
                    "bid": round(bid_price, 2),
                    "ask": round(ask_price, 2),
                },
            )

        # === 필터 4: 배분 체크 (Distribution Wick) ===
        if candle_data:
            distribution_detected = self._check_distribution_wick(candle_data)
            if distribution_detected:
                return self._make_result(
                    symbol=symbol,
                    passed=False,
                    reason=AntiChaseReason.DISTRIBUTION_SUSPECTED,
                    details={
                        "upper_wick_pct": candle_data.get("upper_wick_pct", 0),
                        "threshold_pct": settings.ignition_distribution_wick_pct * 100,
                    },
                )

        # === 모든 필터 통과 ===
        return self._make_result(
            symbol=symbol,
            passed=True,
            reason=AntiChaseReason.PASSED,
            details={
                "elapsed_sec": round(elapsed_sec, 1),
                "extension_atr": round(extension_atr, 2),
                "spread_bps": round(spread_bps, 1),
            },
        )

    def _check_distribution_wick(self, candle_data: dict) -> bool:
        """
        배분 심 체크

        캔들 상단 꼬리가 전체 레인지의 30% 이상이면 배분 의심

        Args:
            candle_data: 캔들 데이터 (open, high, low, close)

        Returns:
            배분 의심 여부
        """
        settings = self._settings

        high = candle_data.get("high", 0)
        low = candle_data.get("low", 0)
        close = candle_data.get("close", 0)
        open_price = candle_data.get("open", 0)

        if high <= low:
            return False

        candle_range = high - low
        body_top = max(open_price, close)
        upper_wick = high - body_top

        upper_wick_pct = upper_wick / candle_range if candle_range > 0 else 0

        # 캔들 데이터에 계산값 추가
        candle_data["upper_wick_pct"] = round(upper_wick_pct * 100, 1)

        threshold = settings.ignition_distribution_wick_pct

        return upper_wick_pct >= threshold

    def _make_result(
        self,
        symbol: str,
        passed: bool,
        reason: AntiChaseReason,
        details: dict,
    ) -> AntiChaseResult:
        """결과 생성 및 기록"""
        result = AntiChaseResult(
            symbol=symbol,
            passed=passed,
            reason=reason,
            details=details,
        )

        self._check_history.append(result)

        # 로깅
        if passed:
            logger.debug(
                "Anti-chase gate passed",
                symbol=symbol,
                details=details,
            )
        else:
            logger.info(
                "Anti-chase gate blocked",
                symbol=symbol,
                reason=reason.value,
                details=details,
            )

        return result

    def quick_check(
        self,
        signal: IgnitionSignal,
        current_price: float,
    ) -> bool:
        """
        빠른 과추격 체크 (시간 + 가격만)

        전체 필터 전에 빠르게 스크리닝

        Args:
            signal: 점화 신호
            current_price: 현재가

        Returns:
            통과 여부
        """
        settings = self._settings

        # 시간 체크
        elapsed = (datetime.utcnow() - signal.signal_time).total_seconds()
        if elapsed > settings.ignition_entry_window_sec:
            return False

        # 가격 체크
        atr = signal.atr_1h
        range_high = signal.range_high
        if atr > 0:
            extension = (current_price - range_high) / atr
            if extension > settings.ignition_over_extension_atr_mult:
                return False

        return True

    def get_recent_blocks(self, limit: int = 20) -> list[AntiChaseResult]:
        """최근 차단 기록"""
        blocked = [r for r in self._check_history if not r.passed]
        return blocked[-limit:]

    def get_block_stats(self) -> dict:
        """차단 통계"""
        total = len(self._check_history)
        blocked = len([r for r in self._check_history if not r.passed])
        passed = total - blocked

        # 사유별 통계
        reason_counts: dict[str, int] = {}
        for result in self._check_history:
            if not result.passed:
                reason = result.reason.value
                reason_counts[reason] = reason_counts.get(reason, 0) + 1

        return {
            "total_checks": total,
            "passed": passed,
            "blocked": blocked,
            "block_rate": round(blocked / total * 100, 1) if total > 0 else 0,
            "reason_breakdown": reason_counts,
        }

    def get_status(self) -> dict:
        """현재 상태"""
        return {
            "total_checks": len(self._check_history),
            "recent_blocks": [r.to_dict() for r in self.get_recent_blocks(10)],
            "stats": self.get_block_stats(),
        }


# 싱글톤 인스턴스
_anti_chase_gate: Optional[AntiChaseGate] = None


def get_anti_chase_gate() -> AntiChaseGate:
    """Anti-Chase Gate 싱글톤"""
    global _anti_chase_gate
    if _anti_chase_gate is None:
        _anti_chase_gate = AntiChaseGate()
    return _anti_chase_gate
