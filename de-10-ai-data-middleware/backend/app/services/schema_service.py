from sqlalchemy import create_engine, inspect
from app.services.db_url import build_db_url


def singularize_table_name(table_name: str) -> str:
    if table_name.endswith("ies") and len(table_name) > 3:
        return table_name[:-3] + "y"
    if table_name.endswith("s") and not table_name.endswith("ss") and len(table_name) > 3:
        return table_name[:-1]
    return table_name


def build_inferred_relationships(table_columns: dict[str, list[dict]]) -> list[dict]:
    relationships: list[dict] = []
    seen_relationships: set[tuple[str, str, str, str]] = set()

    id_backed_tables = {
        table_name
        for table_name, columns in table_columns.items()
        if any(column["name"] == "id" for column in columns)
    }

    singular_table_lookup = {
        singularize_table_name(table_name): table_name for table_name in id_backed_tables
    }

    for table_name, columns in table_columns.items():
        for column in columns:
            column_name = column["name"]
            referred_table = singular_table_lookup.get(column_name)
            if not referred_table:
                continue
            relationship_key = (table_name, column_name, referred_table, "id")
            if relationship_key in seen_relationships:
                continue
            seen_relationships.add(relationship_key)
            relationships.append({
                "from_table": table_name,
                "from_column": column_name,
                "to_table": referred_table,
                "to_column": "id",
            })

    return relationships


def get_schema_metadata(
    db_type: str = "postgresql",
    host: str = "localhost",
    port: int = 5432,
    database: str = "",
    username: str = "",
    password: str = "",
):
    db_url = build_db_url(db_type, host, port, database, username, password)
    engine = create_engine(db_url)

    try:
        inspector = inspect(engine)
        schema_info: dict = {"tables": {}, "relationships": []}
        seen_relationships: set[tuple[str, str, str, str]] = set()

        for table_name in inspector.get_table_names():
            columns = inspector.get_columns(table_name)
            schema_info["tables"][table_name] = [
                {
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col.get("nullable", True),
                }
                for col in columns
            ]

            for fk in inspector.get_foreign_keys(table_name):
                constrained = fk.get("constrained_columns", [])
                referred_table = fk.get("referred_table")
                referred_cols = fk.get("referred_columns", [])
                if not (constrained and referred_table and referred_cols):
                    continue
                key = (table_name, constrained[0], referred_table, referred_cols[0])
                if key in seen_relationships:
                    continue
                seen_relationships.add(key)
                schema_info["relationships"].append({
                    "from_table": table_name,
                    "from_column": constrained[0],
                    "to_table": referred_table,
                    "to_column": referred_cols[0],
                })

        for rel in build_inferred_relationships(schema_info["tables"]):
            key = (rel["from_table"], rel["from_column"], rel["to_table"], rel["to_column"])
            if key in seen_relationships:
                continue
            seen_relationships.add(key)
            schema_info["relationships"].append(rel)

        return schema_info
    finally:
        engine.dispose()
