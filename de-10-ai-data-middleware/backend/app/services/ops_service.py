from __future__ import annotations

import os
from statistics import mean
from typing import Any

import requests

from app.services import control_plane_service, log_service
from app.services.llm_service import get_gateway_base_url
from tokenfirewall.budget import budget_status, usage_analytics


def _auth_configured() -> bool:
    return bool(
        os.getenv("SUPABASE_URL", "").strip()
        and os.getenv("SUPABASE_ANON_KEY", "").strip()
        and os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _environment_name() -> str:
    return (
        os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip()
        or os.getenv("RAILWAY_ENVIRONMENT", "").strip()
        or os.getenv("ENVIRONMENT", "").strip()
        or "development"
    )


def _gateway_health_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        trimmed = trimmed[:-3]
    return trimmed.rstrip("/") + "/health"


def _gateway_health(base_url: str | None) -> bool | None:
    if not base_url:
        return None
    try:
        response = requests.get(_gateway_health_url(base_url), timeout=2)
        return response.ok
    except Exception:
        return False


def _summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    success_count = sum(1 for run in runs if run.get("success"))
    failure_count = len(runs) - success_count
    latencies = [int(run["latency_ms"]) for run in runs if run.get("latency_ms") is not None]
    latest = runs[0] if runs else None
    return {
        "recent_query_count": len(runs),
        "recent_success_count": success_count,
        "recent_failure_count": failure_count,
        "avg_latency_ms": int(mean(latencies)) if latencies else None,
        "latest_question": latest.get("question") if latest else None,
    }


def _recent_runs_for_workspace(
    organization_id: str | None,
    user_id: str | None,
) -> list[dict[str, Any]]:
    if control_plane_service.control_plane_enabled():
        try:
            return control_plane_service.list_recent_query_runs(
                limit=8,
                organization_id=organization_id,
                user_id=user_id,
            )
        except Exception:
            pass
    return [
        {
            "created_at": row.get("timestamp"),
            "question": row.get("question"),
            "generated_sql": row.get("generated_sql"),
            "success": row.get("success"),
            "row_count": row.get("row_count"),
            "error": row.get("error"),
            "connection_id": row.get("connection_id"),
            "latency_ms": row.get("latency_ms"),
        }
        for row in log_service.read_recent_query_logs(limit=8)
    ]


def _saved_source_count(
    organization_id: str | None,
    user_id: str | None,
) -> int:
    if not control_plane_service.control_plane_enabled():
        return 0
    try:
        return len(
            control_plane_service.list_saved_sources(
                organization_id=organization_id,
                user_id=user_id,
            )
        )
    except Exception:
        return 0


def _onboarding_items(
    *,
    authenticated: bool,
    organization_id: str | None,
    saved_source_count: int,
    recent_query_count: int,
    tokenfirewall_routed: bool,
    tokenfirewall_healthy: bool | None,
) -> list[dict[str, Any]]:
    return [
        {
            "key": "sign_in",
            "label": "Sign in to a workspace",
            "done": authenticated,
            "detail": "Users need a workspace session before saved sources and query history become isolated.",
        },
        {
            "key": "workspace",
            "label": "Provision a company workspace",
            "done": bool(organization_id),
            "detail": "A workspace gives the company a real home for connectors, history, and governance.",
        },
        {
            "key": "saved_source",
            "label": "Save at least one reusable source",
            "done": saved_source_count > 0,
            "detail": "Saved sources keep teammates from re-entering credentials and make AI flows repeatable.",
        },
        {
            "key": "first_query",
            "label": "Run live questions through the product",
            "done": recent_query_count > 0,
            "detail": "Recent activity confirms the workspace is doing real work, not just configuration.",
        },
        {
            "key": "cost_control",
            "label": "Keep AI traffic behind cost controls",
            "done": tokenfirewall_routed and bool(tokenfirewall_healthy),
            "detail": "When TokenFirewall is active, repeated or cheaper-routable questions can avoid unnecessary spend.",
        },
    ]


def build_ops_status(
    *,
    authenticated: bool,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    gateway_base = get_gateway_base_url()
    tokenfirewall_enabled = _truthy_env("ENABLE_TOKENFIREWALL", "0")
    tokenfirewall_routed = bool(gateway_base)
    tokenfirewall_healthy = _gateway_health(gateway_base)
    runs = _recent_runs_for_workspace(organization_id, user_id)
    saved_source_count = _saved_source_count(organization_id, user_id)
    run_summary = _summarize_runs(runs)

    payload: dict[str, Any] = {
        "success": True,
        "environment": _environment_name(),
        "authenticated": authenticated,
        "auth_configured": _auth_configured(),
        "control_plane_enabled": control_plane_service.control_plane_enabled(),
        "tokenfirewall_enabled": tokenfirewall_enabled,
        "tokenfirewall_routed": tokenfirewall_routed,
        "tokenfirewall_healthy": tokenfirewall_healthy,
        "tokenfirewall_base_url": gateway_base,
        "openai_model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        "saved_source_count": saved_source_count,
        "recent_query_count": run_summary["recent_query_count"],
        "recent_success_count": run_summary["recent_success_count"],
        "recent_failure_count": run_summary["recent_failure_count"],
        "avg_latency_ms": run_summary["avg_latency_ms"],
        "latest_question": run_summary["latest_question"],
        "recent_runs": runs,
    }

    if tokenfirewall_routed:
        try:
            payload["budget"] = budget_status()
        except Exception:
            payload["budget"] = None

        try:
            payload["gateway_usage"] = usage_analytics()
        except Exception:
            payload["gateway_usage"] = None
    else:
        payload["budget"] = None
        payload["gateway_usage"] = None

    payload["onboarding"] = _onboarding_items(
        authenticated=authenticated,
        organization_id=organization_id,
        saved_source_count=saved_source_count,
        recent_query_count=payload["recent_query_count"],
        tokenfirewall_routed=tokenfirewall_routed,
        tokenfirewall_healthy=tokenfirewall_healthy,
    )
    return payload
