"""Tests for AI DataLab sandbox — multiprocessing Python code runner."""

import os
from pathlib import Path

import pytest

from src.datalab.pipeline.sandbox import SandboxResult, run_code


class TestSimpleMath:
    def test_simple_math(self):
        """result = 2 + 3 → success, '5' in output."""
        res = run_code("result = 2 + 3")
        assert res.success is True
        assert "5" in res.output


class TestPandasAllowed:
    def test_pandas_allowed(self):
        """Import pandas, create DataFrame, describe() → success."""
        code = (
            "import pandas as pd\n"
            "df = pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})\n"
            "result = df.describe().to_string()\n"
        )
        res = run_code(code, timeout=15)
        assert res.success is True


class TestSubprocessBlocked:
    def test_subprocess_blocked(self):
        """import subprocess → NOT success, error mentions blocked/not allowed."""
        res = run_code("import subprocess")
        assert res.success is False
        assert "blocked" in res.error.lower() or "not allowed" in res.error.lower()


class TestSocketBlocked:
    def test_socket_blocked(self):
        """import socket → NOT success."""
        res = run_code("import socket")
        assert res.success is False


class TestTimeoutKillsProcess:
    def test_timeout_kills_process(self):
        """time.sleep(60) with timeout=2 → NOT success, 'timeout' in error."""
        code = "import time\ntime.sleep(60)"
        res = run_code(code, timeout=2)
        assert res.success is False
        assert "timeout" in res.error.lower()


class TestFileReadInSessionDir:
    def test_file_read_in_session_dir(self, tmp_path):
        """Write a CSV to tmpdir, read it with pandas in sandbox → success."""
        csv_file = tmp_path / "test_data.csv"
        csv_file.write_text("name,age\nAlice,30\nBob,25\n")

        code = (
            "import pandas as pd\n"
            f"df = pd.read_csv(r'{csv_file}')\n"
            "result = str(len(df))\n"
        )
        res = run_code(code, timeout=15, allowed_dir=str(tmp_path))
        assert res.success is True
        assert "2" in res.output


class TestDunderImportBlocked:
    def test_dunder_import_blocked(self):
        """__import__('subprocess') → NOT success."""
        res = run_code("__import__('subprocess')")
        assert res.success is False
