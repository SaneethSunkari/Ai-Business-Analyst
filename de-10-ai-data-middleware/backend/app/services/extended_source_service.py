from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote_plus

import duckdb
import httpx
from databricks import sql as databricks_sql
from kafka import KafkaConsumer
from kafka.errors import KafkaError, NoBrokersAvailable
from pyathena import connect as athena_connect
from pymongo import MongoClient
from simple_salesforce import Salesforce
from trino.auth import BasicAuthentication
from trino.dbapi import connect as trino_connect


SPECIAL_SOURCE_ENGINES = {
    "databricks_sql",
    "athena",
    "trino",
    "dremio",
    "salesforce",
    "mongodb",
    "kafka",
}

MAX_SAMPLE_TABLES = 20
DEFAULT_SAMPLE_SIZE = 200


def handles_special_engine(engine_key: str) -> bool:
    return engine_key in SPECIAL_SOURCE_ENGINES


def _clean_options(options: dict[str, str] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in (options or {}).items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text
    return cleaned


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_databricks_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _sanitize_table_name(raw_name: str) -> str:
    text = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw_name.strip().lower())
    while "__" in text:
        text = text.replace("__", "_")
    text = text.strip("_")
    if not text:
        return "source_rows"
    if text[0].isdigit():
        text = f"t_{text}"
    return text


def _normalize_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return str(value)


def _flatten_record(value: Any, prefix: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    out = out or {}
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            next_prefix = f"{prefix}_{key_text}" if prefix else key_text
            if isinstance(nested, dict):
                _flatten_record(nested, next_prefix, out)
            else:
                out[next_prefix] = _normalize_scalar(nested)
        return out

    out[prefix or "value"] = _normalize_scalar(value)
    return out


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, default=str) + "\n")


