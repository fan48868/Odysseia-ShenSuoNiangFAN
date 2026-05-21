import os

from dotenv import load_dotenv


def resolve_project_root(file_path: str, *, parents: int) -> str:
    current = os.path.dirname(os.path.abspath(file_path))
    for _ in range(max(parents, 0)):
        current = os.path.dirname(current)
    return current


def load_project_dotenv(file_path: str, *, parents: int) -> str:
    project_root = resolve_project_root(file_path, parents=parents)
    dotenv_path = os.path.join(project_root, ".env")
    # In Docker Compose, container env vars are snapshotted when the container is
    # created. Overriding from the bind-mounted project .env keeps restarts in
    # sync with the latest on-disk config.
    load_dotenv(dotenv_path=dotenv_path, override=True, encoding="utf-8")
    return dotenv_path
