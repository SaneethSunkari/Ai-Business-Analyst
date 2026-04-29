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


def generate_sql_from_question(
    question: str,
    schema_metadata: dict,
    engine_key: str = "postgresql",
) -> str:
    if not question_targets_known_table(question, schema_metadata):
        return UNANSWERABLE_SQL

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