def _infer_duckdb_schema_from_records(table_records: dict[str, list[dict[str, Any]]]) -> dict:
    conn = duckdb.connect(database=":memory:")
    try:
        schema: dict[str, list[dict[str, Any]]] = {}
        with TemporaryDirectory(prefix="middleware-virtual-schema-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            for table_name, records in table_records.items():
                if not records:
                    schema[table_name] = []
                    continue
                json_path = temp_dir / f"{table_name}.jsonl"
                _write_jsonl(json_path, records)
                conn.execute(
                    f"CREATE OR REPLACE VIEW {_quote_identifier(table_name)} AS "
                    f"SELECT * FROM read_json_auto({_quote_sql_string(str(json_path))})"
                )
                rows = conn.execute(f"DESCRIBE SELECT * FROM {_quote_identifier(table_name)}").fetchall()
                schema[table_name] = [
                    {
                        "name": row[0],
                        "type": row[1],
                        "nullable": True,
                    }
                    for row in rows
                ]
        return {"tables": schema, "relationships": []}
    finally:
        conn.close()


def _query_duckdb_records(table_records: dict[str, list[dict[str, Any]]], sql: str) -> dict:
    conn = duckdb.connect(database=":memory:")
    try:
        with TemporaryDirectory(prefix="middleware-virtual-query-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            for table_name, records in table_records.items():
                if not records:
                    continue
                json_path = temp_dir / f"{table_name}.jsonl"
                _write_jsonl(json_path, records)
                conn.execute(
                    f"CREATE OR REPLACE VIEW {_quote_identifier(table_name)} AS "
                    f"SELECT * FROM read_json_auto({_quote_sql_string(str(json_path))})"
                )
            result = conn.execute(sql)
            rows = result.fetchall()
            columns = [desc[0] for desc in (result.description or [])]
            return {"success": True, "columns": columns, "rows": [list(row) for row in rows]}
    finally:
        conn.close()


def _databricks_connection(host: str | None, password: str | None, options: dict[str, str]):
    server_hostname = (host or "").strip()
    if not server_hostname:
        raise ValueError("Server Hostname is required for Databricks SQL.")
    http_path = options.get("http_path")
    if not http_path:
        raise ValueError("HTTP Path is required for Databricks SQL.")
    access_token = (password or "").strip()
    if not access_token:
        raise ValueError("Access Token is required for Databricks SQL.")
    return databricks_sql.connect(
        server_hostname=server_hostname,
        http_path=http_path,
        access_token=access_token,
    )


def _databricks_apply_context(cursor, catalog: str | None, schema: str | None) -> None:
    if catalog:
        cursor.execute(f"USE CATALOG {_quote_databricks_identifier(catalog)}")
    if schema:
        cursor.execute(f"USE SCHEMA {_quote_databricks_identifier(schema)}")


def _databricks_schema(database: str | None, options: dict[str, str]) -> dict:
    filters = []
    if database:
        filters.append(f"table_catalog = {_quote_sql_string(database)}")
    if options.get("schema"):
        filters.append(f"table_schema = {_quote_sql_string(options['schema'])}")

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    return _rows_to_schema_dict(
        _run_special_sql_query(
            "databricks_sql",
            sql=(
                "SELECT table_schema, table_name, column_name, data_type, is_nullable "
                "FROM system.information_schema.columns "
                f"{where_clause} "
                "ORDER BY table_schema, table_name, ordinal_position"
            ),
            database=database,
            options=options,
        )
    )


def _athena_connection(database: str | None, username: str | None, password: str | None, options: dict[str, str]):
    region = options.get("region")
    if not region:
        raise ValueError("Region is required for Athena.")
    s3_staging_dir = options.get("s3_staging_dir")
    if not s3_staging_dir:
        raise ValueError("S3 Staging Dir is required for Athena.")

    connect_kwargs: dict[str, Any] = {
        "region_name": region,
        "s3_staging_dir": s3_staging_dir,
    }
    if database:
        connect_kwargs["schema_name"] = database
    if username and password:
        connect_kwargs["aws_access_key_id"] = username
        connect_kwargs["aws_secret_access_key"] = password
    if options.get("session_token"):
        connect_kwargs["aws_session_token"] = options["session_token"]
    if options.get("work_group"):
        connect_kwargs["work_group"] = options["work_group"]
    return athena_connect(**connect_kwargs)


def _trino_connection(
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str],
):
    http_scheme = options.get("http_scheme", "https")
    connect_kwargs: dict[str, Any] = {
        "host": (host or "").strip(),
        "port": port or 8080,
        "user": (username or "").strip() or "trino",
        "catalog": (database or "").strip() or "system",
        "schema": options.get("schema") or "information_schema",
        "http_scheme": http_scheme,
    }
    if password:
        connect_kwargs["auth"] = BasicAuthentication(connect_kwargs["user"], password)
    return trino_connect(**connect_kwargs)


def _dremio_base_url(host: str | None) -> str:
    base = (host or "").strip()
    if not base:
        raise ValueError("API Base URL is required for Dremio.")
    if not base.startswith("http://") and not base.startswith("https://"):
        base = f"https://{base}"
    return base.rstrip("/")


def _dremio_headers(password: str | None) -> dict[str, str]:
    token = (password or "").strip()
    if not token:
        raise ValueError("Access Token is required for Dremio.")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _dremio_wait_for_job(client: httpx.Client, base_url: str, project_id: str, job_id: str) -> dict[str, Any]:
    deadline = time.time() + 30
    while time.time() < deadline:
        response = client.get(
            f"{base_url}/v0/projects/{project_id}/job/{job_id}",
            headers=_dremio_headers(client.headers.get("Authorization", "").removeprefix("Bearer ")),
        )
        response.raise_for_status()
        job = response.json()
        state = str(job.get("jobState", "")).upper()
        if state == "COMPLETED":
            return job
        if state in {"FAILED", "CANCELED"}:
            raise ValueError(job.get("errorMessage") or f"Dremio job {state.lower()}.")
        time.sleep(1)
    raise ValueError("Timed out waiting for the Dremio query to complete.")


def _dremio_execute_query(base_url: str, project_id: str, token: str, sql: str) -> dict:
    if not project_id:
        raise ValueError("Project ID is required for Dremio.")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{base_url}/v0/projects/{project_id}/sql",
            headers=headers,
            json={"sql": sql},
        )
        response.raise_for_status()
        payload = response.json()
        job_id = payload.get("id")
        if not job_id:
            raise ValueError("Dremio did not return a job ID.")

        deadline = time.time() + 30
        while time.time() < deadline:
            job_response = client.get(
                f"{base_url}/v0/projects/{project_id}/job/{job_id}",
                headers=headers,
            )
            job_response.raise_for_status()
            job = job_response.json()
            state = str(job.get("jobState", "")).upper()
            if state == "COMPLETED":
                break
            if state in {"FAILED", "CANCELED"}:
                raise ValueError(job.get("errorMessage") or f"Dremio job {state.lower()}.")
            time.sleep(1)
        else:
            raise ValueError("Timed out waiting for the Dremio query to complete.")

        result_response = client.get(
            f"{base_url}/v0/projects/{project_id}/job/{job_id}/results",
            headers=headers,
            params={"limit": 500, "offset": 0},
        )
        result_response.raise_for_status()
        results = result_response.json()
        schema = results.get("schema", [])
        rows = results.get("rows", [])
        columns = [column.get("name") for column in schema]
        formatted_rows = [[row.get(column) for column in columns] for row in rows]
        return {
            "success": True,
            "columns": columns,
            "rows": formatted_rows,
        }


