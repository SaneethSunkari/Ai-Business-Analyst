import atexit
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi import Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.routes import ops as ops_route_module
from app.api.routes.auth import router as auth_router
from app.api.routes.connections import router as connections_router
from app.api.routes.health import router as health_router
from app.api.routes.ops import router as ops_router
from app.api.routes.query import router as query_router
from app.api.routes.schema import router as schema_router
from app.api.routes.tools import router as tools_router
from app.schemas.responses import RootResponse
from app.services import auth_service, query_service
from app.services.dashboard_service import generate_dashboard_spec


_tokenfirewall_process: subprocess.Popen[str] | None = None


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_embedded_gateway_base() -> str:
    host = os.getenv("EMBEDDED_TOKENFIREWALL_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.getenv("TOKENFIREWALL_PORT", "8787").strip() or "8787"
    return f"http://{host}:{port}"


def _should_start_embedded_tokenfirewall() -> bool:
    if not _truthy_env("ENABLE_TOKENFIREWALL", "0"):
        return False
    if os.getenv("OPENAI_BASE_URL", "").strip() or os.getenv("TOKENFIREWALL_BASE_URL", "").strip():
        return False
    return True


def _terminate_embedded_tokenfirewall() -> None:
    global _tokenfirewall_process
    if _tokenfirewall_process is None:
        return
    if _tokenfirewall_process.poll() is None:
        _tokenfirewall_process.terminate()
        try:
            _tokenfirewall_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _tokenfirewall_process.kill()
            _tokenfirewall_process.wait(timeout=5)
    _tokenfirewall_process = None


def _start_embedded_tokenfirewall() -> None:
    global _tokenfirewall_process
    if _tokenfirewall_process is not None and _tokenfirewall_process.poll() is None:
        return

    base_url = _resolve_embedded_gateway_base()
    os.environ.setdefault("TOKENFIREWALL_BASE_URL", base_url)
    os.environ.setdefault("OPENAI_BASE_URL", f"{base_url}/v1")

    host = os.getenv("EMBEDDED_TOKENFIREWALL_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.getenv("TOKENFIREWALL_PORT", "8787").strip() or "8787"
    backend_root = Path(__file__).resolve().parents[1]
    working_dir = backend_root
    _tokenfirewall_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tokenfirewall",
            "server",
            "--host",
            host,
            "--port",
            port,
        ],
        cwd=str(working_dir),
        env=os.environ.copy(),
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )


if _should_start_embedded_tokenfirewall():
    _start_embedded_tokenfirewall()
    atexit.register(_terminate_embedded_tokenfirewall)

API_DESCRIPTION = """
AI Data Middleware connects to multiple data source engines, inspects schema metadata,
and turns plain-English questions into safe read-only SQL.

**Supported SQL and warehouse engines**
- PostgreSQL
- MySQL
- SQL Server
- SQLite
- Oracle
- Snowflake
- BigQuery
- Redshift

**Supported object storage engines**
- Amazon S3
- Azure Blob

**Recommended flow**
1. Use `GET /connections/types` to discover the fields required for each source engine.
2. Use `POST /connections/test` to verify credentials.
3. Use `POST /schema/scan` to inspect tables and relationships.
4. Use `POST /query/ask` for natural-language questions.
5. Use `POST /query/run` when you want to send SQL manually.

**Important local note**
- On this machine, the demo Docker PostgreSQL database is on `localhost:5433`.
"""

TAGS_METADATA = [
    {
        "name": "auth",
        "description": "Supabase-backed signup, login, and session validation.",
    },
    {
        "name": "health",
        "description": "Basic service health checks.",
    },
    {
        "name": "connections",
        "description": "Verify credentials, list supported source engines, and manage saved sources across databases, warehouses, and object stores.",
    },
    {
        "name": "schema",
        "description": "Inspect tables, columns, and inferred relationships for the connected source.",
    },
    {
        "name": "query",
        "description": "Run read-only SQL directly or ask questions in plain English across supported data sources.",
    },
    {
        "name": "ops",
        "description": "Runtime visibility for workspace readiness, cost controls, recent activity, and onboarding progress.",
    },
    {
        "name": "tools",
        "description": "Agent-compatible tool manifest and invoke endpoint (OpenAI function-calling format).",
    },
]

SWAGGER_UI_PARAMETERS = {
    "docExpansion": "none",
    "defaultModelsExpandDepth": -1,
    "displayRequestDuration": True,
    "filter": True,
    "tryItOutEnabled": True,
}

