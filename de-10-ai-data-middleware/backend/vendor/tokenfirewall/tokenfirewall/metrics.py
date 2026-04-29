"""Metrics helpers for TokenFirewall."""

from __future__ import annotations

import json
import sys
from typing import Any


def calculate_savings(
    baseline_input_tokens: int,
    baseline_output_tokens: int,
    optimized_input_tokens: int,
    optimized_output_tokens: int,
) -> dict[str, float | int]:
    """Calculate token savings between baseline and optimized paths."""

    baseline_total = int(baseline_input_tokens) + int(baseline_output_tokens)
    optimized_total = int(optimized_input_tokens) + int(optimized_output_tokens)
    saved_tokens = baseline_total - optimized_total
    saved_percent = (saved_tokens / baseline_total * 100.0) if baseline_total else 0.0
    return {
        "baseline_total": baseline_total,
        "optimized_total": optimized_total,
        "saved_tokens": saved_tokens,
        "saved_percent": saved_percent,
    }


def log_metrics(payload: dict[str, Any]) -> None:
    """Print one JSON metrics line with the supported fields."""

    keys = [
        "request_id",
        "user_id",
        "strategy",
        "model",
        "cache_hit",
        "tool_used",
        "input_tokens",
        "output_tokens",
        "saved_tokens",
        "saved_percent",
        "latency_ms",
        "cost_usd",
    ]
    line = {key: payload[key] for key in keys if key in payload}
    print(json.dumps(line, sort_keys=True, separators=(",", ":")), file=sys.stdout)
