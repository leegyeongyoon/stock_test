"""Risk Engine - 최상위 권한 리스크 관리 (강화 버전)"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import structlog

from src.config import get_settings
from src.models.schemas import TradingMode
from src.risk.liquidity_filter import LiquidityFilter, get_liquidity_filter
from src.risk.modes import ModeManager

if TYPE_CHECKING:
    from src.features.feature_engine import FeatureEngine
    from src.portfolio.ledger import Ledger

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class OrderEvent:
    """주문 이벤트 (실패율 추적용)"""

    timestamp: datetime
    success: bool


@dataclass
class WSLatencyEvent:
    """WS 지연 이벤트"""

    timestamp: datetime
    latency_seconds: float


@dataclass
class ExposureInfo:
    """노출 정보"""

    gross_exposure: float = 0  # 총 노출
    equity: float = 0  # 자기자본
    gross_exposure_ratio: float = 0  # gross_exposure / equity
    symbol_exposures: dict = field(default_factory=dict)  # 심볼별 노출


class RiskEngine:
    """
    Risk Engine - 최상위 권한 (MDD 5% 강화)

    책임:
    1. 모드 관리 (NORMAL/SAFE/HALT)
    2. 리스크 조건 모니터링
    3. 긴급 상황 대응
    4. 노출 제한 관리
    5. 유동성 필터

    SAFE 트리거:
    - WS 데이터 지연 > 2초가 10초 이상 지속
    - 최근 60초 주문 실패율 > 20%
    - 한쪽 체결 후 헤지 실패
    - 일손실 -1.0% (강화됨)
    - BTC 1h ATR% 급증(1.8배) + 급락 동시

    HALT 트리거:
    - Reconcile 실패 (drift 임계 초과)
    - 인증 오류 반복 (3회)
    - 일손실 -1.5% (강화됨)
    - liquidation_distance < 2.5%
    """

    def __init__(
        self,
        ledger: Optional["Ledger"] = None,
        feature_engine: Optional["FeatureEngine"] = None,
    ) -> None:
        self.mode_manager = ModeManager()
        self.ledger = ledger
        self.feature_engine = feature_engine
        self.liquidity_filter = get_liquidity_filter()

        # 모니터링 상태
        self._ws_last_message: Optional[datetime] = None
        self._ws_disconnect_count: int = 0
        self._auth_failure_count: int = 0
        self._last_reconcile_success: Optional[datetime] = None
        self._reconcile_failure_count: int = 0

        # 60초 슬라이딩 윈도우 주문 이벤트
        self._order_events: deque[OrderEvent] = deque(maxlen=1000)

        # WS 지연 이벤트 (10초 윈도우)
        self._ws_latency_events: deque[WSLatencyEvent] = deque(maxlen=100)
        self._ws_high_latency_start: Optional[datetime] = None

        # 노출 정보
        self._exposure_info: ExposureInfo = ExposureInfo()

        # Satellite 비활성화 플래그 (주간 손실)
        self._satellite_disabled_by_weekly_loss: bool = False

        # 설정값 (MDD 5% 강화)
        # 새로운 일손실 제한: -1.0% SAFE, -1.5% HALT
        self._daily_loss_limit_safe = settings.daily_loss_limit_safe  # -0.01
        self._daily_loss_limit_halt = settings.daily_loss_limit_halt  # -0.015
        self._weekly_loss_limit = settings.weekly_loss_limit
        self._max_order_failure_rate = 0.20  # 20%
        self._ws_latency_threshold = 2.0  # 2초
        self._ws_latency_duration = 10.0  # 10초 지속
        self._ws_timeout_seconds = 30
        self._max_ws_disconnect = 3
        self._max_auth_failures = 3
        self._max_reconcile_failures = 3
        self._min_liquidation_distance = settings.min_liquidation_distance
        self._max_gross_exposure = settings.max_gross_exposure

        logger.info(
            "Risk Engine initialized with MDD 5% limits",
            daily_loss_safe=f"{self._daily_loss_limit_safe:.1%}",
            daily_loss_halt=f"{self._daily_loss_limit_halt:.1%}",
        )

        # 모니터링 태스크
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def mode(self) -> TradingMode:
        """현재 모드"""
        return self.mode_manager.mode

    @property
    def can_open_position(self) -> bool:
        """신규 포지션 오픈 가능 여부"""
        return self.mode_manager.can_open_new_position

    @property
    def satellite_enabled(self) -> bool:
        """v3 전략 활성화 여부"""
        if self._satellite_disabled_by_weekly_loss:
            return False
        return settings.v3_enabled

    def set_feature_engine(self, feature_engine: "FeatureEngine") -> None:
        """Feature Engine 설정 (나중에 주입)"""
        self.feature_engine = feature_engine

    async def start(self) -> None:
        """모니터링 시작"""
        if self._running:
            return

        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Risk Engine started")

    async def stop(self) -> None:
        """모니터링 중지"""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Risk Engine stopped")

    async def _monitor_loop(self) -> None:
        """주기적 리스크 모니터링"""
        while self._running:
            try:
                await self._check_all_conditions()
                await asyncio.sleep(1)  # 1초 주기
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Risk monitor error", error=str(e))
                await asyncio.sleep(5)

    async def _check_all_conditions(self) -> None:
        """모든 리스크 조건 체크"""
        # HALT 조건 먼저 체크 (더 심각)
        await self._check_halt_conditions()

        # 이미 HALT면 SAFE 체크 불필요
        if self.mode_manager.is_halt:
            return

        # SAFE 조건 체크
        await self._check_safe_conditions()

        # Kill Switch 체크
        await self._check_kill_switch()

    async def _check_halt_conditions(self) -> None:
        """HALT 트리거 조건 체크"""
        # 1. 일손실 -3% 이상
        if self.ledger:
            daily_pnl_pct = await self._get_daily_pnl_pct()
            if daily_pnl_pct <= self._daily_loss_limit_halt:
                await self.mode_manager.to_halt(
                    f"Daily loss limit exceeded: {daily_pnl_pct:.2%}"
                )
                return

        # 2. Reconcile 연속 실패
        if self._reconcile_failure_count >= self._max_reconcile_failures:
            await self.mode_manager.to_halt(
                f"Reconcile failures: {self._reconcile_failure_count}"
            )
            return

        # 3. 인증 오류 반복
        if self._auth_failure_count >= self._max_auth_failures:
            await self.mode_manager.to_halt(
                f"Auth failures: {self._auth_failure_count}"
            )
            return

        # 4. 청산 거리 임계값 미만
        liquidation_distance = await self._get_min_liquidation_distance()
        if liquidation_distance is not None:
            if liquidation_distance < self._min_liquidation_distance:
                await self.mode_manager.to_halt(
                    f"Liquidation distance critical: {liquidation_distance:.2%}"
                )
                return

    async def _check_safe_conditions(self) -> None:
        """SAFE 트리거 조건 체크"""
        # 1. 일손실 -1.5% 이상
        if self.ledger:
            daily_pnl_pct = await self._get_daily_pnl_pct()
            if daily_pnl_pct <= self._daily_loss_limit_safe:
                await self.mode_manager.to_safe(
                    f"Daily loss warning: {daily_pnl_pct:.2%}"
                )
                return

        # 2. WS 연결 끊김 지속
        if self._ws_disconnect_count >= self._max_ws_disconnect:
            await self.mode_manager.to_safe(
                f"WS disconnects: {self._ws_disconnect_count}"
            )
            return

        # 3. WS 메시지 타임아웃
        if self._ws_last_message:
            elapsed = (datetime.utcnow() - self._ws_last_message).total_seconds()
            if elapsed > self._ws_timeout_seconds:
                await self.mode_manager.to_safe(
                    f"WS timeout: {elapsed:.0f}s since last message"
                )
                return

        # 4. WS 지연 > 2초가 10초 이상 지속
        if self._check_ws_high_latency():
            await self.mode_manager.to_safe(
                "WS high latency persisted > 10s"
            )
            return

        # 5. 최근 60초 주문 실패율 > 20%
        failure_rate = self._get_recent_order_failure_rate()
        if failure_rate >= self._max_order_failure_rate:
            await self.mode_manager.to_safe(
                f"Order failure rate: {failure_rate:.1%}"
            )
            return

        # 6. BTC ATR 급증 체크
        if self.feature_engine:
            btc_regime = self.feature_engine.check_btc_regime()
            if btc_regime.get("is_volatile", False):
                await self.mode_manager.to_safe(
                    f"BTC volatility spike: ATR ratio {btc_regime.get('atr_ratio', 0):.2f}x"
                )
                return

        # 7. 노출 제한 초과
        if self._exposure_info.gross_exposure_ratio > self._max_gross_exposure:
            await self.mode_manager.to_safe(
                f"Gross exposure exceeded: {self._exposure_info.gross_exposure_ratio:.2f}x"
            )
            return

    async def _check_kill_switch(self) -> None:
        """Kill Switch 체크"""
        if not self.ledger:
            return

        # 주간 손실 -5% → Satellite 비활성화
        weekly_pnl_pct = await self._get_weekly_pnl_pct()
        if weekly_pnl_pct <= self._weekly_loss_limit:
            if not self._satellite_disabled_by_weekly_loss:
                self._satellite_disabled_by_weekly_loss = True
                logger.warning(
                    "Satellite disabled due to weekly loss",
                    weekly_pnl_pct=f"{weekly_pnl_pct:.2%}",
                )

    def _check_ws_high_latency(self) -> bool:
        """WS 고지연 지속 체크"""
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=10)

        # 최근 10초간 지연 이벤트 필터링
        recent_events = [
            e for e in self._ws_latency_events
            if e.timestamp > cutoff
        ]

        if not recent_events:
            self._ws_high_latency_start = None
            return False

        # 모든 이벤트가 2초 이상 지연인 경우
        all_high_latency = all(
            e.latency_seconds > self._ws_latency_threshold
            for e in recent_events
        )

        if not all_high_latency:
            self._ws_high_latency_start = None
            return False

        # 고지연 시작 시점 기록
        if self._ws_high_latency_start is None:
            self._ws_high_latency_start = recent_events[0].timestamp

        # 10초 이상 지속 체크
        duration = (now - self._ws_high_latency_start).total_seconds()
        return duration >= self._ws_latency_duration

    def _get_recent_order_failure_rate(self) -> float:
        """최근 60초 주문 실패율"""
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=60)

        recent_events = [
            e for e in self._order_events
            if e.timestamp > cutoff
        ]

        if len(recent_events) < 5:  # 최소 5건 이상
            return 0.0

        failures = sum(1 for e in recent_events if not e.success)
        return failures / len(recent_events)

    async def _get_daily_pnl_pct(self) -> float:
        """오늘 PnL 퍼센트 조회"""
        if not self.ledger:
            return 0.0
        # Ledger에서 실제 PnL 조회
        try:
            summary = self.ledger.get_summary()
            return summary.get("pnl_today_pct", 0.0)
        except Exception:
            return 0.0

    async def _get_weekly_pnl_pct(self) -> float:
        """이번 주 PnL 퍼센트 조회"""
        if not self.ledger:
            return 0.0
        try:
            summary = self.ledger.get_summary()
            return summary.get("pnl_week_pct", 0.0)
        except Exception:
            return 0.0

    async def _get_min_liquidation_distance(self) -> Optional[float]:
        """최소 청산 거리 조회"""
        # TODO: 실제 포지션에서 청산가 대비 현재가 거리 계산
        return None

    # === 노출 관리 ===

    def update_exposure(
        self,
        equity: float,
        gross_exposure: float,
        symbol_exposures: dict,
    ) -> None:
        """노출 정보 업데이트"""
        self._exposure_info = ExposureInfo(
            gross_exposure=gross_exposure,
            equity=equity,
            gross_exposure_ratio=gross_exposure / equity if equity > 0 else 0,
            symbol_exposures=symbol_exposures,
        )

    def check_exposure_limit(
        self,
        symbol: str,
        additional_exposure_usd: float,
        strategy: str = "core",
    ) -> tuple[bool, str]:
        """
        노출 제한 체크

        Args:
            symbol: 심볼
            additional_exposure_usd: 추가 노출 금액
            strategy: "core" 또는 "satellite"

        Returns:
            (통과 여부, 사유)
        """
        equity = self._exposure_info.equity
        if equity <= 0:
            return False, "Equity not available"

        # 1. 총 노출 제한
        new_gross = self._exposure_info.gross_exposure + additional_exposure_usd
        new_ratio = new_gross / equity

        if new_ratio > self._max_gross_exposure:
            return False, f"Gross exposure would exceed {self._max_gross_exposure}x"

        # 2. 심볼별 노출 제한
        max_symbol_exposure = (
            settings.max_symbol_exposure_core
            if strategy == "core"
            else settings.max_symbol_exposure_sat
        )

        current_symbol_exposure = self._exposure_info.symbol_exposures.get(symbol, 0)
        new_symbol_exposure = current_symbol_exposure + additional_exposure_usd
        symbol_exposure_ratio = new_symbol_exposure / equity

        if symbol_exposure_ratio > max_symbol_exposure:
            return False, f"Symbol exposure would exceed {max_symbol_exposure:.0%}"

        return True, "OK"

    # === 이벤트 수신 메서드 ===

    def on_ws_message(self, latency_seconds: float = 0) -> None:
        """WebSocket 메시지 수신 시 호출"""
        now = datetime.utcnow()
        self._ws_last_message = now
        self._ws_disconnect_count = 0

        # 지연 이벤트 기록
        if latency_seconds > 0:
            self._ws_latency_events.append(
                WSLatencyEvent(timestamp=now, latency_seconds=latency_seconds)
            )

    def on_ws_disconnect(self) -> None:
        """WebSocket 연결 끊김 시 호출"""
        self._ws_disconnect_count += 1
        logger.warning(
            "WS disconnected",
            count=self._ws_disconnect_count,
        )

    def on_ws_reconnect(self) -> None:
        """WebSocket 재연결 시 호출"""
        self._ws_disconnect_count = 0
        self._ws_high_latency_start = None
        logger.info("WS reconnected")

    def on_order_success(self) -> None:
        """주문 성공 시 호출"""
        self._order_events.append(
            OrderEvent(timestamp=datetime.utcnow(), success=True)
        )

    def on_order_failure(self) -> None:
        """주문 실패 시 호출"""
        self._order_events.append(
            OrderEvent(timestamp=datetime.utcnow(), success=False)
        )
        logger.warning(
            "Order failed",
            recent_failure_rate=f"{self._get_recent_order_failure_rate():.1%}",
        )

    def on_auth_failure(self) -> None:
        """인증 실패 시 호출"""
        self._auth_failure_count += 1
        logger.error(
            "Auth failed",
            count=self._auth_failure_count,
        )

    def on_reconcile_success(self) -> None:
        """Reconcile 성공 시 호출"""
        self._last_reconcile_success = datetime.utcnow()
        self._reconcile_failure_count = 0

    def on_reconcile_failure(self, drift: float) -> None:
        """Reconcile 실패 시 호출"""
        self._reconcile_failure_count += 1
        logger.error(
            "Reconcile failed",
            drift=drift,
            count=self._reconcile_failure_count,
        )

    async def on_hedge_failure(self, symbol: str, side: str) -> None:
        """한쪽 체결 후 헤지 실패 시 호출"""
        logger.critical(
            "Hedge failure detected",
            symbol=symbol,
            side=side,
        )
        await self.mode_manager.to_safe(
            f"Hedge failure: {symbol} {side}"
        )

    # === 수동 제어 메서드 ===

    async def pause(self, reason: str = "Manual pause") -> bool:
        """SAFE 모드로 전환"""
        return await self.mode_manager.to_safe(reason)

    async def resume(self, reason: str = "Manual resume") -> bool:
        """NORMAL 모드로 복귀"""
        # 복구 전 조건 체크
        if self._reconcile_failure_count > 0:
            logger.warning("Cannot resume: reconcile failures exist")
            return False

        if self._auth_failure_count > 0:
            logger.warning("Cannot resume: auth failures exist")
            return False

        return await self.mode_manager.to_normal(reason)

    async def safe_resume_from_halt(self, reason: str = "Manual safe resume") -> bool:
        """HALT에서 SAFE로 전환"""
        # 카운터 리셋
        self._auth_failure_count = 0
        self._reconcile_failure_count = 0
        return await self.mode_manager.safe_to_normal_from_halt(reason)

    def reset_counters(self) -> None:
        """모든 카운터 리셋"""
        self._ws_disconnect_count = 0
        self._order_events.clear()
        self._ws_latency_events.clear()
        self._ws_high_latency_start = None
        self._auth_failure_count = 0
        self._reconcile_failure_count = 0
        self._satellite_disabled_by_weekly_loss = False
        logger.info("Risk counters reset")

    def enable_satellite(self) -> None:
        """Satellite 전략 수동 활성화 (주간 손실 리셋)"""
        self._satellite_disabled_by_weekly_loss = False
        logger.info("Satellite re-enabled manually")

    def get_status(self) -> dict:
        """현재 상태 조회"""
        return {
            **self.mode_manager.get_state(),
            "ws_last_message": (
                self._ws_last_message.isoformat() if self._ws_last_message else None
            ),
            "ws_disconnect_count": self._ws_disconnect_count,
            "recent_order_failure_rate": f"{self._get_recent_order_failure_rate():.1%}",
            "auth_failure_count": self._auth_failure_count,
            "reconcile_failure_count": self._reconcile_failure_count,
            "last_reconcile_success": (
                self._last_reconcile_success.isoformat()
                if self._last_reconcile_success
                else None
            ),
            "satellite_enabled": self.satellite_enabled,
            "satellite_disabled_by_weekly_loss": self._satellite_disabled_by_weekly_loss,
            "exposure": {
                "gross_exposure": self._exposure_info.gross_exposure,
                "equity": self._exposure_info.equity,
                "gross_exposure_ratio": self._exposure_info.gross_exposure_ratio,
            },
        }
