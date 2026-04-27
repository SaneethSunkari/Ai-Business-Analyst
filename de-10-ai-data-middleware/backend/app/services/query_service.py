from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from time import perf_counter
from app.services.db_url import build_db_url
from app.services.error_service import clean_db_error_message
from app.services.extended_source_service import execute_special_query, handles_special_engine
from app.services.llm_service import UNANSWERABLE_SQL, generate_sql_from_question
from app.services.log_service import build_query_log, write_query_log
from app.services.object_store_service import execute_object_store_sql
from app.services.schema_service import get_schema_metadata
from app.services.sql_validator import validate_read_only_sql


def clean_error_message(error: str) -> str:
    return clean_db_error_message(error)


def execute_sql_query(
    sql: str,
    source_kind: str = "database",
    engine_key: str = "postgresql",
    host: str = "localhost",
    port: int = 5432,
    database: str = "",
    username: str = "",
    password: str = "",
    options: dict[str, str] | None = None,
):
    is_valid, message = validate_read_only_sql(sql)
    if not is_valid:
        return {"success": False, "error": message}

    if source_kind == "object_store":
        try:
            return execute_object_store_sql(
                engine_key=engine_key,
                host=host,
                database=database,
                username=username,
                password=password,
                options=options,
                sql=sql,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    if handles_special_engine(engine_key):
        try:
            return execute_special_query(
                engine_key=engine_key,
                sql=sql,
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                options=options,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

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
        with engine.connect() as connection:
            result = connection.execute(text(sql))
            rows = result.fetchall()
            columns = list(result.keys())
        return {"success": True, "columns": columns, "rows": [list(row) for row in rows]}
    except SQLAlchemyError as e:
        return {"success": False, "error": str(e)}
    finally:
        engine.dispose()


def execute_nl_query(
    question: str,
    connection_id: str | None = None,
    organization_id: str | None = None,
    user_id: str | None = None,
    source_kind: str = "database",
    engine_key: str = "postgresql",
    host: str = "localhost",
    port: int = 5432,
    database: str = "",
    username: str = "",
    password: str = "",
    options: dict[str, str] | None = None,
):
    started_at = perf_counter()
    try:
        schema_metadata = get_schema_metadata(
            source_kind=source_kind,
            engine_key=engine_key,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            options=options,
        )
    except Exception as e:
        error_msg = clean_error_message(str(e))
        write_query_log(
            build_query_log(
                question=question,
                generated_sql="",
                success=False,
                error=error_msg,
                connection_id=connection_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        return {"success": False, "question": question, "sql": "", "error": error_msg}

    if not schema_metadata or not schema_metadata.get("tables"):
        error_msg = "No schema metadata found"
        write_query_log(
            build_query_log(
                question=question,
                generated_sql="",
                success=False,
                error=error_msg,
                connection_id=connection_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        return {"success": False, "question": question, "sql": "", "error": error_msg}

    generated_sql = generate_sql_from_question(
        question=question,
        schema_metadata=schema_metadata,
        engine_key=engine_key,
    )

    if generated_sql == UNANSWERABLE_SQL:
        error_msg = "Question cannot be answered from the available schema"
        write_query_log(
            build_query_log(
                question=question,
                generated_sql=generated_sql,
                success=False,
                error=error_msg,
                connection_id=connection_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        return {"success": False, "question": question, "sql": generated_sql, "error": error_msg}

    query_result = execute_sql_query(
        sql=generated_sql,
        source_kind=source_kind,
        engine_key=engine_key,
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        options=options,
    )

    if not query_result.get("success"):
        error_msg = clean_error_message(query_result.get("error", "Query execution failed"))
        write_query_log(
            build_query_log(
                question=question,
                generated_sql=generated_sql,
                success=False,
                error=error_msg,
                connection_id=connection_id,
                latency_ms=int((perf_counter() - started_at) * 1000),
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        return {"success": False, "question": question, "sql": generated_sql, "error": error_msg}

    columns = query_result.get("columns", [])
    rows = query_result.get("rows", [])
    write_query_log(
        build_query_log(
            question=question,
            generated_sql=generated_sql,
            success=True,
            row_count=len(rows),
            connection_id=connection_id,
            latency_ms=int((perf_counter() - started_at) * 1000),
        ),
        organization_id=organization_id,
        user_id=user_id,
    )

    return {
        "success": True,
        "question": question,
        "sql": generated_sql,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
    }
