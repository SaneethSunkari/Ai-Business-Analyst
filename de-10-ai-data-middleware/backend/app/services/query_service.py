import contextvars
import hashlib
import json
import logging
import re
import string
import time
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from time import perf_counter
from app.services.db_url import build_db_url
from app.services.dashboard_service import generate_dashboard_spec
from app.services.error_service import clean_db_error_message
from app.services.extended_source_service import execute_special_query, handles_special_engine
from app.services.header_row_normalizer import (
    fetch_header_row_overrides,
    rewrite_query_with_header_row_overrides,
)
from app.services.llm_service import UNANSWERABLE_SQL, generate_sql_from_question
from app.services.log_service import build_query_log, write_query_log
from app.services.object_store_service import execute_object_store_sql
from app.services import schema_service
from app.services.sql_validator import validate_read_only_sql

logger = logging.getLogger(__name__)

QUERY_CACHE_TTL_SECONDS = 300
SCHEMA_CACHE_TTL_SECONDS = 600
_CACHE_PREFIX = "adm_cache"
_QUERY_CACHE_CLIENT: Any | None = None
_QUERY_CACHE_BACKEND = "memory"
_FALLBACK_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_STATS = {"hits": 0, "misses": 0}
_LAST_QUERY_CACHE_METADATA: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "last_query_cache_metadata",
    default={"cached": False, "cache_age_seconds": 0},
)


def clean_error_message(error: str) -> str:
    return clean_db_error_message(error)


def extract_tables_used(sql: str) -> list[str]:
    return sorted(
        {
            match.lower()
            for match in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.IGNORECASE)
        }
    )


def infer_confidence(tables_used: list[str], row_count: int) -> str:
    if row_count == 0:
        return "low"
    if len(tables_used) <= 1:
        return "high"
    if len(tables_used) <= 2:
        return "medium"
    return "low"


def configure_query_cache(
    cache_client: Any | None = None,
    *,
    backend_name: str | None = None,
) -> None:
    global _QUERY_CACHE_CLIENT, _QUERY_CACHE_BACKEND
    _QUERY_CACHE_CLIENT = cache_client
    _QUERY_CACHE_BACKEND = backend_name or ("redis" if cache_client is not None else "memory")


def get_last_query_cache_metadata() -> dict[str, Any]:
    return dict(_LAST_QUERY_CACHE_METADATA.get())


def get_query_cache_stats() -> dict[str, Any]:
    total_requests = _CACHE_STATS["hits"] + _CACHE_STATS["misses"]
    hit_rate = (_CACHE_STATS["hits"] / total_requests) if total_requests else 0.0
    return {
        "backend": _QUERY_CACHE_BACKEND,
        "redis_enabled": _QUERY_CACHE_CLIENT is not None,
        "total_requests": total_requests,
        "cache_hits": _CACHE_STATS["hits"],
        "cache_misses": _CACHE_STATS["misses"],
        "hit_rate": round(hit_rate, 4),
    }


def _set_last_query_cache_metadata(*, cached: bool, cache_age_seconds: int = 0) -> None:
    _LAST_QUERY_CACHE_METADATA.set(
        {
            "cached": cached,
            "cache_age_seconds": cache_age_seconds,
        }
    )


def _normalize_question(question: str) -> str:
    lowered = (question or "").lower()
    no_punctuation = lowered.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", no_punctuation).strip()


def _cache_scope(
    *,
    connection_id: str | None,
    source_kind: str,
    engine_key: str,
    host: str,
    port: int,
    database: str,
    username: str,
    options: dict[str, str] | None,
) -> str:
    if connection_id:
        return connection_id
    serialized_options = json.dumps(options or {}, sort_keys=True, default=str)
    return f"inline:{source_kind}|{engine_key}|{host}|{port}|{database}|{username}|{serialized_options}"


def _query_cache_key(*, scope: str, normalized_question: str, engine_key: str) -> str:
    digest = hashlib.sha256(f"{scope}|{normalized_question}|{engine_key}".encode("utf-8")).hexdigest()
    return f"{_CACHE_PREFIX}:query:{scope}:{digest}"


def _schema_cache_key(scope: str) -> str:
    return f"{_CACHE_PREFIX}:schema:{scope}::schema"


def _purge_fallback_cache() -> None:
    now = time.time()
    expired = [key for key, entry in _FALLBACK_CACHE.items() if entry["expires_at"] <= now]
    for key in expired:
        _FALLBACK_CACHE.pop(key, None)


