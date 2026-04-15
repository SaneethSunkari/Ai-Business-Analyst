from fastapi import APIRouter
from app.schemas.query import QueryRequest
from app.schemas.ai_query import AIQueryRequest
from app.schemas.responses import AskQueryResponse, QueryResultResponse
from app.services.query_service import execute_sql_query, execute_nl_query
from app.services import connection_registry as registry

router = APIRouter()


@router.post(
    "/run",
    response_model=QueryResultResponse,
    response_model_exclude_none=True,
    summary="Run Read-Only SQL",
    description="Executes a single validated SELECT query. Pass inline credentials or a saved connection_id.",
)
def run_query(payload: QueryRequest) -> QueryResultResponse:
    try:
        params = registry.resolve(payload)
    except ValueError as e:
        return QueryResultResponse(success=False, error=str(e))

    return execute_sql_query(
        sql=payload.sql,
        db_type=params["db_type"],
        host=params["host"],
        port=params["port"],
        database=params["database"],
        username=params["username"],
        password=params["password"],
    )


@router.post(
    "/ask",
    response_model=AskQueryResponse,
    response_model_exclude_none=True,
    summary="Ask In Plain English",
    description="Uses schema + AI to generate a safe read-only SQL query. Pass inline credentials or a saved connection_id.",
)
def ask_query(payload: AIQueryRequest) -> AskQueryResponse:
    try:
        params = registry.resolve(payload)
    except ValueError as e:
        return AskQueryResponse(success=False, question=payload.question, sql="", error=str(e))

    return execute_nl_query(
        question=payload.question,
        db_type=params["db_type"],
        host=params["host"],
        port=params["port"],
        database=params["database"],
        username=params["username"],
        password=params["password"],
    )
