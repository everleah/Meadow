"""Tests for `meadow.ai.provider` (the OpenAI-compatible chat call).

The provider is where a wrong URL or a wrong header costs money and time, so the
tests pin the two things that differ per vendor — the URL layout and the auth
header — plus the failure paths: an HTTP error, a body without a completion and an
empty answer must all raise instead of returning "no suggestion" silently.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meadow.ai.provider import (  # noqa: E402 - after the path fix
    AZURE_API_VERSION,
    ProviderConfig,
    ProviderError,
    chat,
    chat_stream,
    parse_reply,
)


def _config(
    *,
    provider: str = "generic",
    endpoint: str = "https://api.example.com/v1",
    model: str = "some-model",
    api_key: str = "sk-test",
    temperature: float = 0.2,
) -> ProviderConfig:
    return ProviderConfig(
        provider=provider,
        endpoint=endpoint,
        model=model,
        api_key=api_key,
        temperature=temperature,
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_generic_and_openai_use_the_chat_completions_path_with_a_bearer_header() -> None:
    url, headers = _config().request_target()
    assert url == "https://api.example.com/v1/chat/completions"
    assert headers == {"Authorization": "Bearer sk-test"}
    url, headers = _config(provider="openai").request_target()
    assert url == "https://api.example.com/v1/chat/completions"
    assert headers.get("Authorization") == "Bearer sk-test"


def test_azure_puts_the_deployment_in_the_path_and_the_key_in_a_header() -> None:
    url, headers = _config(
        provider="azure_openai",
        endpoint="https://my-resource.openai.azure.com",
        model="gpt-4o-deployment",
    ).request_target()
    assert url == (
        "https://my-resource.openai.azure.com/openai/deployments/gpt-4o-deployment"
        f"/chat/completions?api-version={AZURE_API_VERSION}"
    )
    assert headers == {"api-key": "sk-test"}
    assert "Authorization" not in headers


def test_ollama_uses_its_openai_endpoint_and_needs_no_key() -> None:
    config = _config(provider="ollama", endpoint="http://localhost:11434", api_key="")
    url, headers = config.request_target()
    assert url == "http://localhost:11434/v1/chat/completions"
    assert headers == {}
    # The endpoint is stripped of a trailing slash, so no double slash appears.
    assert _config(endpoint="https://api.example.com/v1/").request_target()[0] == (
        "https://api.example.com/v1/chat/completions"
    )


def test_the_payload_from_the_core_falls_back_to_the_documented_defaults() -> None:
    config = ProviderConfig.from_payload({"endpoint": " https://x/v1 ", "model": "m"})
    assert config.endpoint == "https://x/v1"
    assert config.provider == "generic"
    assert config.temperature == 0.2
    assert config.max_output_tokens == 2048
    assert config.timeout_ms == 30_000
    assert config.max_requests_per_hour == 60
    # A boolean is not a number: `True` must not become `temperature = 1`.
    assert ProviderConfig.from_payload({"temperature": True}).temperature == 0.2


def test_missing_settings_follow_the_provider() -> None:
    assert _config(endpoint="", model="", api_key="").missing() == [
        "ai_endpoint",
        "ai_model",
        "ai_api_key",
    ]
    assert _config(provider="ollama", api_key="").missing() == []


def test_parse_reply_reads_the_choice_and_reports_provider_errors() -> None:
    reply = parse_reply(
        {
            "model": "some-model",
            "choices": [
                {"message": {"role": "assistant", "content": "def run(obj, ctx, result):\n"}}
            ],
            "usage": {"total_tokens": 12},
        }
    )
    assert "def run" in reply.text
    assert reply.model == "some-model"
    assert reply.usage == {"total_tokens": 12}

    with pytest.raises(ProviderError) as error:
        parse_reply({"error": {"message": "insufficient balance"}})
    assert "insufficient balance" in str(error.value)
    with pytest.raises(ProviderError):
        parse_reply({"choices": [{"message": {"content": "   "}}]})


def test_chat_sends_a_low_temperature_request_and_returns_the_text() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"model": "some-model", "choices": [{"message": {"content": "hello"}}]},
        )

    async def run() -> str:
        async with _client(handler) as client:
            reply = await chat(
                _config(temperature=0.1), [{"role": "user", "content": "hi"}], client=client
            )
        assert reply.latency_ms >= 0
        return reply.text

    assert asyncio.run(run()) == "hello"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "some-model"
    assert body["temperature"] == 0.1
    assert body["stream"] is False
    assert body["max_tokens"] == 2048
    assert seen["auth"] == "Bearer sk-test"
    assert str(seen["url"]).endswith("/v1/chat/completions")


def test_chat_raises_with_the_provider_message_on_an_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    async def run() -> None:
        async with _client(handler) as client:
            await chat(_config(), [{"role": "user", "content": "hi"}], client=client)

    with pytest.raises(ProviderError) as error:
        asyncio.run(run())
    assert "401" in str(error.value)
    assert "invalid api key" in str(error.value)
    # The key itself never becomes part of a message.
    assert "sk-test" not in str(error.value)


def test_chat_raises_on_a_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async def run() -> None:
        async with _client(handler) as client:
            await chat(_config(), [{"role": "user", "content": "hi"}], client=client)

    with pytest.raises(ProviderError) as error:
        asyncio.run(run())
    assert "connection refused" in str(error.value)


def test_chat_stream_reports_every_piece_and_returns_the_whole_answer() -> None:
    # The OpenAI-compatible framing: one `data:` line per packet, a usage-only
    # packet, then `[DONE]` (`plan/detail/ki-assistenz.md` §13).
    body = (
        'data: {"model":"m","choices":[{"delta":{"content":"Hallo "}}]}\n\n'
        'data: {"model":"m","choices":[{"delta":{"content":"Welt"}}]}\n\n'
        'data: {"model":"m","choices":[],"usage":{"total_tokens":7}}\n\n'
        "data: [DONE]\n\n"
    )
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=body)

    pieces: list[str] = []

    async def collect(text: str) -> None:
        pieces.append(text)

    async def run() -> Any:
        async with _client(handler) as client:
            return await chat_stream(
                _config(), [{"role": "user", "content": "hi"}], on_delta=collect, client=client
            )

    reply = asyncio.run(run())
    assert pieces == ["Hallo ", "Welt"]
    assert reply.text == "Hallo Welt"
    assert reply.usage["total_tokens"] == 7
    assert reply.model == "m"
    body_sent = seen["body"]
    assert isinstance(body_sent, dict)
    assert body_sent["stream"] is True


def test_chat_stream_raises_on_an_empty_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="data: [DONE]\n\n")

    async def run() -> None:
        async with _client(handler) as client:
            await chat_stream(_config(), [{"role": "user", "content": "hi"}], client=client)

    with pytest.raises(ProviderError) as error:
        asyncio.run(run())
    assert "empty completion" in str(error.value)
