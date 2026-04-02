from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from study.config import StudyConfig
from study.exercises import slugify


@dataclass(frozen=True)
class NotebookCandidate:
    title: str
    prompt: str
    topic: str
    solution_code: str
    answer_template: str
    tests_template: str
    source_cell_spec: str
    cell_indexes: list[int]


@dataclass(frozen=True)
class NotebookImportDraft:
    draft_id: str
    source_mode: str
    source_path: str
    source_label: str
    topic: str
    notebook_title: str
    markdown_cells: int
    code_cells: int
    candidates: list[NotebookCandidate]


def build_import_draft(
    config: StudyConfig,
    *,
    source_path: str,
    source_mode: str,
    source_label: str,
    topic: str,
    notebook_text: str,
) -> NotebookImportDraft:
    notebook = json.loads(notebook_text)
    cells = notebook.get("cells", [])
    notebook_title = _infer_notebook_title(cells, source_label)
    candidates = parse_notebook_candidates(cells, default_topic=topic)
    draft_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    draft = NotebookImportDraft(
        draft_id=draft_id,
        source_mode=source_mode,
        source_path=source_path,
        source_label=source_label,
        topic=topic.strip(),
        notebook_title=notebook_title,
        markdown_cells=sum(1 for cell in cells if cell.get("cell_type") == "markdown"),
        code_cells=sum(1 for cell in cells if cell.get("cell_type") == "code"),
        candidates=candidates,
    )
    save_import_draft(config, draft)
    return draft


def save_managed_notebook(
    config: StudyConfig,
    *,
    source_label: str,
    notebook_text: str,
) -> Path:
    stem = slugify(Path(source_label).stem or "notebook")
    target = config.sources_dir / f"{stem}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.ipynb"
    target.write_text(notebook_text, encoding="utf-8")
    return target


def load_notebook_text_from_path(notebook_path: str) -> tuple[Path, str]:
    path = Path(notebook_path).expanduser().resolve()
    if path.suffix != ".ipynb":
        raise ValueError("Notebook path must point to a .ipynb file.")
    if not path.is_file():
        raise ValueError("Notebook path does not exist.")
    return path, path.read_text(encoding="utf-8")


def load_import_draft(config: StudyConfig, draft_id: str) -> NotebookImportDraft:
    draft_path = config.imports_dir / f"{draft_id}.json"
    if not draft_path.is_file():
        raise ValueError("Notebook import draft was not found.")
    payload = json.loads(draft_path.read_text(encoding="utf-8"))
    return NotebookImportDraft(
        draft_id=str(payload["draft_id"]),
        source_mode=str(payload["source_mode"]),
        source_path=str(payload["source_path"]),
        source_label=str(payload["source_label"]),
        topic=str(payload["topic"]),
        notebook_title=str(payload["notebook_title"]),
        markdown_cells=int(payload["markdown_cells"]),
        code_cells=int(payload["code_cells"]),
        candidates=[NotebookCandidate(**candidate) for candidate in payload["candidates"]],
    )


def delete_import_draft(config: StudyConfig, draft_id: str) -> None:
    draft_path = config.imports_dir / f"{draft_id}.json"
    if draft_path.exists():
        draft_path.unlink()


