from fastapi import APIRouter, Header, HTTPException
from app.schemas.query import QueryRequest
from app.schemas.ai_query import AIQueryRequest
from app.schemas.responses import AskQueryResponse, QueryResultResponse
from app.services.query_service import execute_sql_query, execute_nl_query
from app.services import connection_registry as registry
from app.services import auth_service

router = APIRouter()


def _resolve_auth_context(authorization: str | None):
    try:
        return auth_service.get_optional_auth_context(authorization)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post(
    "/run",
    response_model=QueryResultResponse,
    response_model_exclude_none=True,
    summary="Run Read-Only SQL",
    description="Executes a single validated SELECT query. Pass inline credentials or a saved connection_id.",
)
def run_query(
    payload: QueryRequest,
    authorization: str | None = Header(None),
) -> QueryResultResponse:
    auth_context = _resolve_auth_context(authorization)
    try:
        params = registry.resolve(
            payload,
            organization_id=auth_context.organization_id if auth_context else None,
            user_id=auth_context.user_id if auth_context else None,
        )
    except ValueError as e:
        return QueryResultResponse(success=False, error=str(e))

    return execute_sql_query(
        sql=payload.sql,
        source_kind=params["source_kind"],
        engine_key=params["engine_key"],
        host=params["host"],
        port=params["port"],
        database=params["database"],
        username=params["username"],
        password=params["password"],
        options=params["options"],
    )


@router.post(
    "/ask",
    response_model=AskQueryResponse,
    response_model_exclude_none=True,
    summary="Ask In Plain English",
    description="Uses schema + AI to generate a safe read-only SQL query. Pass inline credentials or a saved connection_id.",
)
def ask_query(
    payload: AIQueryRequest,
    authorization: str | None = Header(None),
) -> AskQueryResponse:
    auth_context = _resolve_auth_context(authorization)
    try:
        params = registry.resolve(
            payload,
            organization_id=auth_context.organization_id if auth_context else None,
            user_id=auth_context.user_id if auth_context else None,
        )
    except ValueError as e:
        return AskQueryResponse(success=False, question=payload.question, sql="", error=str(e))

    return execute_nl_query(
        question=payload.question,
        connection_id=params.get("connection_id"),
        organization_id=auth_context.organization_id if auth_context else None,
        user_id=auth_context.user_id if auth_context else None,
        source_kind=params["source_kind"],
        engine_key=params["engine_key"],
        host=params["host"],
        port=params["port"],
        database=params["database"],
        username=params["username"],
        password=params["password"],
        options=params["options"],
    )
