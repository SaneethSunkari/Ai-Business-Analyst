import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

UNANSWERABLE_SQL = "SELECT 'UNANSWERABLE' AS error;"

_client: OpenAI | None = None
_client_config: tuple[str, str | None] | None = None
_last_generation_metadata: dict[str, Any] = {
    "sql": UNANSWERABLE_SQL,
    "confidence": "low",
    "intent_type": "RAW_LIST",
    "tables_used": [],
    "explanation": "No SQL has been generated yet.",
}

QUESTION_STOPWORDS = {
    "a",
    "all",
    "an",
    "and",
    "are",
    "by",
    "for",
    "from",
    "get",
    "give",
    "in",
    "into",
    "is",
    "list",
    "me",
    "of",
    "on",
    "show",
    "than",
    "that",
    "the",
    "their",
    "these",
    "those",
    "to",
    "what",
    "which",
    "with",
}

INTENT_TYPES = {
    "COUNT_BY",
    "TREND_OVER_TIME",
    "TOP_N",
    "BREAKDOWN",
    "KPI_SUMMARY",
    "FILTER_QUERY",
    "RAW_LIST",
}

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

_INTENT_GUIDANCE = {
    "COUNT_BY": (
        "Identify the grouping dimension from the question and return one row per group "
        "with a COUNT(*) style aggregation."
    ),
    "TREND_OVER_TIME": (
        "Identify the time field and bucket it by the requested grain such as day, week, "
        "month, quarter, or year using dialect-appropriate date functions."
    ),
    "TOP_N": (
        "Return the requested number of ranked rows ordered by the business metric in "
        "descending order."
    ),
    "BREAKDOWN": (
        "Return a grouped distribution or breakdown using counts or aggregations that are "
        "grounded in explicit schema columns."
    ),
    "KPI_SUMMARY": (
        "Return a compact aggregate query using total, average, minimum, maximum, or sum "
        "only when the metric is supported by the schema."
    ),
    "FILTER_QUERY": (
        "Return matching rows using explicit WHERE filters from the question. Do not invent "
        "filter columns or values."
    ),
    "RAW_LIST": (
        "Return a practical row-level listing using explicit columns when available and a "
        "reasonable LIMIT when the question implies exploration."
    ),
}

_TIME_GRAIN_HINTS = {
    "postgresql": "Use DATE_TRUNC('month', column) or the matching PostgreSQL date bucket.",
    "redshift": "Use DATE_TRUNC('month', column) or the matching Redshift date bucket.",
    "snowflake": "Use DATE_TRUNC('month', column) or the matching Snowflake date bucket.",
    "databricks_sql": "Use date_trunc('month', column) or the matching Databricks SQL bucket.",
    "athena": "Use date_trunc('month', column) or the matching Athena SQL bucket.",
    "trino": "Use date_trunc('month', column) or the matching Trino bucket.",
    "dremio": "Use DATE_TRUNC('MONTH', column) or the matching Dremio bucket.",
    "bigquery": "Use DATE_TRUNC or TIMESTAMP_TRUNC with the matching BigQuery date part.",
    "mysql": "Use YEAR(), MONTH(), DATE_FORMAT(), or another valid MySQL bucketing expression.",
    "sqlite": "Use strftime() with the matching time grain.",
    "sqlserver": "Use DATETRUNC when available or compose a valid SQL Server date bucket.",
    "synapse": "Use DATETRUNC when available or compose a valid Synapse date bucket.",
    "fabric": "Use DATETRUNC when available or compose a valid Fabric Warehouse date bucket.",
    "oracle": "Use TRUNC(column, 'MM') or the matching Oracle date bucket.",
}


def _gateway_base_url() -> str | None:
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
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _client = OpenAI(**kwargs)
        _client_config = config

    return _client


def get_last_generation_metadata() -> dict[str, Any]:
    return dict(_last_generation_metadata)


def normalize_term(term: str) -> str:
    if term.endswith("ies") and len(term) > 3:
        return term[:-3] + "y"
    if term.endswith("s") and not term.endswith("ss") and len(term) > 3:
        return term[:-1]
    return term


def _normalize_identifier(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _tokenize_text(text: str) -> set[str]:
    return {
        normalize_term(token)
        for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower())
        if token not in QUESTION_STOPWORDS and len(token) > 2
    }


