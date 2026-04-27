from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

from app.services.database_catalog import get_database_definition


load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_TIMEOUT = 20


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_options(options: dict[str, str] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in (options or {}).items():
        text = _clean_text(value)
        if text:
            cleaned[key] = text
    return cleaned


def _service_role_headers(prefer: str | None = None) -> dict[str, str]:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not key:
        raise ValueError("SUPABASE_SERVICE_ROLE_KEY is not configured.")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _supabase_rest_url(table: str) -> str:
    base = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not base:
        raise ValueError("SUPABASE_URL is not configured.")
    return f"{base}/rest/v1/{table}"


def _control_plane_encryption_key() -> str:
    key = os.getenv("CONTROL_PLANE_ENCRYPTION_KEY", "").strip()
    if key:
        return key
    secret = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not secret:
        raise ValueError(
            "CONTROL_PLANE_ENCRYPTION_KEY is not configured. "
            "Set a dedicated Fernet key to persist saved connection secrets."
        )
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8")


def _fernet() -> Fernet:
    return Fernet(_control_plane_encryption_key().encode("utf-8"))


def _encrypt_secret_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _fernet().encrypt(serialized).decode("utf-8")


def _decrypt_secret_payload(ciphertext: str | None) -> dict[str, Any]:
    if not ciphertext:
        return {}
    try:
        raw = _fernet().decrypt(ciphertext.encode("utf-8"))
    except InvalidToken as exc:
        raise ValueError("Saved connection secret could not be decrypted. Check CONTROL_PLANE_ENCRYPTION_KEY.") from exc
    return json.loads(raw.decode("utf-8"))


def control_plane_enabled() -> bool:
    return bool(os.getenv("SUPABASE_URL", "").strip() and os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip())


def _rest_request(
    method: str,
    table: str,
    *,
    params: dict[str, str] | None = None,
    json_body: Any | None = None,
    prefer: str | None = None,
) -> requests.Response:
    response = requests.request(
        method=method,
        url=_supabase_rest_url(table),
        headers=_service_role_headers(prefer),
        params=params,
        json=json_body,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return response


def _context_from_env() -> tuple[str | None, str | None]:
    organization_id = _clean_text(os.getenv("CONTROL_PLANE_ORGANIZATION_ID"))
    user_id = _clean_text(os.getenv("CONTROL_PLANE_ACTOR_USER_ID"))
    return organization_id or None, user_id or None


def _context_from_memberships() -> tuple[str | None, str | None]:
    response = _rest_request(
        "GET",
        "memberships",
        params={
            "select": "organization_id,user_id,status,created_at",
            "status": "eq.active",
            "order": "created_at.asc",
            "limit": "1",
        },
    )
    rows = response.json()
    if not rows:
        return None, None
    row = rows[0]
    return row.get("organization_id"), row.get("user_id")


def get_control_plane_context(
    organization_id: str | None = None,
    user_id: str | None = None,
) -> tuple[str, str]:
    if organization_id and user_id:
        return organization_id, user_id

    organization_id, user_id = _context_from_env()
    if organization_id and user_id:
        return organization_id, user_id

    organization_id, user_id = _context_from_memberships()
    if organization_id and user_id:
        return organization_id, user_id

    raise ValueError(
        "No Supabase organization context is available yet. "
        "Create a control-plane owner and organization first, then set "
        "CONTROL_PLANE_ORGANIZATION_ID and CONTROL_PLANE_ACTOR_USER_ID or keep one active membership in Supabase."
    )


def _split_public_and_secret_options(engine_key: str, options: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    meta = get_database_definition(engine_key)
    option_defs = {item["key"]: item for item in meta.get("options", [])}
    public_options: dict[str, str] = {}
    secret_options: dict[str, str] = {}
    for key, value in _clean_options(options).items():
        option_def = option_defs.get(key, {})
        if option_def.get("secret"):
            secret_options[key] = value
        else:
            public_options[key] = value
    return public_options, secret_options


def _map_source_row(row: dict[str, Any]) -> dict[str, Any]:
    secret_payload = _decrypt_secret_payload(row.get("secret_locator"))
    options = dict(row.get("options_json") or {})
    options.update(secret_payload.get("secret_options") or {})
    return {
        "id": row["id"],
        "name": row["name"],
        "source_kind": row["source_kind"],
        "engine_key": row["engine_key"],
        "db_type": row["engine_key"],
        "host": row.get("host"),
        "port": row.get("port"),
        "database": row.get("database_name"),
        "username": secret_payload.get("username") or row.get("storage_account"),
        "password": secret_payload.get("password", ""),
        "options": options,
        "created_at": row.get("created_at"),
    }


def _public_source_payload(
    *,
    organization_id: str,
    user_id: str,
    name: str,
    source_kind: str,
    engine_key: str,
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    public_options: dict[str, str],
) -> dict[str, Any]:
    host_text = _clean_text(host) or None
    database_text = _clean_text(database) or None
    username_text = _clean_text(username) or None
    payload: dict[str, Any] = {
        "organization_id": organization_id,
        "name": name,
        "source_kind": source_kind,
        "engine_key": engine_key,
        "secret_backend": "fernet",
        "host": host_text,
        "port": port,
        "database_name": database_text,
        "options_json": public_options,
        "status": "active",
        "created_by": user_id,
    }

    if source_kind == "object_store":
        payload["bucket_or_container"] = host_text
        payload["path_prefix"] = database_text
        payload["storage_account"] = username_text
    elif source_kind == "warehouse":
        payload["auth_mode"] = "service_role"

    return payload


def _fetch_existing_source(organization_id: str, name: str) -> dict[str, Any] | None:
    response = _rest_request(
        "GET",
        "data_sources",
        params={
            "select": "*",
            "organization_id": f"eq.{quote(organization_id, safe='')}",
            "name": f"eq.{quote(name, safe='')}",
            "limit": "1",
        },
    )
    rows = response.json()
    return rows[0] if rows else None


def register_saved_source(
    *,
    name: str,
    source_kind: str,
    engine_key: str,
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str] | None = None,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> str:
    organization_id, user_id = get_control_plane_context(organization_id, user_id)
    public_options, secret_options = _split_public_and_secret_options(engine_key, options or {})
    secret_ciphertext = _encrypt_secret_payload(
        {
            "username": _clean_text(username) or None,
            "password": password or "",
            "secret_options": secret_options,
        }
    )
    payload = _public_source_payload(
        organization_id=organization_id,
        user_id=user_id,
        name=name,
        source_kind=source_kind,
        engine_key=engine_key,
        host=host,
        port=port,
        database=database,
        username=username,
        public_options=public_options,
    )
    payload["secret_locator"] = secret_ciphertext

    existing = _fetch_existing_source(organization_id, name)
    if existing:
        source_id = existing["id"]
        response = _rest_request(
            "PATCH",
            "data_sources",
            params={"id": f"eq.{quote(source_id, safe='')}"},
            json_body=payload,
            prefer="return=representation",
        )
        rows = response.json()
        return rows[0]["id"] if rows else source_id

    response = _rest_request(
        "POST",
        "data_sources",
        json_body=payload,
        prefer="return=representation",
    )
    rows = response.json()
    return rows[0]["id"]


def list_saved_sources(
    organization_id: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    organization_id, _ = get_control_plane_context(organization_id, user_id)
    response = _rest_request(
        "GET",
        "data_sources",
        params={
            "select": "*",
            "organization_id": f"eq.{quote(organization_id, safe='')}",
            "order": "created_at.desc",
        },
    )
    return [
        {
            key: value
            for key, value in _map_source_row(row).items()
            if key != "password"
        }
        for row in response.json()
    ]


def get_saved_source(
    connection_id: str,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    organization_id, _ = get_control_plane_context(organization_id, user_id)
    response = _rest_request(
        "GET",
        "data_sources",
        params={
            "select": "*",
            "id": f"eq.{quote(connection_id, safe='')}",
            "organization_id": f"eq.{quote(organization_id, safe='')}",
            "limit": "1",
        },
    )
    rows = response.json()
    if not rows:
        return None
    return _map_source_row(rows[0])


def delete_saved_source(
    connection_id: str,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> bool:
    organization_id, _ = get_control_plane_context(organization_id, user_id)
    response = _rest_request(
        "DELETE",
        "data_sources",
        params={
            "id": f"eq.{quote(connection_id, safe='')}",
            "organization_id": f"eq.{quote(organization_id, safe='')}",
        },
        prefer="return=representation",
    )
    return bool(response.json())


def append_query_run(
    *,
    question: str,
    generated_sql: str,
    success: bool,
    row_count: int | None = None,
    error: str | None = None,
    connection_id: str | None = None,
    latency_ms: int | None = None,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> None:
    if not control_plane_enabled():
        return
    organization_id, user_id = get_control_plane_context(organization_id, user_id)
    payload = {
        "organization_id": organization_id,
        "connection_id": connection_id,
        "user_id": user_id,
        "question": question or None,
        "generated_sql": generated_sql or None,
        "success": success,
        "row_count": row_count,
        "latency_ms": latency_ms,
        "error": error,
    }
    _rest_request("POST", "query_runs", json_body=payload, prefer="return=minimal")
