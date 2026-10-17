"""Provider client: one OpenAI-compatible chat request, no provider library.

Meadow speaks the **OpenAI chat-completions protocol**, which Azure OpenAI,
OpenAI, DeepSeek and a local Ollama instance all understand — only the URL, the
header and the model name differ (`plan/detail/ki-assistenz.md` §3). That keeps
the dependency list at `httpx` (already present) instead of pulling in a provider
SDK per vendor.

The module holds no state and no secret: the API key arrives per call from the
core (which resolved it from the settings registry) and is used for exactly one
request. `plan/detail/ki-assistenz.md` §4.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

import httpx

#: Azure OpenAI pins the API version in the URL; this is the one Meadow requests.
AZURE_API_VERSION = "2024-10-21"

#: Providers that speak the plain OpenAI layout (`{endpoint}/chat/completions`).
_OPENAI_LAYOUT = ("generic", "openai", "ollama")


class ProviderError(RuntimeError):
    """The provider could not be reached or answered with an error."""


@dataclass(frozen=True)
class ProviderConfig:
    """Everything one provider call needs, as resolved by the core."""

    provider: str = "generic"
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float = 0.2
    max_output_tokens: int = 2048
    timeout_ms: int = 30_000
    max_requests_per_hour: int = 60

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ProviderConfig:
        """Builds the configuration from the core's `config` object.

        Tolerant on purpose: a value the core did not send falls back to the
        documented default instead of failing the call.
        """

        def number(key: str, default: float) -> float:
            value = payload.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            return default

        def text(key: str) -> str:
            value = payload.get(key)
            return value.strip() if isinstance(value, str) else ""

        return cls(
            provider=text("provider") or "generic",
            endpoint=text("endpoint").rstrip("/"),
            model=text("model"),
            api_key=text("api_key"),
            temperature=number("temperature", 0.2),
            max_output_tokens=int(number("max_output_tokens", 2048)),
            timeout_ms=int(number("timeout_ms", 30_000)),
            max_requests_per_hour=int(number("max_requests_per_hour", 60)),
        )

    def missing(self) -> list[str]:
        """Settings a call cannot work without (an empty list means ready)."""
        missing: list[str] = []
        if not self.endpoint:
            missing.append("ai_endpoint")
        if not self.model:
            missing.append("ai_model")
        if not self.api_key and self.provider != "ollama":
            missing.append("ai_api_key")
        return missing

    def request_target(self) -> tuple[str, dict[str, str]]:
        """The URL and headers for this provider.

        `ollama` uses its OpenAI-compatible endpoint and needs no key; the
        `api-key` header is Azure's convention, `Authorization: Bearer` is
        everyone else's. A trailing slash in the configured endpoint is dropped
        here, so a URL never contains `//`.
        """
        endpoint = self.endpoint.rstrip("/")
        if self.provider == "azure_openai":
            url = (
                f"{endpoint}/openai/deployments/{self.model}"
                f"/chat/completions?api-version={AZURE_API_VERSION}"
            )
            headers = {"api-key": self.api_key} if self.api_key else {}
            return url, headers
        if self.provider == "ollama":
            url = f"{endpoint}/v1/chat/completions"
        elif self.provider in _OPENAI_LAYOUT:
            url = f"{endpoint}/chat/completions"
        else:  # defensive: the core validates the enum, this keeps the URL sane
            url = f"{endpoint}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        return url, headers


class ChatReply(NamedTuple):
    """One provider answer."""

    text: str
    model: str
    latency_ms: int
    usage: dict[str, Any]


def _body(config: ProviderConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    """The chat-completions body (temperature low: this is code, not prose)."""
    return {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_output_tokens,
        "stream": False,
    }


def parse_reply(payload: Mapping[str, Any]) -> ChatReply:
    """Extracts the answer text from a chat-completions response.

    Raises [`ProviderError`][meadow.ai.provider.ProviderError] with the provider's
    own message when the response has no usable choice — a silent empty answer
    would look like a model that returned nothing.
    """
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        error = payload.get("error")
        detail = error.get("message") if isinstance(error, Mapping) else None
        raise ProviderError(f"provider returned no completion: {detail or payload}")
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    text = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("provider returned an empty completion")
    usage = payload.get("usage")
    return ChatReply(
        text=text,
        model=str(payload.get("model") or ""),
        latency_ms=0,  # filled by `chat`, which measured it
        usage=dict(usage) if isinstance(usage, Mapping) else {},
    )


async def chat(
    config: ProviderConfig,
    messages: list[dict[str, str]],
    *,
    client: httpx.AsyncClient | None = None,
) -> ChatReply:
    """Sends one chat request and returns the answer.

    Every failure mode (transport, HTTP status, unparseable body) becomes a
    [`ProviderError`][meadow.ai.provider.ProviderError] — the caller reports it,
    it never degrades into a plausible-looking suggestion.
    """
    url, headers = config.request_target()
    timeout = httpx.Timeout(config.timeout_ms / 1000)
    started = time.monotonic()
    try:
        if client is not None:
            response = await client.post(url, json=_body(config, messages), headers=headers)
        else:
            async with httpx.AsyncClient(timeout=timeout) as owned:
                response = await owned.post(url, json=_body(config, messages), headers=headers)
    except httpx.HTTPError as error:
        raise ProviderError(f"provider request failed: {error}") from error
    latency_ms = int((time.monotonic() - started) * 1000)

    if response.status_code >= 400:
        raise ProviderError(f"provider answered {response.status_code}: {_error_text(response)}")
    try:
        payload = response.json()
    except ValueError as error:
        raise ProviderError(f"provider answered with invalid JSON: {error}") from error
    reply = parse_reply(payload)
    return reply._replace(latency_ms=latency_ms, model=reply.model or config.model)


async def chat_stream(
    config: ProviderConfig,
    messages: list[dict[str, str]],
    *,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    client: httpx.AsyncClient | None = None,
) -> ChatReply:
    """One chat request with `stream: true`; reports each text piece as it arrives.

    Returns the same [`ChatReply`] as [`chat`] once the provider is done, so the
    caller keeps one shape. The pieces reported through `on_delta` are the **raw**
    provider output — the streaming path is only used for prose (`explain`), where
    nothing has to pass a policy check first (`plan/detail/ki-assistenz.md` §13).
    An unchanged OpenAI-compatible endpoint answers `data: {...}` lines and closes
    with `data: [DONE]`; `usage` may arrive in the last packet (and is optional).
    """
    url, headers = config.request_target()
    body = _body(config, messages)
    body["stream"] = True
    timeout = httpx.Timeout(config.timeout_ms / 1000)
    started = time.monotonic()
    pieces: list[str] = []
    usage: dict[str, Any] = {}
    model = config.model
    owned = client is None
    stream_client = client or httpx.AsyncClient(timeout=timeout)
    try:
        async with stream_client.stream("POST", url, json=body, headers=headers) as response:
            if response.status_code >= 400:
                await response.aread()
                raise ProviderError(
                    f"provider answered {response.status_code}: {_error_text(response)}"
                )
            async for line in response.aiter_lines():
                data = line.strip()
                if not data.startswith("data:"):
                    continue
                data = data[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    packet = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(packet, Mapping):
                    continue
                if isinstance(packet.get("usage"), Mapping):
                    usage = dict(packet["usage"])
                model = str(packet.get("model") or model)
                choices = packet.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                delta = choices[0].get("delta") if isinstance(choices[0], Mapping) else None
                text = delta.get("content") if isinstance(delta, Mapping) else None
                if isinstance(text, str) and text:
                    pieces.append(text)
                    if on_delta is not None:
                        await on_delta(text)
    except httpx.HTTPError as error:
        raise ProviderError(f"provider request failed: {error}") from error
    finally:
        if owned:
            await stream_client.aclose()
    latency_ms = int((time.monotonic() - started) * 1000)
    answer = "".join(pieces)
    if not answer.strip():
        raise ProviderError("provider returned an empty completion")
    return ChatReply(text=answer, model=model, latency_ms=latency_ms, usage=usage)


def _error_text(response: httpx.Response) -> str:
    """The provider's error message, trimmed to something a log line can hold."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("message"), str):
            return str(error["message"])[:300]
        if isinstance(error, str):
            return error[:300]
    return response.text[:300]