def _column_names(schema_metadata: dict) -> list[str]:
    names: list[str] = []
    for columns in schema_metadata.get("tables", {}).values():
        for column in columns:
            names.append(str(column.get("name", "")))
    return names


def build_schema_terms(schema_metadata: dict) -> dict[str, set[str]]:
    table_terms: set[str] = set()
    column_terms: set[str] = set()

    for table_name, columns in schema_metadata.get("tables", {}).items():
        lowered_table = table_name.lower()
        table_terms.add(lowered_table)
        table_terms.add(normalize_term(lowered_table))
        table_terms.add(_normalize_identifier(table_name))

        for part in re.split(r"[_\s]+", lowered_table):
            if part:
                table_terms.add(part)
                table_terms.add(normalize_term(part))

        for column in columns:
            column_name = str(column.get("name", ""))
            lowered_column = column_name.lower()
            column_terms.add(lowered_column)
            column_terms.add(normalize_term(lowered_column))
            column_terms.add(_normalize_identifier(column_name))
            for part in re.split(r"[_\s]+", lowered_column):
                if part:
                    column_terms.add(part)
                    column_terms.add(normalize_term(part))

    return {"tables": table_terms, "columns": column_terms}


def question_targets_known_table(question: str, schema_metadata: dict) -> bool:
    tables = schema_metadata.get("tables", {})
    if len(tables) == 1 and question.strip():
        return True

    question_terms = _tokenize_text(question)
    if not question_terms:
        return False

    schema_terms = build_schema_terms(schema_metadata)
    return any(
        term in schema_terms["tables"] or term in schema_terms["columns"]
        for term in question_terms
    )


def extract_referenced_tables(sql: str) -> set[str]:
    return {
        match.lower()
        for match in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.IGNORECASE)
    }


