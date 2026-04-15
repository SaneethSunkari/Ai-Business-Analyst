from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class ConnectionRequest(BaseModel):
    connection_id: str | None = Field(None, description="ID of a previously saved connection. If provided, all other fields are optional.")
    db_type: Literal["postgresql", "mysql", "sqlite"] = Field("postgresql", description="Database engine type.")
    host: str | None = Field(None, description="Hostname or IP address.")
    port: int | None = Field(None, description="Port number.")
    database: str | None = Field(None, description="Database name (or file path for SQLite).")
    username: str | None = Field(None, description="Database username.")
    password: str | None = Field(None, description="Database password.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "db_type": "postgresql",
                "host": "localhost",
                "port": 5433,
                "database": "demo_db",
                "username": "postgres",
                "password": "postgres",
            }
        }
    )


class RegisterConnectionRequest(BaseModel):
    name: str = Field(..., description="A friendly label for this saved connection.")
    db_type: Literal["postgresql", "mysql", "sqlite"] = Field("postgresql", description="Database engine type.")
    host: str | None = Field(None, description="Hostname or IP address.")
    port: int | None = Field(None, description="Port number.")
    database: str | None = Field(None, description="Database name.")
    username: str | None = Field(None, description="Database username.")
    password: str | None = Field(None, description="Database password.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Demo DB",
                "db_type": "postgresql",
                "host": "localhost",
                "port": 5433,
                "database": "demo_db",
                "username": "postgres",
                "password": "postgres",
            }
        }
    )
