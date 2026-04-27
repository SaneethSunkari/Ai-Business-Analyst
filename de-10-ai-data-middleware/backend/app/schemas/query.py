from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.database_catalog import DatabaseType, EngineKey, SourceKind, resolve_source_config


class QueryRequest(BaseModel):
    connection_id: str | None = Field(None, description="ID of a saved connection.")
    source_kind: SourceKind | None = Field(None, description="Source family. Examples: database or warehouse.")
    engine_key: EngineKey | None = Field(None, description="Concrete engine key for the selected source.")
    db_type: DatabaseType | None = Field(None, description="Deprecated alias for engine_key.")
    host: str | None = Field(None, description="Hostname, account identifier, or endpoint.")
    port: int | None = Field(None, description="Port number.")
    database: str | None = Field(None, description="Database name, dataset, or file path depending on the engine.")
    username: str | None = Field(None, description="Source username.")
    password: str | None = Field(None, description="Source password.")
    options: dict[str, str] = Field(
        default_factory=dict,
        description="Source-specific options such as schema, warehouse, project ID, or credentials path.",
    )
    sql: str = Field(..., description="A single read-only SQL SELECT query.")

    @model_validator(mode="after")
    def normalize_source_fields(self):
        self.source_kind, self.engine_key = resolve_source_config(
            source_kind=self.source_kind,
            engine_key=self.engine_key,
            db_type=self.db_type,
        )
        self.db_type = self.engine_key
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_kind": "database",
                "engine_key": "postgresql",
                "host": "localhost",
                "port": 5433,
                "database": "demo_db",
                "username": "postgres",
                "password": "postgres",
                "options": {},
                "sql": "SELECT first, last FROM patients ORDER BY id LIMIT 5;",
            }
        }
    )
