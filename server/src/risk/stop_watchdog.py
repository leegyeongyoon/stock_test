"""
Stop Watchdog (v4.2) - 독립 손절 모니터링

목적:
- 메인 엔진과 분리된 독립 프로세스로 손절 모니터링
- 200-500ms 고속 샘플링으로 급락 감지
- Fast Crash Detection (3% drop in 5 seconds)
- WebSocket 우선, REST 폴백으로 가격 데이터 확보

특징:
- 비동기 이벤트 루프 기반
- 포지션별 손절 레벨 관리
- 긴급 시장가 청산 실행
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

import structlog

from src.config import get_settings

if TYPE_CHECKING:
    from src.exchange.upbit import UpbitExchange

logger = structlog.get_logger()


class StopType(str, Enum):
    """손절 유형"""

    HARD_STOP = "HARD_STOP"           # 고정 손절
    TRAILING_STOP = "TRAILING_STOP"   # 트레일링 손절
    TIME_STOP = "TIME_STOP"           # 타임스톱
    FAST_CRASH = "FAST_CRASH"         # 급락 손절


@dataclass
class WatchedPosition:
    """감시 대상 포지션"""

    symbol: str
    entry_price: float
    quantity: float
    stop_price: float
    stop_type: StopType = StopType.HARD_STOP

    # 트레일링 관련
    highest_price: float = 0.0
    trailing_distance: float = 0.0  # ATR 또는 고정 비율

    # 타임스톱
    entry_time: datetime = field(default_factory=datetime.utcnow)
    time_stop_minutes: int = 0

    # 메타데이터
    strategy: str = ""
    position_id: str = ""

    def update_trailing(self, current_price: float) -> bool:
        """
        트레일링 업데이트

        Returns:
            True if trailing stop was triggered
        """
        if self.stop_type != StopType.TRAILING_STOP:
            return False

        if current_price > self.highest_price:
            self.highest_price = current_price
            new_stop = self.highest_price * (1 - self.trailing_distance)
            if new_stop > self.stop_price:
                self.stop_price = new_stop

        return current_price <= self.stop_price


@dataclass
class PriceSnapshot:
    """가격 스냅샷 (Fast Crash 감지용)"""

    price: float
    timestamp: datetime


@dataclass
class StopEvent:
    """손절 이벤트"""

    symbol: str
    position_id: str
    stop_type: StopType
    trigger_price: float
    stop_price: float
    entry_price: float
    quantity: float
    pnl_pct: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "position_id": self.position_id,
            "stop_type": self.stop_type.value,
            "trigger_price": self.trigger_price,
            "stop_price": self.stop_price,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "pnl_pct": round(self.pnl_pct * 100, 2),
            "timestamp": self.timestamp.isoformat(),
        }


class StopWatchdog:
    """
    Stop Watchdog - 독립 손절 모니터링

    메인 트레이딩 루프와 분리된 고속 손절 감시
    """

    def __init__(
        self,
        exchange: Optional["UpbitExchange"] = None,
        on_stop_triggered: Optional[Callable[[StopEvent], None]] = None,
    ) -> None:
        settings = get_settings()

        # 설정
        self.enabled = getattr(settings, "stop_watchdog_enabled", True)
        self.loop_interval_ms = getattr(settings, "stop_watchdog_loop_ms", 300)
        self.ws_timeout_sec = getattr(settings, "stop_watchdog_ws_timeout_sec", 3)
        self.fast_crash_pct = getattr(settings, "stop_watchdog_fast_crash_pct", 0.03)
        self.fast_crash_window_sec = getattr(
            settings, "stop_watchdog_fast_crash_window_sec", 5
        )

        # 거래소 연결
        self._exchange = exchange

        # 콜백
        self._on_stop_triggered = on_stop_triggered

        # 감시 포지션
        self._positions: dict[str, WatchedPosition] = {}  # position_id -> position

        # 가격 히스토리 (Fast Crash 감지용)
        self._price_history: dict[str, deque[PriceSnapshot]] = {}
        self._max_history = 50  # 최대 50개 스냅샷

        # 최신 가격 캐시
        self._last_prices: dict[str, float] = {}  # symbol -> price
        self._price_update_time: dict[str, datetime] = {}

        # 상태
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._stats = {
            "loops": 0,
            "stops_triggered": 0,
            "fast_crashes_detected": 0,
            "ws_fallbacks": 0,
        }

        logger.info(
            "StopWatchdog initialized",
            enabled=self.enabled,
            loop_ms=self.loop_interval_ms,
            fast_crash_pct=f"{self.fast_crash_pct:.1%}",
            fast_crash_window=f"{self.fast_crash_window_sec}s",
        )

    def set_exchange(self, exchange: "UpbitExchange") -> None:
        """거래소 연결 설정"""
        self._exchange = exchange

    def set_callback(
        self,
        on_stop_triggered: Callable[[StopEvent], None],
    ) -> None:
        """손절 콜백 설정"""
        self._on_stop_triggered = on_stop_triggered

    # === Position Management ===

    def add_position(self, position: WatchedPosition) -> None:
        """감시 대상 포지션 추가"""
        self._positions[position.position_id] = position

        # 가격 히스토리 초기화
        if position.symbol not in self._price_history:
            self._price_history[position.symbol] = deque(maxlen=self._max_history)

        logger.info(
            "Position added to watchdog",
            symbol=position.symbol,
            position_id=position.position_id,
            entry=position.entry_price,
            stop=position.stop_price,
            stop_type=position.stop_type.value,
        )

    def remove_position(self, position_id: str) -> Optional[WatchedPosition]:
        """감시 대상 포지션 제거"""
        position = self._positions.pop(position_id, None)
        if position:
            logger.info(
                "Position removed from watchdog",
                symbol=position.symbol,
                position_id=position_id,
            )
        return position

    def update_stop_price(
        self,
        position_id: str,
        new_stop_price: float,
        stop_type: Optional[StopType] = None,
    ) -> bool:
        """손절 가격 업데이트"""
        position = self._positions.get(position_id)
        if not position:
            return False

        old_stop = position.stop_price
        position.stop_price = new_stop_price
        if stop_type:
            position.stop_type = stop_type

        logger.debug(
            "Stop price updated",
            position_id=position_id,
            old_stop=old_stop,
            new_stop=new_stop_price,
        )
        return True

    def get_watched_symbols(self) -> set[str]:
        """감시 중인 심볼 목록"""
        return {p.symbol for p in self._positions.values()}

    # === Price Updates ===

    def update_price(self, symbol: str, price: float) -> None:
        """
        가격 업데이트 (WebSocket에서 호출)

        이 메서드를 통해 외부에서 실시간 가격 데이터를 공급
        """
        now = datetime.utcnow()

        self._last_prices[symbol] = price
        self._price_update_time[symbol] = now

        # 가격 히스토리 추가 (Fast Crash 감지용)
        if symbol in self._price_history:
            self._price_history[symbol].append(PriceSnapshot(price=price, timestamp=now))

    async def _fetch_price_rest(self, symbol: str) -> Optional[float]:
        """REST API로 가격 조회 (폴백)"""
        if not self._exchange:
            return None

        try:
            ticker = await self._exchange.get_ticker(symbol)
            if ticker:
                return ticker.get("trade_price") or ticker.get("last")
        except Exception as e:
            logger.warning("REST price fetch failed", symbol=symbol, error=str(e))

        return None

    async def _get_current_price(self, symbol: str) -> Optional[float]:
        """현재 가격 조회 (WS 우선, REST 폴백)"""
        now = datetime.utcnow()

        # WebSocket 데이터 확인
        if symbol in self._last_prices:
            last_update = self._price_update_time.get(symbol)
            if last_update:
                age = (now - last_update).total_seconds()
                if age <= self.ws_timeout_sec:
                    return self._last_prices[symbol]

        # WebSocket 데이터 없거나 오래됨 → REST 폴백
        self._stats["ws_fallbacks"] += 1
        price = await self._fetch_price_rest(symbol)

        if price:
            self._last_prices[symbol] = price
            self._price_update_time[symbol] = now

        return price

    # === Fast Crash Detection ===

    def _detect_fast_crash(self, symbol: str, current_price: float) -> bool:
        """
        Fast Crash 감지

        지정된 윈도우 내 급락 감지
        """
        history = self._price_history.get(symbol)
        if not history or len(history) < 2:
            return False

        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=self.fast_crash_window_sec)

        # 윈도우 내 최고가 찾기
        window_high = current_price
        for snapshot in history:
            if snapshot.timestamp >= cutoff:
                window_high = max(window_high, snapshot.price)

        # 급락 비율 계산
        if window_high > 0:
            drop_pct = (window_high - current_price) / window_high
            if drop_pct >= self.fast_crash_pct:
                logger.warning(
                    "Fast crash detected",
                    symbol=symbol,
                    window_high=window_high,
                    current=current_price,
                    drop_pct=f"{drop_pct:.2%}",
                )
                self._stats["fast_crashes_detected"] += 1
                return True

        return False

    # === Main Loop ===

    async def start(self) -> None:
        """Watchdog 시작"""
        if not self.enabled:
            logger.info("StopWatchdog disabled, not starting")
            return

        if self._running:
            logger.warning("StopWatchdog already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("StopWatchdog started")

    async def stop(self) -> None:
        """Watchdog 중지"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("StopWatchdog stopped", stats=self._stats)

    async def _run_loop(self) -> None:
        """메인 감시 루프"""
        interval = self.loop_interval_ms / 1000.0

        while self._running:
            try:
                await self._check_all_positions()
                self._stats["loops"] += 1
            except Exception as e:
                logger.error("Watchdog loop error", error=str(e))

            await asyncio.sleep(interval)

    async def _check_all_positions(self) -> None:
        """모든 포지션 체크"""
        if not self._positions:
            return

        now = datetime.utcnow()
        stops_to_trigger: list[tuple[WatchedPosition, float, StopType]] = []

        for position_id, position in list(self._positions.items()):
            current_price = await self._get_current_price(position.symbol)
            if current_price is None:
                continue

            # 1. Fast Crash 체크 (최우선)
            if self._detect_fast_crash(position.symbol, current_price):
                stops_to_trigger.append((position, current_price, StopType.FAST_CRASH))
                continue

            # 2. Hard Stop 체크
            if current_price <= position.stop_price:
                stops_to_trigger.append(
                    (position, current_price, position.stop_type)
                )
                continue

            # 3. Trailing Stop 업데이트
            if position.stop_type == StopType.TRAILING_STOP:
                if position.update_trailing(current_price):
                    stops_to_trigger.append(
                        (position, current_price, StopType.TRAILING_STOP)
                    )
                    continue

            # 4. Time Stop 체크
            if position.time_stop_minutes > 0:
                elapsed = (now - position.entry_time).total_seconds() / 60
                if elapsed >= position.time_stop_minutes:
                    # 타임스톱은 손실 중일 때만 발동
                    pnl_pct = (current_price - position.entry_price) / position.entry_price
                    if pnl_pct < 0:
                        stops_to_trigger.append(
                            (position, current_price, StopType.TIME_STOP)
                        )
                        continue

        # 손절 실행
        for position, trigger_price, stop_type in stops_to_trigger:
            await self._execute_stop(position, trigger_price, stop_type)

    async def _execute_stop(
        self,
        position: WatchedPosition,
        trigger_price: float,
        stop_type: StopType,
    ) -> None:
        """손절 실행"""
        pnl_pct = (trigger_price - position.entry_price) / position.entry_price

        event = StopEvent(
            symbol=position.symbol,
            position_id=position.position_id,
            stop_type=stop_type,
            trigger_price=trigger_price,
            stop_price=position.stop_price,
            entry_price=position.entry_price,
            quantity=position.quantity,
            pnl_pct=pnl_pct,
        )

        logger.warning(
            "STOP TRIGGERED",
            symbol=position.symbol,
            stop_type=stop_type.value,
            trigger_price=trigger_price,
            stop_price=position.stop_price,
            pnl_pct=f"{pnl_pct:.2%}",
        )

        # 포지션 제거
        self.remove_position(position.position_id)

        # 콜백 호출 (시장가 청산 등)
        if self._on_stop_triggered:
            try:
                self._on_stop_triggered(event)
            except Exception as e:
                logger.error("Stop callback failed", error=str(e))

        self._stats["stops_triggered"] += 1

    # === Status ===

    def get_status(self) -> dict:
        """상태 조회"""
        positions_info = []
        for pos in self._positions.values():
            positions_info.append({
                "symbol": pos.symbol,
                "position_id": pos.position_id,
                "entry_price": pos.entry_price,
                "stop_price": pos.stop_price,
                "stop_type": pos.stop_type.value,
                "highest_price": pos.highest_price,
            })

        return {
            "enabled": self.enabled,
            "running": self._running,
            "loop_interval_ms": self.loop_interval_ms,
            "fast_crash_pct": f"{self.fast_crash_pct:.1%}",
            "fast_crash_window_sec": self.fast_crash_window_sec,
            "positions_count": len(self._positions),
            "positions": positions_info,
            "stats": self._stats,
        }


# 싱글톤 인스턴스
_stop_watchdog: Optional[StopWatchdog] = None


def get_stop_watchdog() -> StopWatchdog:
    """Stop Watchdog 싱글톤"""
    global _stop_watchdog
    if _stop_watchdog is None:
        _stop_watchdog = StopWatchdog()
    return _stop_watchdog


def create_stop_watchdog(
    exchange: Optional["UpbitExchange"] = None,
    on_stop_triggered: Optional[Callable[[StopEvent], None]] = None,
) -> StopWatchdog:
    """새 Stop Watchdog 인스턴스 생성"""
    global _stop_watchdog
    _stop_watchdog = StopWatchdog(
        exchange=exchange,
        on_stop_triggered=on_stop_triggered,
    )
    return _stop_watchdog
