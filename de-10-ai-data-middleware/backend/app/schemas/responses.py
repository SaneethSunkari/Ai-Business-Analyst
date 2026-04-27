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


class AuthUserInfo(BaseModel):
    id: str
    email: str | None = None
    full_name: str | None = None
    organization_id: str | None = None
    organization_name: str | None = None


class AuthSessionInfo(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    token_type: str | None = None
    user: AuthUserInfo


class AuthSessionResponse(BaseModel):
    success: bool
    session: AuthSessionInfo | None = None
    error: str | None = None


class AuthMeResponse(BaseModel):
    authenticated: bool
    user: AuthUserInfo | None = None


class DatabaseOptionField(BaseModel):
    key: str
    label: str
    placeholder: str | None = None
    required: bool = False
    secret: bool = False


class DatabaseTypeInfo(BaseModel):
    key: str
    engine_key: str
    source_kind: str
    label: str
    dialect_label: str
    default_port: int | None = None
    host_label: str
    port_label: str
    database_label: str
    host_placeholder: str | None = None
    database_placeholder: str | None = None
    username_placeholder: str | None = None
    username_label: str | None = None
    password_label: str | None = None
    show_host: bool = True
    show_port: bool = True
    show_username: bool = True
    show_password: bool = True
    help_text: str | None = None
    options: list[DatabaseOptionField] = Field(default_factory=list)


class DatabaseCatalogResponse(BaseModel):
    databases: list[DatabaseTypeInfo]


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
    source_kind: str
    engine_key: str
    db_type: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    options: dict[str, str] = Field(default_factory=dict)
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
