"""Pump & Dump Filter

펌프&덤프 회피 필터:
- RVOL 급증 후 급감 패턴 감지
- 윗꼬리 연속 + VWAP 이탈 패턴 감지
- 호가 불균형 감지
- 거래량 이상 패턴 감지

목적: 펌프&덤프 의심 코인 진입을 방지하여 손실 위험 감소
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class PumpDumpSignal:
    """펌프&덤프 감지 결과"""
    is_pump_suspected: bool       # 펌프 의심
    is_dump_suspected: bool       # 덤프 의심
    score: float                  # 의심 점수 (0~100)
    reasons: list[str]            # 의심 사유들
    should_avoid: bool            # 진입 회피 권고
    severity: str                 # LOW / MEDIUM / HIGH / CRITICAL


class PumpDumpFilter:
    """펌프&덤프 회피 필터"""

    def __init__(
        self,
        rvol_threshold: float = None,
        price_spike_1h: float = None,
        depth_imbalance: float = None,
    ):
        """
        Args:
            rvol_threshold: 펌프 의심 RVOL 임계값 (기본: 10배)
            price_spike_1h: 펌프 의심 1시간 가격 상승률 (기본: 20%)
            depth_imbalance: 호가 불균형 임계값 (기본: 5.0)
        """
        self.enabled = settings.pump_dump_filter_enabled
        self.rvol_threshold = rvol_threshold or settings.pump_dump_rvol_threshold
        self.price_spike_1h = price_spike_1h or settings.pump_dump_price_spike_1h
        self.depth_imbalance = depth_imbalance or settings.pump_dump_depth_imbalance

        logger.info(
            "PumpDumpFilter initialized",
            enabled=self.enabled,
            rvol_threshold=self.rvol_threshold,
            price_spike_1h=self.price_spike_1h,
            depth_imbalance=self.depth_imbalance,
        )

    def check(
        self,
        symbol: str,
        rvol: float = 1.0,
        change_rate_1h: float = 0.0,
        change_rate_24h: float = 0.0,
        volume_24h: float = 0.0,
        depth_ratio: float = 1.0,
        spread_bps: float = 0.0,
        vwap_distance_pct: float = 0.0,
        consecutive_green_candles: int = 0,
        upper_wick_ratio: float = 0.0,
    ) -> PumpDumpSignal:
        """
        펌프&덤프 여부 체크

        Args:
            symbol: 심볼
            rvol: 상대거래량 (현재 / 평균)
            change_rate_1h: 1시간 변화율
            change_rate_24h: 24시간 변화율
            volume_24h: 24시간 거래대금
            depth_ratio: 호가 깊이 비율 (매수 / 매도)
            spread_bps: 스프레드 (bp)
            vwap_distance_pct: VWAP 대비 현재가 거리 (%)
            consecutive_green_candles: 연속 양봉 수
            upper_wick_ratio: 평균 윗꼬리 비율

        Returns:
            PumpDumpSignal
        """
        if not self.enabled:
            return PumpDumpSignal(
                is_pump_suspected=False,
                is_dump_suspected=False,
                score=0.0,
                reasons=[],
                should_avoid=False,
                severity="LOW",
            )

        score = 0.0
        reasons = []
        is_pump = False
        is_dump = False

        # === Pump 감지 ===

        # 1. RVOL 급증 체크
        pump_score, pump_reason = self._check_volume_spike(rvol, change_rate_1h)
        if pump_score > 0:
            score += pump_score
            reasons.append(pump_reason)
            is_pump = True

        # 2. 급격한 가격 상승 체크
        price_score, price_reason = self._check_price_spike(change_rate_1h, change_rate_24h)
        if price_score > 0:
            score += price_score
            reasons.append(price_reason)
            is_pump = True

        # 3. 호가 불균형 체크
        depth_score, depth_reason = self._check_orderbook_imbalance(depth_ratio, spread_bps)
        if depth_score > 0:
            score += depth_score
            reasons.append(depth_reason)
            is_pump = True

        # 4. 연속 양봉 체크
        candle_score, candle_reason = self._check_consecutive_candles(
            consecutive_green_candles, change_rate_1h
        )
        if candle_score > 0:
            score += candle_score
            reasons.append(candle_reason)
            is_pump = True

        # === Dump 감지 ===

        # 5. VWAP 이탈 체크 (현재가가 VWAP 아래)
        vwap_score, vwap_reason = self._check_vwap_deviation(vwap_distance_pct)
        if vwap_score > 0:
            score += vwap_score
            reasons.append(vwap_reason)
            is_dump = True

        # 6. 윗꼬리 연속 체크 (매물대 압박)
        wick_score, wick_reason = self._check_upper_wicks(upper_wick_ratio, change_rate_24h)
        if wick_score > 0:
            score += wick_score
            reasons.append(wick_reason)
            is_dump = True

        # 7. 스프레드 급증 체크
        spread_score, spread_reason = self._check_spread_spike(spread_bps)
        if spread_score > 0:
            score += spread_score
            reasons.append(spread_reason)
            is_dump = True

        # === 종합 판정 ===
        score = min(score, 100.0)

        if score >= 80:
            severity = "CRITICAL"
            should_avoid = True
        elif score >= 60:
            severity = "HIGH"
            should_avoid = True
        elif score >= 40:
            severity = "MEDIUM"
            should_avoid = False  # 경고만
        else:
            severity = "LOW"
            should_avoid = False

        if should_avoid:
            logger.warning(
                "PumpDump signal detected",
                symbol=symbol,
                score=score,
                severity=severity,
                is_pump=is_pump,
                is_dump=is_dump,
                reasons=reasons,
            )

        return PumpDumpSignal(
            is_pump_suspected=is_pump,
            is_dump_suspected=is_dump,
            score=score,
            reasons=reasons,
            should_avoid=should_avoid,
            severity=severity,
        )

    def _check_volume_spike(
        self,
        rvol: float,
        change_rate_1h: float,
    ) -> tuple[float, str]:
        """
        거래량 급증 체크

        RVOL > 임계값 + 가격 상승 = 펌프 의심
        """
        score = 0.0
        reason = ""

        if rvol >= self.rvol_threshold:
            # RVOL 기본 점수
            base_score = min((rvol - self.rvol_threshold) / self.rvol_threshold * 20, 30)
            score = base_score

            # 가격 상승과 동반하면 추가 점수
            if change_rate_1h >= 0.10:  # 1시간 +10% 이상
                score += 15
                reason = f"RVOL {rvol:.1f}x + 1h change {change_rate_1h:.1%}"
            else:
                reason = f"RVOL spike {rvol:.1f}x (threshold: {self.rvol_threshold}x)"

        return score, reason

    def _check_price_spike(
        self,
        change_rate_1h: float,
        change_rate_24h: float,
    ) -> tuple[float, str]:
        """급격한 가격 상승 체크"""
        score = 0.0
        reason = ""

        # 1시간 급등
        if change_rate_1h >= self.price_spike_1h:
            score = 25
            reason = f"1h price spike {change_rate_1h:.1%}"

        # 24시간 급등
        if change_rate_24h >= 0.30:  # +30% 이상
            score += 20
            if reason:
                reason += f", 24h {change_rate_24h:.1%}"
            else:
                reason = f"24h price surge {change_rate_24h:.1%}"

        return score, reason

    def _check_orderbook_imbalance(
        self,
        depth_ratio: float,
        spread_bps: float,
    ) -> tuple[float, str]:
        """호가 불균형 체크"""
        score = 0.0
        reason = ""

        # 매수 쏠림 (depth_ratio > 임계값)
        if depth_ratio >= self.depth_imbalance:
            score = 15
            reason = f"Buy-side imbalance {depth_ratio:.1f}x"

        # 매도 쏠림 (1/depth_ratio > 임계값)
        elif depth_ratio > 0 and (1 / depth_ratio) >= self.depth_imbalance:
            score = 15
            reason = f"Sell-side imbalance {1/depth_ratio:.1f}x"

        # 스프레드 급증과 동반
        if spread_bps >= 30 and score > 0:
            score += 10
            reason += f" + spread {spread_bps:.0f}bps"

        return score, reason

    def _check_consecutive_candles(
        self,
        consecutive_green: int,
        change_rate_1h: float,
    ) -> tuple[float, str]:
        """연속 양봉 체크"""
        score = 0.0
        reason = ""

        # 5개 이상 연속 양봉 + 상승
        if consecutive_green >= 5 and change_rate_1h >= 0.05:
            score = 15
            reason = f"{consecutive_green} consecutive green candles"

        # 7개 이상이면 추가
        if consecutive_green >= 7:
            score += 10
            reason = f"{consecutive_green} consecutive green candles (extended)"

        return score, reason

    def _check_vwap_deviation(self, vwap_distance_pct: float) -> tuple[float, str]:
        """VWAP 이탈 체크"""
        score = 0.0
        reason = ""

        # 현재가가 VWAP보다 5% 이상 아래
        if vwap_distance_pct <= -0.05:
            score = 15
            reason = f"Below VWAP by {abs(vwap_distance_pct):.1%}"

        # 10% 이상 아래면 추가
        if vwap_distance_pct <= -0.10:
            score += 10
            reason = f"Far below VWAP by {abs(vwap_distance_pct):.1%}"

        return score, reason

    def _check_upper_wicks(
        self,
        upper_wick_ratio: float,
        change_rate_24h: float,
    ) -> tuple[float, str]:
        """윗꼬리 연속 체크"""
        score = 0.0
        reason = ""

        # 평균 윗꼬리 비율이 높으면 (캔들 body의 50% 이상)
        if upper_wick_ratio >= 0.5:
            score = 15
            reason = f"High upper wicks {upper_wick_ratio:.0%}"

            # 24시간 상승 중 윗꼬리면 매물대 압박
            if change_rate_24h >= 0.10:
                score += 10
                reason += " + 24h uptrend (selling pressure)"

        return score, reason

    def _check_spread_spike(self, spread_bps: float) -> tuple[float, str]:
        """스프레드 급증 체크"""
        score = 0.0
        reason = ""

        # 스프레드 30bps 이상
        if spread_bps >= 30:
            score = 10
            reason = f"Spread spike {spread_bps:.0f}bps"

        # 50bps 이상이면 위험
        if spread_bps >= 50:
            score += 15
            reason = f"Extreme spread {spread_bps:.0f}bps (liquidity risk)"

        return score, reason

    def quick_check(
        self,
        rvol: float,
        change_rate_1h: float,
        change_rate_24h: float,
    ) -> tuple[bool, str]:
        """
        빠른 펌프&덤프 체크 (핵심 지표만)

        Args:
            rvol: 상대거래량
            change_rate_1h: 1시간 변화율
            change_rate_24h: 24시간 변화율

        Returns:
            (회피 권고, 사유)
        """
        if not self.enabled:
            return False, ""

        # 극단적인 RVOL + 가격 급등
        if rvol >= self.rvol_threshold and change_rate_1h >= self.price_spike_1h:
            return True, f"Pump suspected: RVOL {rvol:.1f}x + 1h {change_rate_1h:.1%}"

        # 극단적인 24시간 급등
        if change_rate_24h >= 0.40:  # +40% 이상
            return True, f"Extreme 24h surge: {change_rate_24h:.1%}"

        return False, ""


# Singleton
_filter: PumpDumpFilter = None


def get_pump_dump_filter() -> PumpDumpFilter:
    """PumpDumpFilter 싱글톤"""
    global _filter
    if _filter is None:
        _filter = PumpDumpFilter()
    return _filter
