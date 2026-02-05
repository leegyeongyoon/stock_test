"""
Dip Scalper Strategy v2.4

핵심 철학:
- "1분봉에서 급락한 종목 즉시 매수"
- 종목별 변동성(ATR) 기반 급락 기준 자동 조정
- 지정가 매수 (금액 기준)

진입 조건:
- 1분봉 변화율 <= -(ATR배수) 또는 고정 최소값
- 급등주(ATR 높음): 5%+ 급락 시
- 일반 종목(ATR 낮음): 2%+ 급락 시

v2.0 추가 조건 (떨어지는 칼날 방지):
- 거래량 급증 확인 (RVOL >= threshold)
- BTC 안정성 확인 (BTC가 폭락 중이면 매수 안함)
- 호가창 매수벽 확인 (bid wall 존재 여부)

v2.3 급등주 필터 (펌프 앤 덤프 방지):
- 5분봉 기준 +7% 이상 급등한 종목은 차단
- 급등 후 급락은 바닥 모름, 연속 폭락 가능
- "급락 스캘핑"이 아닌 "펌프 앤 덤프 희생양" 방지

v2.4 스마트 청산 (TP 도달 후 행동):
- TP 0.8% 도달 시 즉시 매도 안함
- 1~2분간 횡보(±0.2%) → 약한 반등 → 즉시 매도
- 2분 내 +3% 이상 급등 → 강한 반등 → 트레일링 스톱 (최고가 대비 -0.5%)
- TP 도달 후 2분 경과 → 무조건 매도
- SL: -1.2% (고정)
- 타임스톱: 10분 (고정)

특징:
- 복잡한 점수 시스템 없음
- ATR 기반 동적 임계값
- 금액 기준 지정가 매수
- 펌프 앤 덤프 회피
- 스마트 청산 (약한 반등 빠른 탈출 vs 강한 반등 큰 수익)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, time
from typing import Optional
from zoneinfo import ZoneInfo

import structlog

from src.config import get_settings

# 한국 시간대
KST = ZoneInfo("Asia/Seoul")

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class DipSignal:
    """급락 매수 시그널"""

    symbol: str
    current_price: float
    drop_pct: float  # 급락률 (음수)
    atr_pct: float  # ATR 기반 변동성
    threshold_pct: float  # 이 종목의 급락 기준
    entry_price: float  # 지정가 (현재가 or 약간 아래)
    stop_loss: float
    take_profit: float
    buy_amount_krw: float  # 매수 금액 (KRW)
    reason: str
    # v2.0 추가 필드
    rvol: float = 1.0  # 상대 거래량
    bid_strength: float = 0.0  # 매수벽 강도 (0~1)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "current_price": self.current_price,
            "drop_pct": f"{self.drop_pct:.2%}",
            "atr_pct": f"{self.atr_pct:.2%}",
            "threshold_pct": f"{self.threshold_pct:.2%}",
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "buy_amount_krw": self.buy_amount_krw,
            "reason": self.reason,
            "rvol": round(self.rvol, 2),
            "bid_strength": round(self.bid_strength, 2),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class DipPosition:
    """급락 매수 포지션"""

    symbol: str
    entry_price: float
    quantity: float
    entry_time: datetime
    stop_loss: float
    take_profit: float
    highest_price: float
    bid_ratio: float = 0.5  # v2.1: 호가창 매수벽 비율 (동적 익절에 사용)
    # v2.4: 스마트 청산용
    tp_reached_time: Optional[datetime] = None  # TP 도달 시각
    tp_reached_price: float = 0.0  # TP 도달 시 가격

    def to_dict(self) -> dict:
        pnl_pct = (self.highest_price - self.entry_price) / self.entry_price if self.entry_price > 0 else 0
        hold_minutes = (datetime.utcnow() - self.entry_time).total_seconds() / 60
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "highest_price": self.highest_price,
            "bid_ratio": f"{self.bid_ratio:.0%}",
            "pnl_pct": f"{pnl_pct:.2%}",
            "hold_minutes": f"{hold_minutes:.1f}",
        }


class DipScalperStrategy:
    """
    급락 스캘퍼 전략 v2.2

    핵심: 캔들 급락 감지 → 조건 확인 → 지정가 매수

    v2.2: 1분봉/3분봉 선택 지원
    - 1분봉: 빠른 반응, 노이즈 많음
    - 3분봉: 안정적, 갭다운 오인 감소

    종목별 급락 기준 (ATR 기반):
    - ATR 1% 종목 → 2% 급락이면 비정상
    - ATR 3% 종목 → 5~6% 급락이어야 비정상

    공식: threshold = max(min_drop, ATR * multiplier)

    v2.0 추가 조건:
    1. 거래량 급증 (RVOL >= 2.0) - 패닉셀링 확인
    2. BTC 안정성 (BTC 1분 변화 > -3%) - 시장 전체 급락 방지
    3. 호가창 매수벽 (bid_ratio > 0.4) - 지지선 확인

    v2.1: 장 초반 워밍업 (9:00~9:30 비활성화)
    v2.2: 1분봉/3분봉 선택 (candle_interval 설정)
    """

    def __init__(self):
        self._enabled = False
        self._load_settings()

        # 포지션 관리
        self._active_positions: dict[str, DipPosition] = {}
        self._pending_signals: dict[str, DipSignal] = {}

        # v2.3: 미체결 주문 추적
        self._pending_orders: dict[str, dict] = {}  # symbol -> order info

        # 쿨다운 (동일 종목 재진입 방지)
        self._last_exit: dict[str, datetime] = {}

        # BTC 상태 캐시
        self._btc_change_1m: float = 0.0

        # v2.3: 근접 종목 추적 (1초 모니터링)
        self._near_trigger_symbols: dict[str, dict] = {}  # symbol -> tracking info

        # 통계
        self._stats = {
            "total_signals": 0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl_pct": 0.0,
            # v2.0 필터 통계
            "filtered_by_volume": 0,
            "filtered_by_btc": 0,
            "filtered_by_orderbook": 0,
            # v2.3 미체결 통계
            "pending_orders_placed": 0,
            "pending_orders_filled": 0,
            "pending_orders_cancelled": 0,
        }

        logger.info(
            "DipScalper v2.2 initialized",
            enabled=self._enabled,
            candle_interval=f"{self.candle_interval}m",
            min_drop_pct=f"{self.min_drop_pct:.1%}",
            max_drop_pct=f"{self.max_drop_pct:.1%}",
            atr_multiplier=self.atr_multiplier,
            min_rvol=self.min_rvol,
            btc_crash_threshold=f"{self.btc_crash_threshold:.1%}",
            min_bid_ratio=self.min_bid_ratio,
        )

    def _load_settings(self) -> None:
        """설정 로드"""
        # 활성화 여부
        self._enabled = getattr(settings, "dip_scalper_enabled", False)

        # 급락 감지 설정
        self.min_drop_pct = getattr(settings, "dip_min_drop_pct", -0.02)  # 최소 -2%
        self.max_drop_pct = getattr(settings, "dip_max_drop_pct", -0.08)  # 최대 -8%
        self.atr_multiplier = getattr(settings, "dip_atr_multiplier", 1.2)  # ATR * 1.2 (완화)

        # 매수 설정
        self.buy_amount_krw = getattr(settings, "dip_buy_amount_krw", 100000)  # 10만원
        self.max_positions = getattr(settings, "dip_max_positions", 5)
        self.use_limit_order = getattr(settings, "dip_use_limit_order", True)
        self.limit_offset_pct = getattr(settings, "dip_limit_offset_pct", 0.001)  # 0.1% 아래 지정가

        # 청산 설정
        self.take_profit_pct = getattr(settings, "dip_take_profit_pct", 0.008)  # +0.8%
        self.stop_loss_pct = getattr(settings, "dip_stop_loss_pct", -0.012)  # -1.2%
        self.time_stop_minutes = getattr(settings, "dip_time_stop_minutes", 10)  # 10분

        # v2.4: 스마트 청산 (TP 도달 후 행동)
        self.tp_sideways_check_min = getattr(settings, "dip_tp_sideways_check_min", 1)  # 1분 후부터 횡보 체크
        self.tp_sideways_check_max = getattr(settings, "dip_tp_sideways_check_max", 2)  # 2분까지 횡보 체크
        self.sideways_threshold = getattr(settings, "dip_sideways_threshold", 0.002)  # ±0.2% 범위
        self.surge_time_limit = getattr(settings, "dip_surge_time_limit", 2)  # 2분 이내
        self.surge_threshold = getattr(settings, "dip_surge_threshold", 0.03)  # +3% 이상
        self.trailing_stop_pct = getattr(settings, "dip_trailing_stop_pct", 0.005)  # 최고가 대비 -0.5%

        # 쿨다운
        self.cooldown_minutes = getattr(settings, "dip_cooldown_minutes", 5)

        # 기본 필터
        self.min_volume_krw = getattr(settings, "dip_min_volume_krw", 100000000)  # 1억 거래대금

        # v2.0 추가 필터 설정
        self.min_rvol = getattr(settings, "dip_min_rvol", 2.0)  # 최소 RVOL 2.0 (평소의 2배)
        self.btc_crash_threshold = getattr(settings, "dip_btc_crash_threshold", -0.03)  # BTC -3% 이상 하락 시 중단
        self.min_bid_ratio = getattr(settings, "dip_min_bid_ratio", 0.4)  # 매수호가 비중 40% 이상

        # v2.1: 장 초반 비활성화 (9:00~9:30 갭다운 방지)
        self.market_open_hour = 9
        self.market_open_minute = 0
        self.market_warmup_minutes = 30  # 장 시작 후 30분간 비활성화

        # v2.2: 캔들 인터벌 설정 (1 또는 3분)
        self.candle_interval = getattr(settings, "dip_candle_interval", 1)  # 기본 1분봉
        if self.candle_interval not in (1, 3):
            logger.warning(f"Invalid candle_interval {self.candle_interval}, defaulting to 1")
            self.candle_interval = 1

    def set_enabled(self, enabled: bool) -> None:
        """활성화 설정"""
        self._enabled = enabled
        logger.info("DipScalper enabled changed", enabled=enabled)

    def is_enabled(self) -> bool:
        """활성화 여부"""
        return self._enabled

    def set_btc_status(self, btc_change_1m: float) -> None:
        """BTC 1분 변화율 설정 (외부에서 호출)"""
        self._btc_change_1m = btc_change_1m

    def _is_market_warmup(self) -> tuple[bool, str]:
        """
        v2.1: 장 초반 워밍업 시간 체크 (9:00~9:30)

        갭다운을 급락으로 오인하지 않도록 장 시작 후 30분간 비활성화

        Returns:
            (워밍업 중 여부, 사유)
        """
        now_kst = datetime.now(KST)
        market_open = now_kst.replace(
            hour=self.market_open_hour,
            minute=self.market_open_minute,
            second=0,
            microsecond=0,
        )
        warmup_end = market_open + timedelta(minutes=self.market_warmup_minutes)

        if market_open <= now_kst < warmup_end:
            remaining = (warmup_end - now_kst).total_seconds() / 60
            return True, f"장 초반 워밍업 ({remaining:.0f}분 남음)"

        return False, ""

    def calc_drop_threshold(self, atr_pct: float) -> float:
        """
        종목별 급락 기준 계산

        Args:
            atr_pct: ATR / 현재가 (예: 0.02 = 2%)

        Returns:
            급락 기준 (음수, 예: -0.03 = -3%)
        """
        # ATR 기반 동적 임계값
        dynamic_threshold = -abs(atr_pct * self.atr_multiplier)

        # min/max 범위 내로 제한
        threshold = max(self.max_drop_pct, min(self.min_drop_pct, dynamic_threshold))

        return threshold

    def _check_btc_stability(self) -> tuple[bool, str]:
        """
        BTC 안정성 확인

        Returns:
            (통과 여부, 사유)
        """
        if self._btc_change_1m <= self.btc_crash_threshold:
            return False, f"BTC 급락 중: {self._btc_change_1m:.2%}"
        return True, "BTC 안정"

    def _check_volume_spike(self, rvol: float) -> tuple[bool, str]:
        """
        거래량 급증 확인

        Args:
            rvol: 상대 거래량 (1.0 = 평균)

        Returns:
            (통과 여부, 사유)
        """
        if rvol < self.min_rvol:
            return False, f"거래량 부족: RVOL {rvol:.1f} < {self.min_rvol}"
        return True, f"거래량 급증 확인: RVOL {rvol:.1f}"

    def _check_orderbook_support(self, bid_volume: float, ask_volume: float) -> tuple[bool, float, str]:
        """
        호가창 매수벽 확인

        Args:
            bid_volume: 매수호가 총량
            ask_volume: 매도호가 총량

        Returns:
            (통과 여부, bid_ratio, 사유)
        """
        total_volume = bid_volume + ask_volume
        if total_volume <= 0:
            return False, 0.0, "호가창 데이터 없음"

        bid_ratio = bid_volume / total_volume

        if bid_ratio < self.min_bid_ratio:
            return False, bid_ratio, f"매수벽 약함: {bid_ratio:.0%} < {self.min_bid_ratio:.0%}"

        return True, bid_ratio, f"매수벽 확인: {bid_ratio:.0%}"

    def scan_for_dips(self, market_data_list: list[dict]) -> list[DipSignal]:
        """
        급락 종목 스캔 (v2.2: 1분봉/3분봉 선택 지원)

        Args:
            market_data_list: 시장 데이터 리스트
                - symbol: str
                - price: float (현재가)
                - change_1m: float (1분 변화율, 예: -0.03 = -3%)
                - change_3m: float (3분 변화율, 예: -0.03 = -3%)
                - atr_pct: float (ATR/가격, 예: 0.02 = 2%)
                - volume_24h_krw: float (24시간 거래대금)
                - rvol: float (상대 거래량, v2.0)
                - bid_volume: float (매수호가 총량, v2.0)
                - ask_volume: float (매도호가 총량, v2.0)

        Returns:
            list[DipSignal]: 급락 시그널 리스트
        """
        if not self._enabled:
            return []

        signals = []
        current_positions = len(self._active_positions)
        available_slots = self.max_positions - current_positions

        if available_slots <= 0:
            return []

        # 0. v2.1: 장 초반 워밍업 체크 (9:00~9:30 비활성화)
        warmup, warmup_reason = self._is_market_warmup()
        if warmup:
            logger.debug("DipScalper blocked by market warmup", reason=warmup_reason)
            return []

        # 1. BTC 안정성 체크 (전체 적용)
        btc_ok, btc_reason = self._check_btc_stability()
        if not btc_ok:
            logger.debug("DipScalper blocked by BTC", reason=btc_reason)
            self._stats["filtered_by_btc"] += 1
            return []

        for market_data in market_data_list:
            symbol = market_data.get("symbol", "")
            price = market_data.get("price", 0)
            # v2.2: 설정된 캔들 인터벌에 따라 변화율 선택
            change_key = f"change_{self.candle_interval}m"
            candle_change = market_data.get(change_key, 0)
            atr_pct = market_data.get("atr_pct", 0.02)
            volume_24h_krw = market_data.get("volume_24h_krw", 0)
            rvol = market_data.get("rvol", 1.0)
            bid_volume = market_data.get("bid_volume", 0)
            ask_volume = market_data.get("ask_volume", 0)

            # v2.3: 급등주 필터 - 최근 급등한 종목의 급락은 차단
            change_5m = market_data.get("change_5m", 0)
            hot_threshold = getattr(settings, "dip_hot_stock_threshold", 0.07)  # 0.07 (7%)
            if change_5m > hot_threshold:
                logger.debug(
                    "DipScalper blocked by hot stock filter",
                    symbol=symbol,
                    change_5m=f"{change_5m*100:.1f}%",
                    threshold=f"{hot_threshold*100:.0f}%",
                )
                continue

            # 기본 필터
            if price <= 0:
                continue

            # 이미 포지션 있거나 시그널 대기 중이면 스킵 (중복 방지)
            if symbol in self._active_positions:
                continue
            if symbol in self._pending_signals:
                continue

            # 쿨다운 체크
            if not self._check_cooldown(symbol):
                continue

            # 거래대금 필터
            if volume_24h_krw < self.min_volume_krw:
                continue

            # 급락 기준 계산
            threshold = self.calc_drop_threshold(atr_pct)

            # 급락 감지! (v2.2: 설정된 캔들 인터벌 기준)
            if candle_change <= threshold:
                # v2.0: 추가 조건 확인

                # 2. 거래량 급증 확인
                vol_ok, vol_reason = self._check_volume_spike(rvol)
                if not vol_ok:
                    logger.debug(
                        "DipScalper filtered by volume",
                        symbol=symbol,
                        reason=vol_reason,
                    )
                    self._stats["filtered_by_volume"] += 1
                    continue

                # 3. 호가창 매수벽 확인 (v2.1: 차단 안함, 동적 익절에만 사용)
                _, bid_ratio, ob_reason = self._check_orderbook_support(bid_volume, ask_volume)
                # v2.1: 호가창 필터 제거 - 진입은 허용, bid_ratio는 동적 익절에 사용
                if bid_ratio < self.min_bid_ratio:
                    logger.debug(
                        "DipScalper weak orderbook (entering anyway, will use fast TP)",
                        symbol=symbol,
                        bid_ratio=f"{bid_ratio:.0%}",
                    )
                    self._stats["filtered_by_orderbook"] += 1  # 통계만 기록 (차단 안함)

                # 모든 조건 통과! 시그널 생성
                signal = self._create_signal(
                    symbol=symbol,
                    price=price,
                    candle_change=candle_change,
                    atr_pct=atr_pct,
                    threshold=threshold,
                    rvol=rvol,
                    bid_strength=bid_ratio,
                )
                if signal:
                    # 중복 방지: 시그널 생성 즉시 pending에 추가 (race condition 방지)
                    self._pending_signals[symbol] = signal
                    signals.append(signal)
                    self._stats["total_signals"] += 1

                    logger.info(
                        f"DIP DETECTED! (v2.2 {self.candle_interval}m)",
                        symbol=symbol,
                        drop=f"{candle_change:.2%}",
                        threshold=f"{threshold:.2%}",
                        atr=f"{atr_pct:.2%}",
                        rvol=f"{rvol:.1f}",
                        bid_ratio=f"{bid_ratio:.0%}",
                        price=price,
                        fast_tp=bid_ratio < self.min_bid_ratio,
                    )

            if len(signals) >= available_slots:
                break

        return signals

    def _check_cooldown(self, symbol: str) -> bool:
        """쿨다운 체크"""
        last_exit = self._last_exit.get(symbol)
        if not last_exit:
            return True

        elapsed = datetime.utcnow() - last_exit
        return elapsed > timedelta(minutes=self.cooldown_minutes)

    def _create_signal(
        self,
        symbol: str,
        price: float,
        candle_change: float,
        atr_pct: float,
        threshold: float,
        rvol: float = 1.0,
        bid_strength: float = 0.5,
    ) -> Optional[DipSignal]:
        """
        시그널 생성 (v2.3: 임계값 기준 지정가)

        v2.3 변경:
        - 기존: 현재가 - 0.1% → 가격 튀면 미체결
        - 변경: 임계값 가격 (예: -2.4% 레벨)으로 지정가 주문
        - 현재가가 이미 임계값 아래이므로 즉시 체결됨

        예시:
        - 이전 봉 종가: 10,000원
        - 임계값: -2.4%
        - 지정가: 9,760원 (임계값 가격)
        - 현재가: 9,700원 (-3%)
        - 결과: 9,760원으로 주문 → 즉시 체결 (현재가보다 높음)
        """
        # v2.3: 임계값 가격 기준 지정가 계산
        # 이전 봉 종가 역산: prev_close = price / (1 + candle_change)
        # 임계값 가격: threshold_price = prev_close * (1 + threshold)
        # 지정가: 임계값 가격 + 한 호가(0.1%) → 확실히 체결
        if self.use_limit_order and candle_change < 0:
            # 이전 봉 종가 역산
            prev_close = price / (1 + candle_change)
            # 임계값 가격 = 이전 봉 종가 * (1 + threshold)
            # threshold는 음수 (예: -0.024)
            threshold_price = prev_close * (1 + threshold)
            # 한 호가 위로 설정 (0.1%) → 임계값 가격 매물 확실히 체결
            entry_price = threshold_price * (1 + self.limit_offset_pct)

            logger.debug(
                "Dip entry price calculated",
                symbol=symbol,
                current_price=price,
                candle_change=f"{candle_change:.2%}",
                prev_close=prev_close,
                threshold=f"{threshold:.2%}",
                threshold_price=threshold_price,
                entry_price=entry_price,
            )
        else:
            entry_price = price

        # TP/SL 계산
        take_profit = entry_price * (1 + self.take_profit_pct)
        stop_loss = entry_price * (1 + self.stop_loss_pct)

        reason = f"{self.candle_interval}m급락 {candle_change:.1%} (기준: {threshold:.1%}), RVOL {rvol:.1f}, 매수벽 {bid_strength:.0%}"

        return DipSignal(
            symbol=symbol,
            current_price=price,
            drop_pct=candle_change,
            atr_pct=atr_pct,
            threshold_pct=threshold,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            buy_amount_krw=self.buy_amount_krw,
            reason=reason,
            rvol=rvol,
            bid_strength=bid_strength,
        )

    def track_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        bid_ratio: float = 0.5,  # 유지 (호환성)
    ) -> None:
        """포지션 추적 시작 (v2.4: 고정 TP + 스마트 청산)"""
        # v2.4: 고정 TP 사용 (스마트 청산으로 전환)
        take_profit = entry_price * (1 + self.take_profit_pct)
        stop_loss = entry_price * (1 + self.stop_loss_pct)

        self._active_positions[symbol] = DipPosition(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.utcnow(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            highest_price=entry_price,
            bid_ratio=bid_ratio,
            tp_reached_time=None,  # v2.4: 스마트 청산용
            tp_reached_price=0.0,
        )

        self._pending_signals.pop(symbol, None)
        self._stats["total_trades"] += 1

        logger.info(
            "Dip position tracked (v2.4 smart exit)",
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            tp=take_profit,
            tp_pct=f"{self.take_profit_pct:.2%}",
            sl=stop_loss,
            sl_pct=f"{self.stop_loss_pct:.2%}",
        )

    def _get_dynamic_tp(self, bid_ratio: float) -> float:
        """
        v2.1: 호가창 상태에 따른 동적 익절 계산

        bid_ratio 낮음 (< 35%): 매수벽 약함 → 빠른 익절 0.5%
        bid_ratio 중간 (35~50%): 보통 → 기본 익절 0.8%
        bid_ratio 높음 (> 50%): 매수벽 강함 → 넓은 익절 1.0%
        """
        if bid_ratio < 0.35:
            return 0.005  # 0.5% - 빠른 익절
        elif bid_ratio < 0.50:
            return self.take_profit_pct  # 0.8% - 기본값
        else:
            return 0.010  # 1.0% - 넓은 익절

    def should_exit(self, symbol: str, current_price: float) -> Optional[dict]:
        """
        청산 조건 확인 (v2.4 스마트 청산)

        Returns:
            dict: {"action": "FULL", "reason": str, "quantity": float, "exit_type": str}
            None: 청산 조건 미충족
        """
        pos = self._active_positions.get(symbol)
        if not pos:
            return None

        if current_price <= 0 or pos.entry_price <= 0:
            return None

        # 최고가 업데이트
        if current_price > pos.highest_price:
            pos.highest_price = current_price

        pnl_pct = (current_price - pos.entry_price) / pos.entry_price

        # 1. 손절 (-1.2%)
        if current_price <= pos.stop_loss:
            return {
                "action": "FULL",
                "reason": f"Stop loss: {pnl_pct:.2%}",
                "quantity": pos.quantity,
                "exit_type": "stop_loss",
            }

        # 2. TP 도달 체크 (+0.8%)
        if current_price >= pos.take_profit:

            # 첫 TP 도달 시 기록
            if pos.tp_reached_time is None:
                pos.tp_reached_time = datetime.utcnow()
                pos.tp_reached_price = current_price
                logger.info(
                    "TP reached - monitoring for surge/sideways",
                    symbol=symbol,
                    price=current_price,
                    pnl_pct=f"{pnl_pct:.2%}",
                )
                return None  # 아직 매도 안함

            # TP 도달 후 경과 시간
            elapsed_since_tp = datetime.utcnow() - pos.tp_reached_time

            # 2-1. 빠른 급등 감지 (2분 내 +3% 이상)
            if elapsed_since_tp < timedelta(minutes=self.surge_time_limit):

                if pnl_pct >= self.surge_threshold:  # +3% 이상
                    # 트레일링 스톱으로 전환
                    trailing_stop_price = pos.highest_price * (1 - self.trailing_stop_pct)

                    if current_price <= trailing_stop_price:
                        return {
                            "action": "FULL",
                            "reason": f"Trailing stop after surge: {pnl_pct:.2%} (peak: {(pos.highest_price - pos.entry_price) / pos.entry_price:.2%})",
                            "quantity": pos.quantity,
                            "exit_type": "trailing_stop_surge",
                        }

                    logger.debug(
                        "Surge detected - trailing stop active",
                        symbol=symbol,
                        current_pnl=f"{pnl_pct:.2%}",
                        highest_pnl=f"{(pos.highest_price - pos.entry_price) / pos.entry_price:.2%}",
                        trailing_stop=trailing_stop_price,
                    )
                    return None  # 계속 홀드

            # 2-2. 횡보 감지 (TP 도달 후 1~2분간 ±0.2% 이내)
            if timedelta(minutes=self.tp_sideways_check_min) <= elapsed_since_tp <= timedelta(minutes=self.tp_sideways_check_max):
                # 횡보 판단: TP 도달 시 가격 ± 0.2%
                sideways_range = pos.tp_reached_price * self.sideways_threshold
                lower_bound = pos.tp_reached_price - sideways_range
                upper_bound = pos.tp_reached_price + sideways_range

                if lower_bound <= current_price <= upper_bound:
                    # 횡보 확인 → 즉시 매도
                    return {
                        "action": "FULL",
                        "reason": f"TP + sideways (weak momentum): {pnl_pct:.2%}",
                        "quantity": pos.quantity,
                        "exit_type": "take_profit_sideways",
                    }

            # 2-3. TP 도달 후 2분 경과 → 무조건 매도
            if elapsed_since_tp >= timedelta(minutes=self.tp_sideways_check_max):
                return {
                    "action": "FULL",
                    "reason": f"TP + timeout: {pnl_pct:.2%}",
                    "quantity": pos.quantity,
                    "exit_type": "take_profit_timeout",
                }

        # 3. 타임스톱 (10분)
        total_elapsed = datetime.utcnow() - pos.entry_time
        if total_elapsed > timedelta(minutes=self.time_stop_minutes):
            return {
                "action": "FULL",
                "reason": f"Time stop: {total_elapsed.total_seconds()/60:.1f}min, PnL: {pnl_pct:.2%}",
                "quantity": pos.quantity,
                "exit_type": "time_stop",
            }

        return None

    def close_position(
        self,
        symbol: str,
        exit_type: str = "",
        pnl_pct: float = 0.0,
    ) -> None:
        """포지션 청산"""
        pos = self._active_positions.pop(symbol, None)
        if not pos:
            return

        self._last_exit[symbol] = datetime.utcnow()

        # 통계 업데이트
        if pnl_pct >= 0:
            self._stats["winning_trades"] += 1
        else:
            self._stats["losing_trades"] += 1
        self._stats["total_pnl_pct"] += pnl_pct

        logger.info(
            "Dip position closed",
            symbol=symbol,
            pnl_pct=f"{pnl_pct:.2%}",
            exit_type=exit_type,
        )

    def get_position(self, symbol: str) -> Optional[DipPosition]:
        """포지션 조회"""
        return self._active_positions.get(symbol)

    def get_all_positions(self) -> dict[str, DipPosition]:
        """전체 포지션 조회"""
        return self._active_positions.copy()

    def get_status(self) -> dict:
        """전략 상태 조회"""
        total = self._stats["total_trades"]
        win_rate = (self._stats["winning_trades"] / total * 100) if total > 0 else 0
        avg_pnl = (self._stats["total_pnl_pct"] / total * 100) if total > 0 else 0

        return {
            "enabled": self._enabled,
            "settings": {
                "min_drop_pct": f"{self.min_drop_pct:.1%}",
                "max_drop_pct": f"{self.max_drop_pct:.1%}",
                "atr_multiplier": self.atr_multiplier,
                "buy_amount_krw": self.buy_amount_krw,
                "take_profit_pct": f"{self.take_profit_pct:.1%}",
                "stop_loss_pct": f"{self.stop_loss_pct:.1%}",
                "time_stop_minutes": self.time_stop_minutes,
                "max_positions": self.max_positions,
                # v2.0 추가 설정
                "min_rvol": self.min_rvol,
                "btc_crash_threshold": f"{self.btc_crash_threshold:.1%}",
                "min_bid_ratio": f"{self.min_bid_ratio:.0%}",
            },
            "stats": {
                "total_signals": self._stats["total_signals"],
                "total_trades": total,
                "winning_trades": self._stats["winning_trades"],
                "losing_trades": self._stats["losing_trades"],
                "win_rate": f"{win_rate:.1f}%",
                "avg_pnl": f"{avg_pnl:.2f}%",
                # v2.0 필터 통계
                "filtered_by_volume": self._stats["filtered_by_volume"],
                "filtered_by_btc": self._stats["filtered_by_btc"],
                "filtered_by_orderbook": self._stats["filtered_by_orderbook"],
            },
            "btc_status": {
                "change_1m": f"{self._btc_change_1m:.2%}",
                "stable": self._btc_change_1m > self.btc_crash_threshold,
            },
            "active_positions": len(self._active_positions),
            "positions": {
                sym: pos.to_dict() for sym, pos in self._active_positions.items()
            },
        }

    def get_monitoring_data(self) -> dict:
        """대시보드 모니터링용 데이터"""
        return {
            "strategy": "DIP_SCALPER",
            "version": "2.0",
            "enabled": self._enabled,
            "active_positions": len(self._active_positions),
            "max_positions": self.max_positions,
            "settings": {
                "drop_range": f"{self.min_drop_pct:.0%} ~ {self.max_drop_pct:.0%}",
                "atr_mult": f"{self.atr_multiplier}x",
                "tp_sl": f"+{self.take_profit_pct:.1%} / {self.stop_loss_pct:.1%}",
                "time_stop": f"{self.time_stop_minutes}분",
                "buy_amount": f"{self.buy_amount_krw:,.0f}원",
                # v2.0
                "min_rvol": f"{self.min_rvol}x",
                "min_bid_ratio": f"{self.min_bid_ratio:.0%}",
            },
            "btc_status": {
                "change_1m": f"{self._btc_change_1m:.2%}",
                "stable": self._btc_change_1m > self.btc_crash_threshold,
            },
            "positions": {
                sym: pos.to_dict() for sym, pos in self._active_positions.items()
            },
            "stats": {
                "signals": self._stats["total_signals"],
                "trades": self._stats["total_trades"],
                "win_rate": (
                    f"{self._stats['winning_trades'] / self._stats['total_trades'] * 100:.0f}%"
                    if self._stats["total_trades"] > 0
                    else "N/A"
                ),
                "filtered": {
                    "volume": self._stats["filtered_by_volume"],
                    "btc": self._stats["filtered_by_btc"],
                    "orderbook": self._stats["filtered_by_orderbook"],
                },
            },
        }

    # === v2.3: 미체결 주문 추적 ===

    def add_pending_order(
        self,
        symbol: str,
        order_id: str,
        entry_price: float,
        quantity: float,
        signal: DipSignal,
    ) -> None:
        """미체결 주문 등록"""
        self._pending_orders[symbol] = {
            "order_id": order_id,
            "entry_price": entry_price,
            "quantity": quantity,
            "signal": signal,
            "placed_at": datetime.utcnow(),
            "check_count": 0,
        }
        self._stats["pending_orders_placed"] += 1
        logger.info(
            "Pending order added",
            symbol=symbol,
            order_id=order_id,
            entry_price=entry_price,
        )

    def get_pending_orders(self) -> dict[str, dict]:
        """미체결 주문 목록 반환"""
        return self._pending_orders.copy()

    def remove_pending_order(self, symbol: str, reason: str = "filled") -> Optional[dict]:
        """미체결 주문 제거"""
        if symbol in self._pending_orders:
            order = self._pending_orders.pop(symbol)
            if reason == "filled":
                self._stats["pending_orders_filled"] += 1
            else:
                self._stats["pending_orders_cancelled"] += 1
            logger.info(
                "Pending order removed",
                symbol=symbol,
                reason=reason,
            )
            return order
        return None

    def has_pending_order(self, symbol: str) -> bool:
        """미체결 주문 존재 여부"""
        return symbol in self._pending_orders

    # === v2.3: 근접 종목 추적 (1초 모니터링) ===

    def update_near_trigger_symbols(self, market_data_list: list[dict]) -> list[str]:
        """
        급락 기준 근접 종목 업데이트 (proximity >= 75%)

        Returns:
            근접 종목 심볼 리스트 (1초 모니터링 대상)
        """
        if not self._enabled:
            return []

        near_symbols = []
        now = datetime.utcnow()

        for market_data in market_data_list:
            symbol = market_data.get("symbol", "")
            price = market_data.get("price", 0)
            change_key = f"change_{self.candle_interval}m"
            candle_change = market_data.get(change_key, 0)
            atr_pct = market_data.get("atr_pct", 0.02)

            if price <= 0 or candle_change >= 0:
                # 하락 중이 아니면 스킵
                if symbol in self._near_trigger_symbols:
                    del self._near_trigger_symbols[symbol]
                continue

            # 이미 포지션 있거나 미체결 주문 있으면 스킵
            if symbol in self._active_positions or symbol in self._pending_orders:
                continue

            threshold = self.calc_drop_threshold(atr_pct)
            if threshold >= 0:
                continue

            # 근접도 계산 (0~1, 1이면 기준 도달)
            proximity = candle_change / threshold

            # 75% 이상이면 근접 종목으로 추적
            if proximity >= 0.75:
                self._near_trigger_symbols[symbol] = {
                    "price": price,
                    "change": candle_change,
                    "threshold": threshold,
                    "proximity": proximity,
                    "atr_pct": atr_pct,
                    "updated_at": now,
                }
                near_symbols.append(symbol)
            elif symbol in self._near_trigger_symbols:
                # 근접 해제
                del self._near_trigger_symbols[symbol]

        # 오래된 근접 종목 정리 (3분 이상)
        stale_symbols = [
            sym for sym, info in self._near_trigger_symbols.items()
            if (now - info["updated_at"]).total_seconds() > 180
        ]
        for sym in stale_symbols:
            del self._near_trigger_symbols[sym]

        return near_symbols

    def get_near_trigger_symbols(self) -> dict[str, dict]:
        """근접 종목 목록 반환"""
        return self._near_trigger_symbols.copy()


# Singleton
_dip_scalper: Optional[DipScalperStrategy] = None


def get_dip_scalper() -> DipScalperStrategy:
    """DipScalperStrategy 싱글톤"""
    global _dip_scalper
    if _dip_scalper is None:
        _dip_scalper = DipScalperStrategy()
    return _dip_scalper
