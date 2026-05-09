from __future__ import annotations

import re
from typing import Any

from sqlalchemy.engine import Engine


GENERIC_COLUMN_PATTERN = re.compile(r"^c(\d+)$", re.IGNORECASE)
NORMALIZED_CTE_PREFIX = "__adm_norm_"
SIMPLE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def is_sequential_generic_columns(column_names: list[str]) -> bool:
    return bool(column_names) and all(
        (match := GENERIC_COLUMN_PATTERN.match(str(column_name).strip())) and int(match.group(1)) == index + 1
        for index, column_name in enumerate(column_names)
    )


def is_header_like_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > 80:
        return False
    if not re.search(r"[A-Za-z_]", text):
        return False
    if re.fullmatch(r"\d+", text):
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text):
        return False
    return True


def detect_promoted_header_row(
    physical_columns: list[str],
    sample_rows: list[list[Any]],
) -> list[str] | None:
    if not is_sequential_generic_columns(physical_columns) or not sample_rows:
        return None

    candidate = sample_rows[0]
    if len(candidate) != len(physical_columns):
        return None

    header_like_count = sum(1 for value in candidate if is_header_like_value(value))
    normalized_values = [str(value or "").strip() for value in candidate]
    unique_count = len({value.lower() for value in normalized_values if value})

    if header_like_count < max(1, int(len(candidate) * 0.6 + 0.5)):
        return None
    if unique_count != len(candidate):
        return None

    return [value or "column" for value in normalized_values]


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def source_identifier(identifier: str, engine_key: str) -> str:
    text = str(identifier).strip()
    if SIMPLE_IDENTIFIER_PATTERN.fullmatch(text):
        return text
    return quote_identifier(text)


def qualified_table_name(table_name: str, engine_key: str, schema_name: str | None = None) -> str:
    if schema_name:
        return f"{source_identifier(schema_name, engine_key)}.{source_identifier(table_name, engine_key)}"
    return source_identifier(table_name, engine_key)


def fetch_header_row_overrides(
    engine: Engine,
    table_columns: dict[str, list[dict[str, Any]]],
    engine_key: str,
    schema_name: str | None = None,
) -> dict[str, dict[str, list[str]]]:
    overrides: dict[str, dict[str, list[str]]] = {}

    with engine.connect() as connection:
        for table_name, columns in table_columns.items():
            physical_columns = [str(column["name"]) for column in columns]
            if not is_sequential_generic_columns(physical_columns):
                continue

            try:
                qualified_name = qualified_table_name(table_name, engine_key, schema_name)
                result = connection.exec_driver_sql(f"SELECT * FROM {qualified_name} LIMIT 1")
                sample_rows = [list(row) for row in result.fetchall()]
            except Exception:
                continue

            logical_columns = detect_promoted_header_row(physical_columns, sample_rows)
            if not logical_columns:
                continue

            overrides[table_name] = {
                "physical_columns": physical_columns,
                "logical_columns": logical_columns,
            }

    return overrides


def apply_header_row_overrides(
    table_columns: dict[str, list[dict[str, Any]]],
    overrides: dict[str, dict[str, list[str]]],
) -> dict[str, list[dict[str, Any]]]:
    adjusted: dict[str, list[dict[str, Any]]] = {}

    for table_name, columns in table_columns.items():
        override = overrides.get(table_name)
        if not override:
            adjusted[table_name] = columns
            continue

        logical_columns = override["logical_columns"]
        adjusted[table_name] = [
            {
                **column,
                "name": logical_columns[index] if index < len(logical_columns) else column["name"],
            }
            for index, column in enumerate(columns)
        ]

    return adjusted


def _cast_to_text(expression: str, engine_key: str) -> str:
    if engine_key == "oracle":
        return f"CAST({expression} AS VARCHAR2(4000))"
    if engine_key in {"sqlserver", "synapse", "fabric"}:
        return f"CAST({expression} AS VARCHAR(MAX))"
    return f"CAST({expression} AS VARCHAR)"


def _normalized_cte_name(table_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", table_name).strip("_").lower() or "table"
    return f"{NORMALIZED_CTE_PREFIX}{slug}"


def rewrite_query_with_header_row_overrides(
    sql: str,
    overrides: dict[str, dict[str, list[str]]],
    engine_key: str,
    schema_name: str | None = None,
) -> str:
    rewritten_sql = sql
    cte_parts: list[str] = []

    for table_name, override in overrides.items():
        pattern = re.compile(rf"(?i)\b(from|join)\s+({re.escape(table_name)})\b")
        if not pattern.search(rewritten_sql):
            continue

        cte_name = _normalized_cte_name(table_name)
        physical_columns = override["physical_columns"]
        logical_columns = override["logical_columns"]
        select_parts = [
            f"{source_identifier(physical, engine_key)} AS {source_identifier(logical, engine_key)}"
            for physical, logical in zip(physical_columns, logical_columns)
        ]
        filter_parts = [
            f"COALESCE({_cast_to_text(source_identifier(physical, engine_key), engine_key)}, '') = {quote_literal(logical)}"
            for physical, logical in zip(physical_columns, logical_columns)
        ]

        cte_parts.append(
            f"{cte_name} AS ("
            f"SELECT {', '.join(select_parts)} "
            f"FROM {qualified_table_name(table_name, engine_key, schema_name)} "
            f"WHERE NOT ({' AND '.join(filter_parts)})"
            f")"
        )
        rewritten_sql = pattern.sub(lambda match: f"{match.group(1)} {cte_name}", rewritten_sql)

    if not cte_parts:
        return sql

    cte_sql = ", ".join(cte_parts)
    if re.match(r"(?is)^\s*with\b", rewritten_sql):
        return re.sub(r"(?is)^\s*with\b", f"WITH {cte_sql},", rewritten_sql, count=1)

    return f"WITH {cte_sql} {rewritten_sql}"
