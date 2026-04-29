from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest

from tokenfirewall.llm import set_llm_client
from tokenfirewall.server import create_server


@pytest.fixture(autouse=True)
def isolated_gateway_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENFIREWALL_CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    monkeypatch.setenv("TOKENFIREWALL_USAGE_PATH", str(tmp_path / "usage.sqlite3"))
    monkeypatch.setenv("TOKENFIREWALL_FORCE_MOCK", "1")
    set_llm_client(None)
    yield
    set_llm_client(None)


@pytest.fixture
def gateway():
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(server, method: str, path: str, payload: dict | None = None):
    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    body = None if payload is None else json.dumps(payload)
    headers = {"content-type": "application/json"} if payload is not None else {}
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, response.getheaders(), data


def test_health_endpoint(gateway) -> None:
    status, _, data = _request(gateway, "GET", "/health")

    assert status == 200
    assert json.loads(data)["ok"] is True


def test_openai_chat_completion_endpoint(gateway) -> None:
    status, _, data = _request(
        gateway,
        "POST",
        "/v1/chat/completions",
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is 234 * 98?"}],
            "max_tokens": 80,
        },
    )

    payload = json.loads(data)
    assert status == 200
    assert payload["choices"][0]["message"]["content"] == "22932"
    assert payload["tokenfirewall"]["tool_used"] == "math"


def test_openai_gateway_cache_reuse(gateway) -> None:
    request = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Explain cache reuse briefly."}],
        "max_tokens": 80,
    }

    first_status, _, first_data = _request(gateway, "POST", "/v1/chat/completions", request)
    second_status, _, second_data = _request(gateway, "POST", "/v1/chat/completions", request)

    first = json.loads(first_data)
    second = json.loads(second_data)
    assert first_status == 200
    assert second_status == 200
    assert first["tokenfirewall"]["cache_hit"] is False
    assert second["tokenfirewall"]["cache_hit"] is True


def test_anthropic_messages_endpoint(gateway) -> None:
    status, _, data = _request(
        gateway,
        "POST",
        "/v1/messages",
        {
            "model": "claude-3-5-sonnet-latest",
            "max_tokens": 80,
            "system": "Be concise.",
            "messages": [{"role": "user", "content": "What is 12.5% of 800"}],
        },
    )

    payload = json.loads(data)
    assert status == 200
    assert payload["type"] == "message"
    assert payload["content"][0]["text"] == "100"
    assert payload["tokenfirewall"]["tool_used"] == "math"


def test_anthropic_stream_endpoint(gateway) -> None:
    status, headers, data = _request(
        gateway,
        "POST",
        "/v1/messages",
        {
            "model": "claude-3-5-sonnet-latest",
            "max_tokens": 80,
            "stream": True,
            "messages": [{"role": "user", "content": "What is 2 + 2"}],
        },
    )

    content_type = dict((key.lower(), value) for key, value in headers).get("content-type", "")
    text = data.decode("utf-8")
    assert status == 200
    assert "text/event-stream" in content_type
    assert "event: message_start" in text
    assert "event: content_block_delta" in text
    assert "4" in text
