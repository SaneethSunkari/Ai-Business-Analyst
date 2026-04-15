from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class QueryRequest(BaseModel):
    connection_id: str | None = Field(None, description="ID of a saved connection.")
    db_type: Literal["postgresql", "mysql", "sqlite"] = Field("postgresql", description="Database engine type.")
    host: str | None = Field(None, description="Hostname or IP address.")
    port: int | None = Field(None, description="Port number.")
    database: str | None = Field(None, description="Database name.")
    username: str | None = Field(None, description="Database username.")
    password: str | None = Field(None, description="Database password.")
    sql: str = Field(..., description="A single read-only SQL SELECT query.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "db_type": "postgresql",
                "host": "localhost",
                "port": 5433,
                "database": "demo_db",
                "username": "postgres",
                "password": "postgres",
                "sql": "SELECT first, last FROM patients ORDER BY id LIMIT 5;",
            }
        }
    )
