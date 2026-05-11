from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from app.services.llm_service import classify_intent

CHART_TYPE_BY_INTENT = {
    "COUNT_BY": "bar",
    "TREND_OVER_TIME": "line",
    "TOP_N": "horizontal_bar",
    "BREAKDOWN": "donut",
    "KPI_SUMMARY": "kpi",
    "FILTER_QUERY": "table",
    "RAW_LIST": "table",
}

ID_NAME_PATTERN = re.compile(r"(^id$|_id$|uuid|guid|identifier|key$)", re.IGNORECASE)
DATE_NAME_PATTERN = re.compile(r"(date|time|timestamp|month|year|quarter|week|day)", re.IGNORECASE)
MEASURE_NAME_PATTERN = re.compile(
    r"(amount|revenue|cost|count|price|quantity|salary|total|subtotal|balance|value|score|rate|margin|usage|duration|avg|average|sum|max|min)",
    re.IGNORECASE,
)
DIMENSION_NAME_PATTERN = re.compile(
    r"(name|city|state|country|region|status|category|type|segment|group|department|team|role|channel|market|gender|race|ethnicity|zip)",
    re.IGNORECASE,
)
TEXT_NAME_PATTERN = re.compile(r"(description|detail|details|notes|body|comment|message|content|summary|reason)", re.IGNORECASE)
TIME_GRAIN_PATTERN = re.compile(r"\b(year|quarter|month|week|day)s?\b", re.IGNORECASE)


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(text).lower()) if token}


