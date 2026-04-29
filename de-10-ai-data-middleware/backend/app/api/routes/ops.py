from fastapi import APIRouter, Header, Request

from app.schemas.responses import OpsStatusResponse
from app.services import auth_service
from app.services.ops_service import build_ops_status

router = APIRouter()


def _read_access_token(authorization: str | None, request: Request) -> str | None:
    if authorization:
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    return request.cookies.get("adm_access_token")


@router.get(
    "/status",
    response_model=OpsStatusResponse,
    response_model_exclude_none=True,
    summary="Workspace Ops Status",
    description="Return runtime, cost-control, onboarding, and recent workspace activity summary for the current environment.",
)
def ops_status(
    request: Request,
    authorization: str | None = Header(None),
) -> OpsStatusResponse:
    access_token = _read_access_token(authorization, request)
    try:
        context = auth_service.get_optional_auth_context(
            f"Bearer {access_token}" if access_token else None
        )
    except ValueError:
        context = None

    return OpsStatusResponse(
        **build_ops_status(
            authenticated=bool(context),
            organization_id=context.organization_id if context else None,
            user_id=context.user_id if context else None,
        )
    )
