"""
In-memory connection registry.
Maps a UUID connection_id → full connection params.
Passwords are held in memory only for the lifetime of the process.
"""
from uuid import uuid4
from datetime import datetime, timezone

_registry: dict[str, dict] = {}


def register_connection(
    name: str,
    db_type: str,
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
) -> str:
    conn_id = str(uuid4())
    _registry[conn_id] = {
        "id": conn_id,
        "name": name,
        "db_type": db_type,
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "password": password,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return conn_id


def get_connection(conn_id: str) -> dict | None:
    return _registry.get(conn_id)


def list_connections() -> list[dict]:
    """Return all saved connections without the password field."""
    return [
        {k: v for k, v in conn.items() if k != "password"}
        for conn in _registry.values()
    ]


def delete_connection(conn_id: str) -> bool:
    if conn_id in _registry:
        del _registry[conn_id]
        return True
    return False


def resolve(payload) -> dict:
    """
    Given any request payload, return a flat dict with:
      db_type, host, port, database, username, password

    If payload.connection_id is set, look it up from the registry.
    Otherwise fall back to inline fields on the payload.
    Raises ValueError if connection_id is provided but not found.
    """
    conn_id = getattr(payload, "connection_id", None)
    if conn_id:
        conn = get_connection(conn_id)
        if not conn:
            raise ValueError(f"Saved connection '{conn_id}' not found. Register it first via POST /connections/register.")
        return {
            "db_type": conn.get("db_type", "postgresql"),
            "host": conn["host"],
            "port": conn["port"],
            "database": conn["database"],
            "username": conn["username"],
            "password": conn["password"],
        }

    return {
        "db_type": getattr(payload, "db_type", "postgresql"),
        "host": getattr(payload, "host", None),
        "port": getattr(payload, "port", None),
        "database": getattr(payload, "database", None),
        "username": getattr(payload, "username", None),
        "password": getattr(payload, "password", None) or "",
    }
