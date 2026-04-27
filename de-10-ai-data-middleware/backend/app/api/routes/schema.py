from fastapi import APIRouter, Header, HTTPException
from app.schemas.connection import ConnectionRequest
from app.schemas.responses import SchemaScanResponse
from app.services.error_service import clean_db_error_message
from app.services.schema_service import get_schema_metadata
from app.services import connection_registry as registry
from app.services import auth_service

router = APIRouter()


def _resolve_auth_context(authorization: str | None):
    try:
        return auth_service.get_optional_auth_context(authorization)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post(
    "/scan",
    response_model=SchemaScanResponse,
    response_model_exclude_none=True,
    summary="Scan Tables And Relationships",
    description="Returns table list, column metadata, and inferred relationships. Pass inline credentials or a saved connection_id.",
)
def scan_schema(
    payload: ConnectionRequest,
    authorization: str | None = Header(None),
) -> SchemaScanResponse:
    auth_context = _resolve_auth_context(authorization)
    try:
        params = registry.resolve(
            payload,
            organization_id=auth_context.organization_id if auth_context else None,
            user_id=auth_context.user_id if auth_context else None,
        )
    except ValueError as e:
        return SchemaScanResponse(error=str(e))

    try:
        schema = get_schema_metadata(
            source_kind=params["source_kind"],
            engine_key=params["engine_key"],
            host=params["host"],
            port=params["port"],
            database=params["database"],
            username=params["username"],
            password=params["password"],
            options=params["options"],
        )
    except Exception as e:
        return SchemaScanResponse(error=clean_db_error_message(str(e)))

    return SchemaScanResponse(
        tables=schema.get("tables", {}),
        relationships=schema.get("relationships", []),
        error=schema.get("error"),
    )