def _salesforce_client(username: str | None, password: str | None, options: dict[str, str]) -> Salesforce:
    user = (username or "").strip()
    if not user:
        raise ValueError("Username is required for Salesforce.")
    passwd = (password or "").strip()
    if not passwd:
        raise ValueError("Password is required for Salesforce.")
    security_token = options.get("security_token")
    if not security_token:
        raise ValueError("Security Token is required for Salesforce.")

    return Salesforce(
        username=user,
        password=passwd,
        security_token=security_token,
        domain=options.get("domain", "login"),
    )


def _salesforce_object_names(sf: Salesforce, object_name: str | None) -> list[str]:
    if object_name:
        return [object_name]
    global_desc = sf.describe()
    return [
        obj["name"]
        for obj in global_desc.get("sobjects", [])
        if obj.get("queryable")
    ]


def _salesforce_schema(database: str | None, username: str | None, password: str | None, options: dict[str, str]) -> dict:
    sf = _salesforce_client(username, password, options)
    tables: dict[str, list[dict[str, Any]]] = {}
    for object_name in _salesforce_object_names(sf, database)[:MAX_SAMPLE_TABLES]:
        description = getattr(sf, object_name).describe()
        tables[object_name] = [
            {
                "name": field["name"],
                "type": str(field.get("type", "string")).upper(),
                "nullable": bool(field.get("nillable", True)),
            }
            for field in description.get("fields", [])
        ]
    return {"tables": tables, "relationships": []}


def _salesforce_query(sql: str, username: str | None, password: str | None, options: dict[str, str]) -> dict:
    sf = _salesforce_client(username, password, options)
    result = sf.query_all(sql)
    records = result.get("records", [])
    cleaned_records = []
    for record in records:
        cleaned = {key: value for key, value in record.items() if key != "attributes"}
        cleaned_records.append(cleaned)

    columns = sorted({key for record in cleaned_records for key in record.keys()})
    rows = [[record.get(column) for column in columns] for record in cleaned_records]
    return {"success": True, "columns": columns, "rows": rows}


def _mongo_uri(host: str | None, port: int | None, username: str | None, password: str | None, options: dict[str, str]) -> str:
    if options.get("connection_uri"):
        return options["connection_uri"]
    host_text = (host or "").strip() or "localhost"
    if username and password:
        return f"mongodb://{quote_plus(username)}:{quote_plus(password)}@{host_text}:{port or 27017}"
    return f"mongodb://{host_text}:{port or 27017}"


def _mongo_client(
    host: str | None,
    port: int | None,
    username: str | None,
    password: str | None,
    options: dict[str, str],
) -> MongoClient:
    return MongoClient(_mongo_uri(host, port, username, password, options), serverSelectionTimeoutMS=5000)


