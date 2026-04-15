from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from app.services.db_url import build_db_url


def test_connection(
    db_type: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> tuple[bool, str]:
    db_url = build_db_url(db_type, host, port, database, username, password)
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "Connection successful"
    except SQLAlchemyError as e:
        return False, f"Connection failed: {str(e)}"
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
    return test_connection("postgresql", host, port, database, username, password)
