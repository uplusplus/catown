# -*- coding: utf-8 -*-
"""测试 LLM 连接"""
import asyncio
import os
from dotenv import load_dotenv
from pathlib import Path


def _default_catown_home() -> Path:
    configured = os.getenv("CATOWN_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".catown").resolve()


load_dotenv(_default_catown_home() / ".env")

async def test_llm():
    print("=== LLM Connection Test ===")
    print(f"API Key: {os.getenv('LLM_API_KEY', '')[:10]}...")
    print(f"Base URL: {os.getenv('LLM_BASE_URL', '')}")
    print(f"Model: {os.getenv('LLM_MODEL', '')}")
    
    from openai import AsyncOpenAI
    
    client = AsyncOpenAI(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    )
    
    print("\n--- Testing LLM call ---")
    try:
        response = await client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4"),
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say hello in one sentence."}
            ],
            max_tokens=100
        )
        print(f"✅ LLM Response: {response.choices[0].message.content}")
        return True
    except Exception as e:
        print(f"❌ LLM Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    result = asyncio.run(test_llm())
    print(f"\nTest result: {'PASS' if result else 'FAIL'}")
