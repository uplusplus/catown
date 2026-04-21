# -*- coding: utf-8 -*-
"""
Retrieve Memory Tool - Uses both Message history and Memory table
"""
from .base import BaseTool
import asyncio
from typing import Optional


class RetrieveMemoryTool(BaseTool):
    """Tool for retrieving agent memories from database"""
    
    name = "retrieve_memory"
    description = "Retrieve relevant memories or past information from the agent's memory store. Use this to recall previous conversations, learned facts, or context."
    
    async def execute(self, query: str, agent_id: Optional[int] = None, **kwargs) -> str:
        return await asyncio.to_thread(self._execute_sync, query, agent_id)

    def _execute_sync(self, query: str, agent_id: Optional[int] = None) -> str:
        """
        Retrieve memories from the database
        
        Args:
            query: Query to search memories
            agent_id: Agent ID to search memories for
            
        Returns:
            Retrieved memories
        """
        try:
            from models.database import get_db, Message, Agent, Memory
            from sqlalchemy import or_
            
            db = next(get_db())
            try:
                results = []
                agent_map: dict = {}
                
                # 1. Search the Memory table
                mem_query = db.query(Memory).filter(
                    Memory.content.contains(query)
                )
                
                if agent_id is not None:
                    mem_query = mem_query.filter(
                        or_(
                            Memory.agent_id == agent_id,
                        )
                    )
                else:
                    mem_query = mem_query.filter(Memory.agent_id.isnot(None))
                
                memories = mem_query.order_by(
                    Memory.importance.desc(),
                    Memory.created_at.desc()
                ).limit(5).all()
                
                # Build agent name map for memories
                mem_agent_ids = {m.agent_id for m in memories if m.agent_id}
                if mem_agent_ids:
                    agents = db.query(Agent).filter(Agent.id.in_(mem_agent_ids)).all()
                    agent_map = {a.id: getattr(a, 'name', None) for a in agents}
                
                if memories:
                    results.append(f"**Long-term memories ({len(memories)}):**")
                    for m in memories:
                        agent_name = agent_map.get(m.agent_id) if m.agent_id else None
                        who = agent_name or "system"
                        ts = m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "unknown"
                        results.append(f"- [{ts}] [{who}] [importance={m.importance}] {m.content[:200]}")
                
                # 2. Search recent messages
                msg_query = db.query(Message).filter(Message.content.contains(query))
                
                if agent_id is not None:
                    msg_query = msg_query.filter(
                        or_(
                            Message.agent_id == agent_id,
                        )
                    )
                
                messages = msg_query.order_by(Message.created_at.desc()).limit(5).all()
                
                if messages:
                    # Build agent name map for messages
                    msg_agent_ids = {m.agent_id for m in messages if m.agent_id}
                    missing = msg_agent_ids - set(agent_map.keys())
                    if missing:
                        more_agents = db.query(Agent).filter(Agent.id.in_(missing)).all()
                        agent_map.update({a.id: getattr(a, 'name', None) for a in more_agents})
                    
                    results.append(f"\n**Past messages relevant to your query:**")
                    for msg in messages:
                        agent_name = agent_map.get(msg.agent_id) if msg.agent_id else None
                        who = agent_name or "user"
                        ts = msg.created_at.strftime("%Y-%m-%d %H:%M") if msg.created_at else "unknown"
                        preview = msg.content[:150] + "..." if len(msg.content) > 150 else msg.content
                        results.append(f"- [{ts}] {who}: {preview}")
                
                if results:
                    return "\n".join(results[:7]) + (f"\n... and more" if len(results) > 7 else "")
                else:
                    return f"[Memory] No relevant memories found for '{query}'."
            finally:
                db.close()
                
        except Exception as e:
            return f"[Memory] Error retrieving memories: {str(e)}"
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Query string to search memories"
                },
                "agent_id": {
                    "type": "integer",
                    "description": "Optional agent ID to search specific agent's memories"
                }
            },
            "required": ["query"]
        }
