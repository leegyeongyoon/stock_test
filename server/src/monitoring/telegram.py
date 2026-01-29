"""Telegram 알림"""

import asyncio
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class TelegramNotifier:
    """
    Telegram 알림

    중요 이벤트 발생 시 Telegram으로 알림 전송
    """

    def __init__(self) -> None:
        self._token = settings.telegram_bot_token.get_secret_value()
        self._chat_id = settings.telegram_chat_id
        self._enabled = bool(self._token and self._chat_id)
        self._rate_limit_seconds = 1.0
        self._last_send_time = 0.0

    @property
    def is_enabled(self) -> bool:
        """알림 활성화 여부"""
        return self._enabled

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """메시지 전송"""
        if not self._enabled:
            logger.debug("Telegram disabled, skipping notification")
            return False

        try:
            # Rate limiting
            import time

            now = time.time()
            if now - self._last_send_time < self._rate_limit_seconds:
                await asyncio.sleep(self._rate_limit_seconds - (now - self._last_send_time))

            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{self._token}/sendMessage",
                    json={
                        "chat_id": self._chat_id,
                        "text": message,
                        "parse_mode": parse_mode,
                    },
                    timeout=10.0,
                )

                self._last_send_time = time.time()

                if response.status_code == 200:
                    return True

                logger.error(
                    "Telegram send failed",
                    status=response.status_code,
                    response=response.text,
                )
                return False

        except Exception as e:
            logger.error("Telegram error", error=str(e))
            return False

    async def notify_mode_change(self, mode: str, reason: str) -> bool:
        """모드 변경 알림"""
        emoji = {"NORMAL": "🟢", "SAFE": "🟡", "HALT": "🔴"}.get(mode, "⚪")

        message = f"""
{emoji} <b>모드 변경: {mode}</b>

사유: {reason}
        """.strip()

        return await self.send(message)

    async def notify_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy: str,
    ) -> bool:
        """주문 알림"""
        emoji = "🟢" if side == "BUY" else "🔴"

        message = f"""
{emoji} <b>주문 체결</b>

심볼: {symbol}
방향: {side}
수량: {quantity:.4f}
가격: ${price:,.2f}
전략: {strategy}
        """.strip()

        return await self.send(message)

    async def notify_risk_event(self, level: str, message: str) -> bool:
        """리스크 이벤트 알림"""
        emoji = {
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "ERROR": "❌",
            "CRITICAL": "🚨",
        }.get(level, "📢")

        text = f"""
{emoji} <b>리스크 이벤트</b>

레벨: {level}
메시지: {message}
        """.strip()

        return await self.send(text)

    async def notify_daily_summary(
        self,
        equity: float,
        pnl: float,
        pnl_pct: float,
        trades: int,
    ) -> bool:
        """일일 요약 알림"""
        emoji = "📈" if pnl >= 0 else "📉"

        message = f"""
{emoji} <b>일일 요약</b>

총 자산: ${equity:,.2f}
일일 PnL: ${pnl:,.2f} ({pnl_pct:+.2%})
거래 횟수: {trades}
        """.strip()

        return await self.send(message)
