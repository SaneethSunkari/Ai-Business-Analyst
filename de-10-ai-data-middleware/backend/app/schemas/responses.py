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


class OpsBudgetStatus(BaseModel):
    daily_token_budget: int | None = None
    monthly_token_budget: int | None = None
    daily_tokens_used: int | None = None
    monthly_tokens_used: int | None = None


class OpsGatewayUsage(BaseModel):
    total_requests: int = 0
    cache_hits: int = 0
    tool_bypasses: int = 0
    llm_calls: int = 0
    budget_blocks: int = 0
    total_estimated_tokens_saved: int = 0
    total_estimated_cost_saved: float = 0.0
    top_strategies: list[list[Any]] | None = None


class OpsOnboardingItem(BaseModel):
    key: str
    label: str
    done: bool
    detail: str | None = None


class OpsRecentRun(BaseModel):
    created_at: str | None = None
    question: str | None = None
    generated_sql: str | None = None
    success: bool | None = None
    row_count: int | None = None
    error: str | None = None
    connection_id: str | None = None
    latency_ms: int | None = None


class OpsStatusResponse(BaseModel):
    success: bool
    environment: str
    authenticated: bool
    auth_configured: bool
    control_plane_enabled: bool
    tokenfirewall_enabled: bool
    tokenfirewall_routed: bool
    tokenfirewall_healthy: bool | None = None
    tokenfirewall_base_url: str | None = None
    openai_model: str
    saved_source_count: int = 0
    recent_query_count: int = 0
    recent_success_count: int = 0
    recent_failure_count: int = 0
    avg_latency_ms: int | None = None
    latest_question: str | None = None
    budget: OpsBudgetStatus | None = None
    gateway_usage: OpsGatewayUsage | None = None
    onboarding: list[OpsOnboardingItem] = Field(default_factory=list)
    recent_runs: list[OpsRecentRun] = Field(default_factory=list)
