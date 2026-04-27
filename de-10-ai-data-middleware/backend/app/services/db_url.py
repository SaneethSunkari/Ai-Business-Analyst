import re
from urllib.parse import quote_plus

from sqlalchemy.engine import URL


def _clean_options(options: dict[str, str] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in (options or {}).items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text
    return cleaned


def _normalize_snowflake_account(account: str) -> str:
    normalized = account.strip()
    normalized = re.sub(r"^https?://", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.split("/", maxsplit=1)[0]
    normalized = re.sub(r"\.snowflakecomputing(\.[a-z.]+)?$", "", normalized, flags=re.IGNORECASE)
    return normalized


def build_db_url(
    engine_key: str,
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
    options: dict[str, str] | None = None,
) -> str | URL:
    host = (host or "").strip()
    database = (database or "").strip()
    username = (username or "").strip()
    password = password or ""
    cleaned_options = _clean_options(options)

    if engine_key == "postgresql":
        return URL.create(
            "postgresql+psycopg2",
            username=username or None,
            password=password or None,
            host=host or None,
            port=port or None,
            database=database or None,
        )

    if engine_key == "mysql":
        return URL.create(
            "mysql+pymysql",
            username=username or None,
            password=password or None,
            host=host or None,
            port=port or None,
            database=database or None,
        )

    if engine_key in {"sqlserver", "synapse", "fabric"}:
        return URL.create(
            "mssql+pytds",
            username=username or None,
            password=password or None,
            host=host or None,
            port=port or None,
            database=database or None,
        )

    if engine_key == "sqlite":
        if database == ":memory:":
            return "sqlite:///:memory:"
        return f"sqlite:///{database}"

    if engine_key == "oracle":
        query = {}
        if database:
            query["service_name"] = database
        return URL.create(
            "oracle+oracledb",
            username=username or None,
            password=password or None,
            host=host or None,
            port=port or None,
            query=query,
        )

    if engine_key == "snowflake":
        from snowflake.sqlalchemy import URL as SnowflakeURL

        return SnowflakeURL(
            user=username,
            password=password,
            account=_normalize_snowflake_account(host),
            database=database or None,
            schema=cleaned_options.get("schema"),
            warehouse=cleaned_options.get("warehouse"),
            role=cleaned_options.get("role"),
        )

    if engine_key == "bigquery":
        project = cleaned_options.get("project", "")
        dataset = database
        base = f"bigquery://{project}"
        if dataset:
            base += f"/{dataset}"

        query_parts: list[str] = []
        for key in ("location", "credentials_path"):
            value = cleaned_options.get(key)
            if value:
                query_parts.append(f"{key}={quote_plus(value)}")

        if query_parts:
            base += "?" + "&".join(query_parts)
        return base

    if engine_key == "redshift":
        return URL.create(
            "postgresql+psycopg2",
            username=username or None,
            password=password or None,
            host=host or None,
            port=port or None,
            database=database or None,
        )

    raise ValueError(f"Unsupported source engine '{engine_key}'.")
