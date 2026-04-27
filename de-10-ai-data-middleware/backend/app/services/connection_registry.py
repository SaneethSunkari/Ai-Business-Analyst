"""
In-memory connection registry.
Maps a UUID connection_id → full connection params.
Passwords are held in memory only for the lifetime of the process.
"""
from datetime import datetime, timezone
from uuid import uuid4

from app.services import control_plane_service
from app.services.database_catalog import get_database_definition, resolve_source_config

_registry: dict[str, dict] = {}


def _normalize_params(params: dict) -> dict:
    source_kind, engine_key = resolve_source_config(
        source_kind=params.get("source_kind"),
        engine_key=params.get("engine_key"),
        db_type=params.get("db_type"),
    )
    normalized = dict(params)
    normalized["source_kind"] = source_kind
    normalized["engine_key"] = engine_key
    normalized["db_type"] = engine_key
    normalized["options"] = normalized.get("options", {}) or {}
    normalized["password"] = normalized.get("password", "") or ""
    return normalized


def _validate_params(params: dict) -> dict:
    normalized = _normalize_params(params)
    meta = get_database_definition(normalized["engine_key"])

    if meta.get("show_host") and not normalized.get("host"):
        raise ValueError(f"{meta.get('host_label', 'Host')} is required for {meta['label']}.")
    if meta.get("show_port") and normalized.get("port") is None:
        raise ValueError(f"{meta.get('port_label', 'Port')} is required for {meta['label']}.")
    if meta.get("show_username") and not normalized.get("username"):
        raise ValueError(f"Username is required for {meta['label']}.")
    if meta.get("show_password") and not normalized.get("password"):
        raise ValueError(f"Password is required for {meta['label']}.")
    if meta.get("database_required", True) and not normalized.get("database"):
        raise ValueError(f"{meta.get('database_label', 'Database')} is required for {meta['label']}.")

    provided_options = normalized.get("options", {})
    for option in meta.get("options", []):
        if option.get("required") and not provided_options.get(option["key"]):
            raise ValueError(f"{option['label']} is required for {meta['label']}.")

    return normalized


def register_connection(
    name: str,
    source_kind: str | None,
    engine_key: str | None,
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str] | None = None,
    db_type: str | None = None,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> str:
    validated = _validate_params(
        {
            "source_kind": source_kind,
            "engine_key": engine_key,
            "db_type": db_type,
            "host": host,
            "port": port,
            "database": database,
            "username": username,
            "password": password or "",
            "options": options or {},
        }
    )

    if control_plane_service.control_plane_enabled():
        if not (organization_id and user_id):
            raise ValueError("Login required to save data sources.")
        return control_plane_service.register_saved_source(
            name=name,
            source_kind=validated["source_kind"],
            engine_key=validated["engine_key"],
            host=validated["host"],
            port=validated["port"],
            database=validated["database"],
            username=validated["username"],
            password=validated["password"],
            options=validated["options"],
            organization_id=organization_id,
            user_id=user_id,
        )

    conn_id = str(uuid4())
    _registry[conn_id] = {
        "id": conn_id,
        "name": name,
        "source_kind": validated["source_kind"],
        "engine_key": validated["engine_key"],
        "db_type": validated["engine_key"],
        "host": validated["host"],
        "port": validated["port"],
        "database": validated["database"],
        "username": validated["username"],
        "password": validated["password"],
        "options": validated["options"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return conn_id


def get_connection(
    conn_id: str,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> dict | None:
    if control_plane_service.control_plane_enabled():
        if not (organization_id and user_id):
            return None
        try:
            return control_plane_service.get_saved_source(
                conn_id,
                organization_id=organization_id,
                user_id=user_id,
            )
        except ValueError:
            return None
    return _registry.get(conn_id)


def list_connections(
    organization_id: str | None = None,
    user_id: str | None = None,
) -> list[dict]:
    """Return all saved connections without the password field."""
    if control_plane_service.control_plane_enabled():
        if not (organization_id and user_id):
            return []
        try:
            return control_plane_service.list_saved_sources(
                organization_id=organization_id,
                user_id=user_id,
            )
        except ValueError:
            return []
    return [{k: v for k, v in conn.items() if k != "password"} for conn in _registry.values()]


def delete_connection(
    conn_id: str,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> bool:
    if control_plane_service.control_plane_enabled():
        if not (organization_id and user_id):
            return False
        try:
            return control_plane_service.delete_saved_source(
                conn_id,
                organization_id=organization_id,
                user_id=user_id,
            )
        except ValueError:
            return False
    if conn_id in _registry:
        del _registry[conn_id]
        return True
    return False


def resolve(
    payload,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    """
    Given any request payload, return a flat dict with:
      source_kind, engine_key, db_type, host, port, database, username, password, options

    If payload.connection_id is set, look it up from the registry.
    Otherwise fall back to inline fields on the payload.
    Raises ValueError if connection_id is provided but not found.
    """
    conn_id = getattr(payload, "connection_id", None)
    if conn_id:
        conn = get_connection(conn_id, organization_id=organization_id, user_id=user_id)
        if not conn:
            raise ValueError(f"Saved connection '{conn_id}' not found. Register it first via POST /connections/register.")
        return _validate_params(
            {
                "connection_id": conn.get("id", conn_id),
                "source_kind": conn.get("source_kind"),
                "engine_key": conn.get("engine_key"),
                "db_type": conn.get("db_type"),
                "host": conn["host"],
                "port": conn["port"],
                "database": conn["database"],
                "username": conn["username"],
                "password": conn["password"],
                "options": conn.get("options", {}),
            }
        )

    return _validate_params(
        {
            "connection_id": None,
            "source_kind": getattr(payload, "source_kind", None),
            "engine_key": getattr(payload, "engine_key", None),
            "db_type": getattr(payload, "db_type", None),
            "host": getattr(payload, "host", None),
            "port": getattr(payload, "port", None),
            "database": getattr(payload, "database", None),
            "username": getattr(payload, "username", None),
            "password": getattr(payload, "password", None) or "",
            "options": getattr(payload, "options", {}) or {},
        }
    )
