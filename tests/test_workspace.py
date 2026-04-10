"""워크스페이스 유틸 테스트."""
from pathlib import Path
from src.utils.workspace import (
    ensure_workspace, list_input_files, read_files_as_context, get_output_dir,
)


def test_ensure_workspace_creates_dirs(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    assert (tmp_path / "instant" / "input").is_dir()
    assert (tmp_path / "instant" / "output").is_dir()


def test_list_input_files(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    inp = tmp_path / "instant" / "input"
    (inp / "data.csv").write_text("a,b\n1,2")
    (inp / "notes.txt").write_text("hello")
    files = list_input_files("instant", base=tmp_path)
    names = [f["name"] for f in files]
    assert "data.csv" in names
    assert "notes.txt" in names


def test_list_input_files_empty(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    assert list_input_files("instant", base=tmp_path) == []


def test_read_files_as_context_text(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    (tmp_path / "instant" / "input" / "report.csv").write_text("name,value\nfoo,42")
    ctx = read_files_as_context("instant", ["report.csv"], base=tmp_path)
    assert "report.csv" in ctx
    assert "foo,42" in ctx


def test_read_files_as_context_binary(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    (tmp_path / "instant" / "input" / "img.png").write_bytes(b"\x89PNG\r\n")
    ctx = read_files_as_context("instant", ["img.png"], base=tmp_path)
    assert "이미지" in ctx


def test_read_files_as_context_missing_file(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    assert read_files_as_context("instant", ["nope.txt"], base=tmp_path) == ""


def test_read_files_as_context_path_traversal(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    assert read_files_as_context("instant", ["../../etc/passwd"], base=tmp_path) == ""


def test_read_files_as_context_empty_list(tmp_path):
    assert read_files_as_context("instant", [], base=tmp_path) == ""


def test_get_output_dir(tmp_path):
    out = get_output_dir("instant", "abc123", base=tmp_path)
    assert out.is_dir()
    assert "abc123" in str(out)
