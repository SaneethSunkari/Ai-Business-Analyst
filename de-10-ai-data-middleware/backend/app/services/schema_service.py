from sqlalchemy import create_engine, inspect
from app.services.db_url import build_db_url
from app.services.extended_source_service import get_special_schema_metadata, handles_special_engine
from app.services.object_store_service import get_object_store_schema_metadata


def singularize_table_name(table_name: str) -> str:
    if table_name.endswith("ies") and len(table_name) > 3:
        return table_name[:-3] + "y"
    if table_name.endswith("s") and not table_name.endswith("ss") and len(table_name) > 3:
        return table_name[:-1]
    return table_name


def is_id_column(column_name: str) -> bool:
    return column_name.lower() == "id"


def build_inferred_relationships(table_columns: dict[str, list[dict]]) -> list[dict]:
    relationships: list[dict] = []
    seen_relationships: set[tuple[str, str, str, str]] = set()

    id_backed_tables = {
        table_name
        for table_name, columns in table_columns.items()
        if any(is_id_column(column["name"]) for column in columns)
    }

    singular_table_lookup = {
        singularize_table_name(table_name.lower()): table_name for table_name in id_backed_tables
    }

    for table_name, columns in table_columns.items():
        for column in columns:
            column_name = column["name"]
            referred_table = singular_table_lookup.get(column_name.lower())
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
    source_kind: str = "database",
    engine_key: str = "postgresql",
    host: str = "localhost",
    port: int = 5432,
    database: str = "",
    username: str = "",
    password: str = "",
    options: dict[str, str] | None = None,
):
    if source_kind == "object_store":
        return get_object_store_schema_metadata(
            engine_key=engine_key,
            host=host,
            database=database,
            username=username,
            password=password,
            options=options,
        )

    if handles_special_engine(engine_key):
        schema_info = get_special_schema_metadata(
            engine_key=engine_key,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            options=options,
        )
        seen_relationships: set[tuple[str, str, str, str]] = set()
        for rel in build_inferred_relationships(schema_info["tables"]):
            key = (rel["from_table"], rel["from_column"], rel["to_table"], rel["to_column"])
            if key in seen_relationships:
                continue
            seen_relationships.add(key)
            schema_info["relationships"].append(rel)
        return schema_info

    db_url = build_db_url(
        engine_key,
        host,
        port,
        database,
        username,
        password,
        options=options,
    )
    engine = create_engine(db_url)

    try:
        inspector = inspect(engine)
        schema_info: dict = {"tables": {}, "relationships": []}
        seen_relationships: set[tuple[str, str, str, str]] = set()
        schema_name = (options or {}).get("schema") or None

        try:
            table_names = inspector.get_table_names(schema=schema_name) if schema_name else inspector.get_table_names()
        except TypeError:
            table_names = inspector.get_table_names()

        for table_name in table_names:
            try:
                columns = inspector.get_columns(table_name, schema=schema_name) if schema_name else inspector.get_columns(table_name)
            except TypeError:
                columns = inspector.get_columns(table_name)
            schema_info["tables"][table_name] = [
                {
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col.get("nullable", True),
                }
                for col in columns
            ]

            try:
                foreign_keys = inspector.get_foreign_keys(table_name, schema=schema_name) if schema_name else inspector.get_foreign_keys(table_name)
            except TypeError:
                foreign_keys = inspector.get_foreign_keys(table_name)

            for fk in foreign_keys:
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