app = FastAPI(
    title="AI Data Middleware",
    version="0.1.0",
    description=API_DESCRIPTION,
    openapi_tags=TAGS_METADATA,
    swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(ops_router, prefix="/ops", tags=["ops"])
app.include_router(connections_router, prefix="/connections", tags=["connections"])
app.include_router(query_router, prefix="/query", tags=["query"])
app.include_router(schema_router, prefix="/schema", tags=["schema"])
app.include_router(tools_router, prefix="/tools", tags=["tools"])

_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

_demo_data_dir = Path(__file__).resolve().parent / "demo_data"
_demo_catalog = [
    {
        "id": "finance_demo",
        "name": "Finance Demo",
        "description": "General ledger, accounts, departments, and transaction activity for analytics validation.",
        "database_file": "finance.db",
        "tables": ["accounts", "transactions", "cost_centers", "departments"],
        "sample_questions": [
            "Total expenses by department this year",
            "Top 10 accounts by balance",
            "Monthly transaction volume last 6 months",
        ],
    },
    {
        "id": "retail_demo",
        "name": "Retail Demo",
        "description": "Products, customers, orders, and inventory for commerce analytics validation.",
        "database_file": "retail.db",
        "tables": ["products", "orders", "order_items", "customers", "inventory"],
        "sample_questions": [
            "Top 20 products by revenue",
            "Return rate by category",
            "Customer count by region",
        ],
    },
    {
        "id": "hr_demo",
        "name": "HR Demo",
        "description": "Employees, departments, salaries, and performance reviews for people analytics validation.",
        "database_file": "hr.db",
        "tables": ["employees", "departments", "salaries", "performance_reviews"],
        "sample_questions": [
            "Average salary by department",
            "Headcount by location",
            "Top performers by review score",
        ],
    },
    {
        "id": "saas_demo",
        "name": "SaaS Demo",
        "description": "Users, plans, subscriptions, invoices, and product events for recurring-revenue analytics validation.",
        "database_file": "saas.db",
        "tables": ["users", "subscriptions", "invoices", "events", "plans"],
        "sample_questions": [
            "Monthly recurring revenue by plan",
            "Active users last 30 days",
            "Churn count by month",
        ],
    },
]


def _read_access_token(authorization: str | None, request: Request) -> str | None:
    if authorization:
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    return request.cookies.get("adm_access_token")


def _require_authenticated_context(request: Request, authorization: str | None):
    access_token = _read_access_token(authorization, request)
    if not access_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        context = auth_service.get_auth_context_from_token(access_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if not context:
        raise HTTPException(status_code=401, detail="Authentication required")
    return context


def _load_redis_client() -> object | None:
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379").strip() or "redis://localhost:6379"
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(redis_url, decode_responses=True)
        client.ping()
        app.state.redis_client = client
        app.state.redis_available = True
        query_service.configure_query_cache(client, backend_name="redis")
        return client
    except Exception:
        app.state.redis_client = None
        app.state.redis_available = False
        query_service.configure_query_cache(None, backend_name="memory")
        return None


def _wrap_ops_status_builder() -> None:
    original_builder = ops_route_module.build_ops_status

    def _patched_build_ops_status(*args, **kwargs):
        payload = original_builder(*args, **kwargs)
        payload["query_cache"] = query_service.get_query_cache_stats()
        gateway_usage = payload.get("gateway_usage") or {}
        if isinstance(gateway_usage, dict):
            cache_stats = query_service.get_query_cache_stats()
            gateway_usage.setdefault("cache_hits", cache_stats["cache_hits"])
            gateway_usage.setdefault("total_requests", cache_stats["total_requests"])
            payload["gateway_usage"] = gateway_usage
        return payload

    ops_route_module.build_ops_status = _patched_build_ops_status


def _build_demo_source_payload() -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for demo in _demo_catalog:
        database_path = (_demo_data_dir / demo["database_file"]).resolve()
        sources.append(
            {
                "id": demo["id"],
                "name": demo["name"],
                "description": demo["description"],
                "source_kind": "database",
                "engine_key": "sqlite",
                "db_type": "sqlite",
                "badge": "SQLite",
                "database": str(database_path),
                "host": "",
                "port": None,
                "username": "",
                "password": "",
                "options": {},
                "tables": demo["tables"],
                "sample_questions": demo["sample_questions"],
                "connect_payload": {
                    "name": demo["name"],
                    "source_kind": "database",
                    "engine_key": "sqlite",
                    "db_type": "sqlite",
                    "database": str(database_path),
                    "host": "",
                    "port": None,
                    "username": "",
                    "password": "",
                    "options": {},
                },
            }
        )
    return sources


@app.on_event("startup")
def initialize_runtime_cache() -> None:
    _wrap_ops_status_builder()
    _load_redis_client()


@app.on_event("shutdown")
def shutdown_runtime_cache() -> None:
    redis_client = getattr(app.state, "redis_client", None)
    if redis_client is None:
        return
    close = getattr(redis_client, "close", None)
    if callable(close):
        close()


async def _read_response_body(response: Response) -> bytes:
    if hasattr(response, "body") and response.body is not None:
        return response.body
    chunks = [chunk async for chunk in response.body_iterator]
    return b"".join(chunks)


@app.middleware("http")
async def inject_runtime_cache_metadata(request: Request, call_next):
    response = await call_next(request)
    if request.url.path not in {"/query/ask", "/ops/status"}:
        return response
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        return response

    body = await _read_response_body(response)
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return Response(
            content=body,
            status_code=response.status_code,
            headers={key: value for key, value in response.headers.items() if key.lower() != "content-length"},
            media_type=response.media_type,
            background=response.background,
        )

    if request.url.path == "/query/ask" and isinstance(payload, dict):
        payload.update(query_service.get_last_query_cache_metadata())
    if request.url.path == "/ops/status" and isinstance(payload, dict):
        payload["query_cache"] = query_service.get_query_cache_stats()

    return Response(
        content=json.dumps(payload),
        status_code=response.status_code,
        headers={key: value for key, value in response.headers.items() if key.lower() != "content-length"},
        media_type="application/json",
        background=response.background,
    )


@app.delete("/cache/all", summary="Clear All Query Cache")
def clear_all_query_cache(
    request: Request,
    authorization: str | None = Header(None),
):
    _require_authenticated_context(request, authorization)
    return query_service.clear_all_cache()


@app.delete("/cache/{connection_id}", summary="Clear Connection Query Cache")
def clear_connection_query_cache(
    connection_id: str,
    request: Request,
    authorization: str | None = Header(None),
):
    _require_authenticated_context(request, authorization)
    return query_service.clear_connection_cache(connection_id)


@app.post("/dashboard/generate", include_in_schema=False)
def generate_dashboard_plan(
    payload: dict[str, Any],
    request: Request,
    authorization: str | None = Header(None),
):
    _require_authenticated_context(request, authorization)

    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required")

    columns = payload.get("columns") or []
    rows = payload.get("rows") or []
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="Columns and rows must be lists")

    dashboard = generate_dashboard_spec(
        question=question,
        columns=[str(column) for column in columns],
        rows=rows,
        source_question=payload.get("source_question"),
        current_dashboard=payload.get("current_dashboard") if isinstance(payload.get("current_dashboard"), dict) else None,
        schema_metadata=payload.get("schema_metadata") if isinstance(payload.get("schema_metadata"), dict) else None,
    )

    return {
        "success": True,
        "question": question,
        "dashboard": dashboard,
        "dashboard_plan": dashboard.get("dashboard_plan", {}),
        "follow_ups": dashboard.get("follow_ups", []),
        "summary": dashboard.get("summary"),
        "confidence": dashboard.get("confidence"),
    }


@app.get(
    "/demo",
    summary="List Demo Data Sources",
    description="Returns pre-configured cross-domain SQLite demo sources that can be connected without entering credentials.",
)
def list_demo_sources():
    return {
        "success": True,
        "connections": _build_demo_source_payload(),
    }


@app.get("/ui", include_in_schema=False)
def serve_ui(request: Request):
    access_token = request.cookies.get("adm_access_token")
    if not access_token:
        return RedirectResponse(url="/", status_code=303)

    try:
        context = auth_service.get_auth_context_from_token(access_token)
    except ValueError:
        return RedirectResponse(url="/", status_code=303)

    if not context:
        return RedirectResponse(url="/", status_code=303)

    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.get(
    "/",
    include_in_schema=False,
)
def serve_home():
    return FileResponse(os.path.join(_static_dir, "home.html"))


@app.get(
    "/auth",
    include_in_schema=False,
)
def serve_auth():
    return FileResponse(os.path.join(_static_dir, "auth.html"))


@app.get(
    "/api/status",
    response_model=RootResponse,
    summary="API Status",
    description="Quick status check for the middleware API.",
)
def api_status() -> RootResponse:
    return RootResponse(message="AI Data Middleware is running")
