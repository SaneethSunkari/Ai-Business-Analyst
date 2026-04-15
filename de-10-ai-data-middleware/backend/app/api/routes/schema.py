from fastapi import APIRouter
from app.schemas.connection import ConnectionRequest
from app.schemas.responses import SchemaScanResponse
from app.services.schema_service import get_schema_metadata
from app.services import connection_registry as registry

router = APIRouter()


@router.post(
    "/scan",
    response_model=SchemaScanResponse,
    response_model_exclude_none=True,
    summary="Scan Tables And Relationships",
    description="Returns table list, column metadata, and inferred relationships. Pass inline credentials or a saved connection_id.",
)
def scan_schema(payload: ConnectionRequest) -> SchemaScanResponse:
    try:
        params = registry.resolve(payload)
    except ValueError as e:
        return SchemaScanResponse(error=str(e))

    schema = get_schema_metadata(
        db_type=params["db_type"],
        host=params["host"],
        port=params["port"],
        database=params["database"],
        username=params["username"],
        password=params["password"],
    )
    return SchemaScanResponse(
        tables=schema.get("tables", {}),
        relationships=schema.get("relationships", []),
        error=schema.get("error"),
    )