def save_import_draft(config: StudyConfig, draft: NotebookImportDraft) -> None:
    payload = {
        "draft_id": draft.draft_id,
        "source_mode": draft.source_mode,
        "source_path": draft.source_path,
        "source_label": draft.source_label,
        "topic": draft.topic,
        "notebook_title": draft.notebook_title,
        "markdown_cells": draft.markdown_cells,
        "code_cells": draft.code_cells,
        "candidates": [asdict(candidate) for candidate in draft.candidates],
    }
    (config.imports_dir / f"{draft.draft_id}.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def parse_notebook_candidates(cells: list[dict], *, default_topic: str = "") -> list[NotebookCandidate]:
    candidates: list[NotebookCandidate] = []
    current_title = ""
    current_notes: list[str] = []
    current_code: list[str] = []
    current_indexes: list[int] = []

    def flush_candidate() -> None:
        nonlocal current_title, current_notes, current_code, current_indexes
        code = "\n\n".join(block for block in current_code if block.strip()).strip()
        if not code:
            current_title = ""
            current_notes = []
            current_code = []
            current_indexes = []
            return

        title = current_title or _infer_code_title(code, len(candidates) + 1)
        cell_spec = _format_cell_spec(current_indexes)
        prompt = _build_prompt(title, current_notes, code, cell_spec)
        names = _infer_top_level_names(code)
        candidates.append(
            NotebookCandidate(
                title=title,
                prompt=prompt,
                topic=default_topic.strip(),
                solution_code=f"{code}\n",
                answer_template=_build_answer_template(title, names, cell_spec),
                tests_template=_build_tests_template(names, cell_spec),
                source_cell_spec=cell_spec,
                cell_indexes=list(current_indexes),
            )
        )
        current_title = ""
        current_notes = []
        current_code = []
        current_indexes = []

    for index, cell in enumerate(cells, start=1):
        cell_type = str(cell.get("cell_type", ""))
        raw_source = _cell_source(cell)
        if not raw_source:
            continue

        if cell_type == "markdown":
            heading, body = _extract_heading(raw_source)
            if heading:
                flush_candidate()
                current_title = heading
                current_indexes = [index]
                if body:
                    current_notes.append(body)
            else:
                current_notes.append(raw_source)
                current_indexes.append(index)
        elif cell_type == "code":
            current_code.append(raw_source)
            current_indexes.append(index)

    flush_candidate()
    return candidates


def _cell_source(cell: dict) -> str:
    source = cell.get("source", [])
    if isinstance(source, list):
        return "".join(str(part) for part in source).strip()
    return str(source).strip()


def _extract_heading(markdown: str) -> tuple[str | None, str]:
    lines = markdown.splitlines()
    if not lines:
        return None, ""
    first_line = lines[0].strip()
    match = re.match(r"^#{1,6}\s+(.*)$", first_line)
    if not match:
        return None, markdown.strip()
    heading = match.group(1).strip()
    body = "\n".join(lines[1:]).strip()
    return heading, body


def _infer_notebook_title(cells: list[dict], fallback_label: str) -> str:
    for cell in cells:
        if cell.get("cell_type") != "markdown":
            continue
        heading, _ = _extract_heading(_cell_source(cell))
        if heading:
            return heading
    return Path(fallback_label).stem or "Notebook Import"


def _infer_code_title(code: str, index: int) -> str:
    names = _infer_top_level_names(code)
    if names:
        return f"Reimplement {names[0]}"
    return f"Notebook Exercise {index}"


def _infer_top_level_names(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return names


def _format_cell_spec(indexes: list[int]) -> str:
    if not indexes:
        return ""
    ordered = sorted(set(indexes))
    ranges: list[str] = []
    start = ordered[0]
    end = ordered[0]
    for value in ordered[1:]:
        if value == end + 1:
            end = value
            continue
        ranges.append(f"{start}" if start == end else f"{start}-{end}")
        start = end = value
    ranges.append(f"{start}" if start == end else f"{start}-{end}")
    return f"cells {', '.join(ranges)}"


def _build_prompt(title: str, notes: list[str], code: str, cell_spec: str) -> str:
    prompt_lines = [
        f"Reimplement the notebook section `{title}` as a standalone Python script.",
    ]
    note_text = "\n\n".join(note for note in notes if note.strip()).strip()
    if note_text:
        prompt_lines.extend(["", "Context:", note_text])
    prompt_lines.extend(
        [
            "",
            "Focus on reproducing the core logic from memory.",
            f"Source section: {cell_spec or 'unknown cells'}",
            "",
            "The reference implementation is preserved in `solution.py`.",
        ]
    )
    return "\n".join(prompt_lines).strip()


def _build_answer_template(title: str, names: list[str], cell_spec: str) -> str:
    lines = [
        f'"""Reimplement the notebook-derived exercise: {title}."""',
        "",
        f"# Source section: {cell_spec or 'unknown cells'}",
    ]
    if names:
        lines.append("# Recreate these top-level objects from memory:")
        lines.extend(f"# - {name}" for name in names)
    lines.extend(
        [
            "# Replace this placeholder with your implementation.",
            "raise NotImplementedError('Reimplement the exercise in answer.py')",
            "",
        ]
    )
    return "\n".join(lines)


def _build_tests_template(names: list[str], cell_spec: str) -> str:
    focus = ", ".join(names) if names else "the imported notebook logic"
    return "\n".join(
        [
            "import unittest",
            "",
            "",
            "class ExerciseTests(unittest.TestCase):",
            "    def test_placeholder(self) -> None:",
            f"        # Replace this with a minimal contract for {focus}.",
            f"        self.fail('Replace the placeholder tests for {focus} extracted from {cell_spec or 'the notebook source'}.')",
            "",
            "",
            "if __name__ == '__main__':",
            "    unittest.main()",
            "",
        ]
    )