def _mongo_table_records(
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    db_name = (database or "").strip()
    if not db_name:
        raise ValueError("Database is required for MongoDB.")
    sample_size = int(options.get("sample_size", DEFAULT_SAMPLE_SIZE))
    max_tables = int(options.get("max_collections", MAX_SAMPLE_TABLES))
    client = _mongo_client(host, port, username, password, options)
    try:
        db = client[db_name]
        collection_override = options.get("collection")
        collection_names = [collection_override] if collection_override else db.list_collection_names()[:max_tables]
        table_records: dict[str, list[dict[str, Any]]] = {}
        for collection_name in collection_names:
            if not collection_name:
                continue
            rows = []
            for doc in db[collection_name].find().limit(sample_size):
                rows.append(_flatten_record(doc))
            table_records[_sanitize_table_name(collection_name)] = rows
        return table_records
    finally:
        client.close()


def _kafka_bootstrap_servers(host: str | None, port: int | None) -> list[str]:
    host_text = (host or "").strip()
    if not host_text:
        raise ValueError("Bootstrap Servers is required for Kafka.")
    if "," in host_text:
        return [item.strip() for item in host_text.split(",") if item.strip()]
    if ":" in host_text or port is None:
        return [host_text]
    return [f"{host_text}:{port}"]


def _decode_kafka_value(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _kafka_consumer(host: str | None, port: int | None, topic: str, options: dict[str, str]) -> KafkaConsumer:
    return KafkaConsumer(
        topic,
        bootstrap_servers=_kafka_bootstrap_servers(host, port),
        group_id=options.get("group_id") or None,
        auto_offset_reset=options.get("auto_offset_reset", "earliest"),
        enable_auto_commit=False,
        consumer_timeout_ms=5000,
        api_version_auto_timeout_ms=5000,
        value_deserializer=_decode_kafka_value,
    )


def _kafka_topic_name(database: str | None, options: dict[str, str]) -> str:
    topic = (options.get("topic") or database or "").strip()
    if not topic:
        raise ValueError("Topic is required for Kafka.")
    return topic


def _kafka_table_records(host: str | None, port: int | None, database: str | None, options: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    topic = _kafka_topic_name(database, options)
    sample_size = int(options.get("sample_size", "100"))
    table_name = _sanitize_table_name(topic)
    consumer = _kafka_consumer(host, port, topic, options)
    try:
        partitions = consumer.partitions_for_topic(topic)
        if partitions is None:
            raise ValueError(f"Kafka topic '{topic}' was not found.")

        rows: list[dict[str, Any]] = []
        for message in consumer:
            payload = message.value
            if isinstance(payload, dict):
                normalized = _flatten_record(payload)
            else:
                normalized = {"value": _normalize_scalar(payload)}
            normalized["_partition"] = message.partition
            normalized["_offset"] = message.offset
            normalized["_timestamp"] = message.timestamp
            rows.append(normalized)
            if len(rows) >= sample_size:
                break
        return {table_name: rows}
    finally:
        consumer.close()


def _rows_to_schema_dict(query_result: dict) -> dict:
    tables: dict[str, list[dict[str, Any]]] = {}
    if not query_result.get("success"):
        return {"tables": tables, "relationships": []}

    columns = query_result.get("columns", [])
    rows = query_result.get("rows", [])
    if not rows:
        return {"tables": tables, "relationships": []}

    column_indexes = {name: idx for idx, name in enumerate(columns)}
    table_schema_idx = column_indexes.get("table_schema")
    table_name_idx = column_indexes.get("table_name")
    column_name_idx = column_indexes.get("column_name")
    data_type_idx = column_indexes.get("data_type")
    nullable_idx = column_indexes.get("is_nullable")

    if table_name_idx is None or column_name_idx is None:
        return {"tables": tables, "relationships": []}

    for row in rows:
        table_schema = row[table_schema_idx] if table_schema_idx is not None else None
        table_name = row[table_name_idx]
        display_name = str(table_name)
        if table_schema and table_schema not in {"public", "default"}:
            display_name = f"{table_schema}_{table_name}"
        tables.setdefault(display_name, []).append(
            {
                "name": str(row[column_name_idx]),
                "type": str(row[data_type_idx]) if data_type_idx is not None else "UNKNOWN",
                "nullable": str(row[nullable_idx]).upper() != "NO" if nullable_idx is not None else True,
            }
        )

    return {"tables": tables, "relationships": []}


def _cursor_result(cursor) -> dict:
    rows = cursor.fetchall()
    columns = [desc[0] for desc in (cursor.description or [])]
    return {"success": True, "columns": columns, "rows": [list(row) for row in rows]}


def _run_special_sql_query(
    engine_key: str,
    sql: str,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    username: str | None = None,
    password: str | None = None,
    options: dict[str, str] | None = None,
) -> dict:
    normalized_options = _clean_options(options)

    if engine_key == "databricks_sql":
        with _databricks_connection(host, password, normalized_options) as connection:
            with connection.cursor() as cursor:
                _databricks_apply_context(cursor, database, normalized_options.get("schema"))
                cursor.execute(sql)
                return _cursor_result(cursor)

    if engine_key == "athena":
        connection = _athena_connection(database, username, password, normalized_options)
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            return _cursor_result(cursor)
        finally:
            connection.close()

    if engine_key == "trino":
        connection = _trino_connection(host, port, database, username, password, normalized_options)
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            return _cursor_result(cursor)
        finally:
            connection.close()

    if engine_key == "dremio":
        return _dremio_execute_query(
            base_url=_dremio_base_url(host),
            project_id=(database or "").strip(),
            token=password or "",
            sql=sql,
        )

    if engine_key == "salesforce":
        return _salesforce_query(sql, username, password, normalized_options)

    if engine_key == "mongodb":
        return _query_duckdb_records(
            _mongo_table_records(host, port, database, username, password, normalized_options),
            sql,
        )

    if engine_key == "kafka":
        return _query_duckdb_records(
            _kafka_table_records(host, port, database, normalized_options),
            sql,
        )

    raise ValueError(f"Unsupported special source engine '{engine_key}'.")


def test_special_connection(
    engine_key: str,
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str] | None = None,
) -> tuple[bool, str]:
    normalized_options = _clean_options(options)
    try:
        if engine_key == "mongodb":
            client = _mongo_client(host, port, username, password, normalized_options)
            try:
                client.admin.command("ping")
            finally:
                client.close()
            return True, "Connection successful"

        if engine_key == "kafka":
            topic = _kafka_topic_name(database, normalized_options)
            consumer = _kafka_consumer(host, port, topic, normalized_options)
            try:
                partitions = consumer.partitions_for_topic(topic)
                if partitions is None:
                    return False, f"Kafka topic '{topic}' was not found."
            finally:
                consumer.close()
            return True, "Connection successful"

        if engine_key == "salesforce":
            sf = _salesforce_client(username, password, normalized_options)
            sf.describe()
            return True, "Connection successful"

        result = _run_special_sql_query(
            engine_key=engine_key,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            options=normalized_options,
            sql="SELECT 1",
        )
        return result.get("success", False), "Connection successful" if result.get("success") else result.get("error", "Connection failed")
    except (httpx.HTTPError, KafkaError, NoBrokersAvailable, ValueError, OSError) as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def get_special_schema_metadata(
    engine_key: str,
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str] | None = None,
) -> dict:
    normalized_options = _clean_options(options)

    if engine_key == "databricks_sql":
        return _databricks_schema(database, normalized_options)

    if engine_key == "athena":
        schema_name = (database or "").strip() or "default"
        return _rows_to_schema_dict(
            _run_special_sql_query(
                "athena",
                database=database,
                username=username,
                password=password,
                options=normalized_options,
                sql=(
                    "SELECT table_schema, table_name, column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    f"WHERE table_schema = {_quote_sql_string(schema_name)} "
                    "ORDER BY table_name, ordinal_position"
                ),
            )
        )

    if engine_key == "trino":
        schema_name = normalized_options.get("schema") or "default"
        return _rows_to_schema_dict(
            _run_special_sql_query(
                "trino",
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                options=normalized_options,
                sql=(
                    "SELECT table_schema, table_name, column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    f"WHERE table_schema = {_quote_sql_string(schema_name)} "
                    "ORDER BY table_name, ordinal_position"
                ),
            )
        )

    if engine_key == "dremio":
        return _rows_to_schema_dict(
            _run_special_sql_query(
                "dremio",
                host=host,
                database=database,
                password=password,
                options=normalized_options,
                sql=(
                    "SELECT table_schema, table_name, column_name, data_type, is_nullable "
                    "FROM INFORMATION_SCHEMA.COLUMNS "
                    "ORDER BY table_schema, table_name, ordinal_position"
                ),
            )
        )

    if engine_key == "salesforce":
        return _salesforce_schema(database, username, password, normalized_options)

    if engine_key == "mongodb":
        return _infer_duckdb_schema_from_records(
            _mongo_table_records(host, port, database, username, password, normalized_options)
        )

    if engine_key == "kafka":
        return _infer_duckdb_schema_from_records(
            _kafka_table_records(host, port, database, normalized_options)
        )

    raise ValueError(f"Unsupported special source engine '{engine_key}'.")


def execute_special_query(
    engine_key: str,
    sql: str,
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str] | None = None,
) -> dict:
    return _run_special_sql_query(
        engine_key=engine_key,
        sql=sql,
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        options=options,
    )
