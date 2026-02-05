"""
Surge Detector - 급등 시작 실시간 감지

역할:
- 1분봉 기반 급등 "시작" 순간 감지
- 구조/거리 기반 추격 판단 (5분%컷 제거)
- 거래량 급증 + 가격 급등 동시 발생 시 신호
- A(첫 점화) + B(리테스트) 진입 모드 지원

감지 조건:
1. 1분 변화율 >= 3.0% (급등 시작, surge_min_change_1m 설정값)
2. 거래량 >= 평균의 3배 (거래량 급증, surge_min_volume_mult 설정값)
3. 구조적 Anti-Chase 통과 (돌파레벨/VWAP 거리 기반)

필터 4종 (v4.2):
- VolOverheatGuard: 상한 컷 (8x 초과시 차단)
- HotYesterdayPolicy: 전일 급등주 조건 강화
- StructureAntiChase: 구조/거리 기반 추격 판단 (5분%컷 대체)
- UpbitMicrostructureFilter: 호가 구조 필터 (시간 필터 대체)

진입 타입:
- A (FIRST_IGNITION): 첫 점화 - 돌파 직후 진입 (100% 포지션)
- B (RETEST_ENTRY): 리테스트 - 되돌림 후 재진입 (70% 포지션)
- X (BLOCKED): 추격 금지 - 너무 멀리 간 경우
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

from src.config import get_settings
from src.data.candle_manager import Candle, CandleManager, get_candle_manager
from src.strategies.ignition.filters.vol_overheat_guard import (
    get_vol_overheat_guard,
)
from src.strategies.ignition.filters.hot_yesterday_policy import (
    get_hot_yesterday_policy,
)
from src.strategies.ignition.filters.structure_anti_chase import (
    EntryType,
    get_structure_anti_chase,
)

logger = structlog.get_logger()


@dataclass
class SurgeSignal:
    """급등 시작 신호"""

    symbol: str
    current_price: float
    change_1m_pct: float  # 1분 변화율
    change_5m_pct: float  # 5분 변화율 (레거시, 로깅용)
    volume_ratio: float  # 거래량 비율 (vs 평균)
    signal_time: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(seconds=60))

    # 진입/청산 가격
    entry_price: float = 0
    stop_loss: float = 0
    take_profit_1: float = 0
    take_profit_2: float = 0

    # HotYesterday 조정값 (v4.1)
    position_size_mult: float = 1.0  # 포지션 사이즈 배수
    time_stop_min: int = 5  # 타임스톱 (분)
    is_hot_yesterday: bool = False  # 전일 급등주 여부
    vol_zone: str = "EARLY"  # EARLY, OPTIMAL, HIGH

    # 구조적 Anti-Chase 결과 (v4.2)
    entry_type: str = "A"  # A=첫점화, B=리테스트, X=차단
    dist_breakout: float = 0  # 돌파레벨 대비 거리
    dist_vwap: float = 0  # VWAP 대비 이격
    dist_atr: float = 0  # ATR 정규화 거리
    vwap_5m: float = 0  # 5분 VWAP
    breakout_level: float = 0  # 돌파 레벨

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "current_price": round(self.current_price, 2),
            "change_1m_pct": round(self.change_1m_pct, 2),
            "change_5m_pct": round(self.change_5m_pct, 2),
            "volume_ratio": round(self.volume_ratio, 2),
            "entry_price": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "take_profit_1": round(self.take_profit_1, 2),
            "take_profit_2": round(self.take_profit_2, 2),
            "signal_time": self.signal_time.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "position_size_mult": self.position_size_mult,
            "time_stop_min": self.time_stop_min,
            "is_hot_yesterday": self.is_hot_yesterday,
            "vol_zone": self.vol_zone,
            # v4.2: 구조적 Anti-Chase 정보
            "entry_type": self.entry_type,
            "dist_breakout": round(self.dist_breakout, 4),
            "dist_vwap": round(self.dist_vwap, 4),
            "dist_atr": round(self.dist_atr, 2),
            "vwap_5m": round(self.vwap_5m, 2),
            "breakout_level": round(self.breakout_level, 2),
        }


@dataclass
class SurgePosition:
    """급등 포지션"""

    symbol: str
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    highest_price: float
    trailing_stop: Optional[float] = None
    entered_at: datetime = field(default_factory=datetime.utcnow)

    def update_trailing(self, current_price: float, trailing_stop_pct: float = -0.06) -> None:
        """트레일링 스톱 업데이트"""
        if current_price > self.highest_price:
            self.highest_price = current_price
            # 고점에서 설정값% 하락 시 청산 (기본 -6%)
            self.trailing_stop = self.highest_price * (1 + trailing_stop_pct)


@dataclass
class SurgeProximityScore:
    """급등 조건 근접도 점수 (0-100%)"""

    symbol: str
    current_price: float

    # 1분 변화율 (가중치 25%)
    change_1m_score: float = 0.0  # 0-100%
    change_1m_value: float = 0.0  # 실제 값

    # 거래량 배수 (가중치 20%)
    volume_score: float = 0.0  # 0-100%
    volume_ratio: float = 0.0  # 실제 배수
    volume_target: float = 3.0  # 목표 배수

    # 거래량 급증 - 직전 대비 (가중치 15%)
    volume_spike_score: float = 0.0  # 0-100%
    volume_spike_ratio: float = 0.0  # 실제 급증 배수 (현재/직전)

    # 거래량 가속도 (가중치 10%)
    volume_accel_score: float = 0.0  # 0-100%
    volume_accel_ratio: float = 0.0  # 실제 가속도 (최근3분/이전3분)

    # 과열 차단 (가중치 10%)
    vol_overheat_score: float = 0.0  # 통과=100, 차단=0
    vol_overheat_reason: str = ""

    # 구조적 Anti-Chase (가중치 20%)
    anti_chase_score: float = 0.0  # 0-100%
    dist_breakout: float = 0.0
    dist_atr: float = 0.0
    entry_type: str = ""

    # 종합 점수
    total_score: float = 0.0

    # 추가 정보
    is_hot_yesterday: bool = False
    korean_name: str = ""  # 한글명
    english_name: str = ""  # 영문명

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "current_price": round(self.current_price, 2),
            "korean_name": self.korean_name,
            "english_name": self.english_name,
            "change_1m": {
                "score": round(self.change_1m_score, 1),
                "value": round(self.change_1m_value, 2),
                "target": 1.5,
            },
            "volume": {
                "score": round(self.volume_score, 1),
                "ratio": round(self.volume_ratio, 2),
                "target": round(self.volume_target, 2),
            },
            "volume_spike": {
                "score": round(self.volume_spike_score, 1),
                "ratio": round(self.volume_spike_ratio, 2),
                "target": 2.0,
            },
            "volume_accel": {
                "score": round(self.volume_accel_score, 1),
                "ratio": round(self.volume_accel_ratio, 2),
                "target": 1.5,
            },
            "vol_overheat": {
                "score": round(self.vol_overheat_score, 1),
                "reason": self.vol_overheat_reason,
            },
            "anti_chase": {
                "score": round(self.anti_chase_score, 1),
                "dist_breakout": round(self.dist_breakout, 4),
                "dist_atr": round(self.dist_atr, 2),
                "entry_type": self.entry_type,
            },
            "total_score": round(self.total_score, 1),
            "is_hot_yesterday": self.is_hot_yesterday,
        }


class SurgeDetector:
    """
    급등 시작 실시간 감지

    1분봉 기반으로 급등 "시작" 순간을 캐치
    이미 급등한 종목은 자동 제외
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._candle_manager = get_candle_manager()
        self._recent_signals: dict[str, SurgeSignal] = {}
        self._cooldown: dict[str, datetime] = {}  # 쿨다운 (같은 종목 연속 신호 방지)
        self._positions: dict[str, SurgePosition] = {}  # 포지션 추적

    def scan_all_symbols(
        self,
        symbols: list[str],
        market_data_map: dict[str, dict],
    ) -> list[SurgeSignal]:
        """
        전체 심볼 스캔하여 급등 시작 감지

        Args:
            symbols: 스캔할 심볼 리스트
            market_data_map: 심볼별 시장 데이터

        Returns:
            급등 시작 신호 리스트
        """
        signals = []
        now = datetime.utcnow()

        for symbol in symbols:
            # 쿨다운 체크 (5분)
            if symbol in self._cooldown:
                if now < self._cooldown[symbol]:
                    continue

            market_data = market_data_map.get(symbol, {})
            if not market_data:
                continue

            signal = self._check_surge(symbol, market_data)
            if signal:
                signals.append(signal)
                self._recent_signals[symbol] = signal
                self._cooldown[symbol] = now + timedelta(minutes=5)

                logger.info(
                    "Surge detected",
                    symbol=symbol,
                    change_1m=f"{signal.change_1m_pct:.2f}%",
                    change_5m=f"{signal.change_5m_pct:.2f}%",
                    volume_ratio=f"{signal.volume_ratio:.1f}x",
                )

        return signals

    def _check_surge(
        self,
        symbol: str,
        market_data: dict,
    ) -> Optional[SurgeSignal]:
        """
        개별 심볼 급등 체크

        조건:
        1. 1분 변화율 >= 3.0% (설정값: surge_min_change_1m)
        2. 거래량 >= 평균 3배 (설정값: surge_min_volume_mult, HotYesterday시 조정)
        3. 5분 변화율 < 5% (이미 급등 제외)
        4. VolOverheatGuard: 8x 초과시 차단 (너무 늦음)
        """
        cm = self._candle_manager
        current_price = market_data.get("price", 0)

        if current_price <= 0:
            return None

        # === 1분봉 데이터 ===
        candles_1m = cm.get_candles(symbol, CandleManager.INTERVAL_1M, count=10)
        if len(candles_1m) < 5:
            return None

        # 1분 전 가격
        price_1m_ago = candles_1m[-2].close if len(candles_1m) >= 2 else current_price
        change_1m_pct = ((current_price - price_1m_ago) / price_1m_ago * 100) if price_1m_ago > 0 else 0

        # 5분 전 가격
        price_5m_ago = candles_1m[-5].close if len(candles_1m) >= 5 else current_price
        change_5m_pct = ((current_price - price_5m_ago) / price_5m_ago * 100) if price_5m_ago > 0 else 0

        # === 거래량 체크 ===
        volumes = [c.volume for c in candles_1m]
        if len(volumes) >= 5:
            recent_vol = volumes[-1]
            avg_vol = sum(volumes[:-1]) / len(volumes[:-1])
            volume_ratio = recent_vol / avg_vol if avg_vol > 0 else 0
        else:
            volume_ratio = 0

        # === Filter 1: HotYesterdayPolicy (파라미터 조정) ===
        hot_policy = get_hot_yesterday_policy()
        signed_change_rate = market_data.get("signed_change_rate", 0)
        adjustment = hot_policy.check(
            symbol=symbol,
            signed_change_rate=signed_change_rate,
            base_volume_mult=3.0,
            base_time_stop_min=5,
        )
        adjusted_vol_mult = adjustment.volume_mult_adjusted

        # === Filter 2: VolOverheatGuard (상한 컷) ===
        vol_guard = get_vol_overheat_guard()
        vol_check = vol_guard.check(
            symbol=symbol,
            current_volume=recent_vol if len(volumes) >= 5 else 0,
            avg_volume_24h=avg_vol if len(volumes) >= 5 else 1,
        )
        if not vol_check.passed:
            logger.debug(
                "Surge rejected - volume overheat",
                symbol=symbol,
                vol_ratio=f"{volume_ratio:.1f}x",
                reason=vol_check.reason,
            )
            return None

        # === 급등 조건 체크 ===
        # 조건 1: 1분 변화율 >= 설정값 (기본 3.0%)
        min_change_1m = self._settings.surge_min_change_1m * 100  # 0.03 -> 3.0
        if change_1m_pct < min_change_1m:
            return None

        # 조건 2: 거래량 >= 조정된 배수 (기본 3배, HotYesterday시 강화)
        if volume_ratio < adjusted_vol_mult:
            return None

        # === Filter 3: 구조적 Anti-Chase (v4.2 - 5분 % 컷 제거) ===
        structure_chase = get_structure_anti_chase()

        # VWAP/Breakout 레벨 계산
        vwap_5m = self._calc_vwap_5m(symbol, candles_1m)
        breakout_level = self._get_breakout_level(candles_1m)
        atr_5m = self._calc_atr_5m(candles_1m)

        # 리테스트 여부 판단
        is_retest = self._check_retest_pattern(candles_1m, breakout_level)

        chase_result = structure_chase.check(
            symbol=symbol,
            current_price=current_price,
            breakout_level=breakout_level,
            vwap_5m=vwap_5m,
            atr_5m=atr_5m,
            is_retest=is_retest,
        )

        if not chase_result.passed:
            logger.debug(
                "Surge rejected - structure anti-chase",
                symbol=symbol,
                entry_type=chase_result.entry_type.value,
                dist_atr=f"{chase_result.dist_atr:.2f}",
                reason=chase_result.reason,
            )
            return None

        # 진입 타입 및 포지션 배수
        entry_type = chase_result.entry_type.value
        position_mult_structure = chase_result.position_mult

        # === 신호 생성 ===
        # 손절가: 설정값 사용 (기본 -4%)
        stop_loss_pct = self._settings.surge_stop_loss_pct  # -0.04
        stop_loss = current_price * (1 + stop_loss_pct)

        # 목표가: 설정값 사용 (기본 3R)
        risk = current_price - stop_loss
        take_profit_mult = self._settings.surge_take_profit_mult  # 3.0
        take_profit_1 = current_price + risk * take_profit_mult  # 3R
        take_profit_2 = current_price + risk * (take_profit_mult * 2)  # 6R

        # 포지션 배수 결합: HotYesterday * Structure AntiChase
        combined_position_mult = adjustment.position_size_mult * position_mult_structure

        signal = SurgeSignal(
            symbol=symbol,
            current_price=current_price,
            change_1m_pct=change_1m_pct,
            change_5m_pct=change_5m_pct,
            volume_ratio=volume_ratio,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            # HotYesterday 조정값
            position_size_mult=combined_position_mult,
            time_stop_min=adjustment.time_stop_min,
            is_hot_yesterday=adjustment.is_hot,
            vol_zone=vol_check.zone,
            # 구조적 Anti-Chase 결과 (v4.2)
            entry_type=entry_type,
            dist_breakout=chase_result.dist_breakout,
            dist_vwap=chase_result.dist_vwap,
            dist_atr=chase_result.dist_atr,
            vwap_5m=vwap_5m,
            breakout_level=breakout_level,
        )

        return signal

    def _calc_vwap_5m(self, symbol: str, candles: list[Candle]) -> float:
        """5분 VWAP 계산"""
        if len(candles) < 5:
            return candles[-1].close if candles else 0

        recent = candles[-5:]
        total_pv = sum(c.close * c.volume for c in recent)
        total_v = sum(c.volume for c in recent)

        return total_pv / total_v if total_v > 0 else candles[-1].close

    def _get_breakout_level(self, candles: list[Candle]) -> float:
        """돌파 레벨 (최근 5분 고점)"""
        if len(candles) < 5:
            return candles[-1].high if candles else 0

        return max(c.high for c in candles[-5:])

    def _calc_atr_5m(self, candles: list[Candle]) -> float:
        """5분 ATR 계산"""
        if len(candles) < 2:
            return 0

        true_ranges = []
        for i in range(1, min(6, len(candles))):
            tr = max(
                candles[i].high - candles[i].low,
                abs(candles[i].high - candles[i - 1].close),
                abs(candles[i].low - candles[i - 1].close),
            )
            true_ranges.append(tr)

        return sum(true_ranges) / len(true_ranges) if true_ranges else 0

    def _check_retest_pattern(
        self, candles: list[Candle], breakout_level: float
    ) -> bool:
        """
        리테스트 패턴 감지

        조건: 한번 돌파 후 되돌림이 있었고, 다시 상승 중
        """
        if len(candles) < 5:
            return False

        # 최근 5분 중 돌파 후 되돌림이 있었는지
        recent_5 = candles[-5:]

        # 한번 돌파했었음 (최근 5봉 중 고점이 돌파레벨 넘음)
        broke_above = any(c.high > breakout_level for c in recent_5[:-1])

        # 되돌림 있었음 (최근 3봉 중 종가가 돌파레벨 아래)
        pulled_back = any(c.close < breakout_level for c in recent_5[-3:-1])

        # 현재 회복 중 (현재 봉이 이전 봉보다 높음)
        recovering = candles[-1].close > candles[-2].close

        return broke_above and pulled_back and recovering

    def get_active_signals(self) -> list[SurgeSignal]:
        """활성 신호 반환"""
        now = datetime.utcnow()
        active = []
        for signal in self._recent_signals.values():
            if not signal.is_expired:
                active.append(signal)
        return active

    def clear_expired(self) -> int:
        """만료된 신호 정리"""
        expired = [s for s, sig in self._recent_signals.items() if sig.is_expired]
        for symbol in expired:
            del self._recent_signals[symbol]
        return len(expired)

    def track_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        stop_loss: float,
        take_profit: float,
    ) -> None:
        """포지션 추적 시작"""
        self._positions[symbol] = SurgePosition(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            highest_price=entry_price,
        )
        logger.info(
            "Surge position tracked",
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def check_exit(self, symbol: str, current_price: float) -> Optional[tuple[str, float]]:
        """
        청산 조건 체크

        Returns:
            (청산 사유, 청산 비율) 또는 None
        """
        pos = self._positions.get(symbol)
        if not pos:
            return None

        # 트레일링 스톱 업데이트 (설정값 사용)
        trailing_stop_pct = self._settings.surge_trailing_stop_pct  # -0.06
        pos.update_trailing(current_price, trailing_stop_pct)

        # 1. 손절 체크
        if current_price <= pos.stop_loss:
            return "STOP_LOSS", 1.0

        # 2. 익절 체크 (목표가 도달 시 50% 청산)
        if current_price >= pos.take_profit:
            return "TAKE_PROFIT", 0.5

        # 3. 트레일링 스톱 체크
        if pos.trailing_stop and current_price <= pos.trailing_stop:
            return "TRAILING_STOP", 1.0

        # 4. 시간 청산 (설정 시간 경과 후 손실이면 청산)
        time_stop_sec = self._settings.surge_time_stop_minutes * 60  # 15분 -> 900초
        elapsed = (datetime.utcnow() - pos.entered_at).total_seconds()
        if elapsed > time_stop_sec and current_price < pos.entry_price:
            return "TIME_STOP", 1.0

        return None

    def close_position(self, symbol: str, partial: bool = False) -> None:
        """포지션 청산"""
        if symbol in self._positions:
            if partial:
                # 부분 청산 - 수량 50% 감소
                self._positions[symbol].quantity *= 0.5
            else:
                del self._positions[symbol]

    def get_positions(self) -> list[SurgePosition]:
        """활성 포지션 반환"""
        return list(self._positions.values())

    def get_status(self) -> dict:
        """상태 반환"""
        return {
            "active_signals": len(self.get_active_signals()),
            "active_positions": len(self._positions),
            "cooldown_count": len(self._cooldown),
            "signals": [s.to_dict() for s in self.get_active_signals()],
            "positions": [
                {
                    "symbol": p.symbol,
                    "entry_price": p.entry_price,
                    "quantity": p.quantity,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "highest_price": p.highest_price,
                    "trailing_stop": p.trailing_stop,
                }
                for p in self._positions.values()
            ],
            "filter_stats": self.get_filter_stats(),
        }

    def get_filter_stats(self) -> dict:
        """필터 통계 반환"""
        vol_guard = get_vol_overheat_guard()
        hot_policy = get_hot_yesterday_policy()
        structure_chase = get_structure_anti_chase()

        return {
            "vol_overheat_guard": vol_guard.get_stats(),
            "hot_yesterday_policy": hot_policy.get_stats(),
            "structure_anti_chase": structure_chase.get_stats(),
        }

    def calc_surge_proximity(
        self,
        symbol: str,
        market_data: dict,
    ) -> Optional[SurgeProximityScore]:
        """
        개별 심볼의 급등 조건 근접도 계산

        Args:
            symbol: 심볼
            market_data: 시장 데이터

        Returns:
            SurgeProximityScore 또는 None (데이터 부족 시)
        """
        cm = self._candle_manager
        current_price = market_data.get("price", 0)

        if current_price <= 0:
            return None

        # === 1분봉 데이터 ===
        candles_1m = cm.get_candles(symbol, CandleManager.INTERVAL_1M, count=10)
        if len(candles_1m) < 5:
            return None

        # 1분 전 가격
        price_1m_ago = candles_1m[-2].close if len(candles_1m) >= 2 else current_price
        change_1m_pct = ((current_price - price_1m_ago) / price_1m_ago * 100) if price_1m_ago > 0 else 0

        # === 거래량 계산 ===
        volumes = [c.volume for c in candles_1m]
        if len(volumes) >= 5:
            recent_vol = volumes[-1]
            avg_vol = sum(volumes[:-1]) / len(volumes[:-1])
            volume_ratio = recent_vol / avg_vol if avg_vol > 0 else 0
        else:
            recent_vol = 0
            avg_vol = 1
            volume_ratio = 0

        # === 거래량 급증 (Spike) - 직전 1분 대비 ===
        prev_vol = volumes[-2] if len(volumes) >= 2 else 1
        volume_spike = recent_vol / prev_vol if prev_vol > 0 else 0

        # === 거래량 가속도 (Acceleration) - 최근 3분 vs 이전 3분 ===
        if len(volumes) >= 6:
            recent_3_avg = sum(volumes[-3:]) / 3
            prior_3_avg = sum(volumes[-6:-3]) / 3
            volume_accel = recent_3_avg / prior_3_avg if prior_3_avg > 0 else 0
        else:
            volume_accel = 1.0  # 데이터 부족 시 중립

        # === HotYesterday 체크 (목표 배수 조정) ===
        hot_policy = get_hot_yesterday_policy()
        signed_change_rate = market_data.get("signed_change_rate", 0)
        adjustment = hot_policy.check(
            symbol=symbol,
            signed_change_rate=signed_change_rate,
            base_volume_mult=3.0,
            base_time_stop_min=5,
        )
        volume_target = adjustment.volume_mult_adjusted
        is_hot_yesterday = adjustment.is_hot

        # === VolOverheatGuard 체크 ===
        vol_guard = get_vol_overheat_guard()
        vol_check = vol_guard.check(
            symbol=symbol,
            current_volume=recent_vol,
            avg_volume_24h=avg_vol,
        )

        # === 구조적 Anti-Chase 계산 ===
        structure_chase = get_structure_anti_chase()
        vwap_5m = self._calc_vwap_5m(symbol, candles_1m)
        breakout_level = self._get_breakout_level(candles_1m)
        atr_5m = self._calc_atr_5m(candles_1m)
        is_retest = self._check_retest_pattern(candles_1m, breakout_level)

        chase_result = structure_chase.check(
            symbol=symbol,
            current_price=current_price,
            breakout_level=breakout_level,
            vwap_5m=vwap_5m,
            atr_5m=atr_5m,
            is_retest=is_retest,
        )

        # === 최소 조건 필터 ===
        # 1분 변화율이 0.1% 미만이면 급등 근접 종목이 아님
        # (상승 모멘텀이 있어야 "근접"이라 할 수 있음)
        MIN_CHANGE_THRESHOLD = 0.1  # 최소 0.1% 상승
        if change_1m_pct < MIN_CHANGE_THRESHOLD:
            return None

        # === 점수 계산 ===
        # 1. 1분 변화율 점수 (목표: surge_min_change_1m * 100, 최대 100%)
        min_change_target = self._settings.surge_min_change_1m * 100  # 0.03 -> 3.0
        change_1m_score = min(100, max(0, (change_1m_pct / min_change_target) * 100))

        # 2. 거래량 점수 (목표: volume_target, 최대 100%)
        volume_score = min(100, max(0, (volume_ratio / volume_target) * 100))

        # 3. 거래량 급증 점수 (목표: 2x, 최대 100%)
        volume_spike_score = min(100, max(0, (volume_spike / 2.0) * 100))

        # 4. 거래량 가속도 점수 (목표: 1.5x, 최대 100%)
        volume_accel_score = min(100, max(0, (volume_accel / 1.5) * 100))

        # 5. VolOverheat 점수 (통과=100, 차단=0)
        vol_overheat_score = 100.0 if vol_check.passed else 0.0
        vol_overheat_reason = "" if vol_check.passed else vol_check.reason

        # 6. Anti-Chase 점수 (거리 기반)
        # dist_atr이 작을수록 좋음 (0~2 범위를 100~0으로 변환)
        if chase_result.passed:
            anti_chase_score = 100.0
        else:
            # 차단된 경우: 거리에 따라 점수 감소
            anti_chase_score = max(0, 100 - (chase_result.dist_atr * 40))

        # === 가중 평균 계산 ===
        # 가중치: 변화율 25%, 거래량 20%, 급증 15%, 가속 10%, 과열 10%, Anti-Chase 20%
        total_score = (
            change_1m_score * 0.25
            + volume_score * 0.20
            + volume_spike_score * 0.15
            + volume_accel_score * 0.10
            + vol_overheat_score * 0.10
            + anti_chase_score * 0.20
        )

        return SurgeProximityScore(
            symbol=symbol,
            current_price=current_price,
            change_1m_score=change_1m_score,
            change_1m_value=change_1m_pct,
            volume_score=volume_score,
            volume_ratio=volume_ratio,
            volume_target=volume_target,
            volume_spike_score=volume_spike_score,
            volume_spike_ratio=volume_spike,
            volume_accel_score=volume_accel_score,
            volume_accel_ratio=volume_accel,
            vol_overheat_score=vol_overheat_score,
            vol_overheat_reason=vol_overheat_reason,
            anti_chase_score=anti_chase_score,
            dist_breakout=chase_result.dist_breakout,
            dist_atr=chase_result.dist_atr,
            entry_type=chase_result.entry_type.value,
            total_score=total_score,
            is_hot_yesterday=is_hot_yesterday,
            korean_name=market_data.get("korean_name", ""),
            english_name=market_data.get("english_name", ""),
        )

    def get_surge_candidates(
        self,
        symbols: list[str],
        market_data_map: dict[str, dict],
        min_score: float = 70.0,
        limit: int = 20,
    ) -> list[SurgeProximityScore]:
        """
        급등 조건 근접 종목 조회

        Args:
            symbols: 스캔할 심볼 리스트
            market_data_map: 심볼별 시장 데이터
            min_score: 최소 점수 (기본 70%)
            limit: 최대 반환 개수

        Returns:
            점수순으로 정렬된 SurgeProximityScore 리스트
        """
        candidates = []

        for symbol in symbols:
            market_data = market_data_map.get(symbol, {})
            if not market_data:
                continue

            proximity = self.calc_surge_proximity(symbol, market_data)
            if proximity and proximity.total_score >= min_score:
                candidates.append(proximity)

        # 점수 내림차순 정렬
        candidates.sort(key=lambda x: x.total_score, reverse=True)

        return candidates[:limit]


# 싱글톤
_surge_detector: Optional[SurgeDetector] = None


def get_surge_detector() -> SurgeDetector:
    """SurgeDetector 싱글톤"""
    global _surge_detector
    if _surge_detector is None:
        _surge_detector = SurgeDetector()
    return _surge_detector
