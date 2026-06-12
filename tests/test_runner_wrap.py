"""CI entry point for the fleet runner-wrap shell tests.

The actual assertions live in tests/scripts/run-runner-wrap-tests.sh (hermetic:
temp fleet root, stubbed claude binary, no network/ADO/tmux). This wrapper only
makes the suite run under pytest, and pins ``FLEET_PYTHON`` to the interpreter
running the tests so the shell harness reaches an installed ``flotilla``.
"""

import os
from pathlib import Path
import subprocess
import sys

TEST_SCRIPT: Path = Path(__file__).resolve().parent / "scripts" / "run-runner-wrap-tests.sh"


def test_runner_wrap() -> None:
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["sh", str(TEST_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**os.environ, "FLEET_PYTHON": sys.executable},
    )
    assert result.returncode == 0, (
        f"runner-wrap tests failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
    )
