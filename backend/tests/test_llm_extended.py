"""
LLM 客户端扩展测试

覆盖 chat / chat_with_tools / chat_stream / get_llm_client / set_llm_client
"""
import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestLLMClientChat:
    """chat 方法测试"""

    @pytest.mark.asyncio
    async def test_chat_returns_content(self):
        from llm.client import LLMClient

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"

        client = LLMClient()
        client.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result == "Hello!"

    @pytest.mark.asyncio
    async def test_chat_passes_parameters(self):
        from llm.client import LLMClient

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "OK"

        client = LLMClient()
        mock_create = AsyncMock(return_value=mock_response)
        client.client.chat.completions.create = mock_create

        await client.chat(
            [{"role": "user", "content": "test"}],
            temperature=0.3, max_tokens=100
        )

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["temperature"] == 0.3
        assert call_kwargs["max_tokens"] == 100

    @pytest.mark.asyncio
    async def test_chat_error_handling(self):
        from llm.client import LLMClient

        client = LLMClient()
        client.client.chat.completions.create = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        with pytest.raises(Exception, match="LLM API error"):
            await client.chat([{"role": "user", "content": "test"}])


class TestLLMClientChatWithTools:
    """chat_with_tools 方法测试"""

    @pytest.mark.asyncio
    async def test_no_tool_calls(self):
        from llm.client import LLMClient

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Plain response"
        mock_response.choices[0].message.tool_calls = None

        client = LLMClient()
        client.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await client.chat_with_tools([{"role": "user", "content": "hi"}])
        assert result["content"] == "Plain response"
        assert result["tool_calls"] is None

    @pytest.mark.asyncio
    async def test_with_tool_calls(self):
        from llm.client import LLMClient

        mock_tc = MagicMock()
        mock_tc.id = "call_123"
        mock_tc.function.name = "web_search"
        mock_tc.function.arguments = '{"query": "test"}'

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_response.choices[0].message.tool_calls = [mock_tc]

        client = LLMClient()
        client.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await client.chat_with_tools(
            [{"role": "user", "content": "search"}],
            tools=[{"type": "function", "function": {"name": "web_search"}}]
        )
        assert result["tool_calls"] is not None
        assert len(result["tool_calls"]) == 1

    @pytest.mark.asyncio
    async def test_error_handling(self):
        from llm.client import LLMClient
        client = LLMClient()
        client.client.chat.completions.create = AsyncMock(
            side_effect=Exception("API error")
        )
        with pytest.raises(Exception, match="LLM API error with tools"):
            await client.chat_with_tools([{"role": "user", "content": "hi"}])


class TestLLMClientChatStream:
    """chat_stream 方法测试"""

    @pytest.mark.asyncio
    async def test_stream_content(self):
        from llm.client import LLMClient

        # 模拟流式 chunks
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = "Hello "
        chunk1.choices[0].delta.tool_calls = None
        chunk1.choices[0].finish_reason = None

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = "world"
        chunk2.choices[0].delta.tool_calls = None
        chunk2.choices[0].finish_reason = "stop"

        async def mock_stream():
            for c in [chunk1, chunk2]:
                yield c

        client = LLMClient()
        mock_create = AsyncMock(return_value=mock_stream())
        client.client.chat.completions.create = mock_create

        events = []
        async for event in client.chat_stream([{"role": "user", "content": "hi"}]):
            events.append(event)

        content_events = [e for e in events if e["type"] == "content"]
        assert len(content_events) == 2
        assert content_events[0]["delta"] == "Hello "
        assert content_events[1]["delta"] == "world"

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["full_content"] == "Hello world"
        assert any(e["type"] == "request_sent" for e in events)
        assert any(e["type"] == "first_chunk" for e in events)
        assert any(e["type"] == "first_content" for e in events)
        assert done_events[0]["timings"]["request_sent_ms"] >= 0
        assert done_events[0]["timings"]["first_chunk_ms"] >= 0
        assert done_events[0]["timings"]["first_content_ms"] >= 0

    @pytest.mark.asyncio
    async def test_stream_with_tool_calls(self):
        from llm.client import LLMClient

        tc_delta = MagicMock()
        tc_delta.index = 0
        tc_delta.id = "call_abc"
        tc_delta.function = MagicMock()
        tc_delta.function.name = "web_search"
        tc_delta.function.arguments = '{"query": "test"}'

        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = None
        chunk.choices[0].delta.tool_calls = [tc_delta]
        chunk.choices[0].finish_reason = "tool_calls"

        async def mock_stream():
            yield chunk

        client = LLMClient()
        client.client.chat.completions.create = AsyncMock(return_value=mock_stream())

        events = []
        async for event in client.chat_stream([{"role": "user", "content": "search"}]):
            events.append(event)

        done = [e for e in events if e["type"] == "done"][0]
        assert done["tool_calls"] is not None
        assert done["tool_calls"][0]["function"]["name"] == "web_search"

        assert any(e["type"] == "request_sent" for e in events)
        assert any(e["type"] == "first_chunk" for e in events)
        tool_delta = [e for e in events if e["type"] == "tool_call_delta"][0]
        assert tool_delta["tool_name"] == "web_search"
        assert tool_delta["tool_call_index"] == 0
        assert any(e["type"] == "tool_call_ready" for e in events)
        assert done["timings"]["request_sent_ms"] >= 0
        assert done["timings"]["first_chunk_ms"] >= 0
        assert done["timings"]["first_tool_call_ms"] >= 0
        assert done["timings"]["tool_call_ready_ms"] >= 0

    @pytest.mark.asyncio
    async def test_stream_error(self):
        from llm.client import LLMClient

        async def mock_stream():
            raise Exception("Stream broken")
            yield  # unreachable

        client = LLMClient()
        client.client.chat.completions.create = AsyncMock(return_value=mock_stream())

        events = []
        async for event in client.chat_stream([{"role": "user", "content": "hi"}]):
            events.append(event)

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "Stream broken" in error_events[0]["error"]

    @pytest.mark.asyncio
    async def test_stream_empty_chunks(self):
        from llm.client import LLMClient

        empty_chunk = MagicMock()
        empty_chunk.choices = []

        chunk_end = MagicMock()
        chunk_end.choices = [MagicMock()]
        chunk_end.choices[0].delta.content = "OK"
        chunk_end.choices[0].delta.tool_calls = None
        chunk_end.choices[0].finish_reason = "stop"

        async def mock_stream():
            yield empty_chunk
            yield chunk_end

        client = LLMClient()
        client.client.chat.completions.create = AsyncMock(return_value=mock_stream())

        events = []
        async for event in client.chat_stream([{"role": "user", "content": "hi"}]):
            events.append(event)

        content = [e for e in events if e["type"] == "content"]
        assert len(content) == 1


class TestLLMClientSingleton:
    """get_llm_client / set_llm_client 测试"""

    def test_get_creates_instance(self):
        from llm.client import get_llm_client, set_llm_client
        set_llm_client(None)  # reset
        client = get_llm_client()
        assert client is not None

    def test_singleton_behavior(self):
        from llm.client import get_llm_client, set_llm_client
        set_llm_client(None)
        c1 = get_llm_client()
        c2 = get_llm_client()
        assert c1 is c2

    def test_set_replaces(self):
        from llm.client import get_llm_client, set_llm_client, LLMClient
        set_llm_client(None)
        old = get_llm_client()
        new = LLMClient()
        set_llm_client(new)
        assert get_llm_client() is new
        assert get_llm_client() is not old
