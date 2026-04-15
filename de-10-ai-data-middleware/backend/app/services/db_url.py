from urllib.parse import quote_plus


def build_db_url(
    db_type: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> str:
    safe_pw = quote_plus(password)
    safe_user = quote_plus(username)

    if db_type == "mysql":
        return f"mysql+pymysql://{safe_user}:{safe_pw}@{host}:{port}/{database}"
    elif db_type == "sqlite":
        # For SQLite, 'database' is the file path; credentials are ignored
        return f"sqlite:///{database}"
    else:
        # Default: PostgreSQL
        return f"postgresql+psycopg2://{safe_user}:{safe_pw}@{host}:{port}/{database}"
