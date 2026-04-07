"""
WebSocket 管理器测试

覆盖连接管理、房间广播、消息路由
"""
import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestWebSocketConnection:
    """连接管理测试"""

    @pytest.mark.asyncio
    async def test_connect(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()

        await manager.connect(ws)
        assert ws in manager.active_connections
        ws.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        ws = MagicMock()
        ws.accept = AsyncMock()
        await manager.connect(ws)
        assert ws in manager.active_connections

        await manager.disconnect(ws)
        assert ws not in manager.active_connections

    @pytest.mark.asyncio
    async def test_multiple_connections(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        for _ in range(5):
            ws = MagicMock()
            ws.accept = AsyncMock()
            await manager.connect(ws)

        assert len(manager.active_connections) == 5


class TestRoomManagement:
    """房间管理测试"""

    @pytest.mark.asyncio
    async def test_join_room(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        ws = MagicMock()
        ws.accept = AsyncMock()
        await manager.connect(ws)
        await manager.join_room(ws, 100)

        assert 100 in manager.room_connections
        assert ws in manager.room_connections[100]

    @pytest.mark.asyncio
    async def test_leave_room(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        ws = MagicMock()
        ws.accept = AsyncMock()
        await manager.connect(ws)
        await manager.join_room(ws, 100)
        await manager.leave_room(ws, 100)

        assert ws not in manager.room_connections[100]

    @pytest.mark.asyncio
    async def test_multiple_rooms(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        ws1 = MagicMock()
        ws1.accept = AsyncMock()
        ws2 = MagicMock()
        ws2.accept = AsyncMock()

        await manager.connect(ws1)
        await manager.connect(ws2)
        await manager.join_room(ws1, 100)
        await manager.join_room(ws2, 100)
        await manager.join_room(ws1, 200)

        assert len(manager.room_connections[100]) == 2
        assert len(manager.room_connections[200]) == 1


class TestBroadcast:
    """广播测试"""

    @pytest.mark.asyncio
    async def test_broadcast_to_room(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        ws1 = MagicMock()
        ws1.accept = AsyncMock()
        ws1.send_json = AsyncMock()
        ws2 = MagicMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock()

        await manager.connect(ws1)
        await manager.connect(ws2)
        await manager.join_room(ws1, 100)
        await manager.join_room(ws2, 100)

        await manager.broadcast_to_room({"type": "message", "content": "hello"}, 100)

        ws1.send_json.assert_called_once_with({"type": "message", "content": "hello"})
        ws2.send_json.assert_called_once_with({"type": "message", "content": "hello"})

    @pytest.mark.asyncio
    async def test_broadcast_excludes_other_rooms(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        ws1 = MagicMock()
        ws1.accept = AsyncMock()
        ws1.send_json = AsyncMock()
        ws2 = MagicMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock()

        await manager.connect(ws1)
        await manager.connect(ws2)
        await manager.join_room(ws1, 100)
        await manager.join_room(ws2, 200)

        await manager.broadcast_to_room({"type": "msg"}, 100)

        ws1.send_json.assert_called_once()
        ws2.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_global(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        connections = []
        for _ in range(3):
            ws = MagicMock()
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock()
            await manager.connect(ws)
            connections.append(ws)

        await manager.broadcast({"type": "system", "msg": "shutdown"})

        for ws in connections:
            ws.send_json.assert_called_once_with({"type": "system", "msg": "shutdown"})

    @pytest.mark.asyncio
    async def test_broadcast_to_nonexistent_room(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()
        # 不应抛出异常
        await manager.broadcast_to_room({"type": "msg"}, 999)

    @pytest.mark.asyncio
    async def test_send_personal_message(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        ws = MagicMock()
        ws.send_json = AsyncMock()
        await manager.send_personal_message({"type": "private"}, ws)
        ws.send_json.assert_called_once_with({"type": "private"})


class TestReceive:
    """消息接收循环测试"""

    @pytest.mark.asyncio
    async def test_receive_join(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        # 第一条消息是 join，然后抛出异常中断循环
        ws.receive_json = AsyncMock(
            side_effect=[{"type": "join", "chatroom_id": 100}, Exception("break")]
        )

        await manager.connect(ws)
        await manager.receive(ws)

        assert ws in manager.room_connections[100]
        ws.send_json.assert_called_with({"type": "joined", "chatroom_id": 100})

    @pytest.mark.asyncio
    async def test_receive_leave(self):
        from routes.websocket import WebSocketManager
        manager = WebSocketManager()

        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.receive_json = AsyncMock(
            side_effect=[
                {"type": "join", "chatroom_id": 100},
                {"type": "leave", "chatroom_id": 100},
                Exception("break")
            ]
        )

        await manager.connect(ws)
        await manager.receive(ws)

        assert ws not in manager.room_connections[100]
