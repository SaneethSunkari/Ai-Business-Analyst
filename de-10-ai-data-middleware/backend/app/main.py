from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
from app.api.routes.health import router as health_router
from app.api.routes.connections import router as connections_router
from app.api.routes.query import router as query_router
from app.api.routes.schema import router as schema_router
from app.api.routes.tools import router as tools_router
from app.schemas.responses import RootResponse

API_DESCRIPTION = """
AI Data Middleware is a small backend that connects to the demo PostgreSQL database,
inspects the imported 12 CSV tables, and turns simple English questions into safe
read-only SQL.

**Recommended local flow**
1. Use `POST /connections/test` to verify the demo database connection.
2. Use `POST /schema/scan` to inspect tables and inferred relationships.
3. Use `POST /query/ask` for natural-language questions.
4. Use `POST /query/run` when you want to send SQL manually.

**Important local note**
- On this machine, the Docker PostgreSQL database is on `localhost:5433`.

**Good starter questions**
- `Show the first 5 patients`
- `Show provider names and organization names`
- `List all medications and their total cost`
"""

TAGS_METADATA = [
    {
        "name": "health",
        "description": "Basic service health checks.",
    },
    {
        "name": "connections",
        "description": "Verify PostgreSQL credentials before running schema scans or queries.",
    },
    {
        "name": "schema",
        "description": "Inspect the real 12 CSV-backed tables and inferred relationships.",
    },
    {
        "name": "query",
        "description": "Run read-only SQL directly or ask questions in plain English.",
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

app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(connections_router, prefix="/connections", tags=["connections"])
app.include_router(query_router, prefix="/query", tags=["query"])
app.include_router(schema_router, prefix="/schema", tags=["schema"])
app.include_router(tools_router, prefix="/tools", tags=["tools"])

_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/ui", include_in_schema=False)
def serve_ui():
    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.get(
    "/",
    response_model=RootResponse,
    summary="API Status",
    description="Quick status check for the middleware API.",
)
def root() -> RootResponse:
    return RootResponse(message="AI Data Middleware is running")
