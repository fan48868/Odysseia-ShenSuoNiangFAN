# -*- coding: utf-8 -*-

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.runtime_env import load_project_dotenv


def test_load_project_dotenv_overrides_existing_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    project_root = tmp_path / "repo"
    entry_dir = project_root / "src"
    entry_dir.mkdir(parents=True, exist_ok=True)

    entry_file = entry_dir / "main.py"
    entry_file.write_text("# test entrypoint\n", encoding="utf-8")
    (project_root / ".env").write_text(
        'CUSTOM_MODEL_NAME="preset-model"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("CUSTOM_MODEL_NAME", "stale-container-value")

    dotenv_path = load_project_dotenv(str(entry_file), parents=1)

    assert dotenv_path == str(project_root / ".env")
    assert os.environ["CUSTOM_MODEL_NAME"] == "preset-model"
