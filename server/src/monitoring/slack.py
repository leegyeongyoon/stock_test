"""Slack 알림 - 실시간 리스크 알림 및 일일 리포트"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

import httpx
import structlog

from src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class AlertLevel(str, Enum):
    """알림 레벨"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class SlackMessage:
    """Slack 메시지"""

    text: str
    level: AlertLevel = AlertLevel.INFO
    blocks: Optional[list] = None


class SlackNotifier:
    """
    Slack 알림

    즉시 알림:
    - SAFE/HALT 모드 전환
    - 한쪽 체결 사건
    - 일 손실 -1.5% / -3.0% 도달
    - WS 끊김 10초 이상
    - 청산 위험 (liquidation_distance < 5%)

    일일 리포트:
    - 오늘 최대 노출
    - 최대 드로우다운
    - 슬리피지 TOP3
    - 위험 이벤트 요약
    - Core/Satellite 별 PnL
    """

    def __init__(self) -> None:
        self._webhook_url = settings.slack_webhook_url
        self._channel = settings.slack_channel
        self._enabled = bool(self._webhook_url)

        # Rate limiting
        self._rate_limit_seconds = 1.0
        self._last_send_time = 0.0

        # 메시지 큐 (중복 방지)
        self._recent_messages: dict[str, float] = {}
        self._dedup_window_seconds = 60.0

        # 일일 리포트용 통계
        self._daily_stats = {
            "max_exposure": 0.0,
            "max_drawdown": 0.0,
            "risk_events": [],
            "core_pnl": 0.0,
            "satellite_pnl": 0.0,
            "trades_count": 0,
        }

    @property
    def is_enabled(self) -> bool:
        """알림 활성화 여부"""
        return self._enabled

    def _get_level_emoji(self, level: AlertLevel) -> str:
        """레벨별 이모지"""
        return {
            AlertLevel.INFO: ":information_source:",
            AlertLevel.WARNING: ":warning:",
            AlertLevel.ERROR: ":x:",
            AlertLevel.CRITICAL: ":rotating_light:",
        }.get(level, ":speech_balloon:")

    def _get_level_color(self, level: AlertLevel) -> str:
        """레벨별 색상"""
        return {
            AlertLevel.INFO: "#36a64f",
            AlertLevel.WARNING: "#ffcc00",
            AlertLevel.ERROR: "#ff6600",
            AlertLevel.CRITICAL: "#ff0000",
        }.get(level, "#808080")

    def _should_send(self, message_key: str) -> bool:
        """중복 메시지 필터링"""
        import time

        now = time.time()

        # 오래된 메시지 정리
        expired = [
            k
            for k, t in self._recent_messages.items()
            if now - t > self._dedup_window_seconds
        ]
        for k in expired:
            self._recent_messages.pop(k, None)

        # 중복 체크
        if message_key in self._recent_messages:
            return False

        self._recent_messages[message_key] = now
        return True

    async def send(self, message: SlackMessage) -> bool:
        """메시지 전송"""
        if not self._enabled:
            logger.debug("Slack disabled, skipping notification")
            return False

        try:
            import time

            # Rate limiting
            now = time.time()
            if now - self._last_send_time < self._rate_limit_seconds:
                await asyncio.sleep(
                    self._rate_limit_seconds - (now - self._last_send_time)
                )

            # 메시지 구성
            payload = {
                "channel": self._channel,
                "text": message.text,
                "attachments": [
                    {
                        "color": self._get_level_color(message.level),
                        "text": message.text,
                    }
                ],
            }

            if message.blocks:
                payload["blocks"] = message.blocks

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._webhook_url,
                    json=payload,
                    timeout=10.0,
                )

                self._last_send_time = time.time()

                if response.status_code == 200:
                    return True

                logger.error(
                    "Slack send failed",
                    status=response.status_code,
                    response=response.text,
                )
                return False

        except Exception as e:
            logger.error("Slack error", error=str(e))
            return False

    async def notify_mode_change(
        self, new_mode: str, old_mode: str, reason: str
    ) -> bool:
        """모드 변경 알림 (SAFE/HALT 전환)"""
        message_key = f"mode_change:{new_mode}"
        if not self._should_send(message_key):
            return False

        level = (
            AlertLevel.CRITICAL
            if new_mode == "HALT"
            else AlertLevel.WARNING
            if new_mode == "SAFE"
            else AlertLevel.INFO
        )

        mode_emoji = {
            "NORMAL": ":white_check_mark:",
            "SAFE": ":yellow_circle:",
            "HALT": ":red_circle:",
        }.get(new_mode, ":grey_question:")

        text = f"""
{mode_emoji} *모드 변경: {old_mode} -> {new_mode}*
> 사유: {reason}
> 시각: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
        """.strip()

        # 일일 통계에 기록
        self._daily_stats["risk_events"].append(
            {
                "type": "MODE_CHANGE",
                "mode": new_mode,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        return await self.send(SlackMessage(text=text, level=level))

    async def notify_hedge_failure(
        self,
        symbol: str,
        spot_filled: float,
        perp_status: str,
        recovery_attempts: int,
        recovered: bool,
    ) -> bool:
        """한쪽 체결 알림"""
        message_key = f"hedge_failure:{symbol}"
        if not self._should_send(message_key):
            return False

        level = AlertLevel.WARNING if recovered else AlertLevel.CRITICAL
        status_emoji = (
            ":white_check_mark: 복구 성공"
            if recovered
            else ":x: 복구 실패"
        )

        text = f"""
:rotating_light: *헤지 실패 발생*
> 심볼: `{symbol}`
> Spot 체결량: {spot_filled:.4f}
> Perp 상태: {perp_status}
> 복구 시도: {recovery_attempts}회
> 결과: {status_emoji}
        """.strip()

        self._daily_stats["risk_events"].append(
            {
                "type": "HEDGE_FAILURE",
                "symbol": symbol,
                "recovered": recovered,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        return await self.send(SlackMessage(text=text, level=level))

    async def notify_daily_loss(
        self,
        loss_pct: float,
        threshold: str,
        action_taken: str,
    ) -> bool:
        """일 손실 한도 도달 알림"""
        message_key = f"daily_loss:{threshold}"
        if not self._should_send(message_key):
            return False

        level = (
            AlertLevel.CRITICAL
            if threshold == "-3.0%"
            else AlertLevel.WARNING
        )

        text = f"""
:chart_with_downwards_trend: *일 손실 한도 도달*
> 현재 손실: {loss_pct:.2%}
> 한도: {threshold}
> 조치: {action_taken}
        """.strip()

        self._daily_stats["risk_events"].append(
            {
                "type": "DAILY_LOSS",
                "loss_pct": loss_pct,
                "threshold": threshold,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        return await self.send(SlackMessage(text=text, level=level))

    async def notify_ws_disconnection(
        self,
        exchange: str,
        duration_sec: float,
        reconnected: bool,
    ) -> bool:
        """WS 연결 끊김 알림"""
        if duration_sec < 10:
            return False

        message_key = f"ws_disconnect:{exchange}"
        if not self._should_send(message_key):
            return False

        level = AlertLevel.WARNING if reconnected else AlertLevel.ERROR
        status = ":white_check_mark: 재연결됨" if reconnected else ":x: 연결 중"

        text = f"""
:signal_strength: *WebSocket 연결 끊김*
> 거래소: {exchange}
> 끊김 시간: {duration_sec:.1f}초
> 상태: {status}
        """.strip()

        self._daily_stats["risk_events"].append(
            {
                "type": "WS_DISCONNECT",
                "exchange": exchange,
                "duration_sec": duration_sec,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        return await self.send(SlackMessage(text=text, level=level))

    async def notify_liquidation_risk(
        self,
        symbol: str,
        current_price: float,
        liquidation_price: float,
        distance_pct: float,
    ) -> bool:
        """청산 위험 알림"""
        if distance_pct > 5.0:
            return False

        message_key = f"liquidation_risk:{symbol}"
        if not self._should_send(message_key):
            return False

        level = AlertLevel.CRITICAL if distance_pct < 2.5 else AlertLevel.ERROR

        text = f"""
:skull: *청산 위험 경고*
> 심볼: `{symbol}`
> 현재가: ${current_price:,.2f}
> 청산가: ${liquidation_price:,.2f}
> 거리: {distance_pct:.2f}%
        """.strip()

        self._daily_stats["risk_events"].append(
            {
                "type": "LIQUIDATION_RISK",
                "symbol": symbol,
                "distance_pct": distance_pct,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        return await self.send(SlackMessage(text=text, level=level))

    async def notify_order_filled(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy: str,
        slippage_bps: float = 0.0,
    ) -> bool:
        """주문 체결 알림 (선택적)"""
        side_emoji = ":green_circle:" if side == "BUY" else ":red_circle:"

        text = f"""
{side_emoji} *주문 체결*
> 심볼: `{symbol}`
> 방향: {side}
> 수량: {quantity:.4f}
> 가격: ${price:,.2f}
> 전략: {strategy}
> 슬리피지: {slippage_bps:.1f}bps
        """.strip()

        self._daily_stats["trades_count"] += 1

        return await self.send(SlackMessage(text=text, level=AlertLevel.INFO))

    async def send_daily_report(
        self,
        equity: float,
        pnl: float,
        pnl_pct: float,
        max_exposure: float,
        max_drawdown: float,
        slippage_top3: list[tuple[str, float]],
        core_pnl: float,
        satellite_pnl: float,
    ) -> bool:
        """일일 리포트 전송"""
        pnl_emoji = ":chart_with_upwards_trend:" if pnl >= 0 else ":chart_with_downwards_trend:"

        # 슬리피지 TOP3 포맷
        slippage_text = "\n".join(
            f"  {i+1}. `{sym}`: {slip:.2f}bps"
            for i, (sym, slip) in enumerate(slippage_top3)
        ) if slippage_top3 else "  (없음)"

        # 위험 이벤트 요약
        events = self._daily_stats.get("risk_events", [])
        event_summary = {}
        for event in events:
            etype = event.get("type", "UNKNOWN")
            event_summary[etype] = event_summary.get(etype, 0) + 1

        events_text = "\n".join(
            f"  - {etype}: {count}건"
            for etype, count in event_summary.items()
        ) if event_summary else "  (없음)"

        text = f"""
:calendar: *일일 리포트* - {datetime.utcnow().strftime('%Y-%m-%d')}

{pnl_emoji} *수익 현황*
> 총 자산: ${equity:,.2f}
> 일일 PnL: ${pnl:,.2f} ({pnl_pct:+.2%})
> Core PnL: ${core_pnl:,.2f}
> Satellite PnL: ${satellite_pnl:,.2f}

:bar_chart: *노출 & 리스크*
> 최대 노출: {max_exposure:.1%}
> 최대 드로우다운: {max_drawdown:.2%}

:receipt: *슬리피지 TOP3*
{slippage_text}

:warning: *위험 이벤트 요약*
{events_text}

> 총 거래 횟수: {self._daily_stats.get('trades_count', 0)}건
        """.strip()

        # 일일 통계 리셋
        self._reset_daily_stats()

        return await self.send(SlackMessage(text=text, level=AlertLevel.INFO))

    def _reset_daily_stats(self) -> None:
        """일일 통계 리셋"""
        self._daily_stats = {
            "max_exposure": 0.0,
            "max_drawdown": 0.0,
            "risk_events": [],
            "core_pnl": 0.0,
            "satellite_pnl": 0.0,
            "trades_count": 0,
        }

    def update_daily_exposure(self, exposure: float) -> None:
        """일일 최대 노출 업데이트"""
        if exposure > self._daily_stats["max_exposure"]:
            self._daily_stats["max_exposure"] = exposure

    def update_daily_drawdown(self, drawdown: float) -> None:
        """일일 최대 드로우다운 업데이트"""
        if drawdown > self._daily_stats["max_drawdown"]:
            self._daily_stats["max_drawdown"] = drawdown

    def update_strategy_pnl(
        self, strategy: str, pnl: float
    ) -> None:
        """전략별 PnL 업데이트"""
        if strategy == "CORE":
            self._daily_stats["core_pnl"] = pnl
        elif strategy == "SATELLITE":
            self._daily_stats["satellite_pnl"] = pnl
