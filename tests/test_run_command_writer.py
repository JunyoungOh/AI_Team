"""run_command_writer 단위 테스트.

엔트리 감지 우선순위와 생성된 쉘 스크립트 골격을 검증.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from src.utils.run_command_writer import (
    RUN_COMMAND_FILENAME,
    EntryGuess,
    _render_script,
    detect_entry,
    write_run_command,
)


def _touch(path: Path, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_detect_flask_app(tmp_path: Path) -> None:
    _touch(tmp_path / "app.py", "from flask import Flask\napp = Flask(__name__)\napp.run(port=5050)\n")
    _touch(tmp_path / "requirements.txt", "flask\n")

    guess = detect_entry(tmp_path)
    assert guess is not None
    assert "Flask" in guess.label or "Python" in guess.label
    assert guess.command == "python app.py"
    assert guess.needs_python_venv is True
    assert guess.fallback_port == 5050


def test_detect_fastapi_uses_uvicorn(tmp_path: Path) -> None:
    _touch(tmp_path / "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")

    guess = detect_entry(tmp_path)
    assert guess is not None
    assert "FastAPI" in guess.label
    assert "uvicorn" in guess.command
    assert "main:app" in guess.command
    assert guess.fallback_port == 8000


def test_detect_django_manage_py(tmp_path: Path) -> None:
    _touch(tmp_path / "manage.py", "import django\n# django manage.py\n")

    guess = detect_entry(tmp_path)
    assert guess is not None
    assert "Django" in guess.label
    assert "runserver" in guess.command


def test_detect_node_prefers_package_json_dev_script(tmp_path: Path) -> None:
    _touch(
        tmp_path / "package.json",
        json.dumps({"scripts": {"start": "node server.js", "dev": "vite"}}),
    )
    _touch(tmp_path / "server.js", "require('http')")

    guess = detect_entry(tmp_path)
    assert guess is not None
    # dev 우선순위가 start보다 높음 (개발용 핫리로드 서버라서)
    assert guess.command == "npm run dev"
    assert guess.needs_node_install is True
    assert guess.needs_python_venv is False


def test_detect_node_falls_back_to_server_js_without_package_json(tmp_path: Path) -> None:
    _touch(tmp_path / "server.js", "// node server")

    guess = detect_entry(tmp_path)
    assert guess is not None
    assert guess.command == "node server.js"
    assert guess.needs_node_install is False  # package.json 없으므로 install 불필요


def test_detect_returns_none_for_empty_dir(tmp_path: Path) -> None:
    assert detect_entry(tmp_path) is None


def test_write_run_command_creates_executable_file(tmp_path: Path) -> None:
    _touch(tmp_path / "app.py", "print('hi')\n")
    _touch(tmp_path / "requirements.txt", "")

    target = write_run_command(tmp_path)
    assert target is not None
    assert target.name == RUN_COMMAND_FILENAME
    assert target.exists()

    # 실행 비트 확인
    mode = target.stat().st_mode
    assert mode & stat.S_IXUSR
    assert mode & stat.S_IXGRP
    assert mode & stat.S_IXOTH

    body = target.read_text(encoding="utf-8")
    assert body.startswith("#!/usr/bin/env bash")
    assert "SERVER_CMD=" in body
    assert "python app.py" in body
    # URL 자동 감지 패턴이 포함되어 있어야 함
    assert "localhost" in body
    assert "open" in body


def test_write_run_command_returns_none_for_unrecognized_dir(tmp_path: Path) -> None:
    _touch(tmp_path / "README.md", "hello")
    assert write_run_command(tmp_path) is None
    assert not (tmp_path / RUN_COMMAND_FILENAME).exists()


def test_write_run_command_overwrites_existing_file(tmp_path: Path) -> None:
    _touch(tmp_path / "app.py", "print('a')\n")
    target_file = tmp_path / RUN_COMMAND_FILENAME
    target_file.write_text("# stale content\n", encoding="utf-8")

    result = write_run_command(tmp_path)
    assert result is not None
    body = result.read_text(encoding="utf-8")
    assert "stale" not in body
    assert "SERVER_CMD=" in body


def test_write_run_command_handles_nonexistent_dir(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    assert write_run_command(missing) is None


def test_render_script_quotes_command_with_special_chars() -> None:
    """SERVER_CMD에 공백·따옴표·달러기호가 섞여 있어도 안전한 bash 문자열로 인용되어야 한다."""
    guess = EntryGuess(
        label="조작된 엔트리",
        command='echo "hi $USER" && node app.js',
        needs_python_venv=False,
        needs_node_install=False,
        fallback_port=4000,
    )

    body = _render_script(guess)
    # SERVER_CMD 라인이 큰따옴표로 시작·종료되고 내부 큰따옴표가 \" 로 이스케이프되어야 함
    assert 'SERVER_CMD="echo \\"hi $USER\\" && node app.js"' in body
    # bash 구문 검증: bash -n 으로 파싱만 해본다 (실행 안 함)
    import subprocess
    result = subprocess.run(
        ["bash", "-n"],
        input=body,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, f"bash 구문 오류: {result.stderr}"
