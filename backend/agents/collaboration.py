# -*- coding: utf-8 -*-
"""
Multi-Agent Collaboration Module
"""
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
import asyncio


class CollaborationStrategy:
    """Base class for collaboration strategies"""
    
    async def select_agents(self, message: str, agents: List[Any], context: Dict = None) -> List[Any]:
        """Select which agents should respond"""
        raise NotImplementedError


class SingleAgentStrategy(CollaborationStrategy):
    """Default: Single agent responds based on @ mention or default"""
    
    async def select_agents(self, message: str, agents: List[Any], context: Dict = None) -> List[Any]:
        """Select single agent"""
        import re
        
        # Check for @ mentions
        mentions = re.findall(r'@(\w+)', message)
        if mentions:
            for mention in mentions:
                agent = next((a for a in agents if a.name == mention), None)
                if agent:
                    return [agent]
        
        # Default to "assistant" or first agent
        agent = next((a for a in agents if a.name == "assistant"), agents[0] if agents else None)
        return [agent] if agent else []


class MultiAgentStrategy(CollaborationStrategy):
    """Multiple agents collaborate on complex tasks"""
    
    def __init__(self, max_agents: int = 3):
        self.max_agents = max_agents
    
    async def select_agents(self, message: str, agents: List[Any], context: Dict = None) -> List[Any]:
        """Select multiple agents based on task type"""
        import re
        
        # Check for explicit @ mentions
        mentions = re.findall(r'@(\w+)', message)
        mentioned_agents = []
        for mention in mentions:
            agent = next((a for a in agents if a.name == mention), None)
            if agent:
                mentioned_agents.append(agent)
        
        if mentioned_agents:
            return mentioned_agents[:self.max_agents]
        
        # Auto-select based on keywords
        selected = []
        message_lower = message.lower()
        
        # Code-related tasks
        if any(kw in message_lower for kw in ['code', '编程', '程序', 'debug', '代码', 'implement', '实现']):
            coder = next((a for a in agents if a.name == 'coder'), None)
            if coder:
                selected.append(coder)
        
        # Research tasks
        if any(kw in message_lower for kw in ['research', '研究', 'investigate', '调查', 'analyze', '分析']):
            researcher = next((a for a in agents if a.name == 'researcher'), None)
            if researcher:
                selected.append(researcher)
        
        # Review tasks
        if any(kw in message_lower for kw in ['review', '审核', 'check', '检查', 'evaluate', '评估']):
            reviewer = next((a for a in agents if a.name == 'reviewer'), None)
            if reviewer:
                selected.append(reviewer)
        
        # If no specific agents selected, use default
        if not selected:
            assistant = next((a for a in agents if a.name == 'assistant'), None)
            if assistant:
                selected.append(assistant)
            elif agents:
                selected.append(agents[0])
        
        return selected[:self.max_agents]


class CollaborationManager:
    """Manager for agent collaboration"""
    
    def __init__(self, strategy: CollaborationStrategy = None):
        self.strategy = strategy or SingleAgentStrategy()
    
    def set_strategy(self, strategy: CollaborationStrategy):
        """Change collaboration strategy"""
        self.strategy = strategy
    
    async def coordinate(self, message: str, agents: List[Any], context: Dict = None) -> List[Dict]:
        """
        Coordinate agent responses
        
        Returns:
            List of agent responses
        """
        selected_agents = await self.strategy.select_agents(message, agents, context)
        return selected_agents


# Global collaboration manager
collaboration_manager = CollaborationManager()
