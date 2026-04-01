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
    box_intervals: dict[int, int]
    review_order: str


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

    return StudyConfig(
        config_path=config_path,
        data_dir=_resolve_path(base_dir, study.get("data_dir", ".barsky")),
        database=_resolve_path(base_dir, study.get("database", ".barsky/study.db")),
        box_intervals=box_intervals,
        review_order=study.get("review_order", "oldest-first"),
    )
