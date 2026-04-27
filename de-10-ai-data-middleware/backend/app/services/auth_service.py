from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple

import requests
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_TIMEOUT = 20


class AuthContext(NamedTuple):
    user_id: str
    email: str | None
    full_name: str | None
    organization_id: str | None
    organization_name: str | None


def _supabase_base_url() -> str:
    from os import getenv

    url = getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        raise ValueError("SUPABASE_URL is not configured.")
    return url


def _service_role_key() -> str:
    from os import getenv

    key = getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not key:
        raise ValueError("SUPABASE_SERVICE_ROLE_KEY is not configured.")
    return key


def _public_api_key() -> str:
    from os import getenv

    return getenv("SUPABASE_ANON_KEY", "").strip() or _service_role_key()


def _extract_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or f"Supabase request failed with status {response.status_code}."

    if isinstance(payload, dict):
        return (
            payload.get("msg")
            or payload.get("message")
            or payload.get("error_description")
            or payload.get("error")
            or str(payload)
        )
    return str(payload)


def _raise_if_not_ok(response: requests.Response, fallback: str) -> None:
    if response.ok:
        return
    raise ValueError(_extract_error_message(response) or fallback)


def _service_role_headers(prefer: str | None = None) -> dict[str, str]:
    key = _service_role_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _auth_headers(access_token: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": _public_api_key(),
        "Content-Type": "application/json",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    else:
        headers["Authorization"] = f"Bearer {_service_role_key()}"
    return headers


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "workspace"


def _first_membership(user_id: str) -> tuple[str | None, str | None]:
    base_url = _supabase_base_url()
    response = requests.get(
        f"{base_url}/rest/v1/memberships",
        headers=_service_role_headers(),
        params={
            "select": "organization_id,status,created_at",
            "user_id": f"eq.{user_id}",
            "status": "eq.active",
            "order": "created_at.asc",
            "limit": "1",
        },
        timeout=_TIMEOUT,
    )
    _raise_if_not_ok(response, "Failed to load user memberships.")
    rows = response.json()
    if not rows:
        return None, None

    organization_id = rows[0].get("organization_id")
    if not organization_id:
        return None, None

    org_response = requests.get(
        f"{base_url}/rest/v1/organizations",
        headers=_service_role_headers(),
        params={
            "select": "id,name",
            "id": f"eq.{organization_id}",
            "limit": "1",
        },
        timeout=_TIMEOUT,
    )
    _raise_if_not_ok(org_response, "Failed to load the user's organization.")
    org_rows = org_response.json()
    return organization_id, (org_rows[0].get("name") if org_rows else None)


def ensure_user_workspace(user_id: str, email: str | None, full_name: str | None) -> tuple[str, str]:
    organization_id, organization_name = _first_membership(user_id)
    if organization_id:
        return organization_id, organization_name or "Workspace"

    label = (full_name or (email or "Workspace").split("@")[0]).strip() or "Workspace"
    organization_name = f"{label}'s Workspace"
    slug = f"{_slugify(label)}-{user_id.split('-', 1)[0]}"

    base_url = _supabase_base_url()
    org_response = requests.post(
        f"{base_url}/rest/v1/organizations",
        headers=_service_role_headers(prefer="return=representation"),
        json={
            "name": organization_name,
            "slug": slug,
            "created_by": user_id,
        },
        timeout=_TIMEOUT,
    )
    _raise_if_not_ok(org_response, "Failed to create the user's workspace.")
    organization_id = org_response.json()[0]["id"]

    membership_response = requests.post(
        f"{base_url}/rest/v1/memberships",
        headers=_service_role_headers(prefer="return=minimal"),
        json={
            "organization_id": organization_id,
            "user_id": user_id,
            "role": "owner",
            "status": "active",
        },
        timeout=_TIMEOUT,
    )
    _raise_if_not_ok(membership_response, "Failed to create the user's workspace membership.")
    return organization_id, organization_name


def _context_from_user_payload(user: dict[str, Any]) -> AuthContext:
    metadata = user.get("user_metadata") or {}
    full_name = metadata.get("full_name") or metadata.get("name")
    organization_id, organization_name = ensure_user_workspace(
        user_id=user["id"],
        email=user.get("email"),
        full_name=full_name,
    )
    return AuthContext(
        user_id=user["id"],
        email=user.get("email"),
        full_name=full_name,
        organization_id=organization_id,
        organization_name=organization_name,
    )


def _session_payload(session: dict[str, Any], context: AuthContext) -> dict[str, Any]:
    return {
        "access_token": session.get("access_token"),
        "refresh_token": session.get("refresh_token"),
        "expires_in": session.get("expires_in"),
        "token_type": session.get("token_type"),
        "user": {
            "id": context.user_id,
            "email": context.email,
            "full_name": context.full_name,
            "organization_id": context.organization_id,
            "organization_name": context.organization_name,
        },
    }


def sign_up_user(email: str, password: str, full_name: str) -> dict[str, Any]:
    response = requests.post(
        f"{_supabase_base_url()}/auth/v1/admin/users",
        headers=_service_role_headers(),
        json={
            "email": email.strip(),
            "password": password,
            "email_confirm": True,
            "user_metadata": {"full_name": full_name.strip()},
        },
        timeout=_TIMEOUT,
    )
    _raise_if_not_ok(response, "Failed to create the user.")
    return log_in_user(email=email, password=password)


def log_in_user(email: str, password: str) -> dict[str, Any]:
    response = requests.post(
        f"{_supabase_base_url()}/auth/v1/token?grant_type=password",
        headers=_auth_headers(),
        json={"email": email.strip(), "password": password},
        timeout=_TIMEOUT,
    )
    _raise_if_not_ok(response, "Login failed.")
    session = response.json()
    access_token = session.get("access_token")
    if not access_token:
        raise ValueError("Login failed. Supabase did not return an access token.")
    context = get_auth_context_from_token(access_token)
    return _session_payload(session, context)


def refresh_user_session(refresh_token: str) -> dict[str, Any]:
    response = requests.post(
        f"{_supabase_base_url()}/auth/v1/token?grant_type=refresh_token",
        headers=_auth_headers(),
        json={"refresh_token": refresh_token},
        timeout=_TIMEOUT,
    )
    _raise_if_not_ok(response, "Session refresh failed.")
    session = response.json()
    access_token = session.get("access_token")
    if not access_token:
        raise ValueError("Session refresh failed. Supabase did not return an access token.")
    context = get_auth_context_from_token(access_token)
    return _session_payload(session, context)


def log_out_user(access_token: str) -> None:
    response = requests.post(
        f"{_supabase_base_url()}/auth/v1/logout",
        headers=_auth_headers(access_token),
        timeout=_TIMEOUT,
    )
    _raise_if_not_ok(response, "Logout failed.")


def get_auth_context_from_token(access_token: str) -> AuthContext:
    response = requests.get(
        f"{_supabase_base_url()}/auth/v1/user",
        headers=_auth_headers(access_token),
        timeout=_TIMEOUT,
    )
    _raise_if_not_ok(response, "Authentication failed.")
    return _context_from_user_payload(response.json())


def get_optional_auth_context(authorization: str | None) -> AuthContext | None:
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise ValueError("Invalid authorization header. Expected 'Bearer <token>'.")
    return get_auth_context_from_token(parts[1].strip())
