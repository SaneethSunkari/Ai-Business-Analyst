"""SQLite-backed response cache for TokenFirewall."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")
_CAPITAL_PATTERNS = (
    re.compile(
        r"^(?:what\s+is|what's|tell\s+me|please\s+tell\s+me|can\s+you\s+tell\s+me)\s+"
        r"(?:the\s+)?capital\s+of\s+(.+?)\??$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:the\s+)?capital\s+of\s+(.+?)\??$", re.IGNORECASE),
)
_CAPITAL_ENTITY_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,80}$")
_PERSONAL_PROMPT_RE = re.compile(
    r"^(?:(?:please|can\s+you|could\s+you)\s+)?"
    r"(explain|summarize|improve|debug|rewrite)\s+this(?:\s*[:\-]\s*|\s+)(.{8,})$",
    re.IGNORECASE | re.DOTALL,
)


def normalize_text(value: str) -> str:
    """Trim and collapse whitespace without changing case or punctuation."""

    return _WHITESPACE_RE.sub(" ", value.strip())


def canonicalize_prompt_for_cache(value: str) -> str:
    """Apply minimal safe prompt canonicalization for cache keys.

    Only high-confidence factual capital-city patterns are collapsed across
    paraphrases. Other prompts keep their meaning-preserving text form.
    """

    normalized = normalize_text(value)
    for pattern in _CAPITAL_PATTERNS:
        match = pattern.match(normalized)
        if not match:
            continue
        entity = normalize_text(match.group(1)).rstrip("?.! ")
        if not _CAPITAL_ENTITY_RE.fullmatch(entity):
            return normalized
        return f"fact:capital_of:{entity}"
    personal_match = _PERSONAL_PROMPT_RE.match(normalized)
    if personal_match:
        action = personal_match.group(1).lower()
        content = normalize_text(personal_match.group(2))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return f"personal:{action}:content_sha256:{content_hash}"
    return normalized


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_value(value[key]) for key in sorted(value)}
    return value


def canonical_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return canonical structured messages for stable cache keying."""

    canonical: list[dict[str, Any]] = []
    for message in messages:
        normalized = _normalize_value(message)
        if (
            isinstance(normalized, dict)
            and normalized.get("role") == "user"
            and isinstance(normalized.get("content"), str)
        ):
            normalized["content"] = canonicalize_prompt_for_cache(normalized["content"])
        canonical.append(normalized)
    return canonical


def default_cache_path() -> str:
    """Return the configured cache path, creating no files by itself."""

    configured = os.environ.get("TOKENFIREWALL_CACHE_PATH")
    if configured:
        return configured
    return str(Path.home() / ".cache" / "tokenfirewall" / "cache.sqlite3")


def init_db(path: str | None = None) -> sqlite3.Connection:
    """Initialize and return a SQLite connection for the cache database."""

    db_path = Path(path or default_cache_path()).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            response TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            model TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def make_key(
    messages: list[dict[str, Any]],
    model: str,
    notes_hash: str | None,
    version: str,
    tools_version: str,
    mode: str = "normal",
) -> str:
    """Build a stable cache key from structured messages and runtime versions."""

    canonical = canonical_messages(messages)
    canonical_encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    content_hash = hashlib.sha256(canonical_encoded.encode("utf-8")).hexdigest()
    system_content = [
        message.get("content", "")
        for message in canonical
        if message.get("role") == "system"
    ]
    payload = {
        "messages": canonical,
        "content_hash": content_hash,
        "model": normalize_text(model),
        "mode": normalize_text(mode),
        "system_content": system_content,
        "notes_hash": normalize_text(notes_hash) if notes_hash else None,
        "firewall_version": version,
        "tools_version": tools_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cache_get(key: str, path: str | None = None) -> dict[str, Any] | None:
    """Return a cached response by key, or ``None`` when absent."""

    conn = init_db(path)
    try:
        row = conn.execute(
            """
            SELECT key, response, input_tokens, output_tokens, created_at, strategy, model
            FROM cache
            WHERE key = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "key": row["key"],
            "response": row["response"],
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
            "created_at": int(row["created_at"]),
            "strategy": json.loads(row["strategy"]),
            "model": row["model"],
        }
    finally:
        conn.close()


def cache_set(
    key: str,
    response: str,
    input_tokens: int,
    output_tokens: int,
    strategy: list[str],
    model: str,
    path: str | None = None,
) -> None:
    """Persist a successful LLM response."""

    conn = init_db(path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO cache
                (key, response, input_tokens, output_tokens, created_at, strategy, model)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                response,
                int(input_tokens),
                int(output_tokens),
                int(time.time()),
                json.dumps(strategy, separators=(",", ":")),
                model,
            ),
        )
        conn.commit()
    finally:
        conn.close()
