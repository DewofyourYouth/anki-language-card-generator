"""Thin OpenRouter client.

One key, one client, per CLAUDE.md -- this module is used by LLM extraction
today and will be reused for Pipeline 2's image generation later. It is
deliberately minimal: stdlib `urllib.request` rather than `requests`/`httpx`,
because a single POST per call does not justify a new dependency.

The API key is read from the environment *at call time*, not at import time or
construction time, so importing this module -- or constructing a client with
no key set -- never fails. Every test mocks `OpenRouterClient.complete`
directly; no test performs real I/O or needs a key.
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

API_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY_ENV_VAR = "OPENROUTER_API_KEY"

# OpenRouter (and most providers behind it) send prompt/completion cost in USD
# once a request completes when usage accounting is requested. Not all models
# report cost -- see OpenRouterResponse.cost.
_TIMEOUT_SECONDS = 120


class OpenRouterError(Exception):
    """The API call failed, or its response could not be understood.

    Deliberately one exception type for both transport failures (network,
    HTTP error, timeout) and response-shape failures (malformed JSON, missing
    fields) -- a caller deciding what to do about a failed extraction does not
    need to distinguish them.
    """


@dataclass(frozen=True)
class OpenRouterResponse:
    """The part of a chat completion response callers actually need."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    cost: float | None  # USD; None when the provider doesn't report it


class OpenRouterClient:
    """A single OpenRouter chat-completions call."""

    def __init__(self, api_key: str | None = None) -> None:
        # Explicit override for tests; production callers rely on the
        # environment so the key is never threaded through config.yaml or
        # logged as part of a config dump.
        self._api_key_override = api_key

    @property
    def api_key(self) -> str:
        key = self._api_key_override or os.environ.get(API_KEY_ENV_VAR)
        if not key:
            raise OpenRouterError(
                f"{API_KEY_ENV_VAR} is not set. Copy .env.example to .env and "
                f"fill it in."
            )
        return key

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        response_format: dict | None = None,
        temperature: float = 0.0,
    ) -> OpenRouterResponse:
        """Send one chat-completions request and return the parsed result.

        ``temperature=0.0`` by default: extraction is a transcription task,
        not a creative one, and low temperature makes runs more reproducible
        -- important when the same lesson might be re-extracted after a
        wording tweak.
        """
        if not model:
            raise OpenRouterError(
                "no model configured. Set llm_model in config.yaml -- verify "
                "the slug against openrouter.ai first; model names churn."
            )

        body: dict = {"model": model, "messages": messages, "temperature": temperature}
        if response_format is not None:
            body["response_format"] = response_format

        request = urllib.request.Request(
            API_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenRouterError(f"OpenRouter returned {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OpenRouterError(f"could not reach OpenRouter: {exc.reason}") from exc

        return self._parse(raw)

    @staticmethod
    def _parse(raw: bytes) -> OpenRouterResponse:
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError(
                f"unexpected response shape from OpenRouter: {raw[:500]!r}"
            ) from exc

        return OpenRouterResponse(
            content=content,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cost=usage.get("cost"),
        )
