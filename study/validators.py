from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ValidationResult:
    result: str
    summary: str
    failing_tests: list[str]
    raw_output: str


def _extract_failing_tests(output: str) -> list[str]:
    failing: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAIL: "):
            failing.append(stripped.removeprefix("FAIL: ").split(" ", 1)[0])
        elif stripped.startswith("ERROR: "):
            failing.append(stripped.removeprefix("ERROR: ").split(" ", 1)[0])
    return failing


def run_exercise_tests(workspace_dir: Path) -> ValidationResult:
    completed = subprocess.run(
        [sys.executable, "tests.py", "-v"],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    raw_output = f"{completed.stdout}\n{completed.stderr}".strip()
    if completed.returncode == 0:
        return ValidationResult(
            result="pass",
            summary="All exercise tests passed.",
            failing_tests=[],
            raw_output=raw_output,
        )

    failing_tests = _extract_failing_tests(raw_output)
    if failing_tests:
        summary = f"{len(failing_tests)} test(s) failed: {', '.join(failing_tests)}."
    else:
        summary = "Exercise validation failed before reporting individual test names."
    return ValidationResult(
        result="fail",
        summary=summary,
        failing_tests=failing_tests,
        raw_output=raw_output,
    )
