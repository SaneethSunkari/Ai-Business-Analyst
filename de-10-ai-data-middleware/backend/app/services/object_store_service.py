from __future__ import annotations

import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import duckdb
from azure.storage.blob import BlobServiceClient
import boto3
from botocore.exceptions import BotoCoreError, ClientError


SUPPORTED_FORMATS = {"parquet", "csv", "json"}
MAX_DEFAULT_FILES = 25
MAX_DEFAULT_BYTES = 256 * 1024 * 1024


def _clean_options(options: dict[str, str] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in (options or {}).items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text
    return cleaned


def _sanitize_table_name(raw_name: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", raw_name.strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return "source_rows"
    if text[0].isdigit():
        text = f"t_{text}"
    return text


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _parse_int_option(options: dict[str, str], key: str, default: int) -> int:
    value = options.get(key)
    if not value:
        return default
    return max(1, int(value))


def _detect_file_format(file_names: list[str], requested_format: str | None) -> str:
    if requested_format:
        fmt = requested_format.strip().lower()
        if fmt == "auto":
            requested_format = None
        else:
            if fmt not in SUPPORTED_FORMATS:
                raise ValueError(f"Unsupported file format '{requested_format}'. Supported formats: auto, parquet, csv, json.")
            return fmt

    for name in file_names:
        lowered = name.lower()
        if lowered.endswith(".parquet") or lowered.endswith(".parq"):
            return "parquet"
        if lowered.endswith(".csv"):
            return "csv"
        if lowered.endswith(".json") or lowered.endswith(".jsonl") or lowered.endswith(".ndjson"):
            return "json"

    raise ValueError("Could not infer file format from the selected objects. Set options.file_format to parquet, csv, or json.")


def _derive_table_name(engine_key: str, host: str | None, database: str | None, options: dict[str, str]) -> str:
    if options.get("table_name"):
        return _sanitize_table_name(options["table_name"])

    if engine_key == "s3":
        candidate = database or host or "s3_rows"
    else:
        candidate = options.get("prefix") or database or host or "blob_rows"

    return _sanitize_table_name(Path(candidate.rstrip("/")).name or candidate)


def _build_scan_sql(local_paths: list[Path], file_format: str) -> str:
    escaped_paths = [str(path).replace("'", "''") for path in local_paths]
    quoted_paths = ", ".join("'" + path + "'" for path in escaped_paths)
    path_list = f"[{quoted_paths}]"

    if file_format == "parquet":
        return f"SELECT * FROM read_parquet({path_list}, union_by_name=true)"
    if file_format == "csv":
        return f"SELECT * FROM read_csv_auto({path_list}, union_by_name=true, header=true)"
    if file_format == "json":
        return f"SELECT * FROM read_json_auto({path_list}, union_by_name=true)"

    raise ValueError(f"Unsupported file format '{file_format}'.")


def _download_s3_files(
    host: str,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str],
    temp_dir: Path,
) -> tuple[list[Path], str]:
    region = options.get("region")
    if not region:
        raise ValueError("Region is required for Amazon S3.")

    client_kwargs: dict[str, Any] = {"region_name": region}
    if username and password:
        client_kwargs["aws_access_key_id"] = username
        client_kwargs["aws_secret_access_key"] = password
    if options.get("session_token"):
        client_kwargs["aws_session_token"] = options["session_token"]
    if options.get("endpoint_url"):
        client_kwargs["endpoint_url"] = options["endpoint_url"]

    client = boto3.client("s3", **client_kwargs)
    bucket = (host or "").strip()
    prefix = (database or "").strip()

    paginator = client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    max_files = _parse_int_option(options, "max_files", MAX_DEFAULT_FILES)
    max_bytes = _parse_int_option(options, "max_total_bytes", MAX_DEFAULT_BYTES)
    object_keys: list[str] = []
    file_names: list[str] = []
    total_bytes = 0

    for page in pages:
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith("/"):
                continue
            file_names.append(key)
            total_bytes += int(item.get("Size", 0))
            if total_bytes > max_bytes:
                raise ValueError("Matched object set is too large for the current MVP scan limit. Narrow the prefix or reduce the file count.")
            object_keys.append(key)
            if len(object_keys) >= max_files:
                break
        if len(object_keys) >= max_files:
            break

    if not object_keys:
        raise ValueError("No matching files were found in the selected S3 bucket/prefix.")

    file_format = _detect_file_format(file_names, options.get("file_format"))
    local_paths: list[Path] = []

    for index, key in enumerate(object_keys, start=1):
        suffix = Path(key).suffix or f".{file_format}"
        local_path = temp_dir / f"s3_{index}{suffix}"
        client.download_file(bucket, key, str(local_path))
        local_paths.append(local_path)

    return local_paths, file_format


def _build_azure_service_client(host: str | None, options: dict[str, str]) -> BlobServiceClient:
    if options.get("connection_string"):
        return BlobServiceClient.from_connection_string(options["connection_string"])

    if options.get("account_name") and options.get("account_key"):
        account_url = host or f"https://{options['account_name']}.blob.core.windows.net"
        return BlobServiceClient(account_url=account_url, credential=options["account_key"])

    if options.get("sas_token"):
        if not host:
            raise ValueError("Account URL is required when using a SAS token for Azure Blob.")
        return BlobServiceClient(account_url=host, credential=options["sas_token"].lstrip("?"))

    raise ValueError("Azure Blob requires either a connection string, account name + account key, or account URL + SAS token.")


def _download_azure_blob_files(
    host: str | None,
    database: str | None,
    options: dict[str, str],
    temp_dir: Path,
) -> tuple[list[Path], str]:
    service_client = _build_azure_service_client(host, options)
    container_name = (database or "").strip()
    if not container_name:
        raise ValueError("Container is required for Azure Blob.")

    prefix = (options.get("prefix") or "").strip()
    container_client = service_client.get_container_client(container_name)

    max_files = _parse_int_option(options, "max_files", MAX_DEFAULT_FILES)
    max_bytes = _parse_int_option(options, "max_total_bytes", MAX_DEFAULT_BYTES)
    blob_names: list[str] = []
    file_names: list[str] = []
    total_bytes = 0

    for blob in container_client.list_blobs(name_starts_with=prefix):
        name = blob["name"] if isinstance(blob, dict) else blob.name
        size = blob.get("size", 0) if isinstance(blob, dict) else getattr(blob, "size", 0)
        if name.endswith("/"):
            continue
        file_names.append(name)
        total_bytes += int(size or 0)
        if total_bytes > max_bytes:
            raise ValueError("Matched blob set is too large for the current MVP scan limit. Narrow the prefix or reduce the file count.")
        blob_names.append(name)
        if len(blob_names) >= max_files:
            break

    if not blob_names:
        raise ValueError("No matching files were found in the selected Azure Blob container/prefix.")

    file_format = _detect_file_format(file_names, options.get("file_format"))
    local_paths: list[Path] = []

    for index, blob_name in enumerate(blob_names, start=1):
        suffix = Path(blob_name).suffix or f".{file_format}"
        local_path = temp_dir / f"azure_{index}{suffix}"
        blob_client = container_client.get_blob_client(blob_name)
        with open(local_path, "wb") as handle:
            handle.write(blob_client.download_blob().readall())
        local_paths.append(local_path)

    return local_paths, file_format


def _prepare_local_object_store_files(
    engine_key: str,
    host: str | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str],
    temp_dir: Path,
) -> tuple[list[Path], str, str]:
    normalized_options = _clean_options(options)
    table_name = _derive_table_name(engine_key, host, database, normalized_options)

    if engine_key == "s3":
        paths, file_format = _download_s3_files(host or "", database, username, password, normalized_options, temp_dir)
    elif engine_key == "azure_blob":
        paths, file_format = _download_azure_blob_files(host, database, normalized_options, temp_dir)
    else:
        raise ValueError(f"Unsupported object store engine '{engine_key}'.")

    return paths, file_format, table_name


