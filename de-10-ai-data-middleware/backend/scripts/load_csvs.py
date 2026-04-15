from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import psycopg2
from psycopg2 import sql


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load a directory of CSV files into PostgreSQL tables."
    )
    parser.add_argument("--csv-dir", type=Path, required=True)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5433)
    parser.add_argument("--database", default="demo_db")
    parser.add_argument("--username", default="postgres")
    parser.add_argument("--password", default="postgres")
    return parser.parse_args()


def get_csv_files(csv_dir: Path) -> list[Path]:
    csv_files = sorted(csv_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {csv_dir}")
    return csv_files


def read_headers(csv_path: Path) -> list[str]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file)
        try:
            headers = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{csv_path} is empty") from exc

    if not headers:
        raise ValueError(f"{csv_path} has no header row")

    return headers


def normalize_identifier(name: str) -> str:
    normalized = name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        raise ValueError(f"Could not normalize identifier from {name!r}")

    return normalized


def recreate_table(
    connection: psycopg2.extensions.connection,
    table_name: str,
    columns: list[str],
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table_name))
        )
        columns = sql.SQL(", ").join(
            sql.SQL("{} TEXT").format(sql.Identifier(column)) for column in columns
        )
        cursor.execute(
            sql.SQL("CREATE TABLE {} ({})").format(
                sql.Identifier(table_name),
                columns,
            )
        )


def copy_csv_into_table(
    connection: psycopg2.extensions.connection,
    csv_path: Path,
    table_name: str,
    columns: list[str],
) -> None:
    copy_statement = sql.SQL(
        "COPY {} ({}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE)"
    ).format(
        sql.Identifier(table_name),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
    )

    with connection.cursor() as cursor:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            cursor.copy_expert(copy_statement.as_string(connection), csv_file)


def fetch_row_count(
    connection: psycopg2.extensions.connection,
    table_name: str,
) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name))
        )
        row_count = cursor.fetchone()

    if row_count is None:
        return 0

    return int(row_count[0])


def main() -> None:
    args = parse_args()
    csv_files = get_csv_files(args.csv_dir)

    connection = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.database,
        user=args.username,
        password=args.password,
    )

    try:
        for csv_path in csv_files:
            table_name = csv_path.stem.lower()
            headers = read_headers(csv_path)
            columns = [normalize_identifier(header) for header in headers]
            print(f"Loading {csv_path.name} into {table_name}...")

            try:
                recreate_table(connection, table_name, columns)
                copy_csv_into_table(connection, csv_path, table_name, columns)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

            row_count = fetch_row_count(connection, table_name)
            print(f"Loaded {row_count} rows into {table_name}.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
