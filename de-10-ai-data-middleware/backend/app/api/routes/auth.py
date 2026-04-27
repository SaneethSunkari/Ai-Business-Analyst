from fastapi import APIRouter, Header, Request, Response
import logging

from app.schemas.auth import LoginRequest, RefreshSessionRequest, SignUpRequest
from app.schemas.responses import AuthMeResponse, AuthSessionInfo, AuthSessionResponse, AuthUserInfo
from app.services import auth_service

router = APIRouter()
logger = logging.getLogger(__name__)

ACCESS_COOKIE = "adm_access_token"
REFRESH_COOKIE = "adm_refresh_token"
REFRESH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def _cookie_secure(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").strip().lower()
    if forwarded_proto:
        return forwarded_proto == "https"
    return request.url.scheme == "https"


def _set_session_cookies(request: Request, response: Response, session: dict) -> None:
    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    expires_in = session.get("expires_in") or 3600
    secure = _cookie_secure(request)

    if access_token:
        response.set_cookie(
            key=ACCESS_COOKIE,
            value=access_token,
            max_age=expires_in,
            httponly=True,
            samesite="lax",
            secure=secure,
            path="/",
        )

    if refresh_token:
        response.set_cookie(
            key=REFRESH_COOKIE,
            value=refresh_token,
            max_age=REFRESH_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=secure,
            path="/",
        )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


def _public_auth_error(exc: ValueError) -> str:
    message = str(exc)
    if any(
        token in message
        for token in (
            "SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_ANON_KEY",
            "CONTROL_PLANE_ENCRYPTION_KEY",
        )
    ):
        logger.warning("Auth configuration error: %s", message)
        return "Authentication is not configured for this environment. Contact your administrator."
    return message


def _read_access_token(authorization: str | None, request: Request) -> str | None:
    if authorization:
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    return request.cookies.get(ACCESS_COOKIE)


@router.post(
    "/signup",
    response_model=AuthSessionResponse,
    response_model_exclude_none=True,
    summary="Create Account",
    description="Create a new Supabase-authenticated user and return a signed-in session.",
)
def sign_up(payload: SignUpRequest, request: Request, response: Response) -> AuthSessionResponse:
    try:
        session = auth_service.sign_up_user(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
        )
    except ValueError as exc:
        return AuthSessionResponse(success=False, error=_public_auth_error(exc))

    _set_session_cookies(request, response, session)
    return AuthSessionResponse(success=True, session=AuthSessionInfo(**session))


@router.post(
    "/login",
    response_model=AuthSessionResponse,
    response_model_exclude_none=True,
    summary="Sign In",
    description="Authenticate an existing user with email and password.",
)
def log_in(payload: LoginRequest, request: Request, response: Response) -> AuthSessionResponse:
    try:
        session = auth_service.log_in_user(email=payload.email, password=payload.password)
    except ValueError as exc:
        return AuthSessionResponse(success=False, error=_public_auth_error(exc))

    _set_session_cookies(request, response, session)
    return AuthSessionResponse(success=True, session=AuthSessionInfo(**session))


@router.post(
    "/refresh",
    response_model=AuthSessionResponse,
    response_model_exclude_none=True,
    summary="Refresh Session",
    description="Exchange a refresh token for a fresh access token.",
)
def refresh_session(payload: RefreshSessionRequest, request: Request, response: Response) -> AuthSessionResponse:
    try:
        session = auth_service.refresh_user_session(payload.refresh_token)
    except ValueError as exc:
        return AuthSessionResponse(success=False, error=_public_auth_error(exc))

    _set_session_cookies(request, response, session)
    return AuthSessionResponse(success=True, session=AuthSessionInfo(**session))


@router.get(
    "/me",
    response_model=AuthMeResponse,
    response_model_exclude_none=True,
    summary="Current User",
    description="Validate the bearer token and return the active user plus workspace context.",
)
def current_user(
    request: Request,
    authorization: str | None = Header(None),
) -> AuthMeResponse:
    access_token = _read_access_token(authorization, request)
    try:
        context = auth_service.get_optional_auth_context(
            f"Bearer {access_token}" if access_token else None
        )
    except ValueError:
        return AuthMeResponse(authenticated=False)

    if not context:
        return AuthMeResponse(authenticated=False)

    return AuthMeResponse(
        authenticated=True,
        user=AuthUserInfo(
            id=context.user_id,
            email=context.email,
            full_name=context.full_name,
            organization_id=context.organization_id,
            organization_name=context.organization_name,
        ),
    )


@router.post(
    "/logout",
    summary="Sign Out",
    description="Invalidate the active Supabase access token.",
)
def log_out(
    request: Request,
    response: Response,
    authorization: str | None = Header(None),
):
    access_token = _read_access_token(authorization, request)
    try:
        context = auth_service.get_optional_auth_context(
            f"Bearer {access_token}" if access_token else None
        )
    except ValueError as exc:
        _clear_session_cookies(response)
        return {"success": False, "error": str(exc)}

    if not context or not access_token:
        _clear_session_cookies(response)
        return {"success": True}

    try:
        auth_service.log_out_user(access_token)
    except ValueError:
        # Logging out should be best-effort. The client will still clear local state.
        pass

    _clear_session_cookies(response)
    return {"success": True}
