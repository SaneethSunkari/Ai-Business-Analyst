from __future__ import annotations

import copy
import json
import logging
import re
import time
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from app.services.db_url import build_db_url
from app.services.extended_source_service import get_special_schema_metadata, handles_special_engine
from app.services.header_row_normalizer import (
    apply_header_row_overrides,
    fetch_header_row_overrides,
)
from app.services.object_store_service import get_object_store_schema_metadata

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600
_SCHEMA_CACHE: dict[str, dict[str, Any]] = {}

ID_NAME_PATTERN = re.compile(r"(^id$|_id$|_key$|uuid|guid|identifier)", re.IGNORECASE)
DATE_TYPE_PATTERN = re.compile(r"(date|time|timestamp)", re.IGNORECASE)
NUMERIC_TYPE_PATTERN = re.compile(r"(int|numeric|decimal|number|float|double|real|money)", re.IGNORECASE)
TEXT_TYPE_PATTERN = re.compile(r"(char|text|string|clob|varchar)", re.IGNORECASE)
LONG_TEXT_NAME_PATTERN = re.compile(r"(description|detail|details|notes|body|comment|message|content|summary|reason)", re.IGNORECASE)
MEASURE_NAME_PATTERN = re.compile(
    r"(amount|revenue|cost|count|price|quantity|salary|total|subtotal|balance|value|score|rate|margin|utilization|usage|metric|duration)",
    re.IGNORECASE,
)
DIMENSION_NAME_PATTERN = re.compile(
    r"(name|city|state|country|region|status|category|type|segment|group|class|department|team|role|channel|market)",
    re.IGNORECASE,
)

DOMAIN_HINT_TERMS = {
    "healthcare": {
        "patient",
        "encounter",
        "medication",
        "provider",
        "condition",
        "observation",
        "allergy",
        "immunization",
        "imaging",
        "careplan",
    },
    "finance": {
        "invoice",
        "transaction",
        "payment",
        "ledger",
        "balance",
        "expense",
        "revenue",
        "account",
        "salary",
    },
    "retail": {
        "product",
        "order",
        "customer",
        "inventory",
        "sku",
        "store",
        "cart",
        "shipment",
        "category",
    },
    "hr": {
        "employee",
        "department",
        "manager",
        "payroll",
        "recruiter",
        "candidate",
        "benefit",
        "position",
        "salary",
    },
}


def singularize_table_name(table_name: str) -> str:
    if table_name.endswith("ies") and len(table_name) > 3:
        return table_name[:-3] + "y"
    if table_name.endswith("s") and not table_name.endswith("ss") and len(table_name) > 3:
        return table_name[:-1]
    return table_name


