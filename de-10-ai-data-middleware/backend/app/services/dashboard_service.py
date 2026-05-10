from __future__ import annotations

import json
import os
import re
from typing import Any

from app.services.llm_service import get_openai_client

ALLOWED_DASHBOARD_VIEWS = {"bar", "line", "kpi", "table"}


def is_numeric_value(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    if not isinstance(value, str):
        return False
    text = value.strip().replace(",", "")
    if not text:
        return False
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", text))


def is_date_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text)
        or re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", text)
    )


def _column_profiles(columns: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for index, name in enumerate(columns):
        values = [
            row[index]
            for row in rows
            if index < len(row) and row[index] not in (None, "")
        ]
        if not values:
            kind = "category"
        else:
            numeric_count = sum(1 for value in values if is_numeric_value(value))
            date_count = sum(1 for value in values if is_date_value(value))
            if numeric_count / len(values) >= 0.85:
                kind = "numeric"
            elif date_count / len(values) >= 0.85:
                kind = "date"
            else:
                kind = "category"
        profiles.append({"name": name, "index": index, "kind": kind})
    return profiles


def _compact_number(value: float | int | str | None) -> str:
    if value is None:
        return "0"
    try:
        number = float(str(value).replace(",", ""))
    except ValueError:
        return str(value)
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}K"
    if float(number).is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    raw_text = raw_text.strip()
    if not raw_text:
        return None
    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _fallback_dashboard_spec(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    source_question: str | None = None,
    current_dashboard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profiles = _column_profiles(columns, rows)
    numeric_columns = [profile for profile in profiles if profile["kind"] == "numeric"]
    date_columns = [profile for profile in profiles if profile["kind"] == "date"]
    category_columns = [profile for profile in profiles if profile["kind"] == "category"]

    lower_question = question.lower()
    current_dashboard = current_dashboard or {}
    top_n = max(3, min(20, int(current_dashboard.get("top_n") or 8)))

    dimension = (
        current_dashboard.get("dimension")
        or (date_columns[0]["name"] if date_columns else None)
        or (category_columns[0]["name"] if category_columns else None)
    )
    measure = (
        current_dashboard.get("measure")
        or (numeric_columns[0]["name"] if numeric_columns else None)
    )

    if any(term in lower_question for term in ("kpi", "single number", "summary card")):
        view = "kpi"
    elif any(term in lower_question for term in ("table", "rows", "raw data")):
        view = "table"
    elif any(term in lower_question for term in ("line", "trend", "over time")) and dimension and measure:
        view = "line"
    elif any(term in lower_question for term in ("bar", "compare", "top", "ranking")) and dimension and measure:
        view = "bar"
    elif date_columns and numeric_columns:
        view = "line"
    elif category_columns and numeric_columns:
        view = "bar"
    elif numeric_columns:
        view = "kpi"
    else:
        view = "table"

    title = source_question or question
    subtitle = "Auto-generated from the current query result"
    explanation = (
        f"Built a {view} dashboard from {len(rows)} returned rows using "
        f"{measure or 'the available measure'} and {dimension or 'the available dimension'}."
    )
    confidence = "high" if rows and measure else ("medium" if rows else "low")
    insights = []
    if rows and measure:
        insights.append(f"{measure} is available for visual analysis.")
    if rows and dimension:
        insights.append(f"{dimension} can be used to group or trend the result.")
    insights.append("Refine the dashboard in plain English to change the view, grouping, or top-N limit.")

    follow_ups = [
        "Make this a bar chart",
        "Show only the top 5",
        "Explain the most important pattern",
    ]

    return {
        "view": view,
        "title": title[:120],
        "subtitle": subtitle,
        "dimension": dimension,
        "measure": measure,
        "top_n": top_n,
        "explanation": explanation,
        "confidence": confidence,
        "insights": insights[:3],
        "follow_ups": follow_ups,
    }


def _sanitize_dashboard_spec(
    spec: dict[str, Any] | None,
    fallback: dict[str, Any],
    columns: list[str],
) -> dict[str, Any]:
    if not spec:
        return fallback

    valid_columns = set(columns)
    view = str(spec.get("view") or fallback["view"]).lower()
    if view not in ALLOWED_DASHBOARD_VIEWS:
        view = fallback["view"]

    dimension = spec.get("dimension") or fallback.get("dimension")
    if dimension not in valid_columns:
        dimension = fallback.get("dimension")

    measure = spec.get("measure") or fallback.get("measure")
    if measure not in valid_columns:
        measure = fallback.get("measure")

    try:
        top_n = int(spec.get("top_n") or fallback.get("top_n") or 8)
    except (TypeError, ValueError):
        top_n = int(fallback.get("top_n") or 8)
    top_n = max(3, min(20, top_n))

    confidence = str(spec.get("confidence") or fallback.get("confidence") or "medium").lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = fallback.get("confidence", "medium")

    insights = spec.get("insights") if isinstance(spec.get("insights"), list) else fallback.get("insights", [])
    follow_ups = spec.get("follow_ups") if isinstance(spec.get("follow_ups"), list) else fallback.get("follow_ups", [])

    return {
        "view": view,
        "title": str(spec.get("title") or fallback["title"])[:120],
        "subtitle": str(spec.get("subtitle") or fallback["subtitle"])[:180],
        "dimension": dimension,
        "measure": measure,
        "top_n": top_n,
        "explanation": str(spec.get("explanation") or fallback["explanation"])[:280],
        "confidence": confidence,
        "insights": [str(item)[:180] for item in insights[:3]],
        "follow_ups": [str(item)[:120] for item in follow_ups[:4]],
    }


def generate_dashboard_spec(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    source_question: str | None = None,
    current_dashboard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = _fallback_dashboard_spec(
        question=question,
        columns=columns,
        rows=rows,
        source_question=source_question,
        current_dashboard=current_dashboard,
    )

    if not columns or not rows:
        return fallback

    try:
        client = get_openai_client()
    except Exception:
        return fallback

    sample_rows = rows[:20]
    profiles = _column_profiles(columns, sample_rows)
    prompt = {
        "task": "Create a stakeholder-friendly dashboard plan from a query result.",
        "rules": [
            "Return JSON only.",
            "Choose one view: bar, line, kpi, or table.",
            "Only reference dimension and measure names that exist in the columns list.",
            "Prefer line for time-like dimensions, bar for ranked categories, kpi for single-value summaries, and table when charting is weak.",
            "Keep insights short and business-friendly.",
            "Keep follow-ups actionable and plain English.",
        ],
        "source_question": source_question or "",
        "dashboard_request": question,
        "columns": profiles,
        "row_count": len(rows),
        "sample_rows": sample_rows,
        "current_dashboard": current_dashboard or {},
        "output_schema": {
            "view": "bar|line|kpi|table",
            "title": "string",
            "subtitle": "string",
            "dimension": "column name or null",
            "measure": "column name or null",
            "top_n": 8,
            "explanation": "one short sentence",
            "confidence": "low|medium|high",
            "insights": ["short sentence", "short sentence", "short sentence"],
            "follow_ups": ["short prompt", "short prompt", "short prompt"],
        },
    }

    try:
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            messages=[
                {
                    "role": "system",
                    "content": "You turn query results into stakeholder-friendly dashboard plans. Return strict JSON only.",
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt),
                },
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw_output = response.choices[0].message.content or ""
        parsed = _extract_json_object(raw_output)
        return _sanitize_dashboard_spec(parsed, fallback, columns)
    except Exception:
        return fallback
