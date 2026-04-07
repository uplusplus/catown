# -*- coding: utf-8 -*-
"""Save Memory Tool"""
from .base import BaseTool
from typing import Optional


class SaveMemoryTool(BaseTool):
    """Tool for saving important information to the agent's long-term memory"""
    
    name = "save_memory"
    description = "Save important information to long-term memory for future recall. Use this for key decisions, facts, or learnings."
    
    async def execute(self, content: str, agent_id: Optional[int] = None, importance: Optional[int] = None) -> str:
        """
        Save a memory to the database
        
        Args:
            content: The information to remember
            agent_id: Agent ID (optional, from context if available)
            importance: Importance score 1-10 (default: 5)
        """
        try:
            from models.database import get_db, Memory
            
            db = next(get_db())
            try:
                memory = Memory(
                    agent_id=agent_id or 0,
                    memory_type="long_term",
                    content=content,
                    importance=importance or 5,
                    metadata_json='{}'
                )
                db.add(memory)
                db.commit()
                db.refresh(memory)
                return f"Memory saved successfully (id={memory.id}, importance={memory.importance})"
            finally:
                db.close()
                
        except Exception as e:
            return f"Error saving memory: {str(e)}"
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The information to save to long-term memory"
                },
                "agent_id": {
                    "type": "integer",
                    "description": "Agent ID to associate the memory with"
                },
                "importance": {
                    "type": "integer",
                    "description": "How important is this memory (1-10)"
                }
            },
            "required": ["content"]
        }
