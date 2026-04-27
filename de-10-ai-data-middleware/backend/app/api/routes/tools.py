"""
/tools — Agent-compatible endpoints.

GET  /tools/manifest  →  OpenAI function-calling tool definitions
POST /tools/invoke    →  Execute a tool by name with its arguments

Any AI agent (OpenAI, Claude, LangChain, etc.) can:
  1. Fetch /tools/manifest to discover what this middleware can do.
  2. Call /tools/invoke with {"tool": "<name>", "arguments": {...}}
"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Any

from app.services.error_service import clean_db_error_message
from app.services import auth_service
from app.services.connection_service import test_connection
from app.services.schema_service import get_schema_metadata
from app.services.query_service import execute_sql_query, execute_nl_query
from app.services import connection_registry as registry
from app.services.database_catalog import DATABASE_CATALOG

router = APIRouter()

ENGINE_KEY_ENUM = list(DATABASE_CATALOG.keys())


# ── Manifest ────────────────────────────────────────────────────────────────

TOOL_MANIFEST = [
    {
        "type": "function",
        "function": {
            "name": "test_connection",
            "description": "Test whether a data source connection is reachable with the given credentials or a saved connection_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {
                        "type": "string",
                        "description": "ID of a previously saved connection (from register_connection).",
                    },
                    "source_kind": {
                        "type": "string",
                        "description": "Source family, such as database or warehouse.",
                    },
                    "engine_key": {
                        "type": "string",
                        "enum": ENGINE_KEY_ENUM,
                        "description": "Concrete source engine. Defaults to postgresql.",
                    },
                    "db_type": {"type": "string", "description": "Deprecated alias for engine_key."},
                    "host": {"type": "string"},
                    "port": {"type": "integer"},
                    "database": {"type": "string"},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "options": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_connection",
            "description": "Save source credentials under a friendly name and receive a connection_id for reuse.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Friendly label, e.g. 'Production DB'."},
                    "source_kind": {"type": "string"},
                    "engine_key": {
                        "type": "string",
                        "enum": ENGINE_KEY_ENUM,
                    },
                    "db_type": {"type": "string", "description": "Deprecated alias for engine_key."},
                    "host": {"type": "string"},
                    "port": {"type": "integer"},
                    "database": {"type": "string"},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "options": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_schema",
            "description": "Return all table names, column names/types, and inferred foreign-key relationships for a source engine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "source_kind": {"type": "string"},
                    "engine_key": {"type": "string", "enum": ENGINE_KEY_ENUM},
                    "db_type": {"type": "string", "description": "Deprecated alias for engine_key."},
                    "host": {"type": "string"},
                    "port": {"type": "integer"},
                    "database": {"type": "string"},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "options": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
                "description": (
                    "Ask a question in plain English. The middleware converts it to a safe read-only SQL query "
                    "using the live schema and executes it, returning columns and rows."
                ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The business question in plain English, e.g. 'How many patients are there?'",
                    },
                    "connection_id": {"type": "string"},
                    "source_kind": {"type": "string"},
                    "engine_key": {"type": "string", "enum": ENGINE_KEY_ENUM},
                    "db_type": {"type": "string", "description": "Deprecated alias for engine_key."},
                    "host": {"type": "string"},
                    "port": {"type": "integer"},
                    "database": {"type": "string"},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "options": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Execute a raw read-only SELECT query and return columns and rows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "A single SQL SELECT statement."},
                    "connection_id": {"type": "string"},
                    "source_kind": {"type": "string"},
                    "engine_key": {"type": "string", "enum": ENGINE_KEY_ENUM},
                    "db_type": {"type": "string", "description": "Deprecated alias for engine_key."},
                    "host": {"type": "string"},
                    "port": {"type": "integer"},
                    "database": {"type": "string"},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "options": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["sql"],
            },
        },
    },
]


@router.get(
    "/manifest",
    summary="Tool Manifest",
    description=(
        "Returns all available tools in OpenAI function-calling format. "
        "Paste this into any AI agent's tools array to give it access to your data source."
    ),
)
def get_manifest():
    return {"tools": TOOL_MANIFEST}


# ── Invoke ───────────────────────────────────────────────────────────────────

class InvokeRequest(BaseModel):
    tool: str = Field(..., description="Tool name from the manifest.")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments as a JSON object.")


class InvokeResponse(BaseModel):
    success: bool
    result: Any | None = None
    error: str | None = None


def _resolve_auth_context(authorization: str | None):
    try:
        return auth_service.get_optional_auth_context(authorization)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _resolve_from_args(args: dict, authorization: str | None = None) -> dict:
    """Build a simple namespace-like object from raw dict so registry.resolve works."""
    auth_context = _resolve_auth_context(authorization)

    class _Ns:
        pass
    ns = _Ns()
    ns.connection_id = args.get("connection_id")
    ns.source_kind = args.get("source_kind")
    ns.engine_key = args.get("engine_key")
    ns.db_type = args.get("db_type")
    ns.host = args.get("host")
    ns.port = args.get("port")
    ns.database = args.get("database")
    ns.username = args.get("username")
    ns.password = args.get("password") or ""
    ns.options = args.get("options") or {}
    resolved = registry.resolve(
        ns,
        organization_id=auth_context.organization_id if auth_context else None,
        user_id=auth_context.user_id if auth_context else None,
    )
    resolved.pop("db_type", None)
    resolved["_organization_id"] = auth_context.organization_id if auth_context else None
    resolved["_user_id"] = auth_context.user_id if auth_context else None
    return resolved


@router.post(
    "/invoke",
    response_model=InvokeResponse,
    summary="Invoke a Tool",
    description=(
        "Execute any tool from the manifest by name. "
        "Designed for AI agents: pass the tool name and arguments object exactly as returned by the LLM."
    ),
)
def invoke_tool(
    payload: InvokeRequest,
    authorization: str | None = Header(None),
) -> InvokeResponse:
    tool = payload.tool
    args = payload.arguments

    try:
        if tool == "test_connection":
            params = _resolve_from_args(args, authorization)
            params.pop("_organization_id", None)
            params.pop("_user_id", None)
            ok, msg = test_connection(**params)
            return InvokeResponse(success=ok, result={"message": msg})

        elif tool == "register_connection":
            auth_context = _resolve_auth_context(authorization)
            conn_id = registry.register_connection(
                name=args.get("name", "unnamed"),
                source_kind=args.get("source_kind"),
                engine_key=args.get("engine_key"),
                host=args.get("host"),
                port=args.get("port"),
                database=args.get("database"),
                username=args.get("username"),
                password=args.get("password"),
                options=args.get("options") or {},
                db_type=args.get("db_type"),
                organization_id=auth_context.organization_id if auth_context else None,
                user_id=auth_context.user_id if auth_context else None,
            )
            return InvokeResponse(success=True, result={"connection_id": conn_id, "name": args.get("name")})

        elif tool == "inspect_schema":
            params = _resolve_from_args(args, authorization)
            params.pop("_organization_id", None)
            params.pop("_user_id", None)
            schema = get_schema_metadata(**params)
            return InvokeResponse(success=True, result=schema)

        elif tool == "query_database":
            question = args.get("question", "")
            if not question:
                return InvokeResponse(success=False, error="'question' argument is required.")
            params = _resolve_from_args(args, authorization)
            organization_id = params.pop("_organization_id", None)
            user_id = params.pop("_user_id", None)
            result = execute_nl_query(
                question=question,
                connection_id=params.get("connection_id"),
                organization_id=organization_id,
                user_id=user_id,
                **params,
            )
            return InvokeResponse(success=result.get("success", False), result=result)

        elif tool == "run_sql":
            sql = args.get("sql", "")
            if not sql:
                return InvokeResponse(success=False, error="'sql' argument is required.")
            params = _resolve_from_args(args, authorization)
            params.pop("_organization_id", None)
            params.pop("_user_id", None)
            result = execute_sql_query(sql=sql, **params)
            return InvokeResponse(success=result.get("success", False), result=result)

        else:
            return InvokeResponse(success=False, error=f"Unknown tool '{tool}'. See GET /tools/manifest.")

    except ValueError as e:
        return InvokeResponse(success=False, error=str(e))
    except Exception as e:
        return InvokeResponse(success=False, error=clean_db_error_message(str(e)))
