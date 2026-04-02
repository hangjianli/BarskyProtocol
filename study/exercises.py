from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from study.config import StudyConfig
from study.scheduler import to_iso, utc_now


@dataclass(frozen=True)
class ExerciseFiles:
    asset_dir: Path
    prompt_path: Path
    answer_path: Path
    solution_path: Path
    tests_path: Path


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "exercise"


def exercise_asset_dir(config: StudyConfig, *, topic: str, slug: str) -> Path:
    topic_dir = slugify(topic) if topic.strip() else "general"
    return config.cards_dir / topic_dir / slug


def scaffold_exercise_assets(
    config: StudyConfig,
    *,
    title: str,
    topic: str,
    prompt: str,
    slug: str | None = None,
    answer_body: str | None = None,
    solution_body: str | None = None,
    tests_body: str | None = None,
) -> ExerciseFiles:
    safe_slug = slugify(slug or title)
    asset_dir = exercise_asset_dir(config, topic=topic, slug=safe_slug)
    asset_dir.mkdir(parents=True, exist_ok=False)

    prompt_path = asset_dir / "prompt.md"
    answer_path = asset_dir / "answer.py"
    solution_path = asset_dir / "solution.py"
    tests_path = asset_dir / "tests.py"

    prompt_path.write_text(f"# {title}\n\n{prompt.strip()}\n", encoding="utf-8")
    answer_path.write_text(answer_body or _default_answer_body(), encoding="utf-8")
    solution_path.write_text(solution_body or _default_solution_body(), encoding="utf-8")
    tests_path.write_text(tests_body or _default_tests_body(), encoding="utf-8")

    return ExerciseFiles(
        asset_dir=asset_dir,
        prompt_path=prompt_path,
        answer_path=answer_path,
        solution_path=solution_path,
        tests_path=tests_path,
    )


def create_workspace(config: StudyConfig, *, attempt_id: int, asset_dir: Path) -> Path:
    # Each attempt gets an isolated copy so the canonical exercise files stay clean.
    workspace_dir = config.workspaces_dir / f"attempt-{attempt_id}"
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    shutil.copytree(asset_dir, workspace_dir)
    return workspace_dir


def cleanup_workspace(workspace_dir: Path) -> None:
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)


def _default_answer_body() -> str:
    return "\n".join(
        [
            '"""Implement the exercise in this file during review."""',
            "",
            "# Replace this placeholder with your implementation.",
            "raise NotImplementedError('Implement the exercise in answer.py')",
            "",
        ]
    )


def _default_solution_body() -> str:
    return "\n".join(
        [
            '"""Reference solution for the exercise."""',
            "",
            "# Fill in the canonical solution for this exercise.",
            "raise NotImplementedError('Write the reference solution in solution.py')",
            "",
        ]
    )


def _default_tests_body() -> str:
    return "\n".join(
        [
            "import unittest",
            "",
            "",
            "class ExerciseTests(unittest.TestCase):",
            "    def test_placeholder(self) -> None:",
            "        # Replace this with the minimal contract for the exercise.",
            "        self.fail('Replace the placeholder test in tests.py with the real exercise contract.')",
            "",
            "",
            "if __name__ == '__main__':",
            "    unittest.main()",
            "",
        ]
    )
