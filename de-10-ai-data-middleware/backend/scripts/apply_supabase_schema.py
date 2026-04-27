#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg2
import requests


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MIGRATION = ROOT_DIR / "supabase" / "migrations" / "20260426_000001_control_plane.sql"


def load_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def apply_via_postgres(db_url: str, sql_text: str) -> None:
    with psycopg2.connect(db_url) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(sql_text)
        conn.commit()


def apply_via_management_api(project_ref: str, management_pat: str, sql_text: str) -> None:
    response = requests.post(
        f"https://api.supabase.com/v1/projects/{project_ref}/database/query",
        headers={
            "Authorization": f"Bearer {management_pat}",
            "Content-Type": "application/json",
        },
        json={"query": sql_text, "read_only": False},
        timeout=60,
    )
    response.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the control-plane Supabase schema.")
    parser.add_argument(
        "--sql-file",
        default=str(DEFAULT_MIGRATION),
        help="Path to the SQL migration file.",
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("SUPABASE_DB_URL", ""),
        help="Direct Postgres connection URL for the Supabase project.",
    )
    parser.add_argument(
        "--project-ref",
        default=os.getenv("SUPABASE_PROJECT_REF", ""),
        help="Supabase project ref, used with the Management API path.",
    )
    parser.add_argument(
        "--management-pat",
        default=os.getenv("SUPABASE_MANAGEMENT_PAT", ""),
        help="Supabase Personal Access Token for the Management API.",
    )
    args = parser.parse_args()

    sql_path = Path(args.sql_file).resolve()
    if not sql_path.exists():
        raise SystemExit(f"SQL file not found: {sql_path}")

    sql_text = load_sql(sql_path)

    if args.db_url:
        apply_via_postgres(args.db_url, sql_text)
        print(f"Applied schema via direct Postgres connection using {sql_path}.")
        return 0

    if args.project_ref and args.management_pat:
        apply_via_management_api(args.project_ref, args.management_pat, sql_text)
        print(f"Applied schema via Supabase Management API for project {args.project_ref}.")
        return 0

    raise SystemExit(
        "No supported Supabase admin credential path was provided. "
        "Set SUPABASE_DB_URL or both SUPABASE_PROJECT_REF and SUPABASE_MANAGEMENT_PAT. "
        "Note: the service_role key is not enough to run DDL."
    )


if __name__ == "__main__":
    raise SystemExit(main())
