import argparse
import os
import subprocess
import sys

from dotenv import load_dotenv


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
load_dotenv(ENV_PATH, override=False, encoding="utf-8")


def run_compose_psql(db_service: str, user: str, database: str, sql: str) -> None:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        db_service,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        user,
        "-d",
        database,
        "-c",
        sql,
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def terminate_connections(db_service: str, user: str, target_db: str) -> None:
    sql = (
        "SELECT pg_terminate_backend(pid) "
        "FROM pg_stat_activity "
        f"WHERE datname = '{target_db}' AND pid <> pg_backend_pid();"
    )
    run_compose_psql(db_service, user, "postgres", sql)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fix PostgreSQL collation version mismatch warnings inside docker compose."
    )
    parser.add_argument("--db-service", default="db", help="Compose service name for PostgreSQL.")
    parser.add_argument(
        "--target-db",
        default=os.getenv("POSTGRES_DB", "braingirl_db"),
        help="Application database to repair.",
    )
    parser.add_argument(
        "--db-user",
        default=os.getenv("POSTGRES_USER", "user"),
        help="PostgreSQL user.",
    )
    parser.add_argument(
        "--refresh-only",
        action="store_true",
        help="Only refresh collation version metadata without reindexing.",
    )
    args = parser.parse_args()

    print("Preparing PostgreSQL collation warning fix...")
    print(f"Database service: {args.db_service}")
    print(f"Target database: {args.target_db}")
    print(f"User: {args.db_user}")

    if not args.refresh_only:
        print("Terminating active connections to target database...")
        terminate_connections(args.db_service, args.db_user, args.target_db)

        print("Reindexing target database...")
        run_compose_psql(
            args.db_service,
            args.db_user,
            args.target_db,
            f'REINDEX DATABASE "{args.target_db}";',
        )

        print("Reindexing postgres database...")
        run_compose_psql(
            args.db_service,
            args.db_user,
            "postgres",
            'REINDEX DATABASE "postgres";',
        )

    print("Refreshing collation version for target database...")
    run_compose_psql(
        args.db_service,
        args.db_user,
        "postgres",
        f'ALTER DATABASE "{args.target_db}" REFRESH COLLATION VERSION;',
    )

    print("Refreshing collation version for postgres database...")
    run_compose_psql(
        args.db_service,
        args.db_user,
        "postgres",
        'ALTER DATABASE "postgres" REFRESH COLLATION VERSION;',
    )

    print("Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}.", file=sys.stderr)
        raise SystemExit(exc.returncode)
