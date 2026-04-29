"""Deterministic rule-based model routing."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .tools import file_read_detect, math_detect


@dataclass(frozen=True)
class RouterConfig:
    """Model routing configuration."""

    cheap_model: str = "gpt-4o-mini"
    default_model: str = "gpt-4o"
    strong_model: str = "gpt-4o"


@dataclass(frozen=True)
class RoutingDecision:
    """Selected model and route reason."""

    selected_model: str
    reason: str


_FACTUAL_RE = re.compile(
    r"\b(?:what\s+is|who\s+is|where\s+is|when\s+is|capital\s+of|define|explain\s+briefly)\b",
    re.IGNORECASE,
)
_SUMMARY_RE = re.compile(r"\b(?:summarize|summary|tl;dr|recap)\b", re.IGNORECASE)
_STRONG_RE = re.compile(
    r"\b(?:code|debug|traceback|exception|architecture|design\s+review|deep\s+reasoning|"
    r"prove|optimize|performance|refactor|security\s+review|system\s+design)\b",
    re.IGNORECASE,
)


def router_config_from_env() -> RouterConfig:
    """Load routing config from environment variables."""

    return RouterConfig(
        cheap_model=os.environ.get("TOKENFIREWALL_CHEAP_MODEL", "gpt-4o-mini"),
        default_model=os.environ.get("TOKENFIREWALL_DEFAULT_MODEL", "gpt-4o"),
        strong_model=os.environ.get("TOKENFIREWALL_STRONG_MODEL", "gpt-4o"),
    )


def route_query(
    query: str,
    model_override: str | None = None,
    config: RouterConfig | None = None,
) -> RoutingDecision:
    """Select a model using deterministic rules only."""

    if config is None:
        config = router_config_from_env()
    if model_override:
        return RoutingDecision(model_override, "user_override")
    if math_detect(query) or file_read_detect(query):
        return RoutingDecision("tool", "tool_candidate")
    if _STRONG_RE.search(query):
        return RoutingDecision(config.strong_model, "strong_rule")
    if _FACTUAL_RE.search(query) or _SUMMARY_RE.search(query):
        return RoutingDecision(config.cheap_model, "cheap_rule")
    if len(query.split()) <= 18:
        return RoutingDecision(config.cheap_model, "short_query_rule")
    return RoutingDecision(config.default_model, "default_rule")
