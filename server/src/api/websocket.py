"""FastAPI WebSocket 핸들러"""

import asyncio
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import structlog

logger = structlog.get_logger()

websocket_router = APIRouter()

# 연결된 클라이언트
_clients: set[WebSocket] = set()

# 엔진 인스턴스
_engine = None


def set_engine(engine):
    """엔진 설정"""
    global _engine
    _engine = engine


class ConnectionManager:
    """WebSocket 연결 관리"""

    def __init__(self):
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info("WebSocket client connected", count=len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info("WebSocket client disconnected", count=len(self.active_connections))

    async def broadcast(self, message: dict):
        """모든 클라이언트에 브로드캐스트"""
        if not self.active_connections:
            return

        data = json.dumps(message, default=str)
        disconnected = set()

        for connection in self.active_connections:
            try:
                await connection.send_text(data)
            except Exception:
                disconnected.add(connection)

        # 끊어진 연결 제거
        self.active_connections -= disconnected


manager = ConnectionManager()


@websocket_router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """
    실시간 스트림

    1초~5초 주기로 summary/positions/events 전송
    """
    await manager.connect(websocket)

    try:
        # 초기 데이터 전송
        if _engine:
            await websocket.send_json({
                "type": "initial",
                "data": {
                    "summary": _engine.get_summary(),
                    "positions": _engine.get_positions(),
                    "events": _engine.get_events(limit=50),
                    "mode": _engine.mode.value,
                },
                "timestamp": datetime.utcnow().isoformat(),
            })

        # 주기적 업데이트 루프
        while True:
            try:
                # 클라이언트로부터 메시지 대기 (핑/퐁 처리)
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=5.0,  # 5초마다 업데이트
                )

                # 클라이언트 메시지 처리 (ping 등)
                if data == "ping":
                    await websocket.send_text("pong")

            except asyncio.TimeoutError:
                # 타임아웃 시 업데이트 전송
                if _engine:
                    await websocket.send_json({
                        "type": "update",
                        "data": {
                            "summary": _engine.get_summary(),
                            "positions": _engine.get_positions(),
                            "mode": _engine.mode.value,
                        },
                        "timestamp": datetime.utcnow().isoformat(),
                    })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error", error=str(e))
        manager.disconnect(websocket)


async def broadcast_event(event: dict):
    """이벤트 브로드캐스트"""
    await manager.broadcast({
        "type": "event",
        "data": event,
        "timestamp": datetime.utcnow().isoformat(),
    })


async def broadcast_update(update_type: str, data: dict):
    """업데이트 브로드캐스트"""
    await manager.broadcast({
        "type": update_type,
        "data": data,
        "timestamp": datetime.utcnow().isoformat(),
    })
