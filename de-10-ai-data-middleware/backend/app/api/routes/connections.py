from fastapi import APIRouter, HTTPException
from app.schemas.connection import ConnectionRequest, RegisterConnectionRequest
from app.schemas.responses import (
    ConnectionTestResponse,
    RegisterConnectionResponse,
    ConnectionListResponse,
    SavedConnectionInfo,
)
from app.services.connection_service import test_connection
from app.services import connection_registry as registry

router = APIRouter()


@router.post(
    "/test",
    response_model=ConnectionTestResponse,
    summary="Test Database Connection",
    description="Checks whether the provided credentials (or a saved connection_id) can connect successfully.",
)
def test_conn(payload: ConnectionRequest) -> ConnectionTestResponse:
    try:
        params = registry.resolve(payload)
    except ValueError as e:
        return ConnectionTestResponse(success=False, message=str(e))

    success, message = test_connection(
        db_type=params["db_type"],
        host=params["host"],
        port=params["port"],
        database=params["database"],
        username=params["username"],
        password=params["password"],
    )
    return ConnectionTestResponse(success=success, message=message)


@router.post(
    "/register",
    response_model=RegisterConnectionResponse,
    summary="Save a Connection",
    description="Store connection credentials in memory and receive a connection_id to reuse across all endpoints.",
)
def register_conn(payload: RegisterConnectionRequest) -> RegisterConnectionResponse:
    conn_id = registry.register_connection(
        name=payload.name,
        db_type=payload.db_type,
        host=payload.host,
        port=payload.port,
        database=payload.database,
        username=payload.username,
        password=payload.password,
    )
    return RegisterConnectionResponse(
        connection_id=conn_id,
        name=payload.name,
        message=f"Connection '{payload.name}' saved. Use connection_id '{conn_id}' in any endpoint.",
    )


@router.get(
    "/",
    response_model=ConnectionListResponse,
    summary="List Saved Connections",
    description="Returns all saved connections (passwords excluded).",
)
def list_conns() -> ConnectionListResponse:
    conns = registry.list_connections()
    return ConnectionListResponse(
        connections=[SavedConnectionInfo(**c) for c in conns]
    )


@router.delete(
    "/{connection_id}",
    summary="Delete Saved Connection",
    description="Remove a saved connection by its ID.",
)
def delete_conn(connection_id: str):
    deleted = registry.delete_connection(connection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Connection '{connection_id}' not found.")
    return {"message": f"Connection '{connection_id}' deleted."}
