from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
import os
from app.api.routes.auth import router as auth_router
from app.services import auth_service
from app.api.routes.health import router as health_router
from app.api.routes.connections import router as connections_router
from app.api.routes.query import router as query_router
from app.api.routes.schema import router as schema_router
from app.api.routes.tools import router as tools_router
from app.schemas.responses import RootResponse

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
app.include_router(connections_router, prefix="/connections", tags=["connections"])
app.include_router(query_router, prefix="/query", tags=["query"])
app.include_router(schema_router, prefix="/schema", tags=["schema"])
app.include_router(tools_router, prefix="/tools", tags=["tools"])

_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


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
    "/api/status",
    response_model=RootResponse,
    summary="API Status",
    description="Quick status check for the middleware API.",
)
def api_status() -> RootResponse:
    return RootResponse(message="AI Data Middleware is running")