def test_object_store_connection(
    engine_key: str,
    host: str | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str] | None = None,
) -> tuple[bool, str]:
    normalized_options = _clean_options(options)
    try:
        if engine_key == "s3":
            region = normalized_options.get("region")
            if not region:
                return False, "Region is required for Amazon S3."
            client_kwargs: dict[str, Any] = {"region_name": region}
            if username and password:
                client_kwargs["aws_access_key_id"] = username
                client_kwargs["aws_secret_access_key"] = password
            if normalized_options.get("session_token"):
                client_kwargs["aws_session_token"] = normalized_options["session_token"]
            if normalized_options.get("endpoint_url"):
                client_kwargs["endpoint_url"] = normalized_options["endpoint_url"]

            client = boto3.client("s3", **client_kwargs)
            client.head_bucket(Bucket=(host or "").strip())
            prefix = (database or "").strip()
            if prefix:
                resp = client.list_objects_v2(Bucket=(host or "").strip(), Prefix=prefix, MaxKeys=1)
                if not resp.get("Contents"):
                    return True, "Connection successful. No matching files found for the current prefix."
            return True, "Connection successful"

        if engine_key == "azure_blob":
            service_client = _build_azure_service_client(host, normalized_options)
            container_name = (database or "").strip()
            container_client = service_client.get_container_client(container_name)
            container_client.get_container_properties()
            prefix = (normalized_options.get("prefix") or "").strip()
            if prefix:
                iterator = container_client.list_blobs(name_starts_with=prefix, results_per_page=1).by_page()
                first_page = next(iterator, [])
                if not list(first_page):
                    return True, "Connection successful. No matching files found for the current prefix."
            return True, "Connection successful"

        return False, f"Unsupported object store engine '{engine_key}'."
    except (ClientError, BotoCoreError, ValueError, OSError) as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def get_object_store_schema_metadata(
    engine_key: str,
    host: str | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str] | None = None,
) -> dict:
    with TemporaryDirectory(prefix="middleware-object-scan-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        local_paths, file_format, table_name = _prepare_local_object_store_files(
            engine_key=engine_key,
            host=host,
            database=database,
            username=username,
            password=password,
            options=options or {},
            temp_dir=temp_dir,
        )

        scan_sql = _build_scan_sql(local_paths, file_format)
        conn = duckdb.connect(database=":memory:")
        try:
            conn.execute(f"CREATE OR REPLACE VIEW {_quote_identifier(table_name)} AS {scan_sql}")
            rows = conn.execute(f"DESCRIBE SELECT * FROM {_quote_identifier(table_name)}").fetchall()
            columns = [
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": True,
                }
                for row in rows
            ]
            return {
                "tables": {table_name: columns},
                "relationships": [],
            }
        finally:
            conn.close()


def execute_object_store_sql(
    engine_key: str,
    host: str | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str] | None,
    sql: str,
) -> dict:
    with TemporaryDirectory(prefix="middleware-object-query-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        local_paths, file_format, table_name = _prepare_local_object_store_files(
            engine_key=engine_key,
            host=host,
            database=database,
            username=username,
            password=password,
            options=options or {},
            temp_dir=temp_dir,
        )

        scan_sql = _build_scan_sql(local_paths, file_format)
        conn = duckdb.connect(database=":memory:")
        try:
            conn.execute(f"CREATE OR REPLACE VIEW {_quote_identifier(table_name)} AS {scan_sql}")
            result = conn.execute(sql)
            rows = result.fetchall()
            columns = [desc[0] for desc in (result.description or [])]
            return {"success": True, "columns": columns, "rows": [list(row) for row in rows]}
        finally:
            conn.close()
