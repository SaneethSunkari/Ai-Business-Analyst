"""Local HTTP gateway for OpenAI- and Anthropic-compatible clients."""

from __future__ import annotations

import json
import os
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import __version__
from .llm import LLMResponse, MockLLMClient, set_llm_client
from .main import MODE_OUTPUT_TOKENS, ask
from .tokenizer import count_messages_tokens, count_text_tokens


def _content_to_text(content: Any) -> str:
    """Convert provider content blocks to plain text for TokenFirewall."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "tool_result":
                    parts.append(_content_to_text(item.get("content", "")))
                else:
                    parts.append(json.dumps(item, sort_keys=True))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        return json.dumps(content, sort_keys=True)
    return str(content)


def _split_query_and_history(
    messages: list[dict[str, Any]],
    system: Any = None,
) -> tuple[str, list[dict[str, str]]]:
    """Split provider messages into TokenFirewall query and chat history."""

    normalized: list[dict[str, str]] = []
    if system:
        normalized.append({"role": "system", "content": _content_to_text(system)})
    for message in messages:
        role = str(message.get("role", "user"))
        if role not in {"system", "user", "assistant"}:
            role = "user"
        normalized.append({"role": role, "content": _content_to_text(message.get("content", ""))})

    last_user_index = next(
        (
            index
            for index in range(len(normalized) - 1, -1, -1)
            if normalized[index]["role"] == "user"
        ),
        len(normalized) - 1,
    )
    if last_user_index < 0:
        raise ValueError("messages must contain at least one message")
    query = normalized[last_user_index]["content"]
    history = normalized[:last_user_index]
    if not query.strip():
        raise ValueError("last user message must not be empty")
    return query, history


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _sse_response(handler: BaseHTTPRequestHandler, events: list[tuple[str | None, dict[str, Any] | str]]) -> None:
    handler.send_response(HTTPStatus.OK)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("cache-control", "no-cache")
    handler.end_headers()
    for event, payload in events:
        if event:
            handler.wfile.write(f"event: {event}\n".encode("utf-8"))
        data = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
        handler.wfile.write(f"data: {data}\n\n".encode("utf-8"))
    handler.wfile.flush()


class GatewayLLMClient:
    """Provider client used by the gateway for real upstream LLM calls."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_output_tokens: int = 300,
    ) -> LLMResponse:
        if os.environ.get("TOKENFIREWALL_FORCE_MOCK") == "1":
            return MockLLMClient().complete(messages, model, max_output_tokens)
        if model.lower().startswith("claude"):
            return self._complete_anthropic(messages, model, max_output_tokens)
        if os.environ.get("OPENAI_API_KEY"):
            return self._complete_openai(messages, model, max_output_tokens)
        if os.environ.get("ANTHROPIC_API_KEY"):
            return self._complete_anthropic(messages, model, max_output_tokens)
        return MockLLMClient().complete(messages, model, max_output_tokens)

    def _complete_openai(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_output_tokens: int,
    ) -> LLMResponse:
        start = time.perf_counter()
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI upstream calls")
        base_url = os.environ.get("TOKENFIREWALL_UPSTREAM_OPENAI_BASE_URL", "https://api.openai.com/v1")
        url = base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": str(message["role"]), "content": str(message["content"])}
                for message in messages
            ],
            "max_tokens": max_output_tokens,
        }
        response = _post_json(
            url,
            payload,
            headers={"authorization": f"Bearer {api_key}"},
        )
        answer = response["choices"][0]["message"].get("content") or ""
        usage = response.get("usage", {})
        return LLMResponse(
            answer=answer,
            input_tokens=int(usage.get("prompt_tokens") or count_messages_tokens(messages, model)),
            output_tokens=int(usage.get("completion_tokens") or count_text_tokens(answer, model)),
            latency_ms=int((time.perf_counter() - start) * 1000),
        )

    def _complete_anthropic(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_output_tokens: int,
    ) -> LLMResponse:
        start = time.perf_counter()
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for Anthropic upstream calls")
        system_messages = [
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "system"
        ]
        anthropic_messages = [
            {"role": str(message["role"]), "content": str(message["content"])}
            for message in messages
            if message.get("role") in {"user", "assistant"}
        ]
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_output_tokens,
            "messages": anthropic_messages,
        }
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)
        base_url = os.environ.get("TOKENFIREWALL_UPSTREAM_ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        response = _post_json(
            base_url.rstrip("/") + "/v1/messages",
            payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": os.environ.get("ANTHROPIC_VERSION", "2023-06-01"),
            },
        )
        answer = "\n".join(
            block.get("text", "")
            for block in response.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = response.get("usage", {})
        return LLMResponse(
            answer=answer,
            input_tokens=int(usage.get("input_tokens") or count_messages_tokens(messages, model)),
            output_tokens=int(usage.get("output_tokens") or count_text_tokens(answer, model)),
            latency_ms=int((time.perf_counter() - start) * 1000),
        )


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    encoded = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=encoded,
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urlopen(request, timeout=float(os.environ.get("TOKENFIREWALL_UPSTREAM_TIMEOUT", "120"))) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"upstream HTTP {exc.code}: {detail}") from exc


