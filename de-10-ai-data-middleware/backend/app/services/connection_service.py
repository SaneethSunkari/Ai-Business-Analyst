from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from app.services.db_url import build_db_url
from app.services.database_catalog import get_ping_sql
from app.services.error_service import clean_db_error_message
from app.services.extended_source_service import handles_special_engine, test_special_connection
from app.services.object_store_service import test_object_store_connection


def test_connection(
    source_kind: str,
    engine_key: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    options: dict[str, str] | None = None,
) -> tuple[bool, str]:
    if source_kind == "object_store":
        ok, message = test_object_store_connection(
            engine_key=engine_key,
            host=host,
            database=database,
            username=username,
            password=password,
            options=options,
        )
        return ok, clean_db_error_message(message) if not ok else message

    if handles_special_engine(engine_key):
        ok, message = test_special_connection(
            engine_key=engine_key,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            options=options,
        )
        return ok, clean_db_error_message(message) if not ok else message

    db_url = build_db_url(engine_key, host, port, database, username, password, options=options)
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            conn.execute(text(get_ping_sql(engine_key)))
        return True, "Connection successful"
    except SQLAlchemyError as e:
        return False, clean_db_error_message(str(e))
    finally:
        engine.dispose()


# Keep old name for backwards-compat with any direct callers
def test_postgres_connection(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> tuple[bool, str]:
    return test_connection("database", "postgresql", host, port, database, username, password)
