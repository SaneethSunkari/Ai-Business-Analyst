#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / "backend" / ".env")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def service_headers() -> dict[str, str]:
    key = env("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise SystemExit("SUPABASE_SERVICE_ROLE_KEY is required.")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def base_url() -> str:
    url = env("SUPABASE_URL")
    if not url:
        raise SystemExit("SUPABASE_URL is required.")
    return url.rstrip("/")


def create_or_get_user(email: str, password: str, full_name: str) -> str:
    payload = {
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": {"full_name": full_name},
    }
    create_resp = requests.post(
        f"{base_url()}/auth/v1/admin/users",
        headers=service_headers(),
        json=payload,
        timeout=30,
    )
    if create_resp.ok:
        return create_resp.json()["id"]

    list_resp = requests.get(
        f"{base_url()}/auth/v1/admin/users",
        headers=service_headers(),
        params={"page": 1, "per_page": 1000},
        timeout=30,
    )
    list_resp.raise_for_status()
    users = list_resp.json().get("users", [])
    for user in users:
        if (user.get("email") or "").lower() == email.lower():
            return user["id"]

    raise SystemExit(f"Could not create or find Supabase user for {email}: {create_resp.text}")


def rest_request(method: str, table: str, *, params=None, json_body=None, prefer: str | None = None):
    headers = service_headers()
    if prefer:
        headers["Prefer"] = prefer
    resp = requests.request(
        method,
        f"{base_url()}/rest/v1/{table}",
        headers=headers,
        params=params,
        json=json_body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp


def create_or_get_organization(user_id: str, name: str, slug: str) -> str:
    existing = rest_request(
        "GET",
        "organizations",
        params={"select": "id,slug", "slug": f"eq.{slug}", "limit": "1"},
    ).json()
    if existing:
        return existing[0]["id"]

    created = rest_request(
        "POST",
        "organizations",
        json_body={"name": name, "slug": slug, "created_by": user_id},
        prefer="return=representation",
    ).json()
    return created[0]["id"]


def ensure_membership(organization_id: str, user_id: str, role: str = "owner") -> None:
    existing = rest_request(
        "GET",
        "memberships",
        params={
            "select": "id,organization_id,user_id",
            "organization_id": f"eq.{organization_id}",
            "user_id": f"eq.{user_id}",
            "limit": "1",
        },
    ).json()
    if existing:
        return

    rest_request(
        "POST",
        "memberships",
        json_body={
            "organization_id": organization_id,
            "user_id": user_id,
            "role": role,
            "status": "active",
        },
        prefer="return=minimal",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap a Supabase control-plane owner and organization.")
    parser.add_argument("--email", default=env("CONTROL_PLANE_OWNER_EMAIL", "owner@example.com"))
    parser.add_argument("--password", default=env("CONTROL_PLANE_OWNER_PASSWORD", "ChangeMe123!"))
    parser.add_argument("--full-name", default=env("CONTROL_PLANE_OWNER_NAME", "Control Plane Owner"))
    parser.add_argument("--org-name", default=env("CONTROL_PLANE_ORGANIZATION_NAME", "Default Workspace"))
    parser.add_argument("--org-slug", default=env("CONTROL_PLANE_ORGANIZATION_SLUG", "default-workspace"))
    args = parser.parse_args()

    user_id = create_or_get_user(args.email, args.password, args.full_name)
    organization_id = create_or_get_organization(user_id, args.org_name, args.org_slug)
    ensure_membership(organization_id, user_id)

    print("Control plane bootstrap complete.")
    print(f"CONTROL_PLANE_ACTOR_USER_ID={user_id}")
    print(f"CONTROL_PLANE_ORGANIZATION_ID={organization_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
