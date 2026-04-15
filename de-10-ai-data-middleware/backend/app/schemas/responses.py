from pydantic import BaseModel, Field
from typing import Any

RowValue = str | int | float | bool | None


class RootResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str


class ColumnMetadata(BaseModel):
    name: str
    type: str
    nullable: bool


class RelationshipMetadata(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str


class SchemaScanResponse(BaseModel):
    tables: dict[str, list[ColumnMetadata]] = Field(default_factory=dict)
    relationships: list[RelationshipMetadata] = Field(default_factory=list)
    error: str | None = None


class QueryResultResponse(BaseModel):
    success: bool
    columns: list[str] | None = None
    rows: list[list[RowValue]] | None = None
    error: str | None = None


class AskQueryResponse(BaseModel):
    success: bool
    question: str
    sql: str
    columns: list[str] | None = None
    rows: list[list[RowValue]] | None = None
    row_count: int | None = None
    error: str | None = None


class SavedConnectionInfo(BaseModel):
    id: str
    name: str
    db_type: str
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    created_at: str


class RegisterConnectionResponse(BaseModel):
    connection_id: str
    name: str
    message: str


class ConnectionListResponse(BaseModel):
    connections: list[SavedConnectionInfo]


class ToolInvokeResponse(BaseModel):
    success: bool
    result: Any | None = None
    error: str | None = None
