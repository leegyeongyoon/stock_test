"""트레이딩 모드 관리 - NORMAL/SAFE/HALT"""

import asyncio
from datetime import datetime
from typing import Optional

import structlog

from src.models.schemas import TradingMode

logger = structlog.get_logger()


class ModeManager:
    """
    트레이딩 모드 상태 머신

    NORMAL → SAFE → HALT (단방향 에스컬레이션)
    HALT → SAFE → NORMAL (수동 복구만)
    """

    def __init__(self) -> None:
        self._mode: TradingMode = TradingMode.NORMAL
        self._reason: str = "System initialized"
        self._changed_at: datetime = datetime.utcnow()
        self._lock = asyncio.Lock()
        self._listeners: list = []

    @property
    def mode(self) -> TradingMode:
        """현재 모드"""
        return self._mode

    @property
    def reason(self) -> str:
        """현재 모드 사유"""
        return self._reason

    @property
    def changed_at(self) -> datetime:
        """마지막 모드 변경 시간"""
        return self._changed_at

    @property
    def is_normal(self) -> bool:
        """NORMAL 모드 여부"""
        return self._mode == TradingMode.NORMAL

    @property
    def is_safe(self) -> bool:
        """SAFE 모드 여부"""
        return self._mode == TradingMode.SAFE

    @property
    def is_halt(self) -> bool:
        """HALT 모드 여부"""
        return self._mode == TradingMode.HALT

    @property
    def can_open_new_position(self) -> bool:
        """신규 포지션 오픈 가능 여부"""
        return self._mode == TradingMode.NORMAL

    @property
    def can_close_position(self) -> bool:
        """포지션 청산 가능 여부"""
        return self._mode != TradingMode.HALT or self._mode == TradingMode.HALT

    async def to_safe(self, reason: str) -> bool:
        """
        SAFE 모드로 전환

        Returns:
            True if mode changed, False if already in SAFE or HALT
        """
        async with self._lock:
            if self._mode == TradingMode.HALT:
                logger.warning(
                    "Cannot change to SAFE from HALT",
                    current_mode=self._mode.value,
                )
                return False

            if self._mode == TradingMode.SAFE:
                logger.info("Already in SAFE mode")
                return False

            prev_mode = self._mode
            self._mode = TradingMode.SAFE
            self._reason = reason
            self._changed_at = datetime.utcnow()

            logger.warning(
                "Mode changed to SAFE",
                previous=prev_mode.value,
                reason=reason,
            )

            await self._notify_listeners()
            return True

    async def to_halt(self, reason: str) -> bool:
        """
        HALT 모드로 전환 (긴급)

        Returns:
            True if mode changed, False if already in HALT
        """
        async with self._lock:
            if self._mode == TradingMode.HALT:
                logger.info("Already in HALT mode")
                return False

            prev_mode = self._mode
            self._mode = TradingMode.HALT
            self._reason = reason
            self._changed_at = datetime.utcnow()

            logger.critical(
                "Mode changed to HALT",
                previous=prev_mode.value,
                reason=reason,
            )

            await self._notify_listeners()
            return True

    async def to_normal(self, reason: str = "Manual resume") -> bool:
        """
        NORMAL 모드로 복귀 (수동)

        주의: HALT에서는 SAFE를 거쳐야 함
        """
        async with self._lock:
            if self._mode == TradingMode.HALT:
                logger.warning(
                    "Cannot resume directly from HALT. Use to_safe first.",
                    current_mode=self._mode.value,
                )
                return False

            if self._mode == TradingMode.NORMAL:
                logger.info("Already in NORMAL mode")
                return False

            prev_mode = self._mode
            self._mode = TradingMode.NORMAL
            self._reason = reason
            self._changed_at = datetime.utcnow()

            logger.info(
                "Mode changed to NORMAL",
                previous=prev_mode.value,
                reason=reason,
            )

            await self._notify_listeners()
            return True

    async def safe_to_normal_from_halt(self, reason: str = "Manual safe resume") -> bool:
        """HALT에서 SAFE로 전환"""
        async with self._lock:
            if self._mode != TradingMode.HALT:
                logger.warning("Not in HALT mode")
                return False

            prev_mode = self._mode
            self._mode = TradingMode.SAFE
            self._reason = reason
            self._changed_at = datetime.utcnow()

            logger.warning(
                "Mode changed from HALT to SAFE",
                previous=prev_mode.value,
                reason=reason,
            )

            await self._notify_listeners()
            return True

    def add_listener(self, callback) -> None:
        """모드 변경 리스너 추가"""
        self._listeners.append(callback)

    async def _notify_listeners(self) -> None:
        """리스너에게 모드 변경 알림"""
        for listener in self._listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(self._mode, self._reason)
                else:
                    listener(self._mode, self._reason)
            except Exception as e:
                logger.error("Mode listener error", error=str(e))

    def get_state(self) -> dict:
        """현재 상태 반환"""
        return {
            "mode": self._mode.value,
            "reason": self._reason,
            "changed_at": self._changed_at.isoformat(),
        }
