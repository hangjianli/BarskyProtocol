from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_INTERVALS = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}


@dataclass(frozen=True)
class StudyConfig:
    config_path: Path
    data_dir: Path
    database: Path
    cards_dir: Path
    sources_dir: Path
    imports_dir: Path
    workspaces_dir: Path
    notebook_split_mode: str
    box_intervals: dict[int, int]
    scheduler: str
    concept_scheduler: str
    exercise_scheduler: str
    review_order: str
    llm_validator: str
    llm_model: str
    llm_auth_file: Path


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def find_config_path(start: Path | None = None) -> Path | None:
    env_value = os.environ.get("BARSKY_CONFIG")
    if env_value:
        env_path = Path(env_value).expanduser().resolve()
        if env_path.is_file():
            return env_path
        raise FileNotFoundError(f"BARSKY_CONFIG points to a missing file: {env_path}")

    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / "config.toml"
        if candidate.is_file():
            return candidate
    return None


def load_config(start: Path | None = None) -> StudyConfig:
    config_path = find_config_path(start)
    if config_path is None:
        raise FileNotFoundError(
            "No config.toml found. Run `python3 cli.py init` from the project root."
        )

    with config_path.open("rb") as handle:
        raw_config = tomllib.load(handle)

    study = raw_config.get("study", {})
    base_dir = config_path.parent
    raw_intervals = study.get("box_intervals", list(DEFAULT_INTERVALS.values()))
    if len(raw_intervals) != 5:
        raise ValueError("`study.box_intervals` must contain exactly 5 values.")

    box_intervals = {index + 1: days for index, days in enumerate(raw_intervals)}
    data_dir = _resolve_path(base_dir, study.get("data_dir", ".barsky"))

    return StudyConfig(
        config_path=config_path,
        data_dir=data_dir,
        database=_resolve_path(base_dir, study.get("database", ".barsky/study.db")),
        cards_dir=_resolve_path(base_dir, study.get("cards_dir", "cards")),
        sources_dir=_resolve_path(base_dir, study.get("sources_dir", ".barsky/sources")),
        imports_dir=_resolve_path(base_dir, study.get("imports_dir", ".barsky/imports")),
        workspaces_dir=_resolve_path(base_dir, study.get("workspaces_dir", ".barsky/workspaces")),
        notebook_split_mode=study.get("notebook_split_mode", "balanced"),
        box_intervals=box_intervals,
        scheduler=study.get("scheduler", "leitner_fallback"),
        concept_scheduler=study.get("concept_scheduler", "leitner_fallback"),
        exercise_scheduler=study.get("exercise_scheduler", "leitner_fallback"),
        review_order=study.get("review_order", "oldest-first"),
        llm_validator=study.get("llm_validator", "codex_oauth"),
        llm_model=study.get("llm_model", "gpt-4.1-mini"),
        llm_auth_file=_resolve_path(base_dir, study.get("llm_auth_file", "~/.codex/auth.json")),
    )