def _anthropic_requires_passthrough(payload: dict[str, Any]) -> bool:
    if payload.get("tools") or payload.get("tool_choice"):
        return os.environ.get("TOKENFIREWALL_GATEWAY_PASSTHROUGH_TOOLS", "1") == "1"
    for message in payload.get("messages", []):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and str(block.get("type", "")).startswith("tool"):
                    return os.environ.get("TOKENFIREWALL_GATEWAY_PASSTHROUGH_TOOLS", "1") == "1"
    return False


class TokenFirewallGatewayHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the local TokenFirewall gateway."""

    server_version = f"TokenFirewallGateway/{__version__}"

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path in {"/", "/health", "/healthz"}:
            _json_response(self, HTTPStatus.OK, {"ok": True, "version": __version__})
            return
        if self.path == "/v1/models":
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "object": "list",
                    "data": [
                        {"id": "gpt-4o-mini", "object": "model", "owned_by": "tokenfirewall"},
                        {"id": "gpt-4o", "object": "model", "owned_by": "tokenfirewall"},
                        {"id": "claude-3-5-sonnet-latest", "object": "model", "owned_by": "tokenfirewall"},
                    ],
                },
            )
            return
        _json_response(self, HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        try:
            payload = self._read_json()
            if self.path == "/v1/chat/completions":
                self._handle_openai_chat(payload)
                return
            if self.path == "/v1/messages":
                self._handle_anthropic_messages(payload)
                return
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})
        except ValueError as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": {"message": str(exc)}})
        except Exception as exc:
            _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": {"message": str(exc)}})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server API
        if os.environ.get("TOKENFIREWALL_GATEWAY_LOG", "0") == "1":
            super().log_message(format, *args)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length <= 0:
            raise ValueError("request body is required")
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _handle_openai_chat(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload.get("messages"), list):
            raise ValueError("messages must be a list")
        query, chat_history = _split_query_and_history(payload["messages"])
        max_tokens = int(payload.get("max_tokens") or payload.get("max_completion_tokens") or MODE_OUTPUT_TOKENS["normal"])
        result = ask(
            query,
            chat_history=chat_history,
            model=str(payload.get("model") or "") or None,
            max_output_tokens=max_tokens,
            mode=os.environ.get("TOKENFIREWALL_GATEWAY_MODE", "normal"),
            debug=True,
            force=self.headers.get("x-tokenfirewall-force") == "1",
        )
        if payload.get("stream"):
            self._openai_stream(result)
            return
        response_id = "chatcmpl-" + uuid.uuid4().hex
        _json_response(
            self,
            HTTPStatus.OK,
            {
                "id": response_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": result["selected_model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": result["answer"]},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": result["input_tokens"],
                    "completion_tokens": result["output_tokens"],
                    "total_tokens": result["optimized_tokens"],
                },
                "tokenfirewall": _gateway_metrics(result),
            },
        )

    def _handle_anthropic_messages(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload.get("messages"), list):
            raise ValueError("messages must be a list")
        if _anthropic_requires_passthrough(payload):
            self._anthropic_passthrough(payload)
            return
        query, chat_history = _split_query_and_history(payload["messages"], system=payload.get("system"))
        max_tokens = int(payload.get("max_tokens") or MODE_OUTPUT_TOKENS["normal"])
        result = ask(
            query,
            chat_history=chat_history,
            model=str(payload.get("model") or "") or None,
            max_output_tokens=max_tokens,
            mode=os.environ.get("TOKENFIREWALL_GATEWAY_MODE", "normal"),
            debug=True,
            force=self.headers.get("x-tokenfirewall-force") == "1",
        )
        if payload.get("stream"):
            self._anthropic_stream(result)
            return
        _json_response(
            self,
            HTTPStatus.OK,
            {
                "id": "msg_" + uuid.uuid4().hex,
                "type": "message",
                "role": "assistant",
                "model": result["selected_model"],
                "content": [{"type": "text", "text": result["answer"]}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": result["input_tokens"],
                    "output_tokens": result["output_tokens"],
                },
                "tokenfirewall": _gateway_metrics(result),
            },
        )

    def _openai_stream(self, result: dict[str, Any]) -> None:
        response_id = "chatcmpl-" + uuid.uuid4().hex
        created = int(time.time())
        model = result["selected_model"]
        _sse_response(
            self,
            [
                (
                    None,
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                    },
                ),
                (
                    None,
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": result["answer"]}, "finish_reason": None}],
                    },
                ),
                (
                    None,
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    },
                ),
                (None, "[DONE]"),
            ],
        )

    def _anthropic_stream(self, result: dict[str, Any]) -> None:
        message_id = "msg_" + uuid.uuid4().hex
        _sse_response(
            self,
            [
                (
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": message_id,
                            "type": "message",
                            "role": "assistant",
                            "model": result["selected_model"],
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": result["input_tokens"], "output_tokens": 0},
                        },
                    },
                ),
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": result["answer"]}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": result["output_tokens"]}}),
                ("message_stop", {"type": "message_stop"}),
            ],
        )

    def _anthropic_passthrough(self, payload: dict[str, Any]) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            _json_response(
                self,
                HTTPStatus.BAD_GATEWAY,
                {"error": {"message": "ANTHROPIC_API_KEY is required for tool-use passthrough"}},
            )
            return
        base_url = os.environ.get("TOKENFIREWALL_UPSTREAM_ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        headers = {
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": self.headers.get("anthropic-version") or os.environ.get("ANTHROPIC_VERSION", "2023-06-01"),
        }
        if self.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = self.headers["anthropic-beta"]
        request = Request(
            base_url.rstrip("/") + "/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=float(os.environ.get("TOKENFIREWALL_UPSTREAM_TIMEOUT", "120"))) as response:
                data = response.read()
                self.send_response(response.status)
                self.send_header("content-type", response.headers.get("content-type", "application/json"))
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except HTTPError as exc:
            detail = exc.read()
            self.send_response(exc.code)
            self.send_header("content-type", exc.headers.get("content-type", "application/json"))
            self.send_header("content-length", str(len(detail)))
            self.end_headers()
            self.wfile.write(detail)


def _gateway_metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy": result["strategy"],
        "cache_hit": result["cache_hit"],
        "tool_used": result["tool_used"],
        "saved_tokens": result["saved_tokens"],
        "saved_percent": result["saved_percent"],
        "estimated_cost_usd": result["estimated_cost_usd"],
        "budget_blocked": result["budget_blocked"],
        "fallback_used": result["fallback_used"],
    }


def create_server(host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    """Create a configured TokenFirewall gateway server."""

    set_llm_client(GatewayLLMClient())
    return ThreadingHTTPServer((host, port), TokenFirewallGatewayHandler)


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    """Run the local gateway until interrupted."""

    server = create_server(host, port)
    print(f"TokenFirewall gateway listening on http://{host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
