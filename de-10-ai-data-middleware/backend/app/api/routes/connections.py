from fastapi import APIRouter, Header, HTTPException
from app.schemas.connection import ConnectionRequest, RegisterConnectionRequest
from app.schemas.responses import (
    ConnectionTestResponse,
    RegisterConnectionResponse,
    ConnectionListResponse,
    SavedConnectionInfo,
    DatabaseCatalogResponse,
    DatabaseTypeInfo,
)
from app.services.connection_service import test_connection
from app.services import connection_registry as registry
from app.services import auth_service
from app.services import control_plane_service
from app.services.database_catalog import get_database_catalog

router = APIRouter()


@router.post(
    "/test",
    response_model=ConnectionTestResponse,
    summary="Test Data Source Connection",
    description="Checks whether the provided source credentials (or a saved connection_id) can connect successfully.",
)
def test_conn(
    payload: ConnectionRequest,
    authorization: str | None = Header(None),
) -> ConnectionTestResponse:
    auth_context = _resolve_auth_context(authorization)
    try:
        params = registry.resolve(
            payload,
            organization_id=auth_context.organization_id if auth_context else None,
            user_id=auth_context.user_id if auth_context else None,
        )
    except ValueError as e:
        return ConnectionTestResponse(success=False, message=str(e))

    success, message = test_connection(
        source_kind=params["source_kind"],
        engine_key=params["engine_key"],
        host=params["host"],
        port=params["port"],
        database=params["database"],
        username=params["username"],
        password=params["password"],
        options=params["options"],
    )
    return ConnectionTestResponse(success=success, message=message)


def _resolve_auth_context(authorization: str | None):
    try:
        return auth_service.get_optional_auth_context(authorization)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _register_conn(
    payload: RegisterConnectionRequest,
    authorization: str | None = None,
) -> RegisterConnectionResponse:
    auth_context = _resolve_auth_context(authorization)
    if control_plane_service.control_plane_enabled() and not auth_context:
        raise HTTPException(status_code=401, detail="Login required to save data sources.")
    try:
        conn_id = registry.register_connection(
            name=payload.name,
            source_kind=payload.source_kind,
            engine_key=payload.engine_key,
            host=payload.host,
            port=payload.port,
            database=payload.database,
            username=payload.username,
            password=payload.password,
            options=payload.options,
            organization_id=auth_context.organization_id if auth_context else None,
            user_id=auth_context.user_id if auth_context else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return RegisterConnectionResponse(
        connection_id=conn_id,
        name=payload.name,
        message=f"Connection '{payload.name}' saved. Use connection_id '{conn_id}' in any endpoint.",
    )


@router.post(
    "/register",
    response_model=RegisterConnectionResponse,
    summary="Save a Connection",
    description="Store source credentials and receive a connection_id to reuse across all endpoints.",
)
def register_conn_with_auth(
    payload: RegisterConnectionRequest,
    authorization: str | None = Header(None),
) -> RegisterConnectionResponse:
    return _register_conn(payload, authorization)


@router.get(
    "/",
    response_model=ConnectionListResponse,
    summary="List Saved Connections",
    description="Returns all saved connections (passwords excluded).",
)
def list_conns(authorization: str | None = Header(None)) -> ConnectionListResponse:
    auth_context = _resolve_auth_context(authorization)
    conns = registry.list_connections(
        organization_id=auth_context.organization_id if auth_context else None,
        user_id=auth_context.user_id if auth_context else None,
    )
    return ConnectionListResponse(
        connections=[SavedConnectionInfo(**c) for c in conns]
    )


@router.get(
    "/types",
    response_model=DatabaseCatalogResponse,
    summary="List Supported Source Engines",
    description="Returns the source engines supported by the UI, including source kind, labels, default ports, and extra option fields.",
)
def list_db_types() -> DatabaseCatalogResponse:
    return DatabaseCatalogResponse(
        databases=[DatabaseTypeInfo(**db) for db in get_database_catalog()]
    )


@router.delete(
    "/{connection_id}",
    summary="Delete Saved Connection",
    description="Remove a saved connection by its ID.",
)
def delete_conn(connection_id: str, authorization: str | None = Header(None)):
    auth_context = _resolve_auth_context(authorization)
    deleted = registry.delete_connection(
        connection_id,
        organization_id=auth_context.organization_id if auth_context else None,
        user_id=auth_context.user_id if auth_context else None,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Connection '{connection_id}' not found.")
    return {"message": f"Connection '{connection_id}' deleted."}