def _cache_get(key: str) -> dict[str, Any] | None:
    _purge_fallback_cache()
    if _QUERY_CACHE_CLIENT is not None:
        try:
            raw_value = _QUERY_CACHE_CLIENT.get(key)
            if raw_value:
                return json.loads(raw_value)
        except Exception:
            logger.exception("query_cache_redis_get_failed key=%s", key)

    fallback_entry = _FALLBACK_CACHE.get(key)
    if not fallback_entry:
        return None
    return dict(fallback_entry["value"])


def _cache_set(key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
    envelope = {"stored_at": time.time(), "payload": payload}
    if _QUERY_CACHE_CLIENT is not None:
        try:
            _QUERY_CACHE_CLIENT.setex(key, ttl_seconds, json.dumps(envelope, default=str))
            return
        except Exception:
            logger.exception("query_cache_redis_set_failed key=%s", key)

    _FALLBACK_CACHE[key] = {
        "expires_at": time.time() + ttl_seconds,
        "value": envelope,
    }


def _cache_delete(key: str) -> bool:
    deleted = False
    if _QUERY_CACHE_CLIENT is not None:
        try:
            deleted = bool(_QUERY_CACHE_CLIENT.delete(key)) or deleted
        except Exception:
            logger.exception("query_cache_redis_delete_failed key=%s", key)

    if key in _FALLBACK_CACHE:
        _FALLBACK_CACHE.pop(key, None)
        deleted = True
    return deleted


def _cache_delete_prefix(prefix: str) -> int:
    deleted = 0
    if _QUERY_CACHE_CLIENT is not None:
        try:
            for redis_key in _QUERY_CACHE_CLIENT.scan_iter(match=f"{prefix}*"):
                deleted += int(_QUERY_CACHE_CLIENT.delete(redis_key) or 0)
        except Exception:
            logger.exception("query_cache_redis_delete_prefix_failed prefix=%s", prefix)

    keys_to_delete = [key for key in _FALLBACK_CACHE if key.startswith(prefix)]
    for key in keys_to_delete:
        _FALLBACK_CACHE.pop(key, None)
        deleted += 1
    return deleted


def clear_connection_cache(connection_id: str) -> dict[str, Any]:
    query_cleared = _cache_delete_prefix(f"{_CACHE_PREFIX}:query:{connection_id}:")
    schema_cleared = int(_cache_delete(_schema_cache_key(connection_id)))
    internal_schema_key = f"connection:{connection_id}::schema"
    if hasattr(schema_service, "_SCHEMA_CACHE"):
        if schema_service._SCHEMA_CACHE.pop(internal_schema_key, None) is not None:  # type: ignore[attr-defined]
            schema_cleared += 1
    return {
        "success": True,
        "connection_id": connection_id,
        "query_entries_cleared": query_cleared,
        "schema_entries_cleared": schema_cleared,
    }


def clear_all_cache() -> dict[str, Any]:
    query_cleared = _cache_delete_prefix(f"{_CACHE_PREFIX}:query:")
    schema_cleared = _cache_delete_prefix(f"{_CACHE_PREFIX}:schema:")
    internal_schema_cleared = 0
    if hasattr(schema_service, "_SCHEMA_CACHE"):
        internal_schema_cleared = len(schema_service._SCHEMA_CACHE)  # type: ignore[attr-defined]
        schema_service._SCHEMA_CACHE.clear()  # type: ignore[attr-defined]
    _CACHE_STATS["hits"] = 0
    _CACHE_STATS["misses"] = 0
    return {
        "success": True,
        "query_entries_cleared": query_cleared,
        "schema_entries_cleared": schema_cleared + internal_schema_cleared,
    }


def _record_cache_hit(key: str, age_seconds: int) -> None:
    _CACHE_STATS["hits"] += 1
    logger.info("[CACHE HIT] key=%s age=%ss", key, age_seconds)


def _record_cache_miss(key: str) -> None:
    _CACHE_STATS["misses"] += 1
    logger.info("[CACHE MISS] key=%s", key)


def _maybe_return_cached_query_result(cache_key: str) -> dict[str, Any] | None:
    cached_entry = _cache_get(cache_key)
    if not cached_entry:
        _record_cache_miss(cache_key)
        return None

    stored_at = float(cached_entry.get("stored_at") or time.time())
    age_seconds = max(0, int(time.time() - stored_at))
    payload = dict(cached_entry.get("payload") or {})
    payload["cached"] = True
    payload["cache_age_seconds"] = age_seconds
    _record_cache_hit(cache_key, age_seconds)
    _set_last_query_cache_metadata(cached=True, cache_age_seconds=age_seconds)
    return payload


def _cache_query_result(cache_key: str, response_payload: dict[str, Any]) -> None:
    payload = {
        key: value
        for key, value in response_payload.items()
        if key not in {"cached", "cache_age_seconds"}
    }
    _cache_set(cache_key, payload, QUERY_CACHE_TTL_SECONDS)


def _get_schema_with_cache(
    *,
    scope: str,
    connection_id: str | None,
    source_kind: str,
    engine_key: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    options: dict[str, str] | None,
) -> dict[str, Any]:
    cache_key = _schema_cache_key(scope)
    cached_entry = _cache_get(cache_key)
    if cached_entry:
        stored_at = float(cached_entry.get("stored_at") or time.time())
        age_seconds = max(0, int(time.time() - stored_at))
        logger.info("[CACHE HIT] key=%s age=%ss", cache_key, age_seconds)
        payload = dict(cached_entry.get("payload") or {})
        payload["cached"] = True
        return payload

    logger.info("[CACHE MISS] key=%s", cache_key)
    schema_metadata = schema_service.get_schema_metadata(
        source_kind=source_kind,
        engine_key=engine_key,
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        options=options,
        connection_id=f"{scope}::schema" if connection_id else None,
        force_refresh=False,
    )
    payload = {
        key: value
        for key, value in schema_metadata.items()
        if key != "cached"
    }
    _cache_set(cache_key, payload, SCHEMA_CACHE_TTL_SECONDS)
    payload["cached"] = False
    return payload


def execute_sql_query(
    sql: str,
    source_kind: str = "database",
    engine_key: str = "postgresql",
    host: str = "localhost",
    port: int = 5432,
    database: str = "",
    username: str = "",
    password: str = "",
    options: dict[str, str] | None = None,
):
    is_valid, message = validate_read_only_sql(sql)
    if not is_valid:
        return {"success": False, "error": message}

    if source_kind == "object_store":
        try:
            return execute_object_store_sql(
                engine_key=engine_key,
                host=host,
                database=database,
                username=username,
                password=password,
                options=options,
                sql=sql,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    if handles_special_engine(engine_key):
        try:
            return execute_special_query(
                engine_key=engine_key,
                sql=sql,
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                options=options,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    db_url = build_db_url(
        engine_key,
        host,
        port,
        database,
        username,
        password,
        options=options,
    )
    engine = create_engine(db_url)
    try:
        schema_name = (options or {}).get("schema") or None
        inspector = inspect(engine)

        def load_columns(table_name: str) -> list[dict[str, str]]:
            try:
                raw_columns = inspector.get_columns(table_name, schema=schema_name) if schema_name else inspector.get_columns(table_name)
            except TypeError:
                raw_columns = inspector.get_columns(table_name)
            return [{"name": column["name"]} for column in raw_columns]

        try:
            table_names = inspector.get_table_names(schema=schema_name) if schema_name else inspector.get_table_names()
        except TypeError:
            table_names = inspector.get_table_names()

        header_row_overrides = fetch_header_row_overrides(
            engine=engine,
            table_columns={table_name: load_columns(table_name) for table_name in table_names},
            engine_key=engine_key,
            schema_name=schema_name,
        )
        rewritten_sql = rewrite_query_with_header_row_overrides(
            sql=sql,
            overrides=header_row_overrides,
            engine_key=engine_key,
            schema_name=schema_name,
        )
        with engine.connect() as connection:
            result = connection.execute(text(rewritten_sql))
            rows = result.fetchall()
            columns = list(result.keys())
        return {"success": True, "columns": columns, "rows": [list(row) for row in rows]}
    except SQLAlchemyError as e:
        return {"success": False, "error": str(e)}
    finally:
        engine.dispose()


def execute_nl_query(
    question: str,
    connection_id: str | None = None,
    organization_id: str | None = None,
    user_id: str | None = None,
    source_kind: str = "database",
    engine_key: str = "postgresql",
    host: str = "localhost",
    port: int = 5432,
    database: str = "",
    username: str = "",
    password: str = "",
    options: dict[str, str] | None = None,
):
    started_at = perf_counter()
    scope = _cache_scope(
        connection_id=connection_id,
        source_kind=source_kind,
        engine_key=engine_key,
        host=host,
        port=port,
        database=database,
        username=username,
        options=options,
    )
    normalized_question = _normalize_question(question)
    query_cache_key = _query_cache_key(
        scope=scope,
        normalized_question=normalized_question,
        engine_key=engine_key,
    )

    cached_response = _maybe_return_cached_query_result(query_cache_key)
    if cached_response is not None:
        write_query_log(
            build_query_log(
                question=question,
                generated_sql=cached_response.get("sql", ""),
                success=bool(cached_response.get("success")),
                row_count=cached_response.get("row_count"),
                error=cached_response.get("error"),
                connection_id=connection_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        return cached_response

    try:
        schema_metadata = _get_schema_with_cache(
            scope=scope,
            connection_id=connection_id,
            source_kind=source_kind,
            engine_key=engine_key,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            options=options,
        )
    except Exception as e:
        error_msg = clean_error_message(str(e))
        _set_last_query_cache_metadata(cached=False, cache_age_seconds=0)
        write_query_log(
            build_query_log(
                question=question,
                generated_sql="",
                success=False,
                error=error_msg,
                connection_id=connection_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        return {"success": False, "question": question, "sql": "", "error": error_msg}

    if not schema_metadata or not schema_metadata.get("tables"):
        error_msg = "No schema metadata found"
        _set_last_query_cache_metadata(cached=False, cache_age_seconds=0)
        write_query_log(
            build_query_log(
                question=question,
                generated_sql="",
                success=False,
                error=error_msg,
                connection_id=connection_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        return {"success": False, "question": question, "sql": "", "error": error_msg}

    generated_sql = generate_sql_from_question(
        question=question,
        schema_metadata=schema_metadata,
        engine_key=engine_key,
    )

    if generated_sql == UNANSWERABLE_SQL:
        error_msg = "Question cannot be answered from the available schema"
        _set_last_query_cache_metadata(cached=False, cache_age_seconds=0)
        write_query_log(
            build_query_log(
                question=question,
                generated_sql=generated_sql,
                success=False,
                error=error_msg,
                connection_id=connection_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        return {"success": False, "question": question, "sql": generated_sql, "error": error_msg}

    query_result = execute_sql_query(
        sql=generated_sql,
        source_kind=source_kind,
        engine_key=engine_key,
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        options=options,
    )

    if not query_result.get("success"):
        error_msg = clean_error_message(query_result.get("error", "Query execution failed"))
        _set_last_query_cache_metadata(cached=False, cache_age_seconds=0)
        write_query_log(
            build_query_log(
                question=question,
                generated_sql=generated_sql,
                success=False,
                error=error_msg,
                connection_id=connection_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        return {"success": False, "question": question, "sql": generated_sql, "error": error_msg}

    columns = query_result.get("columns", [])
    rows = query_result.get("rows", [])
    tables_used = extract_tables_used(generated_sql)
    dashboard = generate_dashboard_spec(
        question=question,
        columns=columns,
        rows=rows,
        source_question=question,
    )
    confidence = dashboard.get("confidence") or infer_confidence(tables_used, len(rows))
    explanation = dashboard.get("explanation") or (
        f"Generated a read-only query using {len(tables_used) or 1} table(s) and returned {len(rows)} rows."
    )
    follow_ups = dashboard.get("follow_ups") or []
    response_payload = {
        "success": True,
        "question": question,
        "sql": generated_sql,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "explanation": explanation,
        "confidence": confidence,
        "tables_used": tables_used,
        "follow_ups": follow_ups,
        "dashboard": dashboard,
        "cached": False,
        "cache_age_seconds": 0,
    }
    _cache_query_result(query_cache_key, response_payload)
    _set_last_query_cache_metadata(cached=False, cache_age_seconds=0)
    write_query_log(
        build_query_log(
            question=question,
            generated_sql=generated_sql,
            success=True,
            row_count=len(rows),
            connection_id=connection_id,
            latency_ms=int((perf_counter() - started_at) * 1000),
        ),
        organization_id=organization_id,
        user_id=user_id,
    )
    return response_payload


def refine_dashboard_plan(
    question: str,
    columns: list[str],
    rows: list[list[str | int | float | bool | None]],
    source_question: str | None = None,
    current_dashboard: dict[str, Any] | None = None,
):
    dashboard = generate_dashboard_spec(
        question=question,
        columns=columns,
        rows=rows,
        source_question=source_question,
        current_dashboard=current_dashboard,
    )
    return {
        "success": True,
        "question": question,
        "dashboard": dashboard,
    }
