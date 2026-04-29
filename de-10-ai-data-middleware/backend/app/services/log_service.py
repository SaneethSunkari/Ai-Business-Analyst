import json
from collections import deque
from datetime import datetime
from pathlib import Path

from app.services import control_plane_service


# Resolve project root dynamically
BASE_DIR = Path(__file__).resolve().parents[3]

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "query_logs.jsonl"


def write_query_log(
    entry: dict,
    *,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    try:
        control_plane_service.append_query_run(
            question=entry.get("question", "") or "",
            generated_sql=entry.get("generated_sql", "") or "",
            success=bool(entry.get("success", False)),
            row_count=entry.get("row_count"),
            error=entry.get("error"),
            connection_id=entry.get("connection_id"),
            latency_ms=entry.get("latency_ms"),
            organization_id=organization_id,
            user_id=user_id,
        )
    except Exception:
        # Never break the primary request path just because remote log mirroring failed.
        pass


def build_query_log(
    question: str,
    generated_sql: str,
    success: bool,
    row_count: int | None = None,
    error: str | None = None,
    connection_id: str | None = None,
    latency_ms: int | None = None,
) -> dict:
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "question": question,
        "generated_sql": generated_sql,
        "success": success,
        "row_count": row_count,
        "error": error,
        "connection_id": connection_id,
        "latency_ms": latency_ms,
    }


def read_recent_query_logs(limit: int = 20) -> list[dict]:
    if limit <= 0 or not LOG_FILE.exists():
        return []

    recent_lines: deque[str] = deque(maxlen=limit)
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recent_lines.append(line)

    parsed: list[dict] = []
    for line in reversed(recent_lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            parsed.append(payload)
    return parsed
