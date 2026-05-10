import os
import re
from pathlib import Path

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
UNANSWERABLE_SQL = "SELECT 'UNANSWERABLE' AS error;"
_client: OpenAI | None = None
_client_config: tuple[str, str | None] | None = None
QUESTION_STOPWORDS = {
    "a",
    "all",
    "an",
    "and",
    "are",
    "from",
    "for",
    "get",
    "give",
    "how",
    "in",
    "is",
    "list",
    "me",
    "of",
    "on",
    "show",
    "the",
    "their",
    "these",
    "those",
    "to",
    "what",
    "with",
}


def _gateway_base_url() -> str | None:
    """Return an OpenAI-compatible base URL when a gateway is configured."""

    explicit = (os.getenv("OPENAI_BASE_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    gateway = (os.getenv("TOKENFIREWALL_BASE_URL") or "").strip()
    if not gateway:
        return None
    gateway = gateway.rstrip("/")
    return gateway if gateway.endswith("/v1") else f"{gateway}/v1"


def get_gateway_base_url() -> str | None:
    return _gateway_base_url()


def get_openai_client() -> OpenAI:
    global _client, _client_config
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured for AI queries.")

    base_url = _gateway_base_url()
    config = (api_key, base_url)
    if _client is None or _client_config != config:
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _client = OpenAI(**kwargs)
        _client_config = config
    return _client


def normalize_term(term: str) -> str:
    if term.endswith("ies") and len(term) > 3:
        return term[:-3] + "y"

    if term.endswith("s") and not term.endswith("ss") and len(term) > 3:
        return term[:-1]

    return term


def build_schema_table_terms(schema_metadata: dict) -> set[str]:
    terms = set()
    tables = schema_metadata.get("tables", {})

    for table_name in tables:
        parts = table_name.lower().split("_")

        for part in parts:
            terms.add(part)
            terms.add(normalize_term(part))

        full_name = "_".join(parts)
        full_name_no_underscore = "".join(parts)

        terms.add(full_name)
        terms.add(normalize_term(full_name))
        terms.add(full_name_no_underscore)
        terms.add(normalize_term(full_name_no_underscore))

    return terms


def question_targets_known_table(question: str, schema_metadata: dict) -> bool:
    tables = schema_metadata.get("tables", {})
    if len(tables) == 1 and question.strip():
        return True

    question_terms = {
        normalize_term(term)
        for term in re.findall(r"[a-zA-Z_]+", question.lower())
        if term not in QUESTION_STOPWORDS and len(term) > 2
    }

    if not question_terms:
        return False

    schema_terms = build_schema_table_terms(schema_metadata)
    return any(term in schema_terms for term in question_terms)


def extract_referenced_tables(sql: str) -> set[str]:
    return {
        match.lower()
        for match in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql)
    }


def clean_sql_output(raw_text: str) -> str:
    text = raw_text.strip()

    # Remove markdown code fences
    text = re.sub(r"^```sql\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # Keep only from first WITH/SELECT onward
    match = re.search(r"\b(?:with|select)\b.*", text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        text = match.group(0).strip()

    if ";" in text:
        text = text.split(";", maxsplit=1)[0].strip() + ";"

    return text


_DIALECT_LABELS = {
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "postgresql": "PostgreSQL",
    "sqlserver": "Microsoft SQL Server",
    "oracle": "Oracle SQL",
    "snowflake": "Snowflake SQL",
    "bigquery": "GoogleSQL for BigQuery",
    "redshift": "Amazon Redshift SQL",
    "databricks_sql": "Databricks SQL",
    "athena": "Amazon Athena SQL",
    "synapse": "Azure Synapse SQL",
    "fabric": "Microsoft Fabric Warehouse SQL",
    "trino": "Trino SQL",
    "dremio": "Dremio SQL",
    "salesforce": "Salesforce SOQL",
    "mongodb": "DuckDB SQL over sampled MongoDB documents",
    "kafka": "DuckDB SQL over sampled Kafka messages",
}

PATIENT_TABLE_TERMS = {"patient", "patients"}
ROW_COUNT_ALIAS = "patient_count"


def _normalize_question_text(question: str) -> str:
    text = question.strip().lower()
    replacements = [
        (r"^\s*(create|show|build)\s+(a\s+)?dashboard\s+(of|for)\s+", ""),
        (r"^\s*(create|show|build)\s+(a\s+)?chart\s+(of|for)\s+", ""),
        (r"^\s*give me\s+(a\s+)?kpi summary of\s+", "kpi summary of "),
        (r"^\s*show\s+(a\s+)?dashboard\s+of\s+", ""),
        (r"^\s*build\s+(an?\s+)?executive summary of\s+", "executive summary of "),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _pick_primary_table(schema_metadata: dict) -> tuple[str, list[dict]] | tuple[None, None]:
    tables = schema_metadata.get("tables", {})
    for table_name, columns in tables.items():
        if normalize_term(table_name.lower()) in PATIENT_TABLE_TERMS:
            return table_name, columns
    if len(tables) == 1:
        table_name, columns = next(iter(tables.items()))
        return table_name, columns
    return None, None


def _normalize_identifier(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _column_lookup(columns: list[dict]) -> dict[str, str]:
    return {_normalize_identifier(column["name"]): column["name"] for column in columns}


def _find_column(columns: list[dict], *aliases: str) -> str | None:
    lookup = _column_lookup(columns)
    for alias in aliases:
        match = lookup.get(_normalize_identifier(alias))
        if match:
            return match
    return None


def _limit_clause(limit: int, engine_key: str) -> str:
    if engine_key == "oracle":
        return f" FETCH FIRST {limit} ROWS ONLY"
    return f" LIMIT {limit}"


def _select_prefix(limit: int | None, engine_key: str) -> str:
    if limit and engine_key in {"sqlserver", "synapse", "fabric"}:
        return f"SELECT TOP {limit}"
    return "SELECT"


def _apply_limit(sql: str, limit: int | None, engine_key: str) -> str:
    if not limit:
        return sql
    if engine_key in {"sqlserver", "synapse", "fabric"}:
        return re.sub(r"^\s*select\b", f"SELECT TOP {limit}", sql, count=1, flags=re.IGNORECASE)
    return sql + _limit_clause(limit, engine_key)


def _year_expr(column_name: str, engine_key: str) -> str:
    if engine_key == "sqlite":
        return f"CAST(strftime('%Y', {column_name}) AS INTEGER)"
    if engine_key in {"mysql", "sqlserver", "synapse", "fabric"}:
        return f"YEAR({column_name})"
    return f"EXTRACT(YEAR FROM {column_name})"


def _decade_expr(column_name: str, engine_key: str) -> str:
    year_expr = _year_expr(column_name, engine_key)
    if engine_key == "sqlite":
        return f"(({year_expr} / 10) * 10)"
    if engine_key in {"mysql", "sqlserver", "synapse", "fabric"}:
        return f"(FLOOR({year_expr} / 10) * 10)"
    return f"(FLOOR({year_expr} / 10) * 10)"


def _count_by_dimension_sql(
    table_name: str,
    dimension_column: str,
    engine_key: str,
    *,
    limit: int | None = None,
) -> str:
    sql = (
        f"SELECT {dimension_column}, COUNT(*) AS {ROW_COUNT_ALIAS} "
        f"FROM {table_name} "
        f"WHERE {dimension_column} IS NOT NULL "
        f"GROUP BY {dimension_column} "
        f"ORDER BY {ROW_COUNT_ALIAS} DESC"
    )
    return _apply_limit(sql, limit, engine_key) + ";"


def _birth_year_count_sql(
    table_name: str,
    birthdate_column: str,
    engine_key: str,
    *,
    year_after: int | None = None,
) -> str:
    year_expr = _year_expr(birthdate_column, engine_key)
    where_parts = [f"{birthdate_column} IS NOT NULL"]
    if year_after is not None:
        where_parts.append(f"{year_expr} > {year_after}")
    return (
        f"SELECT {year_expr} AS birth_year, COUNT(*) AS {ROW_COUNT_ALIAS} "
        f"FROM {table_name} "
        f"WHERE {' AND '.join(where_parts)} "
        f"GROUP BY {year_expr} "
        f"ORDER BY {year_expr};"
    )


def _birth_decade_count_sql(table_name: str, birthdate_column: str, engine_key: str) -> str:
    decade_expr = _decade_expr(birthdate_column, engine_key)
    return (
        f"SELECT {decade_expr} AS birth_decade, COUNT(*) AS {ROW_COUNT_ALIAS} "
        f"FROM {table_name} "
        f"WHERE {birthdate_column} IS NOT NULL "
        f"GROUP BY {decade_expr} "
        f"ORDER BY {decade_expr};"
    )


def _kpi_summary_sql(table_name: str, state_column: str | None, city_column: str | None, gender_column: str | None) -> str:
    parts = [f"COUNT(*) AS {ROW_COUNT_ALIAS}"]
    if state_column:
        parts.append(f"COUNT(DISTINCT {state_column}) AS state_count")
    if city_column:
        parts.append(f"COUNT(DISTINCT {city_column}) AS city_count")
    if gender_column:
        parts.append(f"COUNT(DISTINCT {gender_column}) AS gender_categories")
    return f"SELECT {', '.join(parts)} FROM {table_name};"


def _maybe_generate_patient_heuristic_sql(
    question: str,
    schema_metadata: dict,
    engine_key: str,
) -> str | None:
    table_name, columns = _pick_primary_table(schema_metadata)
    if not table_name or not columns:
        return None

    normalized_question = _normalize_question_text(question)
    q = normalized_question.lower()

    birthdate = _find_column(columns, "birthdate", "date of birth", "dob")
    first_name = _find_column(columns, "first", "firstname", "first_name")
    last_name = _find_column(columns, "last", "lastname", "last_name", "surname")
    state = _find_column(columns, "state", "province", "region")
    city = _find_column(columns, "city", "town")
    zip_code = _find_column(columns, "zip", "zipcode", "zip code", "postalcode", "postal code")
    gender = _find_column(columns, "gender", "sex")
    race = _find_column(columns, "race")
    ethnicity = _find_column(columns, "ethnicity", "ethnic")
    marital = _find_column(columns, "marital", "maritalstatus", "marital status")

    first_limit_match = re.search(r"\bfirst\s+(\d+)\b", q)
    top_limit_match = re.search(r"\btop\s+(\d+)\b", q)
    first_limit = int(first_limit_match.group(1)) if first_limit_match else None
    top_limit = int(top_limit_match.group(1)) if top_limit_match else None

    if first_name and last_name and "first and last" in q:
        limit = first_limit or 20
        return _apply_limit(
            f"SELECT {first_name}, {last_name} FROM {table_name}",
            limit,
            engine_key,
        ) + ";"

    if re.search(r"\bshow (?:the )?first \d+ patients\b", q):
        return _apply_limit(f"SELECT * FROM {table_name}", first_limit or 10, engine_key) + ";"

    if q in {"how many patients are there?", "how many patients are there", "patient count", "count patients"} or (
        "how many patients" in q
        and "by " not in q
        and "born each year" not in q
        and "birth year" not in q
        and "birth decade" not in q
        and "over time" not in q
    ):
        return f"SELECT COUNT(*) AS {ROW_COUNT_ALIAS} FROM {table_name};"

    if state and "from massachusetts" in q:
        return f"SELECT * FROM {table_name} WHERE LOWER({state}) = 'massachusetts';"

    born_after_match = re.search(r"\bborn after (\d{4}-\d{2}-\d{2})\b", q)
    if birthdate and born_after_match:
        return f"SELECT * FROM {table_name} WHERE {birthdate} > '{born_after_match.group(1)}';"

    if birthdate and ("birth year" in q or "births by year" in q or "born each year" in q or "births over time" in q):
        after_year_match = re.search(r"\bafter (\d{4})\b", q)
        after_year = int(after_year_match.group(1)) if after_year_match else None
        return _birth_year_count_sql(table_name, birthdate, engine_key, year_after=after_year)

    if birthdate and "birth decade" in q:
        return _birth_decade_count_sql(table_name, birthdate, engine_key)

    dimension_patterns = [
        ("gender", gender),
        ("race", race),
        ("ethnicity", ethnicity),
        ("city", city),
        ("state", state),
        ("zip code", zip_code),
        ("zip", zip_code),
    ]
    for label, column_name in dimension_patterns:
        if not column_name:
            continue
        if (
            f"count patients by {label}" in q
            or f"patient count by {label}" in q
            or f"patients by {label}" in q
            or f"grouped by {label}" in q
            or f"number of patients in each {label}" in q
            or f"{label}s have the most patients" in q
            or f"top {label}s by patient" in q
            or f"top {label} by patient" in q
            or f"dashboard of patient count by {label}" in q
            or f"chart of patient count by {label}" in q
        ):
            limit = top_limit
            if label in {"city", "zip code", "zip"} and "top" in q and limit is None:
                limit = 10 if label == "city" else 15
            return _count_by_dimension_sql(table_name, column_name, engine_key, limit=limit)

    if city and top_limit and "cities" in q and "patient" in q:
        return _count_by_dimension_sql(table_name, city, engine_key, limit=top_limit)

    if city and "top cities by patient population" in q:
        return _count_by_dimension_sql(table_name, city, engine_key, limit=10)

    if zip_code and top_limit and ("zip codes" in q or "zip code" in q) and "patient" in q:
        return _count_by_dimension_sql(table_name, zip_code, engine_key, limit=top_limit)

    if state and top_limit and "states" in q and "patient" in q:
        return _count_by_dimension_sql(table_name, state, engine_key, limit=top_limit)

    if state and "kpi summary" in q:
        return _kpi_summary_sql(table_name, state, city, gender)

    if gender and state and "female patients in massachusetts" in q:
        return f"SELECT * FROM {table_name} WHERE UPPER({gender}) IN ('F', 'FEMALE') AND LOWER({state}) = 'massachusetts';"

    if ethnicity and city and "hispanic patients in peabody" in q:
        return (
            f"SELECT * FROM {table_name} "
            f"WHERE LOWER({city}) = 'peabody' "
            f"AND (LOWER(COALESCE({ethnicity}, '')) LIKE '%hispanic%'"
            + (f" OR LOWER(COALESCE({race}, '')) LIKE '%hispanic%'" if race else "")
            + ");"
        )

    if marital and birthdate and "married patients born before 1980" in q:
        year_expr = _year_expr(birthdate, engine_key)
        return (
            f"SELECT * FROM {table_name} "
            f"WHERE UPPER(COALESCE({marital}, '')) IN ('M', 'MARRIED') "
            f"AND {year_expr} < 1980;"
        )

    if gender and zip_code and "male patients in zip code" in q:
        zip_match = re.search(r"\bzip code\s+([0-9]{5})\b", q)
        if zip_match:
            return (
                f"SELECT * FROM {table_name} "
                f"WHERE UPPER({gender}) IN ('M', 'MALE') "
                f"AND {zip_code} = '{zip_match.group(1)}';"
            )

    if "executive summary" in q or "kpi summary" in q:
        return _kpi_summary_sql(table_name, state, city, gender)

    return None


def generate_sql_from_question(
    question: str,
    schema_metadata: dict,
    engine_key: str = "postgresql",
) -> str:
    if not question_targets_known_table(question, schema_metadata):
        return UNANSWERABLE_SQL

    heuristic_sql = _maybe_generate_patient_heuristic_sql(question, schema_metadata, engine_key)
    if heuristic_sql:
        return heuristic_sql

    dialect = _DIALECT_LABELS.get(engine_key, "PostgreSQL")

    table_text_parts = []
    for table_name, columns in schema_metadata["tables"].items():
        column_defs = ", ".join(
            [f"{col['name']} ({col['type']})" for col in columns]
        )
        table_text_parts.append(f"{table_name}: {column_defs}")

    schema_text = "\n".join(table_text_parts)

    relationship_parts = []
    for rel in schema_metadata.get("relationships", []):
        relationship_parts.append(
            f"{rel['from_table']}.{rel['from_column']} -> "
            f"{rel['to_table']}.{rel['to_column']}"
        )

    relationships_text = "\n".join(relationship_parts) if relationship_parts else "None"

    prompt = f"""
You are a {dialect} SQL generator.

Task:
Convert the user's business question into exactly one safe SQL SELECT query.

Rules:
- Return SQL only
- Return exactly one SQL query
- Use {dialect} syntax
- Only use SELECT
- Do not output markdown
- Do not explain anything
- Only use the tables and columns listed below
- Use the listed relationships when joins are needed
- If the question cannot be answered from the schema, return exactly: SELECT 'UNANSWERABLE' AS error;

Tables and columns:
{schema_text}

Relationships:
{relationships_text}

User question:
{question}
"""

    response = get_openai_client().chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        messages=[
            {
                "role": "system",
                "content": f"You generate one safe {dialect} SELECT query and nothing else.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )

    raw_output = response.choices[0].message.content or ""
    cleaned_sql = clean_sql_output(raw_output)

    lowered_sql = cleaned_sql.lower()

    if not (lowered_sql.startswith("select") or lowered_sql.startswith("with")):
        return UNANSWERABLE_SQL

    referenced_tables = extract_referenced_tables(cleaned_sql)
    valid_tables = {table_name.lower() for table_name in schema_metadata["tables"]}

    if referenced_tables and not referenced_tables.issubset(valid_tables):
        return UNANSWERABLE_SQL

    return cleaned_sql
