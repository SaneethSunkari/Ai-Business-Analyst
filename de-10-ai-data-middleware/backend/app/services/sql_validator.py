import re


FORBIDDEN_KEYWORDS = [
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "grant",
    "revoke",
    "copy",
]


def validate_read_only_sql(sql: str) -> tuple[bool, str]:
    cleaned_sql = sql.strip().lower()

    if not cleaned_sql:
        return False, "SQL query cannot be empty"

    if not cleaned_sql.startswith("select"):
        return False, "Only SELECT queries are allowed"

    if ";" in cleaned_sql[:-1]:
        return False, "Multiple SQL statements are not allowed"

    for keyword in FORBIDDEN_KEYWORDS:
        pattern = rf"\b{keyword}\b"
        if re.search(pattern, cleaned_sql):
            return False, f"Forbidden SQL keyword detected: {keyword}"

    return True, "Query is valid"
