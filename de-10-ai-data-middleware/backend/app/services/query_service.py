import re
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from app.services.db_url import build_db_url
from app.services.llm_service import UNANSWERABLE_SQL, generate_sql_from_question
from app.services.log_service import build_query_log, write_query_log
from app.services.schema_service import get_schema_metadata
from app.services.sql_validator import validate_read_only_sql


def clean_error_message(error: str) -> str:
    cleaned_error = error.strip()
    cleaned_error = re.sub(r"\n+\(Background on this error at:.*$", "", cleaned_error, flags=re.DOTALL)
    cleaned_error = re.sub(r"^\([^)]+\)\s*", "", cleaned_error)
    cleaned_error = cleaned_error.strip()
    normalized_error = cleaned_error.lower()

    if "only select queries are allowed" in normalized_error:
        return "Only SELECT queries are allowed"
    if "multiple sql statements are not allowed" in normalized_error:
        return "Multiple SQL statements are not allowed"
    if "password authentication failed" in normalized_error:
        return "Unable to connect to the database with the provided credentials"
    if "role \"" in normalized_error and "does not exist" in normalized_error:
        return "Unable to connect to the database with the provided credentials"
    if "connection refused" in normalized_error or "could not connect to server" in normalized_error:
        return "Unable to connect to the database server"
    if "relation \"" in normalized_error and "does not exist" in normalized_error:
        return "Generated SQL referenced a table that does not exist"
    if "column \"" in normalized_error and "does not exist" in normalized_error:
        return "Generated SQL referenced a column that does not exist"
    if "syntax error at or near" in normalized_error:
        return "Generated SQL was invalid"
    if not cleaned_error:
        return "Query failed"
    first_line = cleaned_error.splitlines()[0].strip()
    return first_line or "Query failed"


def execute_sql_query(
    sql: str,
    db_type: str = "postgresql",
    host: str = "localhost",
    port: int = 5432,
    database: str = "",
    username: str = "",
    password: str = "",
):
    is_valid, message = validate_read_only_sql(sql)
    if not is_valid:
        return {"success": False, "error": message}

    db_url = build_db_url(db_type, host, port, database, username, password)
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
    db_type: str = "postgresql",
    host: str = "localhost",
    port: int = 5432,
    database: str = "",
    username: str = "",
    password: str = "",
):
    try:
        schema_metadata = get_schema_metadata(
            db_type=db_type,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
        )
    except Exception as e:
        error_msg = clean_error_message(str(e))
        write_query_log(build_query_log(question=question, generated_sql="", success=False, error=error_msg))
        return {"success": False, "question": question, "sql": "", "error": error_msg}

    if not schema_metadata or not schema_metadata.get("tables"):
        error_msg = "No schema metadata found"
        write_query_log(build_query_log(question=question, generated_sql="", success=False, error=error_msg))
        return {"success": False, "question": question, "sql": "", "error": error_msg}

    generated_sql = generate_sql_from_question(
        question=question,
        schema_metadata=schema_metadata,
        db_type=db_type,
    )

    if generated_sql == UNANSWERABLE_SQL:
        error_msg = "Question cannot be answered from the available schema"
        write_query_log(build_query_log(question=question, generated_sql=generated_sql, success=False, error=error_msg))
        return {"success": False, "question": question, "sql": generated_sql, "error": error_msg}

    query_result = execute_sql_query(
        sql=generated_sql,
        db_type=db_type,
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
    )

    if not query_result.get("success"):
        error_msg = clean_error_message(query_result.get("error", "Query execution failed"))
        write_query_log(build_query_log(question=question, generated_sql=generated_sql, success=False, error=error_msg))
        return {"success": False, "question": question, "sql": generated_sql, "error": error_msg}

    columns = query_result.get("columns", [])
    rows = query_result.get("rows", [])
    write_query_log(build_query_log(question=question, generated_sql=generated_sql, success=True, row_count=len(rows)))

    return {
        "success": True,
        "question": question,
        "sql": generated_sql,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
    }
