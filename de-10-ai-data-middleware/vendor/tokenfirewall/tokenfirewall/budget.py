"""Cost estimation, budget guardrails, and usage analytics."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cache import default_cache_path


@dataclass(frozen=True)
class Price:
    """Estimated model price per million tokens."""

    input_per_million: float
    output_per_million: float


DEFAULT_PRICES: dict[str, Price] = {
    "gpt-4o-mini": Price(input_per_million=0.15, output_per_million=0.60),
    "gpt-4o": Price(input_per_million=2.50, output_per_million=10.00),
    "gpt-4.1": Price(input_per_million=2.00, output_per_million=8.00),
    "tool": Price(input_per_million=0.0, output_per_million=0.0),
}


def usage_db_path() -> str:
    """Return the configured usage database path."""

    configured = os.environ.get("TOKENFIREWALL_USAGE_PATH")
    if configured:
        return configured
    cache_path = Path(default_cache_path())
    return str(cache_path.with_name("usage.sqlite3"))


def init_usage_db(path: str | None = None) -> sqlite3.Connection:
    """Initialize and return a SQLite usage connection."""

    db_path = Path(path or usage_db_path()).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            request_id TEXT NOT NULL,
            selected_model TEXT NOT NULL,
            strategy TEXT NOT NULL,
            cache_hit INTEGER NOT NULL,
            tool_used TEXT,
            llm_call INTEGER NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            baseline_tokens INTEGER NOT NULL,
            optimized_tokens INTEGER NOT NULL,
            saved_tokens INTEGER NOT NULL,
            estimated_cost_usd REAL NOT NULL,
            estimated_cost_saved_usd REAL NOT NULL,
            budget_blocked INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _float_env(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int_env(name: str) -> int | None:
    value = _float_env(name)
    if value is None:
        return None
    return int(value)


def _price_for_model(model: str) -> Price:
    input_override = _float_env(f"TOKENFIREWALL_PRICE_{model}_INPUT")
    output_override = _float_env(f"TOKENFIREWALL_PRICE_{model}_OUTPUT")
    if input_override is not None and output_override is not None:
        return Price(input_override, output_override)
    return DEFAULT_PRICES.get(model, DEFAULT_PRICES["gpt-4o"])


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate request cost in USD."""

    price = _price_for_model(model)
    return (
        max(input_tokens, 0) * price.input_per_million
        + max(output_tokens, 0) * price.output_per_million
    ) / 1_000_000


def _period_start(now: int, period: str) -> int:
    local = time.localtime(now)
    if period == "day":
        return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))
    return int(time.mktime((local.tm_year, local.tm_mon, 1, 0, 0, 0, 0, 0, -1)))


def tokens_used_since(start: int, path: str | None = None) -> int:
    """Return paid optimized tokens recorded since ``start``."""

    conn = init_usage_db(path)
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(optimized_tokens), 0) AS tokens
            FROM usage_events
            WHERE created_at >= ? AND budget_blocked = 0
            """,
            (start,),
        ).fetchone()
        return int(row["tokens"])
    finally:
        conn.close()


def budget_status(path: str | None = None, now: int | None = None) -> dict[str, int | None]:
    """Return configured budgets and current token usage."""

    timestamp = int(now or time.time())
    daily_used = tokens_used_since(_period_start(timestamp, "day"), path)
    monthly_used = tokens_used_since(_period_start(timestamp, "month"), path)
    return {
        "daily_token_budget": _int_env("TOKENFIREWALL_DAILY_TOKEN_BUDGET"),
        "monthly_token_budget": _int_env("TOKENFIREWALL_MONTHLY_TOKEN_BUDGET"),
        "daily_tokens_used": daily_used,
        "monthly_tokens_used": monthly_used,
    }


def check_budget(
    *,
    estimated_tokens: int,
    estimated_cost_usd: float,
    force: bool = False,
    max_cost: float | None = None,
    daily_token_budget: int | None = None,
    monthly_token_budget: int | None = None,
    path: str | None = None,
) -> tuple[bool, str | None, dict[str, int | None]]:
    """Return whether a request is allowed under configured budgets."""

    status = budget_status(path)
    daily_budget = (
        daily_token_budget
        if daily_token_budget is not None
        else status["daily_token_budget"]
    )
    monthly_budget = (
        monthly_token_budget
        if monthly_token_budget is not None
        else status["monthly_token_budget"]
    )
    if force:
        return True, None, status
    if max_cost is not None and estimated_cost_usd > max_cost:
        return False, "estimated cost exceeds max_cost", status
    if daily_budget is not None and int(status["daily_tokens_used"] or 0) + estimated_tokens > daily_budget:
        return False, "daily token budget exceeded", status
    if monthly_budget is not None and int(status["monthly_tokens_used"] or 0) + estimated_tokens > monthly_budget:
        return False, "monthly token budget exceeded", status
    return True, None, status


def record_usage_event(payload: dict[str, Any], path: str | None = None) -> None:
    """Persist one request usage event."""

    conn = init_usage_db(path)
    try:
        conn.execute(
            """
            INSERT INTO usage_events (
                created_at, request_id, selected_model, strategy, cache_hit, tool_used,
                llm_call, input_tokens, output_tokens, baseline_tokens, optimized_tokens,
                saved_tokens, estimated_cost_usd, estimated_cost_saved_usd, budget_blocked
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(payload.get("created_at", time.time())),
                str(payload.get("request_id", "")),
                str(payload.get("selected_model", "")),
                json.dumps(payload.get("strategy", []), separators=(",", ":")),
                int(bool(payload.get("cache_hit", False))),
                payload.get("tool_used"),
                int(bool(payload.get("llm_call", False))),
                int(payload.get("input_tokens", 0)),
                int(payload.get("output_tokens", 0)),
                int(payload.get("baseline_tokens", 0)),
                int(payload.get("optimized_tokens", 0)),
                int(payload.get("saved_tokens", 0)),
                float(payload.get("estimated_cost_usd", 0.0)),
                float(payload.get("estimated_cost_saved_usd", 0.0)),
                int(bool(payload.get("budget_blocked", False))),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def usage_analytics(path: str | None = None) -> dict[str, Any]:
    """Return aggregate usage analytics for the CLI."""

    conn = init_usage_db(path)
    try:
        rows = conn.execute(
            """
            SELECT strategy, cache_hit, tool_used, llm_call, saved_tokens,
                   estimated_cost_saved_usd, budget_blocked
            FROM usage_events
            """
        ).fetchall()
    finally:
        conn.close()

    strategies: Counter[str] = Counter()
    for row in rows:
        try:
            strategy = ",".join(json.loads(row["strategy"]))
        except Exception:
            strategy = str(row["strategy"])
        strategies[strategy] += 1

    return {
        "total_requests": len(rows),
        "cache_hits": sum(int(row["cache_hit"]) for row in rows),
        "tool_bypasses": sum(1 for row in rows if row["tool_used"]),
        "llm_calls": sum(int(row["llm_call"]) for row in rows),
        "budget_blocks": sum(int(row["budget_blocked"]) for row in rows),
        "total_estimated_tokens_saved": sum(int(row["saved_tokens"]) for row in rows),
        "total_estimated_cost_saved": sum(float(row["estimated_cost_saved_usd"]) for row in rows),
        "top_strategies": strategies.most_common(10),
    }