def _normalize_identifier(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _cache_key(
    *,
    source_kind: str,
    engine_key: str,
    host: str,
    port: int,
    database: str,
    username: str,
    options: dict[str, str] | None,
    connection_id: str | None,
) -> str:
    if connection_id:
        return f"connection:{connection_id}"
    serialized_options = json.dumps(options or {}, sort_keys=True, default=str)
    return f"{source_kind}|{engine_key}|{host}|{port}|{database}|{username}|{serialized_options}"


def _get_cached_schema(cache_key: str, force_refresh: bool) -> dict[str, Any] | None:
    if force_refresh:
        logger.info("schema_cache_miss key=%s reason=force_refresh", cache_key)
        _SCHEMA_CACHE.pop(cache_key, None)
        return None

    cached = _SCHEMA_CACHE.get(cache_key)
    if not cached:
        logger.info("schema_cache_miss key=%s reason=empty", cache_key)
        return None

    age = time.time() - cached["stored_at"]
    if age > CACHE_TTL_SECONDS:
        logger.info("schema_cache_miss key=%s reason=expired age=%.2f", cache_key, age)
        _SCHEMA_CACHE.pop(cache_key, None)
        return None

    logger.info("schema_cache_hit key=%s age=%.2f", cache_key, age)
    result = copy.deepcopy(cached["value"])
    result["cached"] = True
    return result


def _store_cached_schema(cache_key: str, schema_info: dict[str, Any]) -> dict[str, Any]:
    cached_value = copy.deepcopy(schema_info)
    cached_value["cached"] = False
    _SCHEMA_CACHE[cache_key] = {"stored_at": time.time(), "value": cached_value}
    return copy.deepcopy(cached_value)


def _quote_identifier(engine: Engine, identifier: str) -> str:
    return engine.dialect.identifier_preparer.quote(identifier)


def _qualified_table_name(engine: Engine, table_name: str, schema_name: str | None) -> str:
    if schema_name:
        return f"{_quote_identifier(engine, schema_name)}.{_quote_identifier(engine, table_name)}"
    return _quote_identifier(engine, table_name)


def _sample_query(engine: Engine, table_name: str, schema_name: str | None, limit: int) -> str:
    qualified = _qualified_table_name(engine, table_name, schema_name)
    engine_name = engine.dialect.name.lower()
    if engine_name in {"mssql"}:
        return f"SELECT TOP {limit} * FROM {qualified}"
    if engine_name == "oracle":
        return f"SELECT * FROM {qualified} FETCH FIRST {limit} ROWS ONLY"
    return f"SELECT * FROM {qualified} LIMIT {limit}"


def _is_numeric_type(type_text: str) -> bool:
    return bool(NUMERIC_TYPE_PATTERN.search(type_text))


def _is_date_type(type_text: str) -> bool:
    return bool(DATE_TYPE_PATTERN.search(type_text))


def _is_text_type(type_text: str) -> bool:
    return bool(TEXT_TYPE_PATTERN.search(type_text))


def _is_long_text_type(type_text: str) -> bool:
    lowered = type_text.lower()
    return "text" in lowered or "clob" in lowered or "varchar(max" in lowered


def _type_family(type_text: str) -> str:
    if _is_date_type(type_text):
        return "date"
    if _is_numeric_type(type_text):
        return "numeric"
    if _is_text_type(type_text):
        return "text"
    return "other"


def _is_id_column(column_name: str) -> bool:
    return bool(ID_NAME_PATTERN.search(column_name))


def _sample_values_from_rows(rows: list[dict[str, Any]], column_name: str) -> list[str]:
    seen: list[str] = []
    for row in rows:
        value = row.get(column_name)
        if value in (None, ""):
            continue
        text_value = str(value)
        if text_value not in seen:
            seen.append(text_value)
        if len(seen) >= 3:
            break
    return seen


def _infer_semantic_type(
    *,
    column_name: str,
    type_text: str,
    is_primary_key: bool,
    is_foreign_key: bool,
    sample_values: list[str],
) -> str:
    if is_primary_key or is_foreign_key or _is_id_column(column_name):
        return "ID"

    if _is_date_type(type_text) or re.search(r"(date|time|timestamp|created|updated|month|year)", column_name, re.IGNORECASE):
        return "DATE"

    if LONG_TEXT_NAME_PATTERN.search(column_name) or (
        _is_long_text_type(type_text) and any(len(value) > 40 for value in sample_values)
    ):
        return "TEXT"

    if _is_numeric_type(type_text) and MEASURE_NAME_PATTERN.search(column_name):
        return "MEASURE"

    if _is_numeric_type(type_text) and not _is_id_column(column_name):
        if any(re.search(r"\d", value) for value in sample_values):
            return "MEASURE"

    if DIMENSION_NAME_PATTERN.search(column_name) or _is_text_type(type_text):
        return "DIMENSION"

    if _is_numeric_type(type_text):
        return "MEASURE"

    return "DIMENSION"


def _normalize_table_columns(raw_tables: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {
        table_name: [
            {
                "name": str(column.get("name", "")),
                "type": str(column.get("type", "unknown")),
                "nullable": bool(column.get("nullable", True)),
                **{k: v for k, v in column.items() if k not in {"name", "type", "nullable"}},
            }
            for column in columns
        ]
        for table_name, columns in raw_tables.items()
    }


def _normalize_raw_schema(raw_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "tables": _normalize_table_columns(raw_schema.get("tables", {})),
        "relationships": [dict(item) for item in raw_schema.get("relationships", [])],
    }


def _build_declared_relationships(
    fk_map: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    relationships: list[dict[str, str]] = []
    for table_name, foreign_keys in fk_map.items():
        for fk in foreign_keys:
            relationships.append(
                {
                    "from_table": table_name,
                    "from_column": fk["from_column"],
                    "to_table": fk["to_table"],
                    "to_column": fk["to_column"],
                    "confidence": "HIGH",
                }
            )
    return relationships


def _build_named_id_relationships(
    table_columns: dict[str, list[dict[str, Any]]],
    primary_keys: dict[str, set[str]],
) -> list[dict[str, str]]:
    relationships: list[dict[str, str]] = []
    table_lookup = {
        table_name.lower(): table_name for table_name in table_columns
    }
    singular_lookup = {
        singularize_table_name(table_name.lower()): table_name for table_name in table_columns
    }

    for table_name, columns in table_columns.items():
        for column in columns:
            column_name = str(column["name"])
            normalized = column_name.lower()
            for other_key, other_table in {**table_lookup, **singular_lookup}.items():
                if other_table == table_name:
                    continue
                if normalized in {f"{other_key}_id", f"{singularize_table_name(other_key)}_id"}:
                    pk_candidates = primary_keys.get(other_table) or {"id"}
                    target_column = next(iter(sorted(pk_candidates)))
                    relationships.append(
                        {
                            "from_table": table_name,
                            "from_column": column_name,
                            "to_table": other_table,
                            "to_column": target_column,
                            "confidence": "HIGH",
                        }
                    )
                    break
    return relationships


def _build_same_name_relationships(table_columns: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    by_name: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    relationships: list[dict[str, str]] = []

    for table_name, columns in table_columns.items():
        for column in columns:
            key = (_normalize_identifier(column["name"]), _type_family(str(column["type"])))
            by_name[key].append((table_name, str(column["name"])))

    for (_, type_family), matches in by_name.items():
        if len(matches) < 2 or type_family == "other":
            continue
        for index in range(len(matches)):
            for other_index in range(index + 1, len(matches)):
                from_table, from_col = matches[index]
                to_table, to_col = matches[other_index]
                if from_table == to_table:
                    continue
                relationships.append(
                    {
                        "from_table": from_table,
                        "from_column": from_col,
                        "to_table": to_table,
                        "to_column": to_col,
                        "confidence": "MEDIUM",
                    }
                )
    return relationships


def _build_similarity_relationships(table_columns: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    relationships: list[dict[str, str]] = []
    columns_index: list[tuple[str, str, str]] = []

    for table_name, columns in table_columns.items():
        for column in columns:
            column_name = str(column["name"])
            type_family = _type_family(str(column["type"]))
            if type_family == "other":
                continue
            columns_index.append((table_name, column_name, type_family))

    for index in range(len(columns_index)):
        from_table, from_col, from_family = columns_index[index]
        from_norm = _normalize_identifier(from_col)
        if len(from_norm) < 4:
            continue
        for other_index in range(index + 1, len(columns_index)):
            to_table, to_col, to_family = columns_index[other_index]
            if from_table == to_table or from_family != to_family:
                continue
            to_norm = _normalize_identifier(to_col)
            if len(to_norm) < 4 or from_norm == to_norm:
                continue
            ratio = SequenceMatcher(None, from_norm, to_norm).ratio()
            if ratio < 0.8:
                continue
            relationships.append(
                {
                    "from_table": from_table,
                    "from_column": from_col,
                    "to_table": to_table,
                    "to_column": to_col,
                    "confidence": "LOW",
                }
            )
    return relationships


def _dedupe_relationships(relationships: list[dict[str, str]]) -> list[dict[str, str]]:
    confidence_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    deduped: dict[tuple[str, str, str, str], dict[str, str]] = {}

    for relationship in relationships:
        key = (
            relationship["from_table"],
            relationship["from_column"],
            relationship["to_table"],
            relationship["to_column"],
        )
        existing = deduped.get(key)
        if not existing or confidence_rank[relationship["confidence"]] > confidence_rank[existing["confidence"]]:
            deduped[key] = relationship

    return list(deduped.values())


def _infer_relationships(
    table_columns: dict[str, list[dict[str, Any]]],
    declared_foreign_keys: dict[str, list[dict[str, str]]],
    primary_keys: dict[str, set[str]],
) -> list[dict[str, str]]:
    relationships = []
    relationships.extend(_build_declared_relationships(declared_foreign_keys))
    relationships.extend(_build_named_id_relationships(table_columns, primary_keys))
    relationships.extend(_build_same_name_relationships(table_columns))
    relationships.extend(_build_similarity_relationships(table_columns))
    return _dedupe_relationships(relationships)


def _estimate_row_count(
    connection,
    engine_key: str,
    schema_name: str | None,
    table_name: str,
) -> int | None:
    try:
        if engine_key in {"postgresql", "redshift"}:
            query = text(
                """
                SELECT c.reltuples::BIGINT
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = :table_name
                  AND n.nspname = :schema_name
                """
            )
            row = connection.execute(query, {"table_name": table_name, "schema_name": schema_name or "public"}).first()
            return int(row[0]) if row and row[0] is not None else None
        if engine_key == "mysql":
            query = text(
                """
                SELECT TABLE_ROWS
                FROM information_schema.tables
                WHERE table_schema = :schema_name
                  AND table_name = :table_name
                """
            )
            row = connection.execute(query, {"schema_name": schema_name, "table_name": table_name}).first()
            return int(row[0]) if row and row[0] is not None else None
        if engine_key == "snowflake":
            schema_value = (schema_name or "PUBLIC").upper()
            query = text(
                f"""
                SELECT ROW_COUNT
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = '{schema_value}'
                  AND TABLE_NAME = :table_name
                """
            )
            row = connection.execute(query, {"table_name": table_name.upper()}).first()
            return int(row[0]) if row and row[0] is not None else None
        if engine_key in {"bigquery", "trino", "athena", "databricks_sql", "dremio"}:
            query = text(
                """
                SELECT row_count
                FROM information_schema.tables
                WHERE table_schema = :schema_name
                  AND table_name = :table_name
                """
            )
            row = connection.execute(query, {"schema_name": schema_name, "table_name": table_name}).first()
            return int(row[0]) if row and row[0] is not None else None
        if engine_key in {"sqlserver", "synapse", "fabric"}:
            query = text(
                """
                SELECT SUM(p.rows)
                FROM sys.tables t
                JOIN sys.schemas s ON s.schema_id = t.schema_id
                JOIN sys.partitions p ON p.object_id = t.object_id
                WHERE s.name = :schema_name
                  AND t.name = :table_name
                  AND p.index_id IN (0, 1)
                """
            )
            row = connection.execute(query, {"schema_name": schema_name or "dbo", "table_name": table_name}).first()
            return int(row[0]) if row and row[0] is not None else None
        if engine_key == "oracle":
            query = text(
                """
                SELECT NUM_ROWS
                FROM ALL_TABLES
                WHERE OWNER = :schema_name
                  AND TABLE_NAME = :table_name
                """
            )
            row = connection.execute(query, {"schema_name": (schema_name or "").upper(), "table_name": table_name.upper()}).first()
            return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None
    return None


def _fetch_sample_rows(
    connection,
    engine: Engine,
    table_name: str,
    schema_name: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        result = connection.execute(text(_sample_query(engine, table_name, schema_name, limit)))
        rows = result.fetchall()
        columns = list(result.keys())
        return [dict(zip(columns, row)) for row in rows]
    except Exception:
        return []


def _build_table_details(
    table_columns: dict[str, list[dict[str, Any]]],
    primary_keys: dict[str, set[str]],
    foreign_keys: dict[str, list[dict[str, str]]],
    row_count_estimates: dict[str, int | None],
    sample_rows_by_table: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    fk_columns = {
        (table_name, fk["from_column"])
        for table_name, fks in foreign_keys.items()
        for fk in fks
    }

    enriched_table_map: dict[str, list[dict[str, Any]]] = {}
    table_details: list[dict[str, Any]] = []

    for table_name, columns in table_columns.items():
        enriched_columns: list[dict[str, Any]] = []
        sample_rows = sample_rows_by_table.get(table_name, [])
        for column in columns:
            column_name = str(column["name"])
            type_text = str(column.get("type", "unknown"))
            is_primary_key = column_name in primary_keys.get(table_name, set())
            is_foreign_key = (table_name, column_name) in fk_columns
            sample_values = _sample_values_from_rows(sample_rows, column_name)
            semantic_type = _infer_semantic_type(
                column_name=column_name,
                type_text=type_text,
                is_primary_key=is_primary_key,
                is_foreign_key=is_foreign_key,
                sample_values=sample_values,
            )
            enriched = {
                **column,
                "name": column_name,
                "type": type_text,
                "nullable": bool(column.get("nullable", True)),
                "semantic_type": semantic_type,
                "is_primary_key": is_primary_key,
                "sample_values": sample_values,
            }
            enriched_columns.append(enriched)

        enriched_table_map[table_name] = enriched_columns
        table_details.append(
            {
                "name": table_name,
                "row_count_estimate": row_count_estimates.get(table_name),
                "columns": enriched_columns,
            }
        )

    return enriched_table_map, table_details


def _detect_domain_hint(table_columns: dict[str, list[dict[str, Any]]]) -> str:
    tokens: set[str] = set()
    for table_name, columns in table_columns.items():
        tokens.update(_normalize_identifier(table_name).split())
        tokens.update(re.findall(r"[a-zA-Z]+", table_name.lower()))
        for column in columns:
            column_name = str(column["name"]).lower()
            tokens.update(re.findall(r"[a-zA-Z]+", column_name))

    scores = {
        domain: len(tokens & terms)
        for domain, terms in DOMAIN_HINT_TERMS.items()
    }
    top_domain = max(scores, key=scores.get) if scores else "generic"
    return top_domain if scores.get(top_domain, 0) > 0 else "generic"


def _build_raw_schema_for_regular_engine(
    *,
    engine: Engine,
    engine_key: str,
    schema_name: str | None,
) -> dict[str, Any]:
    inspector = inspect(engine)
    table_columns: dict[str, list[dict[str, Any]]] = {}
    foreign_keys: dict[str, list[dict[str, str]]] = {}
    primary_keys: dict[str, set[str]] = {}

    try:
        table_names = inspector.get_table_names(schema=schema_name) if schema_name else inspector.get_table_names()
    except TypeError:
        table_names = inspector.get_table_names()

    with engine.connect() as connection:
        row_count_estimates = {table_name: _estimate_row_count(connection, engine_key, schema_name, table_name) for table_name in table_names}

        for table_name in table_names:
            try:
                raw_columns = inspector.get_columns(table_name, schema=schema_name) if schema_name else inspector.get_columns(table_name)
            except TypeError:
                raw_columns = inspector.get_columns(table_name)

            table_columns[table_name] = [
                {
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col.get("nullable", True),
                }
                for col in raw_columns
            ]

            try:
                pk_constraint = inspector.get_pk_constraint(table_name, schema=schema_name) if schema_name else inspector.get_pk_constraint(table_name)
            except TypeError:
                pk_constraint = inspector.get_pk_constraint(table_name)
            primary_keys[table_name] = set(pk_constraint.get("constrained_columns") or [])

            try:
                fk_rows = inspector.get_foreign_keys(table_name, schema=schema_name) if schema_name else inspector.get_foreign_keys(table_name)
            except TypeError:
                fk_rows = inspector.get_foreign_keys(table_name)

            foreign_keys[table_name] = []
            for fk in fk_rows:
                constrained_columns = fk.get("constrained_columns") or []
                referred_columns = fk.get("referred_columns") or []
                referred_table = fk.get("referred_table")
                if constrained_columns and referred_columns and referred_table:
                    foreign_keys[table_name].append(
                        {
                            "from_column": constrained_columns[0],
                            "to_table": referred_table,
                            "to_column": referred_columns[0],
                        }
                    )

        header_row_overrides = fetch_header_row_overrides(
            engine=engine,
            table_columns=table_columns,
            engine_key=engine_key,
            schema_name=schema_name,
        )
        if header_row_overrides:
            table_columns = apply_header_row_overrides(table_columns, header_row_overrides)

        sample_rows_by_table: dict[str, list[dict[str, Any]]] = {}
        for table_name in table_names:
            sample_rows = _fetch_sample_rows(connection, engine, table_name, schema_name, limit=5)
            override = header_row_overrides.get(table_name) if header_row_overrides else None
            if override and sample_rows:
                first_row = sample_rows[0]
                if list(first_row.values()) == override["logical_columns"]:
                    sample_rows = sample_rows[1:]
            sample_rows_by_table[table_name] = sample_rows

    enriched_table_map, table_details = _build_table_details(
        table_columns=table_columns,
        primary_keys=primary_keys,
        foreign_keys=foreign_keys,
        row_count_estimates=row_count_estimates,
        sample_rows_by_table=sample_rows_by_table,
    )
    relationships = _infer_relationships(enriched_table_map, foreign_keys, primary_keys)
    return {
        "tables": enriched_table_map,
        "table_details": table_details,
        "relationships": relationships,
        "cached": False,
        "domain_hint": _detect_domain_hint(enriched_table_map),
    }


def _build_extended_schema_from_raw(raw_schema: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_raw_schema(raw_schema)
    table_columns = normalized["tables"]
    sample_rows_by_table = {table_name: [] for table_name in table_columns}
    primary_keys = {table_name: set() for table_name in table_columns}
    foreign_keys: dict[str, list[dict[str, str]]] = defaultdict(list)

    for relationship in normalized.get("relationships", []):
        confidence = relationship.get("confidence", "HIGH")
        foreign_keys[relationship["from_table"]].append(
            {
                "from_column": relationship["from_column"],
                "to_table": relationship["to_table"],
                "to_column": relationship["to_column"],
            }
        )
        relationship["confidence"] = confidence

    enriched_table_map, table_details = _build_table_details(
        table_columns=table_columns,
        primary_keys=primary_keys,
        foreign_keys=foreign_keys,
        row_count_estimates={table_name: None for table_name in table_columns},
        sample_rows_by_table=sample_rows_by_table,
    )
    relationships = _infer_relationships(enriched_table_map, foreign_keys, primary_keys)
    return {
        "tables": enriched_table_map,
        "table_details": table_details,
        "relationships": relationships,
        "cached": False,
        "domain_hint": _detect_domain_hint(enriched_table_map),
    }


def get_schema_metadata(
    source_kind: str = "database",
    engine_key: str = "postgresql",
    host: str = "localhost",
    port: int = 5432,
    database: str = "",
    username: str = "",
    password: str = "",
    options: dict[str, str] | None = None,
    force_refresh: bool = False,
    connection_id: str | None = None,
):
    cache_key = _cache_key(
        source_kind=source_kind,
        engine_key=engine_key,
        host=host,
        port=port,
        database=database,
        username=username,
        options=options,
        connection_id=connection_id,
    )

    cached = _get_cached_schema(cache_key, force_refresh=force_refresh)
    if cached:
        return cached

    if source_kind == "object_store":
        schema_info = _build_extended_schema_from_raw(
            get_object_store_schema_metadata(
                engine_key=engine_key,
                host=host,
                database=database,
                username=username,
                password=password,
                options=options,
            )
        )
        return _store_cached_schema(cache_key, schema_info)

    if handles_special_engine(engine_key):
        schema_info = _build_extended_schema_from_raw(
            get_special_schema_metadata(
                engine_key=engine_key,
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                options=options,
            )
        )
        return _store_cached_schema(cache_key, schema_info)

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
    schema_name = (options or {}).get("schema") or None

    try:
        schema_info = _build_raw_schema_for_regular_engine(
            engine=engine,
            engine_key=engine_key,
            schema_name=schema_name,
        )
        return _store_cached_schema(cache_key, schema_info)
    finally:
        engine.dispose()