def clean_sql_output(raw_text: str) -> str:
    text = raw_text.strip()
    text = re.sub(r"^```sql\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    match = re.search(r"\b(?:with|select)\b.*", text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        text = match.group(0).strip()

    if ";" in text:
        text = text.split(";", maxsplit=1)[0].strip() + ";"

    return text


def _extract_group_phrase(question: str) -> str | None:
    patterns = [
        r"\b(?:count|counts|number)\s+\w+\s+by\s+([a-zA-Z0-9_ ]+?)(?:\s+(?:last|this|where|with|from|for)\b|$)",
        r"\b(?:breakdown|distribution)\s+of\s+([a-zA-Z0-9_ ]+?)(?:\s+(?:by|for|where|with)\b|$)",
        r"\bgroup(?:ed)?\s+by\s+([a-zA-Z0-9_ ]+?)(?:\s+(?:last|this|where|with|from|for)\b|$)",
        r"\bby\s+([a-zA-Z0-9_ ]+?)(?:\s+(?:last|this|where|with|from|for)\b|$)",
        r"\bper\s+([a-zA-Z0-9_ ]+?)(?:\s+(?:last|this|where|with|from|for)\b|$)",
    ]
    lowered = question.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return match.group(1).strip()
    return None


def _extract_metric_phrase(question: str) -> str | None:
    lowered = question.lower()
    patterns = [
        r"\b(?:average|avg|max|maximum|min|minimum|sum|total)\s+(?:of\s+)?([a-zA-Z0-9_ ]+?)(?:\s+(?:by|for|where|with|last|this)\b|$)",
        r"\btop\s+\d+\s+([a-zA-Z0-9_ ]+?)\s+by\s+([a-zA-Z0-9_ ]+?)(?:\s+(?:where|with|from|for|last|this)\b|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            if len(match.groups()) == 1:
                return match.group(1).strip()
            return match.group(2).strip()
    return None


def _extract_filter_phrase(question: str) -> str | None:
    lowered = question.lower()
    for pattern in (
        r"\bwhere\s+([a-zA-Z0-9_ =<>!'\-/.]+)$",
        r"\bfrom\s+([a-zA-Z0-9_ '\-/.]+)$",
        r"\bwith\s+([a-zA-Z0-9_ '\-/.]+)$",
    ):
        match = re.search(pattern, lowered)
        if match:
            return match.group(1).strip()
    return None


def _extract_time_grain(question: str) -> str | None:
    lowered = question.lower()
    for grain in ("year", "quarter", "month", "week", "day"):
        if re.search(rf"\b{grain}s?\b", lowered):
            return grain
    if "over time" in lowered or "trend" in lowered:
        return "time"
    return None


def classify_intent(question: str) -> dict[str, Any]:
    lowered = question.strip().lower()
    top_match = re.search(r"\btop\s+(\d+)\b", lowered)
    time_grain = _extract_time_grain(lowered)
    group_phrase = _extract_group_phrase(lowered)
    metric_phrase = _extract_metric_phrase(lowered)
    filter_phrase = _extract_filter_phrase(lowered)

    if time_grain and (
        "over time" in lowered
        or "trend" in lowered
        or "by month" in lowered
        or "by year" in lowered
        or "by quarter" in lowered
        or "by week" in lowered
        or "by day" in lowered
    ):
        intent_type = "TREND_OVER_TIME"
    elif top_match and " by " in lowered:
        intent_type = "TOP_N"
    elif (
        "count " in lowered
        or "counts " in lowered
        or "how many" in lowered
        or "number of" in lowered
    ) and (" by " in lowered or " per " in lowered or "grouped by" in lowered):
        intent_type = "COUNT_BY"
    elif any(term in lowered for term in ("breakdown", "distribution")):
        intent_type = "BREAKDOWN"
    elif any(term in lowered for term in ("average", "avg", "sum", "total", "maximum", "minimum", "max ", "min ")):
        intent_type = "KPI_SUMMARY"
    elif any(term in lowered for term in (" where ", " with ", " from ")):
        intent_type = "FILTER_QUERY"
    else:
        intent_type = "RAW_LIST"

    return {
        "type": intent_type if intent_type in INTENT_TYPES else "RAW_LIST",
        "top_n": int(top_match.group(1)) if top_match else None,
        "group_by_hint": group_phrase,
        "metric_hint": metric_phrase,
        "filter_hint": filter_phrase,
        "time_grain": time_grain,
    }


def _match_schema(question: str, schema_metadata: dict) -> dict[str, Any]:
    question_terms = _tokenize_text(question)
    matched_tables: set[str] = set()
    matched_columns: set[str] = set()
    matched_sample_columns: set[str] = set()

    for table_name, columns in schema_metadata.get("tables", {}).items():
        table_tokens = _tokenize_text(table_name.replace("_", " "))
        if question_terms & table_tokens:
            matched_tables.add(table_name)

        for column in columns:
            column_name = str(column.get("name", ""))
            column_tokens = _tokenize_text(column_name.replace("_", " "))
            if question_terms & column_tokens:
                matched_columns.add(f"{table_name}.{column_name}")

            sample_terms: set[str] = set()
            for sample in _column_samples(column):
                sample_terms |= _tokenize_text(sample.replace("_", " "))
            if sample_terms and question_terms & sample_terms:
                matched_sample_columns.add(f"{table_name}.{column_name}")

    return {
        "question_terms": sorted(question_terms),
        "matched_tables": sorted(matched_tables),
        "matched_columns": sorted(matched_columns),
        "matched_sample_columns": sorted(matched_sample_columns),
    }


def _column_samples(column: dict[str, Any]) -> list[str]:
    for key in ("sample_values", "samples", "example_values", "examples"):
        value = column.get(key)
        if isinstance(value, list):
            return [str(item) for item in value[:3]]
    for key in ("sample", "example"):
        value = column.get(key)
        if value not in (None, ""):
            return [str(value)]
    return []


def _format_schema_text(schema_metadata: dict) -> str:
    table_parts: list[str] = []
    for table_name, columns in schema_metadata.get("tables", {}).items():
        column_parts = []
        for column in columns:
            nullable = "nullable" if column.get("nullable", True) else "not null"
            samples = _column_samples(column)
            sample_text = f" | sample values: {', '.join(samples)}" if samples else ""
            column_parts.append(
                f"{column['name']} ({column.get('type', 'unknown')}, {nullable}{sample_text})"
            )
        table_parts.append(f"{table_name}: " + ", ".join(column_parts))
    return "\n".join(table_parts)


def _format_relationship_text(schema_metadata: dict) -> str:
    relationships = schema_metadata.get("relationships", [])
    if not relationships:
        return "None"
    return "\n".join(
        f"{rel['from_table']}.{rel['from_column']} -> {rel['to_table']}.{rel['to_column']}"
        for rel in relationships
    )


def _build_clarifying_question(intent: dict[str, Any], schema_match: dict[str, Any], schema_metadata: dict) -> str:
    matched_columns = schema_match.get("matched_columns", [])
    if intent["type"] in {"COUNT_BY", "BREAKDOWN", "TREND_OVER_TIME"} and not matched_columns:
        available = ", ".join(_column_names(schema_metadata)[:8]) or "the available schema"
        return (
            f"I could not confidently match the grouping or time field. "
            f"Please name the exact column or metric to analyze, for example one of: {available}."
        )
    if not schema_match.get("matched_tables") and not matched_columns:
        table_names = ", ".join(schema_metadata.get("tables", {}).keys())
        return f"I could not match the request to the current schema. Please mention one of these tables or fields: {table_names}."
    return "I need a more specific question or an exact column name before generating SQL."


def _assess_confidence(intent: dict[str, Any], schema_match: dict[str, Any], schema_metadata: dict) -> tuple[str, str]:
    matched_tables = schema_match.get("matched_tables", [])
    matched_columns = schema_match.get("matched_columns", [])
    matched_sample_columns = schema_match.get("matched_sample_columns", [])
    multi_table_schema = len(schema_metadata.get("tables", {})) > 1

    if not matched_tables and not matched_columns and not matched_sample_columns:
        return "low", _build_clarifying_question(intent, schema_match, schema_metadata)

    if intent["type"] in {"COUNT_BY", "TREND_OVER_TIME", "TOP_N", "BREAKDOWN", "KPI_SUMMARY"}:
        if len(matched_columns) >= 2:
            return "high", f"Intent {intent['type']} is clear and the referenced fields are present in the schema."
        if matched_columns or (matched_tables and not multi_table_schema):
            return "medium", f"Intent {intent['type']} is clear, but at least one field will be inferred from the schema context."
        return "low", _build_clarifying_question(intent, schema_match, schema_metadata)

    if intent["type"] == "FILTER_QUERY":
        if matched_columns:
            return "high", "The filter request maps cleanly to the available schema fields."
        if matched_sample_columns and matched_tables:
            return "medium", "The filter request maps to a likely field using available schema samples."
        if matched_tables and not multi_table_schema:
            return "medium", "The filter request is understandable, but the exact filter field will be inferred."
        return "low", _build_clarifying_question(intent, schema_match, schema_metadata)

    if matched_columns or matched_tables:
        level = "high" if matched_columns else "medium"
        explanation = (
            "The listing request maps directly to the schema."
            if level == "high"
            else "The listing request is understandable, but explicit fields are being inferred."
        )
        return level, explanation

    return "low", _build_clarifying_question(intent, schema_match, schema_metadata)


def _build_prompt(
    question: str,
    schema_metadata: dict,
    engine_key: str,
    intent: dict[str, Any],
    confidence: str,
    confidence_explanation: str,
) -> str:
    dialect = _DIALECT_LABELS.get(engine_key, "PostgreSQL")
    schema_text = _format_schema_text(schema_metadata)
    relationships_text = _format_relationship_text(schema_metadata)
    time_hint = _TIME_GRAIN_HINTS.get(
        engine_key,
        "Use the database dialect's native date bucketing functions for time-series questions.",
    )

    return f"""
You are a {dialect} SQL generator.

You have no domain knowledge. Use ONLY the schema provided.
Do not assume column meanings.
Generate correct SQL for the detected dialect.

Task:
Convert the user's business question into exactly one safe SQL SELECT query.

Detected dialect:
- {dialect}

Classified intent:
- {intent['type']}
- Intent guidance: {_INTENT_GUIDANCE[intent['type']]}
- Time bucketing guidance: {time_hint}
- Intent hints: group_by={intent.get('group_by_hint') or 'none'}, metric={intent.get('metric_hint') or 'none'}, filter={intent.get('filter_hint') or 'none'}, top_n={intent.get('top_n') or 'none'}, time_grain={intent.get('time_grain') or 'none'}

Confidence assessment:
- {confidence.upper()}
- {confidence_explanation}

Rules:
- Return SQL only
- Return exactly one SQL query
- Use {dialect} syntax
- Only use SELECT
- Do not output markdown
- Do not explain anything
- Only use the tables, columns, and relationships listed below
- Never invent fields, joins, or business definitions
- If the request cannot be answered confidently from the schema, return exactly: SELECT 'UNANSWERABLE' AS error;

Tables and columns:
{schema_text}

Relationships:
{relationships_text}

User question:
{question}
"""


def build_sql_generation_context(
    question: str,
    schema_metadata: dict,
    engine_key: str = "postgresql",
) -> dict[str, Any]:
    intent = classify_intent(question)
    schema_match = _match_schema(question, schema_metadata)
    confidence, confidence_explanation = _assess_confidence(intent, schema_match, schema_metadata)
    prompt = _build_prompt(
        question=question,
        schema_metadata=schema_metadata,
        engine_key=engine_key,
        intent=intent,
        confidence=confidence,
        confidence_explanation=confidence_explanation,
    )
    return {
        "dialect": _DIALECT_LABELS.get(engine_key, "PostgreSQL"),
        "intent_type": intent["type"],
        "intent": intent,
        "schema_match": schema_match,
        "confidence": confidence,
        "explanation": confidence_explanation,
        "prompt": prompt,
    }


def generate_sql_details(
    question: str,
    schema_metadata: dict,
    engine_key: str = "postgresql",
) -> dict[str, Any]:
    global _last_generation_metadata

    context = build_sql_generation_context(question, schema_metadata, engine_key=engine_key)
    intent_type = context["intent_type"]
    confidence = context["confidence"]
    explanation = context["explanation"]
    candidate_tables = context["schema_match"].get("matched_tables", [])

    if confidence == "low":
        _last_generation_metadata = {
            "sql": UNANSWERABLE_SQL,
            "confidence": confidence,
            "intent_type": intent_type,
            "tables_used": candidate_tables,
            "explanation": explanation,
        }
        return dict(_last_generation_metadata)

    if not question_targets_known_table(question, schema_metadata):
        explanation = "The question does not map clearly to the available schema."
        _last_generation_metadata = {
            "sql": UNANSWERABLE_SQL,
            "confidence": "low",
            "intent_type": intent_type,
            "tables_used": [],
            "explanation": explanation,
        }
        return dict(_last_generation_metadata)

    response = get_openai_client().chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        messages=[
            {
                "role": "system",
                "content": (
                    f"You generate one safe {_DIALECT_LABELS.get(engine_key, 'PostgreSQL')} "
                    "SELECT query and nothing else."
                ),
            },
            {"role": "user", "content": context["prompt"]},
        ],
        temperature=0,
    )

    raw_output = response.choices[0].message.content or ""
    cleaned_sql = clean_sql_output(raw_output)
    lowered_sql = cleaned_sql.lower()

    if not (lowered_sql.startswith("select") or lowered_sql.startswith("with")):
        cleaned_sql = UNANSWERABLE_SQL

    referenced_tables = sorted(extract_referenced_tables(cleaned_sql))
    valid_tables = {table_name.lower() for table_name in schema_metadata.get("tables", {})}
    if referenced_tables and not set(referenced_tables).issubset(valid_tables):
        cleaned_sql = UNANSWERABLE_SQL
        referenced_tables = []

    if cleaned_sql == UNANSWERABLE_SQL:
        if confidence == "high":
            confidence = "medium"
        explanation = (
            "The request was understood, but the generated SQL could not be validated "
            "against the provided schema."
        )

    if cleaned_sql != UNANSWERABLE_SQL and not referenced_tables:
        referenced_tables = candidate_tables

    if cleaned_sql != UNANSWERABLE_SQL:
        explanation = (
            f"Interpreted the request as {intent_type} and generated a "
            f"{_DIALECT_LABELS.get(engine_key, 'PostgreSQL')} query from the live schema."
        )

    _last_generation_metadata = {
        "sql": cleaned_sql,
        "confidence": confidence,
        "intent_type": intent_type,
        "tables_used": referenced_tables,
        "explanation": explanation,
    }
    return dict(_last_generation_metadata)


def generate_sql_from_question(
    question: str,
    schema_metadata: dict,
    engine_key: str = "postgresql",
) -> str:
    return generate_sql_details(
        question=question,
        schema_metadata=schema_metadata,
        engine_key=engine_key,
    )["sql"]
