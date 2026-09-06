"""OpenRouter client: request construction and response handling.

Every test here mocks urllib.request.urlopen. No test performs real network
I/O or requires OPENROUTER_API_KEY to be set -- CLAUDE.md invariant.
"""

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from anki_cardgen.openrouter import (
    API_KEY_ENV_VAR,
    OpenRouterClient,
    OpenRouterError,
)


def fake_response(body: dict, status: int = 200):
    """A context-manager stand-in for what urlopen returns."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


SUCCESS_BODY = {
    "choices": [{"message": {"content": "hello"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0012},
}


class TestApiKeyHandling:
    def test_missing_key_raises_before_any_network_call(self, monkeypatch):
        monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
        client = OpenRouterClient()

        with patch("urllib.request.urlopen") as urlopen:
            with pytest.raises(OpenRouterError, match=API_KEY_ENV_VAR):
                client.complete([{"role": "user", "content": "x"}], model="m")
            urlopen.assert_not_called()

    def test_explicit_key_overrides_environment(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "env-key")
        client = OpenRouterClient(api_key="explicit-key")
        assert client.api_key == "explicit-key"

    def test_reads_key_lazily_not_at_construction(self, monkeypatch):
        # Constructing a client with no key set must not raise -- only using
        # it should. This is what keeps every other test in this file from
        # needing a key.
        monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
        OpenRouterClient()  # must not raise


class TestRequestConstruction:
    def test_sends_bearer_auth_and_json_body(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "sk-test-123")
        client = OpenRouterClient()

        with patch("urllib.request.urlopen", return_value=fake_response(SUCCESS_BODY)) as urlopen:
            client.complete([{"role": "user", "content": "hi"}], model="a/b")

        request = urlopen.call_args[0][0]
        assert request.get_header("Authorization") == "Bearer sk-test-123"
        assert request.get_header("Content-type") == "application/json"
        body = json.loads(request.data)
        assert body["model"] == "a/b"
        assert body["messages"] == [{"role": "user", "content": "hi"}]

    def test_temperature_defaults_to_zero(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "k")
        client = OpenRouterClient()
        with patch("urllib.request.urlopen", return_value=fake_response(SUCCESS_BODY)) as urlopen:
            client.complete([{"role": "user", "content": "hi"}], model="a/b")
        assert json.loads(urlopen.call_args[0][0].data)["temperature"] == 0.0

    def test_response_format_included_when_given(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "k")
        client = OpenRouterClient()
        fmt = {"type": "json_schema", "json_schema": {"name": "x"}}
        with patch("urllib.request.urlopen", return_value=fake_response(SUCCESS_BODY)) as urlopen:
            client.complete([{"role": "user", "content": "hi"}], model="a/b", response_format=fmt)
        assert json.loads(urlopen.call_args[0][0].data)["response_format"] == fmt

    def test_missing_model_is_refused_before_any_network_call(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "k")
        client = OpenRouterClient()
        with patch("urllib.request.urlopen") as urlopen:
            with pytest.raises(OpenRouterError, match="no model configured"):
                client.complete([{"role": "user", "content": "hi"}], model="")
            urlopen.assert_not_called()


class TestResponseParsing:
    def test_extracts_content_and_usage(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "k")
        client = OpenRouterClient()
        with patch("urllib.request.urlopen", return_value=fake_response(SUCCESS_BODY)):
            result = client.complete([{"role": "user", "content": "hi"}], model="a/b")

        assert result.content == "hello"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5
        assert result.cost == 0.0012

    def test_missing_cost_field_is_none_not_zero(self, monkeypatch):
        """Some providers don't report cost. None must mean 'unknown', not
        silently presenting a fabricated $0.00 that looks like a real answer."""
        monkeypatch.setenv(API_KEY_ENV_VAR, "k")
        client = OpenRouterClient()
        body = {"choices": [{"message": {"content": "x"}}], "usage": {}}
        with patch("urllib.request.urlopen", return_value=fake_response(body)):
            result = client.complete([{"role": "user", "content": "hi"}], model="a/b")
        assert result.cost is None

    def test_malformed_response_raises(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "k")
        client = OpenRouterClient()
        with patch("urllib.request.urlopen", return_value=fake_response({"nope": True})):
            with pytest.raises(OpenRouterError, match="unexpected response shape"):
                client.complete([{"role": "user", "content": "hi"}], model="a/b")


class TestTransportErrors:
    def test_http_error_is_wrapped(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "k")
        client = OpenRouterClient()
        error = urllib.error.HTTPError(
            "url", 429, "Too Many Requests", {}, BytesIO(b'{"error": "rate limited"}')
        )
        try:
            with patch("urllib.request.urlopen", side_effect=error):
                with pytest.raises(OpenRouterError, match="429"):
                    client.complete([{"role": "user", "content": "hi"}], model="a/b")
        finally:
            error.close()  # HTTPError wraps a file object; GC-time ResourceWarning
                            # would otherwise trip the suite's -W error config.

    def test_url_error_is_wrapped(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "k")
        client = OpenRouterClient()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
            with pytest.raises(OpenRouterError, match="could not reach OpenRouter"):
                client.complete([{"role": "user", "content": "hi"}], model="a/b")
