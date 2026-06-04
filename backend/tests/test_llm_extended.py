"""
LLM 客户端扩展测试

覆盖 chat / chat_with_tools / chat_stream / get_llm_client / set_llm_client
"""
import gzip
import httpx
import json
import os
import pytest
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from openai import APITimeoutError
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

    @pytest.mark.asyncio
    async def test_error_handling_logs_and_records_network_event(self, caplog):
        from llm.client import LLMClient

        client = LLMClient()
        events = []
        client._record_network_event = lambda **kwargs: events.append(kwargs)
        client.client.chat.completions.create = AsyncMock(
            side_effect=Exception("API error")
        )

        with pytest.raises(Exception, match="LLM API error with tools"):
            await client.chat_with_tools([{"role": "user", "content": "hi"}])

        assert any("LLM tool chat failed" in message for message in caplog.messages)
        assert len(events) == 1
        assert events[0]["success"] is False
        assert "API error" in events[0]["error"]
        assert events[0]["metadata"]["sync_tool_chat"] is True

    @pytest.mark.asyncio
    async def test_empty_completion_logs_and_preserves_original_return_shape(self, caplog):
        from llm.client import LLMClient

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_response.choices[0].message.tool_calls = None
        mock_response.choices[0].message.refusal = None
        mock_response.choices[0].finish_reason = "stop"

        client = LLMClient()
        events = []
        client._record_network_event = lambda **kwargs: events.append(kwargs)
        client.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await client.chat_with_tools([{"role": "user", "content": "hi"}])

        assert result["content"] is None
        assert result["tool_calls"] is None
        assert any("LLM tool chat returned empty completion" in message for message in caplog.messages)
        assert len(events) == 1
        assert events[0]["success"] is False
        assert events[0]["error"] == "empty tool chat completion"
        assert events[0]["metadata"]["response_validation"] == "empty_completion"

    @pytest.mark.asyncio
    async def test_retry_later_rate_limit_retries_with_exponential_backoff_and_succeeds(self, caplog):
        from llm.client import LLMClient

        caplog.set_level("INFO", logger="catown.llm")
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Recovered"
        mock_response.choices[0].message.tool_calls = None

        attempts = []

        async def flaky_create(**_kwargs):
            attempts.append("call")
            if len(attempts) < 4:
                raise Exception("Error code: 429 - {'error': {'message': 'Concurrency limit exceeded for account, please retry later', 'type': 'rate_limit_error'}}")
            return mock_response

        client = LLMClient()
        events = []
        client._record_network_event = lambda **kwargs: events.append(kwargs)
        client.client.chat.completions.create = AsyncMock(side_effect=flaky_create)

        with patch("llm.client.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            result = await client.chat_with_tools([{"role": "user", "content": "hi"}])

        assert result["content"] == "Recovered"
        assert len(attempts) == 4
        assert mock_sleep.await_count == 3
        assert [call.args[0] for call in mock_sleep.await_args_list] == [1.0, 2.0, 4.0]
        assert any("upstream failure; retrying" in message for message in caplog.messages)
        assert any("retry succeeded" in message for message in caplog.messages)
        assert len(events) == 3
        assert events[0]["metadata"]["retryable"] is True
        assert events[0]["metadata"]["will_retry"] is True

    @pytest.mark.asyncio
    async def test_retry_later_rate_limit_stops_after_five_minute_budget(self, caplog):
        from llm.client import LLMClient

        client = LLMClient()
        events = []
        client._record_network_event = lambda **kwargs: events.append(kwargs)
        client.client.chat.completions.create = AsyncMock(
            side_effect=Exception("Error code: 429 - {'error': {'message': 'Concurrency limit exceeded for account, please retry later', 'type': 'rate_limit_error'}}")
        )

        perf_counter_values = iter([0.0, 0.0, 1.0, 3.0, 7.0, 15.0, 31.0, 63.0, 123.0, 183.0, 243.0, 303.0, 303.0])
        with patch("llm.client.asyncio.sleep", new=AsyncMock()) as mock_sleep, patch("llm.client.time.perf_counter", side_effect=lambda: next(perf_counter_values)):
            with pytest.raises(Exception, match="LLM API error with tools"):
                await client.chat_with_tools([{"role": "user", "content": "hi"}])

        assert mock_sleep.await_count == 5
        assert [call.args[0] for call in mock_sleep.await_args_list] == [1.0, 2.0, 4.0, 8.0, 16.0]
        assert len(events) == 6
        assert events[0]["metadata"]["retryable"] is True
        assert events[-1]["metadata"]["attempts"] == 6
        assert any("LLM tool chat failed" in message for message in caplog.messages)


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
    async def test_stream_requests_usage_and_emits_done_usage(self):
        from llm.client import LLMClient

        content_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="OK", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        usage_chunk = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4, total_tokens=16),
        )

        async def mock_stream():
            yield content_chunk
            yield usage_chunk

        client = LLMClient()
        mock_create = AsyncMock(return_value=mock_stream())
        client.client.chat.completions.create = mock_create

        events = []
        async for event in client.chat_stream([{"role": "user", "content": "hi"}]):
            events.append(event)

        assert mock_create.call_args[1]["stream_options"] == {"include_usage": True}
        done_event = next(event for event in events if event["type"] == "done")
        assert done_event["usage"] == {
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "total_tokens": 16,
        }
        assert client._stream_usage_supported is True

    @pytest.mark.asyncio
    async def test_stream_retries_without_usage_when_provider_rejects_stream_options(self):
        from llm.client import LLMClient

        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="fallback", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        create_calls = []

        async def mock_stream():
            yield chunk

        async def mock_create_impl(**kwargs):
            create_calls.append(dict(kwargs))
            if kwargs.get("stream_options") == {"include_usage": True}:
                raise Exception("400 unsupported parameter: stream_options.include_usage")
            return mock_stream()

        client = LLMClient()
        client.client.chat.completions.create = AsyncMock(side_effect=mock_create_impl)

        events = []
        async for event in client.chat_stream([{"role": "user", "content": "hi"}]):
            events.append(event)

        assert len(create_calls) == 2
        assert create_calls[0]["stream_options"] == {"include_usage": True}
        assert "stream_options" not in create_calls[1]
        assert client._stream_usage_supported is False
        done_event = next(event for event in events if event["type"] == "done")
        assert done_event["full_content"] == "fallback"

        async for _ in client.chat_stream([{"role": "user", "content": "retry"}]):
            pass

        assert len(create_calls) == 3
        assert "stream_options" not in create_calls[2]

    @pytest.mark.asyncio
    async def test_stream_reads_terminal_usage_chunk_after_finish_reason(self):
        from llm.client import LLMClient

        content_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="terminal", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        usage_chunk = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=7, total_tokens=27),
        )

        async def mock_stream():
            yield content_chunk
            yield usage_chunk

        client = LLMClient()
        client.client.chat.completions.create = AsyncMock(return_value=mock_stream())

        events = []
        async for event in client.chat_stream([{"role": "user", "content": "hi"}]):
            events.append(event)

        done_event = next(event for event in events if event["type"] == "done")
        assert done_event["full_content"] == "terminal"
        assert done_event["usage"] == {
            "prompt_tokens": 20,
            "completion_tokens": 7,
            "total_tokens": 27,
        }

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
    async def test_responses_http_stream_sends_previous_response_id_and_maps_text(self):
        from llm.client import LLMClient

        async def mock_stream():
            yield SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp_next", status="in_progress"),
            )
            yield SimpleNamespace(
                type="response.output_text.delta",
                delta="Hello",
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_next",
                    status="completed",
                    previous_response_id="resp_prev",
                    usage=SimpleNamespace(input_tokens=11, output_tokens=3, total_tokens=14),
                ),
            )

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-test",
            provider_mode="responses_http",
        )
        client.client.responses.create = AsyncMock(return_value=mock_stream())

        events = []
        async for event in client.chat_stream(
            [
                {"role": "system", "content": "System rules"},
                {"role": "user", "content": "Old question"},
                {"role": "assistant", "content": "Old answer"},
                {"role": "tool", "tool_call_id": "old_call", "content": "Old tool output"},
                {"role": "user", "content": "Hi"},
            ],
            previous_response_id="resp_prev",
        ):
            events.append(event)

        call_kwargs = client.client.responses.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-test"
        assert call_kwargs["stream"] is True
        assert call_kwargs["store"] is True
        assert call_kwargs["previous_response_id"] == "resp_prev"
        assert call_kwargs["instructions"] == "System rules"
        assert call_kwargs["input"] == [{"role": "user", "content": "Hi"}]

        assert any(event["type"] == "content" and event["delta"] == "Hello" for event in events)
        done = next(event for event in events if event["type"] == "done")
        assert done["response_id"] == "resp_next"
        assert done["full_content"] == "Hello"
        assert done["usage"] == {
            "prompt_tokens": 11,
            "completion_tokens": 3,
            "total_tokens": 14,
        }
        assert done["provider_mode"] == "responses_http"
        assert done["provider_request"]["omitted_input_item_count"] == 3
        request_sent = next(event for event in events if event["type"] == "request_sent")
        provider_request = request_sent["provider_request"]
        assert {
            key: provider_request[key]
            for key in (
                "stateful_delta",
                "full_input_item_count",
                "sent_input_item_count",
                "omitted_input_item_count",
            )
        } == {
            "stateful_delta": True,
            "full_input_item_count": 4,
            "sent_input_item_count": 1,
            "omitted_input_item_count": 3,
        }
        assert provider_request["estimated_full_input_tokens"] > provider_request["estimated_sent_input_tokens"] > 0
        assert provider_request["estimated_omitted_input_tokens"] == (
            provider_request["estimated_full_input_tokens"] - provider_request["estimated_sent_input_tokens"]
        )
        assert provider_request["estimated_instruction_tokens"] > 0

    @pytest.mark.asyncio
    async def test_responses_http_retries_full_context_when_previous_response_id_is_unsupported(self):
        from llm.client import LLMClient

        async def mock_stream():
            yield SimpleNamespace(
                type="response.output_text.delta",
                delta="Fallback",
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_fallback",
                    status="completed",
                    usage=SimpleNamespace(input_tokens=21, output_tokens=2, total_tokens=23),
                ),
            )

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-test",
            provider_mode="responses_http",
        )
        client.client.responses.create = AsyncMock(
            side_effect=[
                Exception("previous_response_id is only supported on Responses WebSocket v2"),
                mock_stream(),
            ]
        )
        network_events = []
        client._record_network_event = lambda **kwargs: network_events.append(kwargs)

        events = []
        async for event in client.chat_stream(
            [
                {"role": "system", "content": "System rules"},
                {"role": "user", "content": "Old question"},
                {"role": "assistant", "content": "Old answer"},
                {"role": "user", "content": "Hi"},
            ],
            previous_response_id="resp_prev",
        ):
            events.append(event)

        first_call = client.client.responses.create.call_args_list[0].kwargs
        fallback_call = client.client.responses.create.call_args_list[1].kwargs
        assert first_call["previous_response_id"] == "resp_prev"
        assert first_call["input"] == [{"role": "user", "content": "Hi"}]
        assert "previous_response_id" not in fallback_call
        assert fallback_call["input"] == [
            {"role": "user", "content": "Old question"},
            {"role": "assistant", "content": "Old answer"},
            {"role": "user", "content": "Hi"},
        ]

        done = next(event for event in events if event["type"] == "done")
        assert done["full_content"] == "Fallback"
        assert done["response_id"] == "resp_fallback"
        assert done["provider_request"]["response_state_fallback"] is True
        assert done["provider_request"]["response_state_fallback_reason"] == "previous_response_id_unsupported"
        assert done["provider_request"]["stateful_delta"] is False
        assert network_events[0]["metadata"]["response_state_fallback"] is True

    @pytest.mark.asyncio
    async def test_responses_websocket_sends_response_create_and_maps_text(self):
        from llm.client import LLMClient

        class FakeWebSocket:
            def __init__(self):
                self.sent = []
                self.events = [
                    {"type": "response.created", "response": {"id": "resp_ws", "status": "in_progress"}},
                    {"type": "response.output_text.delta", "delta": "Hello"},
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws",
                            "status": "completed",
                            "previous_response_id": "resp_prev",
                            "usage": {"input_tokens": 9, "output_tokens": 2, "total_tokens": 11},
                        },
                    },
                ]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def send(self, payload):
                self.sent.append(payload)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.events:
                    raise StopAsyncIteration
                return json.dumps(self.events.pop(0))

        fake_socket = FakeWebSocket()
        connect_calls = []

        def fake_connect(url, headers):
            connect_calls.append((url, headers))
            return fake_socket

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-test",
            provider_mode="responses_websocket",
        )
        network_events = []
        client._record_network_event = lambda **kwargs: network_events.append(kwargs)

        with patch("llm.client._connect_responses_websocket", side_effect=fake_connect):
            events = []
            async for event in client.chat_stream(
                [
                    {"role": "system", "content": "System rules"},
                    {"role": "user", "content": "Old question"},
                    {"role": "assistant", "content": "Old answer"},
                    {"role": "user", "content": "Hi"},
                ],
                previous_response_id="resp_prev",
            ):
                events.append(event)

        assert connect_calls == [("wss://api.openai.com/v1/responses", {"Authorization": "Bearer test"})]
        sent_event = json.loads(fake_socket.sent[0])
        assert sent_event["type"] == "response.create"
        assert sent_event["model"] == "gpt-test"
        assert "stream" not in sent_event
        assert sent_event["store"] is True
        assert sent_event["previous_response_id"] == "resp_prev"
        assert sent_event["instructions"] == "System rules"
        assert sent_event["input"] == [{"role": "user", "content": "Hi"}]

        assert any(event["type"] == "content" and event["delta"] == "Hello" for event in events)
        done = next(event for event in events if event["type"] == "done")
        assert done["provider_mode"] == "responses_websocket"
        assert done["response_id"] == "resp_ws"
        assert done["usage"] == {
            "prompt_tokens": 9,
            "completion_tokens": 2,
            "total_tokens": 11,
        }
        assert network_events[0]["metadata"]["transport"] == "websocket"
        assert network_events[0]["success"] is True

    @pytest.mark.asyncio
    async def test_responses_websocket_maps_completed_output_text(self):
        from llm.client import LLMClient

        class FakeWebSocket:
            def __init__(self):
                self.sent = []
                self.events = [
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_output",
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {"type": "output_text", "text": "Completed text"},
                                    ],
                                }
                            ],
                            "usage": {"input_tokens": 6, "output_tokens": 2, "total_tokens": 8},
                        },
                    },
                ]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def send(self, payload):
                self.sent.append(payload)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.events:
                    raise StopAsyncIteration
                return json.dumps(self.events.pop(0))

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-test",
            provider_mode="responses_websocket",
        )
        client._record_network_event = lambda **_kwargs: None

        with patch("llm.client._connect_responses_websocket", return_value=FakeWebSocket()):
            events = []
            async for event in client.chat_stream([{"role": "user", "content": "Hi"}]):
                events.append(event)

        done = next(event for event in events if event["type"] == "done")
        assert done["response_id"] == "resp_ws_output"
        assert done["full_content"] == "Completed text"
        assert done["usage"] == {
            "prompt_tokens": 6,
            "completion_tokens": 2,
            "total_tokens": 8,
        }

    @pytest.mark.asyncio
    async def test_responses_compact_sends_full_context_and_returns_output_window(self):
        from llm.client import LLMClient

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-test",
            provider_mode="responses_http",
        )
        client.client.responses.compact = AsyncMock(
            return_value=SimpleNamespace(
                id="resp_compact",
                status="completed",
                output=[
                    {"type": "compaction", "id": "cmp_123", "summary": "Old context compacted."},
                    {"role": "assistant", "content": "Ready."},
                ],
                usage=SimpleNamespace(input_tokens=33, output_tokens=0, total_tokens=33),
            )
        )

        result = await client.compact_responses_context(
            [
                {"role": "system", "content": "System rules"},
                {"role": "user", "content": "Old question"},
                {"role": "assistant", "content": "Old answer"},
                {"role": "user", "content": "New question"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )

        call_kwargs = client.client.responses.compact.call_args.kwargs
        assert call_kwargs["model"] == "gpt-test"
        assert call_kwargs["instructions"] == "System rules"
        assert call_kwargs["input"] == [
            {"role": "user", "content": "Old question"},
            {"role": "assistant", "content": "Old answer"},
            {"role": "user", "content": "New question"},
        ]
        assert call_kwargs["tools"][0]["name"] == "web_search"
        assert result["id"] == "cmp_123"
        assert result["response_id"] == "resp_compact"
        assert result["usage"] == {
            "prompt_tokens": 33,
            "completion_tokens": 0,
            "total_tokens": 33,
        }
        assert result["output"][0]["type"] == "compaction"

    @pytest.mark.asyncio
    async def test_responses_http_stream_reuses_compact_window_without_previous_response_id(self, tmp_path):
        from llm.client import LLMClient

        compact_payload_path = tmp_path / "providercmp_cmp_123.json"
        compact_payload_path.write_text(
            """
            {
              "id": "providercmp_cmp_123",
              "kind": "provider_native_response_compaction",
              "output": [
                {"type": "compaction", "id": "cmp_123", "summary": "Older context."},
                {"role": "assistant", "content": "Compacted answer."}
              ]
            }
            """,
            encoding="utf-8",
        )

        async def mock_stream():
            yield SimpleNamespace(
                type="response.output_text.delta",
                delta="Fresh",
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_after_compact",
                    status="completed",
                    usage=SimpleNamespace(input_tokens=12, output_tokens=2, total_tokens=14),
                ),
            )

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-test",
            provider_mode="responses_http",
        )
        client.client.responses.create = AsyncMock(return_value=mock_stream())

        events = []
        async for event in client.chat_stream(
            [
                {"role": "system", "content": "System rules"},
                {"role": "user", "content": "Old question"},
                {"role": "assistant", "content": "Old answer"},
                {"role": "user", "content": "New question"},
            ],
            provider_session={
                "previous_response_id": "resp_before_compact",
                "last_response_id": "resp_before_compact",
                "compact_checkpoint_id": "providercmp_cmp_123",
                "provider_compaction": {
                    "id": "providercmp_cmp_123",
                    "kind": "provider_native_response_compaction",
                    "path": str(compact_payload_path),
                    "ready_for_next_request": True,
                },
            },
        ):
            events.append(event)

        call_kwargs = client.client.responses.create.call_args.kwargs
        assert "previous_response_id" not in call_kwargs
        assert call_kwargs["input"] == [
            {"type": "compaction", "id": "cmp_123", "summary": "Older context."},
            {"role": "assistant", "content": "Compacted answer."},
            {"role": "user", "content": "New question"},
        ]
        request_sent = next(event for event in events if event["type"] == "request_sent")
        provider_request = request_sent["provider_request"]
        assert provider_request["compact_window_reused"] is True
        assert provider_request["compact_checkpoint_id"] == "providercmp_cmp_123"
        assert provider_request["compacted_input_item_count"] == 2
        assert provider_request["sent_delta_item_count"] == 1
        done = next(event for event in events if event["type"] == "done")
        assert done["response_id"] == "resp_after_compact"
        assert done["full_content"] == "Fresh"

    @pytest.mark.asyncio
    async def test_responses_http_compact_window_can_continue_with_tool_output(self, tmp_path):
        from llm.client import LLMClient

        compact_payload_path = tmp_path / "providercmp_cmp_tool.json"
        compact_payload_path.write_text(
            """
            {
              "id": "providercmp_cmp_tool",
              "kind": "provider_native_response_compaction",
              "output": [
                {"type": "compaction", "id": "cmp_tool", "summary": "Older context."},
                {
                  "type": "function_call",
                  "call_id": "call_search",
                  "name": "web_search",
                  "arguments": "{\\"query\\":\\"catown\\"}",
                  "status": "completed"
                }
              ]
            }
            """,
            encoding="utf-8",
        )

        async def mock_stream():
            yield SimpleNamespace(
                type="response.output_text.delta",
                delta="Tool output accepted",
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_after_tool",
                    status="completed",
                    usage=SimpleNamespace(input_tokens=16, output_tokens=3, total_tokens=19),
                ),
            )

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-test",
            provider_mode="responses_http",
        )
        client.client.responses.create = AsyncMock(return_value=mock_stream())

        events = []
        async for event in client.chat_stream(
            [
                {"role": "system", "content": "System rules"},
                {"role": "user", "content": "Run a search"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_search",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"catown"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_search",
                    "name": "web_search",
                    "content": "Search result summary",
                },
            ],
            provider_session={
                "previous_response_id": "resp_before_compact",
                "last_response_id": "resp_before_compact",
                "compact_checkpoint_id": "providercmp_cmp_tool",
                "provider_compaction": {
                    "id": "providercmp_cmp_tool",
                    "kind": "provider_native_response_compaction",
                    "path": str(compact_payload_path),
                    "ready_for_next_request": True,
                },
            },
        ):
            events.append(event)

        call_kwargs = client.client.responses.create.call_args.kwargs
        assert "previous_response_id" not in call_kwargs
        assert call_kwargs["input"] == [
            {"type": "compaction", "id": "cmp_tool", "summary": "Older context."},
            {
                "type": "function_call",
                "call_id": "call_search",
                "name": "web_search",
                "arguments": '{"query":"catown"}',
                "status": "completed",
            },
            {
                "type": "function_call_output",
                "call_id": "call_search",
                "output": "Search result summary",
            },
        ]
        request_sent = next(event for event in events if event["type"] == "request_sent")
        provider_request = request_sent["provider_request"]
        assert provider_request["compact_window_reused"] is True
        assert provider_request["sent_delta_item_count"] == 1
        assert next(event for event in events if event["type"] == "done")["response_id"] == "resp_after_tool"

    @pytest.mark.asyncio
    async def test_responses_http_compact_window_retry_keeps_canonical_input(self, tmp_path):
        from llm.client import LLMClient

        compact_payload_path = tmp_path / "providercmp_cmp_retry.json"
        compact_payload_path.write_text(
            """
            {
              "id": "providercmp_cmp_retry",
              "kind": "provider_native_response_compaction",
              "output": [
                {"type": "compaction", "id": "cmp_retry", "summary": "Older context."},
                {"role": "assistant", "content": "Compacted answer."}
              ]
            }
            """,
            encoding="utf-8",
        )
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        attempts = []

        async def success_stream():
            yield SimpleNamespace(
                type="response.output_text.delta",
                delta="Recovered",
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_after_retry",
                    status="completed",
                    usage=SimpleNamespace(input_tokens=13, output_tokens=2, total_tokens=15),
                ),
            )

        async def mock_create_impl(**_kwargs):
            attempts.append("call")
            if len(attempts) == 1:
                raise APITimeoutError(request=request) from httpx.ConnectTimeout("connect timeout")
            return success_stream()

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-test",
            provider_mode="responses_http",
        )
        network_events = []
        client._record_network_event = lambda **kwargs: network_events.append(kwargs)
        client.client.responses.create = AsyncMock(side_effect=mock_create_impl)

        with patch("llm.client.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            events = []
            async for event in client.chat_stream(
                [
                    {"role": "system", "content": "System rules"},
                    {"role": "user", "content": "Old question"},
                    {"role": "assistant", "content": "Old answer"},
                    {"role": "user", "content": "New question"},
                ],
                provider_session={
                    "previous_response_id": "resp_before_compact",
                    "last_response_id": "resp_before_compact",
                    "compact_checkpoint_id": "providercmp_cmp_retry",
                    "provider_compaction": {
                        "id": "providercmp_cmp_retry",
                        "kind": "provider_native_response_compaction",
                        "path": str(compact_payload_path),
                        "ready_for_next_request": True,
                    },
                },
            ):
                events.append(event)

        assert len(attempts) == 2
        assert mock_sleep.await_count == 1
        expected_input = [
            {"type": "compaction", "id": "cmp_retry", "summary": "Older context."},
            {"role": "assistant", "content": "Compacted answer."},
            {"role": "user", "content": "New question"},
        ]
        for call in client.client.responses.create.call_args_list:
            call_kwargs = call.kwargs
            assert "previous_response_id" not in call_kwargs
            assert call_kwargs["input"] == expected_input
        request_sent = next(event for event in events if event["type"] == "request_sent")
        assert request_sent["provider_request"]["compact_window_reused"] is True
        assert network_events[0]["metadata"]["retry_phase"] == "pre_first_output"
        done = next(event for event in events if event["type"] == "done")
        assert done["response_id"] == "resp_after_retry"
        assert done["full_content"] == "Recovered"

    @pytest.mark.asyncio
    async def test_responses_http_stateful_tool_round_sends_only_tool_outputs(self):
        from llm.client import LLMClient

        async def mock_stream():
            yield SimpleNamespace(
                type="response.output_text.delta",
                delta="Tool result received",
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_after_tool",
                    status="completed",
                    usage=SimpleNamespace(input_tokens=4, output_tokens=4, total_tokens=8),
                ),
            )

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-test",
            provider_mode="responses_http",
        )
        client.client.responses.create = AsyncMock(return_value=mock_stream())

        events = []
        async for event in client.chat_stream(
            [
                {"role": "system", "content": "System rules"},
                {"role": "user", "content": "Run a search"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_search",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"catown"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_search",
                    "name": "web_search",
                    "content": "Search result summary",
                },
            ],
            previous_response_id="resp_tool_call",
        ):
            events.append(event)

        call_kwargs = client.client.responses.create.call_args.kwargs
        assert call_kwargs["previous_response_id"] == "resp_tool_call"
        assert call_kwargs["input"] == [
            {
                "type": "function_call_output",
                "call_id": "call_search",
                "output": "Search result summary",
            }
        ]
        request_sent = next(event for event in events if event["type"] == "request_sent")
        provider_request = request_sent["provider_request"]
        assert {
            key: provider_request[key]
            for key in (
                "stateful_delta",
                "full_input_item_count",
                "sent_input_item_count",
                "omitted_input_item_count",
            )
        } == {
            "stateful_delta": True,
            "full_input_item_count": 3,
            "sent_input_item_count": 1,
            "omitted_input_item_count": 2,
        }
        assert provider_request["estimated_full_input_tokens"] > provider_request["estimated_sent_input_tokens"] > 0
        assert provider_request["estimated_omitted_input_tokens"] == (
            provider_request["estimated_full_input_tokens"] - provider_request["estimated_sent_input_tokens"]
        )
        assert provider_request["estimated_instruction_tokens"] > 0

    @pytest.mark.asyncio
    async def test_responses_http_stream_maps_function_calls_and_tool_schema(self):
        from llm.client import LLMClient

        async def mock_stream():
            yield SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item=SimpleNamespace(
                    id="fc_1",
                    type="function_call",
                    call_id="call_search",
                    name="web_search",
                    arguments="",
                ),
            )
            yield SimpleNamespace(
                type="response.function_call_arguments.delta",
                item_id="fc_1",
                output_index=0,
                delta='{"query"',
            )
            yield SimpleNamespace(
                type="response.function_call_arguments.done",
                item_id="fc_1",
                output_index=0,
                name="web_search",
                arguments='{"query":"test"}',
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_tool",
                    status="completed",
                    usage=SimpleNamespace(input_tokens=7, output_tokens=5, total_tokens=12),
                ),
            )

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-test",
            provider_mode="responses_http",
        )
        client.client.responses.create = AsyncMock(return_value=mock_stream())

        events = []
        async for event in client.chat_stream(
            [{"role": "user", "content": "search"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        ):
            events.append(event)

        call_kwargs = client.client.responses.create.call_args.kwargs
        assert call_kwargs["tools"] == [
            {
                "type": "function",
                "name": "web_search",
                "description": "Search the web",
                "parameters": {"type": "object"},
                "strict": False,
            }
        ]

        tool_delta = next(event for event in events if event["type"] == "tool_call_delta")
        assert tool_delta["tool_call_index"] == 0
        assert tool_delta["tool_name"] == "web_search"
        ready = next(event for event in events if event["type"] == "tool_call_ready")
        assert ready["tool_calls"][0]["id"] == "call_search"
        assert ready["tool_calls"][0]["function"]["arguments"] == '{"query":"test"}'
        done = next(event for event in events if event["type"] == "done")
        assert done["finish_reason"] == "tool_calls"
        assert done["tool_calls"][0]["function"]["name"] == "web_search"

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
    async def test_stream_retries_retryable_upstream_failure_before_first_output_and_succeeds(self, caplog):
        from llm.client import LLMClient

        caplog.set_level("INFO", logger="catown.llm")
        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        attempts = []

        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="Recovered", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

        async def success_stream():
            yield chunk

        async def mock_create_impl(**_kwargs):
            attempts.append("call")
            if len(attempts) == 1:
                raise APITimeoutError(request=request) from httpx.ConnectTimeout("connect timeout")
            return success_stream()

        client = LLMClient()
        events = []
        network_events = []
        client._record_network_event = lambda **kwargs: network_events.append(kwargs)
        client.client.chat.completions.create = AsyncMock(side_effect=mock_create_impl)

        with patch("llm.client.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            async for event in client.chat_stream([{"role": "user", "content": "hi"}]):
                events.append(event)

        done_event = next(event for event in events if event["type"] == "done")
        assert done_event["full_content"] == "Recovered"
        assert len(attempts) == 2
        assert mock_sleep.await_count == 1
        assert mock_sleep.await_args_list[0].args[0] == 1.0
        assert network_events[0]["metadata"]["retry_phase"] == "pre_first_output"
        assert any("retry succeeded before first output" in message for message in caplog.messages)

    @pytest.mark.asyncio
    async def test_stream_does_not_retry_after_first_output(self):
        from llm.client import LLMClient

        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        attempts = []

        async def broken_stream():
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="partial", tool_calls=None),
                        finish_reason=None,
                    )
                ],
                usage=None,
            )
            raise APITimeoutError(request=request) from httpx.ReadTimeout("read timeout")

        async def mock_create_impl(**_kwargs):
            attempts.append("call")
            return broken_stream()

        client = LLMClient()
        network_events = []
        client._record_network_event = lambda **kwargs: network_events.append(kwargs)
        client.client.chat.completions.create = AsyncMock(side_effect=mock_create_impl)

        with patch("llm.client.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            events = []
            async for event in client.chat_stream([{"role": "user", "content": "hi"}]):
                events.append(event)

        assert len(attempts) == 1
        assert mock_sleep.await_count == 0
        assert any(event["type"] == "content" and event["delta"] == "partial" for event in events)
        error_event = next(event for event in events if event["type"] == "error")
        assert "Request timed out." in error_event["error"]
        assert network_events[-1]["metadata"]["attempts"] == 1

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


class TestLLMClientNetworkCapture:
    def test_append_network_event_inherits_runtime_context(self):
        from llm.client import LLMClient
        from monitoring import monitor_network_buffer
        from services.llm_runtime_context import llm_runtime_context

        client = LLMClient(base_url="https://example.com/v1", api_key="test", model="test-model", agent_name="valet")
        monitor_network_buffer.clear()

        with llm_runtime_context(task_run_id=42, chatroom_id=7):
            event = monitor_network_buffer.append(
                {
                    "category": "backend_llm",
                    "source": "backend",
                    "protocol": "HTTPS",
                    "from_entity": client.agent_name,
                    "to_entity": "LLM (example.com)",
                    "method": "POST",
                    "url": client.base_url,
                    "host": "example.com",
                    "path": "/v1",
                }
            )

        assert event["task_run_id"] == 42
        assert event["chatroom_id"] == 7

    @pytest.mark.asyncio
    async def test_capture_http_request_redacts_multimodal_payload_for_monitor(self):
        from llm.client import LLMClient
        from services.multimodal_log_redaction import (
            clear_multimodal_data_uri_references,
            register_multimodal_data_uri_reference,
        )

        raw_pdf = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="
        pdf_data_uri = f"data:application/pdf;base64,{raw_pdf}"
        clear_multimodal_data_uri_references()
        register_multimodal_data_uri_reference(
            pdf_data_uri,
            file_id="file_monitor123",
            mime_type="application/pdf",
            file_name="spec.pdf",
            file_size=26,
            sha256="monitor-sha",
        )
        body = (
            '{"model":"test-model","messages":[{"role":"user","content":['
            '{"type":"text","text":"read this"},'
            '{"type":"file","file":{"filename":"spec.pdf","file_data":"'
            + pdf_data_uri
            + '"}}]}]}'
        ).encode("utf-8")

        client = LLMClient(base_url="https://example.com/v1", api_key="test", model="test-model", agent_name="valet")
        events = []
        client._append_network_event = lambda event: events.append(event)

        request = httpx.Request(
            "POST",
            "https://example.com/v1/chat/completions",
            headers={"content-type": "application/json"},
            content=body,
        )

        await client._capture_http_request(request)

        assert len(events) == 1
        assert events[0]["request_bytes"] == len(body)
        assert raw_pdf not in events[0]["raw_request"]
        assert "data:application/pdf;base64" not in events[0]["raw_request"]
        assert "<cached_file " in events[0]["raw_request"]
        assert 'file_id=\\"file_monitor123\\"' in events[0]["raw_request"]
        assert 'mime=\\"application/pdf\\"' in events[0]["raw_request"]
        assert raw_pdf not in events[0]["preview"]

    @pytest.mark.asyncio
    async def test_capture_http_request_labels_memory_extraction_context(self):
        from llm.client import LLMClient
        from services.llm_network_context import llm_network_audit_context

        client = LLMClient(base_url="https://example.com/v1", api_key="test", model="test-model", agent_name="valet")
        events = []
        client._append_network_event = lambda event: events.append(event)

        request = httpx.Request(
            "POST",
            "https://example.com/v1/chat/completions",
            headers={"content-type": "application/json"},
            content=b'{"messages":[]}',
        )

        with llm_network_audit_context(
            call_purpose="memory_extraction",
            purpose_label="memory extraction",
            metadata={"memory_agent_type": "valet"},
        ):
            await client._capture_http_request(request)

        assert len(events) == 1
        assert events[0]["request_direction"] == "valet memory extraction -> LLM (example.com)"
        assert events[0]["response_direction"] == "LLM (example.com) -> valet memory extraction"
        assert events[0]["metadata"]["llm_call_purpose"] == "memory_extraction"
        assert events[0]["metadata"]["llm_call_purpose_label"] == "memory extraction"
        assert events[0]["metadata"]["memory_agent_type"] == "valet"
        assert events[0]["metadata"]["frame_type"] == "request"

    @pytest.mark.asyncio
    async def test_chat_framework_llm_uses_configured_fallback(self, monkeypatch):
        import llm.client as llm_mod

        class FailingLLM:
            async def chat(self, messages, **kwargs):
                raise RuntimeError("primary down")

        class FallbackLLM:
            async def chat(self, messages, **kwargs):
                return "fallback ok"

        monkeypatch.setattr(llm_mod, "get_framework_llm_client", lambda: FailingLLM())
        monkeypatch.setattr(llm_mod, "get_framework_fallback_llm_client", lambda: FallbackLLM())

        result = await llm_mod.chat_framework_llm([{"role": "user", "content": "extract"}])

        assert result == "fallback ok"

    def test_get_framework_llm_client_uses_main_client_without_override(self, monkeypatch):
        import llm.client as llm_mod

        class MainLLM:
            pass

        main_client = MainLLM()
        llm_mod.clear_client_cache()
        monkeypatch.setattr(llm_mod, "_load_framework_provider", lambda: None)
        monkeypatch.setattr(llm_mod, "get_llm_client", lambda: main_client)

        assert llm_mod.get_framework_llm_client() is main_client

    @pytest.mark.asyncio
    async def test_capture_http_response_decodes_gzip_chunks_for_monitor(self):
        from llm.client import LLMClient

        payload = b'{"id":"resp_123","choices":[{"message":{"content":"ok"}}]}'
        compressed = gzip.compress(payload)

        class StaticStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield compressed

            async def aclose(self) -> None:
                return None

        client = LLMClient(base_url="https://example.com/v1", api_key="test", model="test-model", agent_name="valet")
        events = []
        client._append_network_event = lambda event: events.append(event)

        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        request.extensions["catown_raw_capture"] = {
            "flow_id": "llm-http-test",
            "flow_kind": "llm_http",
            "flow_seq": 1,
            "started_at": time.perf_counter(),
            "protocol": "HTTPS",
            "host": "example.com",
            "path": "/v1/chat/completions",
            "url": "https://example.com/v1/chat/completions",
        }
        response = httpx.Response(
            200,
            headers={
                "content-type": "application/json; charset=utf-8",
                "content-encoding": "gzip",
            },
            request=request,
            stream=StaticStream(),
        )

        await client._capture_http_response(response)

        streamed = bytearray()
        async for chunk in response.stream:
            streamed.extend(chunk)
        await response.stream.aclose()

        assert bytes(streamed) == compressed
        assert len(events) == 2
        assert events[0]["metadata"]["frame_type"] == "response_start"
        assert events[1]["metadata"]["frame_type"] == "response_chunk"
        assert events[1]["response_bytes"] == len(compressed)
        assert events[1]["raw_response"] == payload.decode("utf-8")
        assert '"id":"resp_123"' in events[1]["preview"]


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
