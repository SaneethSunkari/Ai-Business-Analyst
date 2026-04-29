"""Safe context pruning."""

from __future__ import annotations

import json
import re
from typing import Any

from .cache import canonical_messages

_DURABLE_FACT_PATTERNS = (
    re.compile(r"\bmy\s+name\s+is\s+[^.?!,;]+", re.IGNORECASE),
    re.compile(r"\bi\s+work\s+at\s+[^.?!,;]+", re.IGNORECASE),
    re.compile(r"\bproject\s+name\s+is\s+[^.?!,;]+", re.IGNORECASE),
    re.compile(r"^\s*remember\s+.{3,}", re.IGNORECASE),
)


def _message_key(message: dict[str, Any]) -> str:
    return json.dumps(canonical_messages([message])[0], sort_keys=True, separators=(",", ":"))


def _tags_intersect(message: dict[str, Any], important_tags: set[str] | None) -> bool:
    if not important_tags:
        return False
    raw_tags = message.get("tags", [])
    if isinstance(raw_tags, str):
        tags = {raw_tags}
    else:
        try:
            tags = {str(tag) for tag in raw_tags}
        except TypeError:
            tags = set()
    return bool(tags & important_tags)


def is_auto_important(message: dict[str, Any]) -> bool:
    """Return true for simple durable user facts worth preserving."""

    if message.get("role") != "user":
        return False
    content = str(message.get("content", ""))
    if re.search(r"\bmy\s+friend\b", content, re.IGNORECASE):
        return False
    return any(pattern.search(content) for pattern in _DURABLE_FACT_PATTERNS)


def tag_auto_important_messages(
    chat_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy messages and mark simple durable facts as important."""

    tagged: list[dict[str, Any]] = []
    for message in chat_history:
        copied = dict(message)
        if is_auto_important(copied):
            copied["important"] = True
            raw_tags = copied.get("tags", [])
            if isinstance(raw_tags, str):
                tags = {raw_tags}
            else:
                try:
                    tags = {str(tag) for tag in raw_tags}
                except TypeError:
                    tags = set()
            tags.add("durable_fact")
            copied["tags"] = sorted(tags)
        tagged.append(copied)
    return tagged


def prune_context(
    chat_history: list[dict[str, Any]],
    max_turns: int = 5,
    important_tags: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Prune chat history while preserving required context and chronology."""

    if not chat_history:
        return []
    if max_turns < 0:
        raise ValueError("max_turns must be non-negative")

    chat_history = tag_auto_important_messages(chat_history)
    keep_indices: set[int] = set()
    for index, message in enumerate(chat_history):
        if message.get("role") == "system":
            keep_indices.add(index)
        if message.get("important") is True:
            keep_indices.add(index)
        if _tags_intersect(message, important_tags):
            keep_indices.add(index)

    user_indices = [
        index
        for index, message in enumerate(chat_history)
        if message.get("role") == "user"
    ]
    selected_user_indices = set(user_indices[-max_turns:]) if max_turns else set()

    for user_index in selected_user_indices:
        keep_indices.add(user_index)
        next_user_index = None
        for later_index in user_indices:
            if later_index > user_index:
                next_user_index = later_index
                break
        stop = next_user_index if next_user_index is not None else len(chat_history)
        for index in range(user_index + 1, stop):
            if chat_history[index].get("role") == "assistant":
                keep_indices.add(index)

    pruned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, message in enumerate(chat_history):
        if index not in keep_indices:
            continue
        key = _message_key(message)
        if key in seen:
            continue
        seen.add(key)
        pruned.append(dict(message))
    return pruned
