"""명령 큐 - API에서 엔진으로 명령 전달"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

import structlog

logger = structlog.get_logger()


class CommandType(str, Enum):
    """명령 타입"""

    PAUSE = "PAUSE"  # SAFE 모드 전환
    RESUME = "RESUME"  # NORMAL 복귀
    FLATTEN = "FLATTEN"  # 포지션 정리
    FLATTEN_SYMBOL = "FLATTEN_SYMBOL"  # 특정 심볼 정리
    REFRESH_POSITIONS = "REFRESH_POSITIONS"  # 포지션 새로고침
    RECONCILE = "RECONCILE"  # 수동 Reconcile


@dataclass
class Command:
    """명령 객체"""

    command_type: CommandType
    params: dict = field(default_factory=dict)
    command_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=datetime.utcnow)
    result: Optional[Any] = None
    error: Optional[str] = None
    completed: bool = False


class CommandQueue:
    """
    명령 큐

    API에서 엔진으로 비동기 명령 전달
    """

    def __init__(self, max_size: int = 100) -> None:
        self._queue: asyncio.Queue[Command] = asyncio.Queue(maxsize=max_size)
        self._pending: dict[str, Command] = {}
        self._results: dict[str, Command] = {}
        self._lock = asyncio.Lock()

    async def put(self, command: Command) -> str:
        """명령 추가"""
        async with self._lock:
            self._pending[command.command_id] = command

        await self._queue.put(command)
        logger.info(
            "Command queued",
            command_id=command.command_id,
            type=command.command_type.value,
        )
        return command.command_id

    async def get(self) -> Command:
        """명령 가져오기 (블로킹)"""
        return await self._queue.get()

    async def get_nowait(self) -> Optional[Command]:
        """명령 가져오기 (논블로킹)"""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def complete(
        self,
        command_id: str,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        """명령 완료 처리"""
        async with self._lock:
            if command_id in self._pending:
                command = self._pending.pop(command_id)
                command.result = result
                command.error = error
                command.completed = True
                self._results[command_id] = command

                logger.info(
                    "Command completed",
                    command_id=command_id,
                    success=error is None,
                )

    async def get_result(self, command_id: str, timeout: float = 30.0) -> Command:
        """명령 결과 대기"""
        start = asyncio.get_event_loop().time()

        while True:
            async with self._lock:
                if command_id in self._results:
                    return self._results.pop(command_id)

            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > timeout:
                raise TimeoutError(f"Command {command_id} timed out")

            await asyncio.sleep(0.1)

    def is_empty(self) -> bool:
        """큐가 비어있는지"""
        return self._queue.empty()

    @property
    def pending_count(self) -> int:
        """대기 중인 명령 수"""
        return len(self._pending)
