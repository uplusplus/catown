# -*- coding: utf-8 -*-
"""
Web Search Tool - DuckDuckGo Instant Answer API
"""
from .base import BaseTool
import urllib.request
import urllib.parse
import json


class WebSearchTool(BaseTool):
    """Tool for searching the web via DuckDuckGo"""
    
    name = "web_search"
    description = "Search the web for information. Use this to find current information, facts, or answers to questions."
    
    async def execute(self, query: str, **kwargs) -> str:
        """
        Execute web search via DuckDuckGo Instant Answer API
        
        Args:
            query: Search search query string
            
        Returns:
            Search results as text
        """
        try:
            # Use DuckDuckGo Instant Answer API (no API key needed)
            url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1
            })
            
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0"
            })
            
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
            
            results = []
            
            # Abstract (summary)
            if data.get("Abstract"):
                results.append(f"**Summary**: {data['Abstract']}")
                if data.get("AbstractURL"):
                    results.append(f"Source: {data['AbstractURL']}")
            
            # Related topics
            if data.get("RelatedTopics"):
                results.append("\n**Related Topics**:")
                for i, topic in enumerate(data["RelatedTopics"][:5]):
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append(f"  {i+1}. {topic['Text'][:200]}")
            
            # Answer (if available)
            if data.get("Answer"):
                results.append(f"\n**Answer**: {data['Answer']}")
            
            if results:
                return "\n".join(results)
            else:
                return f"[Web Search] No instant answer found for '{query}'. Try a more specific query."
                
        except Exception as e:
            return f"[Web Search] Error: {str(e)}"
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string"
                }
            },
            "required": ["query"]
        }
