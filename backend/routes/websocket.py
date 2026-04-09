# -*- coding: utf-8 -*-
"""
WebSocket 管理器
"""
import logging
from typing import Dict, Set
from fastapi import WebSocket
import json

logger = logging.getLogger("catown.websocket")


class WebSocketManager:
    """WebSocket 连接管理器"""
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.room_connections: Dict[int, Set[WebSocket]] = {}  # chatroom_id -> connections
    
    async def connect(self, websocket: WebSocket):
        """接受 WebSocket 连接"""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_connections)}")
    
    async def disconnect(self, websocket: WebSocket):
        """处理 WebSocket 断开"""
        self.active_connections.discard(websocket)
        
        # 从所有房间移除
        for room_connections in self.room_connections.values():
            room_connections.discard(websocket)
        
        logger.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")
    
    async def join_room(self, websocket: WebSocket, chatroom_id: int):
        """加入聊天室"""
        if chatroom_id not in self.room_connections:
            self.room_connections[chatroom_id] = set()
        self.room_connections[chatroom_id].add(websocket)
    
    async def leave_room(self, websocket: WebSocket, chatroom_id: int):
        """离开聊天室"""
        if chatroom_id in self.room_connections:
            self.room_connections[chatroom_id].discard(websocket)
    
    async def send_personal_message(self, message: Dict, websocket: WebSocket):
        """发送个人消息"""
        await websocket.send_json(message)
    
    async def broadcast(self, message: Dict):
        """广播消息给所有连接"""
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for conn in dead:
            self.active_connections.discard(conn)
    
    async def broadcast_to_room(self, message: Dict, chatroom_id: int):
        """广播消息到特定聊天室"""
        if chatroom_id in self.room_connections:
            dead = []
            for connection in self.room_connections[chatroom_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    dead.append(connection)
            for conn in dead:
                self.room_connections[chatroom_id].discard(conn)
    
    async def receive(self, websocket: WebSocket):
        """处理接收消息的循环"""
        while True:
            try:
                data = await websocket.receive_json()
                message_type = data.get('type')
                
                if message_type == 'join':
                    chatroom_id = data.get('chatroom_id')
                    if chatroom_id:
                        await self.join_room(websocket, chatroom_id)
                        await self.send_personal_message({
                            'type': 'joined',
                            'chatroom_id': chatroom_id
                        }, websocket)
                
                elif message_type == 'leave':
                    chatroom_id = data.get('chatroom_id')
                    if chatroom_id:
                        await self.leave_room(websocket, chatroom_id)
                
                elif message_type == 'message':
                    # 广播消息到房间
                    chatroom_id = data.get('chatroom_id')
                    if chatroom_id:
                        await self.broadcast_to_room({
                            'type': 'message',
                            'content': data.get('content'),
                            'sender': data.get('sender')
                        }, chatroom_id)
                
            except Exception as e:
                logger.error(f"WebSocket receive error: {e}")
                break


websocket_manager = WebSocketManager()
