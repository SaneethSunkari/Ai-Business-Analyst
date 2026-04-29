"""Token counting helpers."""

from __future__ import annotations

from typing import Any

try:
    import tiktoken
except ImportError:  # pragma: no cover - exercised only when dependency is missing
    tiktoken = None  # type: ignore[assignment]


def _encoding_for_model(model: str):
    if tiktoken is None:
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        try:
            return tiktoken.get_encoding("o200k_base")
        except Exception:
            return tiktoken.get_encoding("cl100k_base")


def count_text_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    """Count tokens for a text string using tiktoken when available."""

    encoding = _encoding_for_model(model)
    if encoding is None:
        return len(text.split()) if text else 0
    return len(encoding.encode(text))


def count_messages_tokens(
    messages: list[dict[str, Any]],
    model: str = "gpt-4o-mini",
) -> int:
    """Approximately count structured chat messages consistently."""

    total = 3
    for message in messages:
        total += 3
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))
        total += count_text_tokens(role, model)
        total += count_text_tokens(content, model)
        if "name" in message:
            total += count_text_tokens(str(message["name"]), model)
    return max(total, 0)