def _is_numeric_value(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return not isinstance(value, bool)
    if not isinstance(value, str):
        return False
    text = value.strip().replace(",", "")
    return bool(text) and bool(re.fullmatch(r"-?\d+(\.\d+)?", text))


def _to_number(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", ""))
        except ValueError:
            return math.nan
    return math.nan


def _is_date_value(value: Any) -> bool:
    return _parse_date(value) is not None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    if re.fullmatch(r"\d{4}", text):
        try:
            return date(int(text), 1, 1)
        except ValueError:
            return None
    return None


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
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _column_samples(column: dict[str, Any]) -> list[str]:
    if isinstance(column.get("sample_values"), list):
        return [str(item) for item in column["sample_values"][:3]]
    return []


def _schema_column_lookup(schema_metadata: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not schema_metadata:
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    tables = schema_metadata.get("tables", {})
    if isinstance(tables, dict):
        for table_name, columns in tables.items():
            for column in columns or []:
                if column.get("name"):
                    lookup[str(column["name"]).lower()] = dict(column) | {"table_name": table_name}
    table_details = schema_metadata.get("table_details", [])
    if isinstance(table_details, list):
        for table in table_details:
            for column in table.get("columns", []) or []:
                if column.get("name"):
                    lookup[str(column["name"]).lower()] = dict(column) | {"table_name": table.get("name")}
    return lookup


def _infer_semantic_type(name: str, values: list[Any], schema_column: dict[str, Any] | None = None) -> str:
    if schema_column and schema_column.get("semantic_type"):
        return str(schema_column["semantic_type"]).upper()

    lowered = name.lower()
    if ID_NAME_PATTERN.search(lowered):
        return "ID"
    if DATE_NAME_PATTERN.search(lowered) or (values and sum(1 for value in values if _is_date_value(value)) / len(values) >= 0.75):
        return "DATE"
    if TEXT_NAME_PATTERN.search(lowered):
        return "TEXT"

    numeric_ratio = (sum(1 for value in values if _is_numeric_value(value)) / len(values)) if values else 0
    if numeric_ratio >= 0.75:
        if MEASURE_NAME_PATTERN.search(lowered):
            return "MEASURE"
        return "MEASURE"

    if DIMENSION_NAME_PATTERN.search(lowered):
        return "DIMENSION"
    return "DIMENSION"


def _build_profiles(
    columns: list[str],
    rows: list[list[Any]],
    schema_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    schema_lookup = _schema_column_lookup(schema_metadata)
    profiles: list[dict[str, Any]] = []
    for index, name in enumerate(columns):
        values = [row[index] for row in rows if index < len(row) and row[index] not in (None, "")]
        schema_column = schema_lookup.get(name.lower())
        semantic_type = _infer_semantic_type(name, values, schema_column)
        numeric_ratio = (sum(1 for value in values if _is_numeric_value(value)) / len(values)) if values else 0
        date_ratio = (sum(1 for value in values if _is_date_value(value)) / len(values)) if values else 0
        profiles.append(
            {
                "name": name,
                "index": index,
                "semantic_type": semantic_type,
                "is_numeric": numeric_ratio >= 0.75,
                "is_date": date_ratio >= 0.75 or semantic_type == "DATE",
                "distinct_count": len({str(value) for value in values[:100]}),
                "sample_values": _column_samples(schema_column or {}) if schema_column else [str(value) for value in values[:3]],
            }
        )
    return profiles


def _pick_profile_by_hint(
    profiles: list[dict[str, Any]],
    hint: str | None,
    *,
    prefer: str | None = None,
) -> dict[str, Any] | None:
    if not hint:
        return None
    hint_tokens = _tokenize(hint)
    if not hint_tokens:
        return None
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for profile in profiles:
        profile_tokens = _tokenize(profile["name"])
        overlap = len(hint_tokens & profile_tokens)
        if overlap <= 0:
            continue
        prefer_bonus = 1 if prefer and profile["semantic_type"] == prefer else 0
        scored.append((overlap, prefer_bonus, profile))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


def _pick_dimension_profile(intent: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
    hinted = _pick_profile_by_hint(profiles, intent.get("group_by_hint"), prefer="DIMENSION")
    if hinted:
        return hinted
    if intent["type"] == "TREND_OVER_TIME":
        for profile in profiles:
            if profile["semantic_type"] == "DATE" or profile["is_date"]:
                return profile
    dimension_candidates = [profile for profile in profiles if profile["semantic_type"] == "DIMENSION"]
    if dimension_candidates:
        dimension_candidates.sort(key=lambda item: (item["distinct_count"], item["name"]))
        return dimension_candidates[0]
    for profile in profiles:
        if profile["semantic_type"] == "DATE" or profile["is_date"]:
            return profile
    return None


def _pick_measure_profile(intent: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
    hinted = _pick_profile_by_hint(profiles, intent.get("metric_hint"), prefer="MEASURE")
    if hinted:
        return hinted
    measure_candidates = [profile for profile in profiles if profile["semantic_type"] == "MEASURE" and not ID_NAME_PATTERN.search(profile["name"])]
    if measure_candidates:
        measure_candidates.sort(key=lambda item: (0 if MEASURE_NAME_PATTERN.search(item["name"]) else 1, item["name"]))
        return measure_candidates[0]
    return None


def _normalize_dashboard_intent(
    question: str,
    intent: dict[str, Any],
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = dict(intent)
    lowered = question.lower()
    date_profile = next((profile for profile in profiles if profile["semantic_type"] == "DATE" or profile["is_date"]), None)
    if normalized["type"] == "COUNT_BY" and (
        "over time" in lowered
        or "trend" in lowered
        or TIME_GRAIN_PATTERN.search(lowered)
        or (date_profile and normalized.get("group_by_hint") and date_profile["name"].lower() in normalized["group_by_hint"].lower())
    ):
        normalized["type"] = "TREND_OVER_TIME"
        normalized["time_grain"] = normalized.get("time_grain") or "month"
    if normalized["type"] == "RAW_LIST" and any(term in lowered for term in ("breakdown", "distribution", "share")):
        normalized["type"] = "BREAKDOWN"
    return normalized


def _bucket_time(value: Any, grain: str | None) -> str:
    parsed = _parse_date(value)
    if not parsed:
        return str(value)
    if grain == "year":
        return str(parsed.year)
    if grain == "quarter":
        quarter = ((parsed.month - 1) // 3) + 1
        return f"{parsed.year}-Q{quarter}"
    if grain == "month":
        return parsed.strftime("%Y-%m")
    if grain == "week":
        iso_year, iso_week, _ = parsed.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return parsed.isoformat()


def _aggregate_series(
    rows: list[list[Any]],
    x_profile: dict[str, Any] | None,
    y_profile: dict[str, Any] | None,
    *,
    intent_type: str,
    top_n: int | None,
    time_grain: str | None,
) -> list[dict[str, Any]]:
    if not x_profile:
        return []

    grouped: dict[str, float] = defaultdict(float)
    for row in rows:
        if x_profile["index"] >= len(row):
            continue
        raw_x = row[x_profile["index"]]
        if raw_x in (None, ""):
            continue
        label = (
            _bucket_time(raw_x, time_grain)
            if (x_profile["semantic_type"] == "DATE" or x_profile["is_date"])
            else str(raw_x)
        )
        if y_profile and y_profile["index"] < len(row) and _is_numeric_value(row[y_profile["index"]]):
            grouped[label] += _to_number(row[y_profile["index"]])
        else:
            grouped[label] += 1

    points = [{"x": key, "y": value} for key, value in grouped.items()]
    if intent_type == "TREND_OVER_TIME":
        points.sort(key=lambda item: item["x"])
    else:
        points.sort(key=lambda item: item["y"], reverse=True)
    if top_n and intent_type in {"TOP_N", "COUNT_BY", "BREAKDOWN"}:
        points = points[:top_n]
    return points


def _table_rows(columns: list[str], rows: list[list[Any]], limit: int = 12) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows[:limit]:
        output.append({columns[index]: (row[index] if index < len(row) else None) for index in range(len(columns))})
    return output


def _build_kpi_panels(
    rows: list[list[Any]],
    measure_profile: dict[str, Any] | None,
    dimension_profile: dict[str, Any] | None,
    series: list[dict[str, Any]],
    *,
    intent_type: str,
) -> list[dict[str, Any]]:
    panels: list[dict[str, Any]] = []
    panels.append(
        {
            "id": "panel_kpi_rows",
            "title": "Rows returned",
            "chart_type": "kpi",
            "x_axis": None,
            "y_axis": None,
            "data": [{"label": "Rows returned", "value": len(rows), "formatted_value": _compact_number(len(rows))}],
            "insight": f"The current result contains {_compact_number(len(rows))} rows.",
        }
    )

    if measure_profile and series:
        total = sum(float(point["y"]) for point in series)
        panels.append(
            {
                "id": "panel_kpi_total",
                "title": f"Total {measure_profile['name']}",
                "chart_type": "kpi",
                "x_axis": None,
                "y_axis": measure_profile["name"],
                "data": [{"label": f"Total {measure_profile['name']}", "value": total, "formatted_value": _compact_number(total)}],
                "insight": f"The visible total for {measure_profile['name']} is {_compact_number(total)}.",
            }
        )
    elif dimension_profile and series:
        panels.append(
            {
                "id": "panel_kpi_groups",
                "title": f"Distinct {dimension_profile['name']}",
                "chart_type": "kpi",
                "x_axis": None,
                "y_axis": None,
                "data": [{"label": f"Distinct {dimension_profile['name']}", "value": len(series), "formatted_value": _compact_number(len(series))}],
                "insight": f"There are {_compact_number(len(series))} visible {dimension_profile['name']} groups in this view.",
            }
        )

    if intent_type == "TREND_OVER_TIME" and series:
        first = series[0]["y"]
        last = series[-1]["y"]
        delta = last - first
        panels.append(
            {
                "id": "panel_kpi_delta",
                "title": "Change over period",
                "chart_type": "kpi",
                "x_axis": None,
                "y_axis": None,
                "data": [{"label": "Change", "value": delta, "formatted_value": _compact_number(delta)}],
                "insight": f"The visible change from the first to the last point is {_compact_number(delta)}.",
            }
        )
    elif series:
        leader = series[0]
        panels.append(
            {
                "id": "panel_kpi_leader",
                "title": "Leading segment",
                "chart_type": "kpi",
                "x_axis": None,
                "y_axis": None,
                "data": [{"label": str(leader['x']), "value": leader["y"], "formatted_value": _compact_number(leader["y"])}],
                "insight": f"{leader['x']} currently leads at {_compact_number(leader['y'])}.",
            }
        )

    return panels[:3]


def _build_primary_panel(
    *,
    question: str,
    intent_type: str,
    dimension_profile: dict[str, Any] | None,
    measure_profile: dict[str, Any] | None,
    rows: list[list[Any]],
    columns: list[str],
    top_n: int,
    time_grain: str | None,
) -> dict[str, Any]:
    chart_type = CHART_TYPE_BY_INTENT.get(intent_type, "table")
    if chart_type == "kpi":
        value = None
        if measure_profile:
            values = [
                _to_number(row[measure_profile["index"]])
                for row in rows
                if measure_profile["index"] < len(row) and _is_numeric_value(row[measure_profile["index"]])
            ]
            if values:
                value = sum(values) if any(term in question.lower() for term in ("sum", "total")) else values[0]
        if value is None:
            value = len(rows)
        return {
            "id": "panel_primary_kpi",
            "title": "Executive KPI",
            "chart_type": "kpi",
            "x_axis": None,
            "y_axis": measure_profile["name"] if measure_profile else None,
            "data": [{"label": question, "value": value, "formatted_value": _compact_number(value)}],
            "insight": f"The primary KPI for this question is {_compact_number(value)}.",
        }

    if chart_type == "table" or not dimension_profile:
        return {
            "id": "panel_primary_table",
            "title": "Detail table",
            "chart_type": "table",
            "x_axis": None,
            "y_axis": None,
            "columns": columns,
            "data": _table_rows(columns, rows),
            "insight": "This question is best reviewed as a detailed table.",
        }

    series = _aggregate_series(
        rows,
        dimension_profile,
        measure_profile,
        intent_type=intent_type,
        top_n=top_n,
        time_grain=time_grain,
    )
    if not series:
        return {
            "id": "panel_primary_table",
            "title": "Detail table",
            "chart_type": "table",
            "x_axis": None,
            "y_axis": None,
            "columns": columns,
            "data": _table_rows(columns, rows),
            "insight": "There was not enough structured data to draw a chart, so the result is shown as a table.",
        }

    leader = series[0]
    if chart_type == "line":
        title = f"{measure_profile['name'] if measure_profile else 'Row count'} over {dimension_profile['name']}"
        insight = f"The visible trend runs across {len(series)} points, and the latest point is {_compact_number(series[-1]['y'])}."
        panel_type = "line"
    elif chart_type == "horizontal_bar":
        title = f"Top {len(series)} by {dimension_profile['name']}"
        insight = f"{leader['x']} is the top-ranked category at {_compact_number(leader['y'])}."
        panel_type = "bar_horizontal"
    elif chart_type == "donut":
        title = f"Breakdown by {dimension_profile['name']}"
        insight = f"{leader['x']} has the largest visible share at {_compact_number(leader['y'])}."
        panel_type = "donut"
    else:
        title = f"{measure_profile['name'] if measure_profile else 'Row count'} by {dimension_profile['name']}"
        insight = f"{leader['x']} is the leading category at {_compact_number(leader['y'])}."
        panel_type = "bar"

    return {
        "id": "panel_primary_chart",
        "title": title,
        "chart_type": panel_type,
        "x_axis": dimension_profile["name"],
        "y_axis": measure_profile["name"] if measure_profile else "row_count",
        "data": series,
        "insight": insight,
    }


def _build_supporting_panels(
    *,
    primary_panel: dict[str, Any],
    rows: list[list[Any]],
    columns: list[str],
    measure_profile: dict[str, Any] | None,
    dimension_profile: dict[str, Any] | None,
    intent_type: str,
) -> list[dict[str, Any]]:
    primary_series = primary_panel.get("data") if primary_panel.get("chart_type") != "table" else []
    kpis = _build_kpi_panels(rows, measure_profile, dimension_profile, primary_series or [], intent_type=intent_type)
    non_kpi_support: list[dict[str, Any]] = []
    if primary_panel["chart_type"] != "table":
        non_kpi_support.append(
            {
                "id": "panel_support_table",
                "title": "Supporting table",
                "chart_type": "table",
                "x_axis": None,
                "y_axis": None,
                "columns": columns if primary_panel.get("columns") is None else primary_panel["columns"],
                "data": (
                    _table_rows(columns, rows)
                    if primary_panel["chart_type"] == "kpi"
                    else [
                        {
                            primary_panel.get("x_axis") or "category": item["x"],
                            primary_panel.get("y_axis") or "value": item["y"],
                        }
                        for item in (primary_series or [])[:12]
                    ]
                ),
                "insight": "The supporting table lets stakeholders review the numbers behind the visual.",
            }
        )

    panels = kpis[:2]
    if non_kpi_support and len(panels) < 2:
        panels.append(non_kpi_support[0])
    return panels[:2]


def _build_summary(question: str, primary_panel: dict[str, Any], intent_type: str) -> str:
    chart_type = primary_panel.get("chart_type")
    data = primary_panel.get("data") or []
    if chart_type in {"bar", "bar_horizontal", "donut"} and data:
        leader = data[0]
        return f'For "{question}", {leader["x"]} is the leading category at {_compact_number(leader["y"])}.'
    if chart_type == "line" and data:
        first, last = data[0], data[-1]
        return f'For "{question}", the visible trend runs from {first["x"]} to {last["x"]}, ending at {_compact_number(last["y"])}.'
    if chart_type == "kpi" and data:
        return f'For "{question}", the primary KPI is {_compact_number(data[0]["value"])}.'
    if intent_type in {"FILTER_QUERY", "RAW_LIST"}:
        return f'For "{question}", the result is best reviewed as a detailed table.'
    return f'For "{question}", the dashboard highlights the most relevant visual and supporting context from the current result.'


def _follow_ups(intent_type: str, primary_panel: dict[str, Any]) -> list[str]:
    base = [
        "Show only the top 5",
        "Explain the biggest change",
        "Replace this with a table",
    ]
    if primary_panel.get("chart_type") in {"bar", "bar_horizontal"}:
        return ["Make this a line chart", "Add KPI cards", *base][:3]
    if primary_panel.get("chart_type") == "line":
        return ["Compare this by quarter", "Show the top periods", *base][:3]
    if primary_panel.get("chart_type") == "donut":
        return ["Turn this into a ranked bar chart", "Show top categories only", *base][:3]
    if intent_type in {"FILTER_QUERY", "RAW_LIST"}:
        return ["Summarize this with KPI cards", "Group this by a category", "Show a trend over time"]
    return base


def _confidence(intent_type: str, dimension_profile: dict[str, Any] | None, measure_profile: dict[str, Any] | None, rows: list[list[Any]]) -> str:
    if not rows:
        return "low"
    if intent_type in {"COUNT_BY", "TREND_OVER_TIME", "TOP_N", "BREAKDOWN"} and dimension_profile:
        return "high" if (measure_profile or intent_type in {"COUNT_BY", "BREAKDOWN"}) else "medium"
    if intent_type in {"KPI_SUMMARY", "FILTER_QUERY", "RAW_LIST"}:
        return "medium" if rows else "low"
    return "low"


def _legacy_view(panel: dict[str, Any]) -> str:
    chart_type = panel.get("chart_type")
    if chart_type in {"bar", "bar_horizontal"}:
        return "bar"
    if chart_type == "line":
        return "line"
    if chart_type == "donut":
        return "bar"
    if chart_type == "kpi":
        return "kpi"
    return "table"


def generate_dashboard_spec(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    source_question: str | None = None,
    current_dashboard: dict[str, Any] | None = None,
    schema_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = rows or []
    columns = columns or []
    current_dashboard = current_dashboard or {}
    profiles = _build_profiles(columns, rows, schema_metadata=schema_metadata)
    intent = _normalize_dashboard_intent(question, classify_intent(question), profiles)
    dimension_profile = _pick_dimension_profile(intent, profiles)
    measure_profile = _pick_measure_profile(intent, profiles)
    top_n = int(intent.get("top_n") or current_dashboard.get("top_n") or 8)
    top_n = max(3, min(20, top_n))

    primary_panel = _build_primary_panel(
        question=source_question or question,
        intent_type=intent["type"],
        dimension_profile=dimension_profile,
        measure_profile=measure_profile,
        rows=rows,
        columns=columns,
        top_n=top_n,
        time_grain=intent.get("time_grain"),
    )
    supporting_panels = _build_supporting_panels(
        primary_panel=primary_panel,
        rows=rows,
        columns=columns,
        measure_profile=measure_profile,
        dimension_profile=dimension_profile,
        intent_type=intent["type"],
    )

    panels = [*supporting_panels, primary_panel] if primary_panel.get("chart_type") != "kpi" else [primary_panel, *supporting_panels]
    summary = _build_summary(source_question or question, primary_panel, intent["type"])
    confidence = _confidence(intent["type"], dimension_profile, measure_profile, rows)
    follow_ups = _follow_ups(intent["type"], primary_panel)
    explanation = primary_panel.get("insight") or summary

    dashboard_plan = {
        "intent_type": intent["type"],
        "panels": panels,
        "summary": summary,
        "confidence": confidence,
        "follow_ups": follow_ups,
        "filter_fields": [profile["name"] for profile in profiles if profile["semantic_type"] == "DIMENSION"][:6],
        "date_fields": [profile["name"] for profile in profiles if profile["semantic_type"] == "DATE"][:3],
    }

    return {
        "view": _legacy_view(primary_panel),
        "title": primary_panel.get("title") or (source_question or question),
        "subtitle": "Intent-driven stakeholder dashboard",
        "dimension": primary_panel.get("x_axis"),
        "measure": primary_panel.get("y_axis"),
        "top_n": top_n,
        "explanation": explanation,
        "confidence": confidence,
        "insights": [panel.get("insight") for panel in panels[:3] if panel.get("insight")],
        "follow_ups": follow_ups,
        "summary": summary,
        "panels": panels,
        "dashboard_plan": dashboard_plan,
    }
