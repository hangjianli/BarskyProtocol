from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from study.config import StudyConfig
from study.exercises import slugify
from study.grading import GradingError, _call_json_llm


@dataclass(frozen=True)
class NotebookCandidate:
    title: str
    prompt: str
    topic: str
    tags: list[str]
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
    source_kind: str
    topic: str
    split_mode: str
    source_title: str
    prose_sections: int
    code_sections: int
    candidates: list[NotebookCandidate]


@dataclass(frozen=True)
class SupportBlock:
    code: str
    indexes: list[int]
    provided_names: set[str]
    referenced_names: set[str]


def build_import_draft(
    config: StudyConfig,
    *,
    source_path: str,
    source_mode: str,
    source_label: str,
    source_kind: str,
    topic: str,
    split_mode: str,
    source_text: str,
    draft_id: str | None = None,
) -> NotebookImportDraft:
    source_title, prose_sections, code_sections, candidates = parse_source_candidates(
        source_text,
        source_kind=source_kind,
        source_label=source_label,
        default_topic=topic,
        split_mode=split_mode,
    )
    candidates = enrich_candidate_metadata(config, candidates, default_topic=topic.strip())
    resolved_draft_id = draft_id or datetime.now().strftime("%Y%m%d%H%M%S%f")
    draft = NotebookImportDraft(
        draft_id=resolved_draft_id,
        source_mode=source_mode,
        source_path=source_path,
        source_label=source_label,
        source_kind=source_kind,
        topic=topic.strip(),
        split_mode=split_mode,
        source_title=source_title,
        prose_sections=prose_sections,
        code_sections=code_sections,
        candidates=candidates,
    )
    save_import_draft(config, draft)
    return draft


def enrich_candidate_metadata(
    config: StudyConfig,
    candidates: list[NotebookCandidate],
    *,
    default_topic: str,
) -> list[NotebookCandidate]:
    if not candidates:
        return candidates

    try:
        response = _call_json_llm(
            config,
            system_prompt=(
                "You suggest metadata for coding exercise cards. "
                "Return strict JSON with a top-level key `candidates`. "
                "Each candidate must include `topic` and `tags`. "
                "`topic` should be a short study area label. "
                "`tags` should be a short list of 1-4 concise technical tags."
            ),
            user_prompt=_build_metadata_prompt(candidates, default_topic=default_topic),
        )
        raw_candidates = response.content["candidates"]
        if not isinstance(raw_candidates, list) or len(raw_candidates) != len(candidates):
            raise GradingError("Notebook metadata response did not match the candidate count.")
    except (GradingError, KeyError, TypeError):
        return candidates

    enriched: list[NotebookCandidate] = []
    for candidate, metadata in zip(candidates, raw_candidates, strict=True):
        topic = default_topic
        tags: list[str] = []
        if isinstance(metadata, dict):
            topic = str(metadata.get("topic") or default_topic).strip() or default_topic
            raw_tags = metadata.get("tags", [])
            if isinstance(raw_tags, list):
                tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()][:4]
        enriched.append(
            NotebookCandidate(
                title=candidate.title,
                prompt=candidate.prompt,
                topic=topic,
                tags=tags,
                solution_code=candidate.solution_code,
                answer_template=candidate.answer_template,
                tests_template=candidate.tests_template,
                source_cell_spec=candidate.source_cell_spec,
                cell_indexes=candidate.cell_indexes,
            )
        )
    return enriched


def save_managed_source(
    config: StudyConfig,
    *,
    source_label: str,
    source_text: str,
    source_kind: str,
) -> Path:
    stem = slugify(Path(source_label).stem or "source")
    suffix = _normalized_source_kind(source_kind)
    target = config.sources_dir / f"{stem}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.{suffix}"
    target.write_text(source_text, encoding="utf-8")
    return target


def load_source_text_from_path(source_path: str) -> tuple[Path, str, str]:
    path = Path(source_path).expanduser().resolve()
    source_kind = _infer_source_kind_from_path(path)
    if source_kind is None:
        raise ValueError("Source path must point to a .ipynb or .py file.")
    if not path.is_file():
        raise ValueError("Source path does not exist.")
    return path, path.read_text(encoding="utf-8"), source_kind


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
        source_kind=str(payload.get("source_kind", "ipynb")),
        topic=str(payload["topic"]),
        split_mode=str(payload.get("split_mode", "balanced")),
        source_title=str(payload.get("source_title", payload.get("notebook_title", payload["source_label"]))),
        prose_sections=int(payload.get("prose_sections", payload.get("markdown_cells", 0))),
        code_sections=int(payload.get("code_sections", payload.get("code_cells", 0))),
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
        "source_kind": draft.source_kind,
        "topic": draft.topic,
        "split_mode": draft.split_mode,
        "source_title": draft.source_title,
        "prose_sections": draft.prose_sections,
        "code_sections": draft.code_sections,
        "candidates": [asdict(candidate) for candidate in draft.candidates],
    }
    (config.imports_dir / f"{draft.draft_id}.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def parse_source_candidates(
    source_text: str,
    *,
    source_kind: str,
    source_label: str,
    default_topic: str = "",
    split_mode: str = "balanced",
) -> tuple[str, int, int, list[NotebookCandidate]]:
    if _normalized_source_kind(source_kind) == "py":
        return parse_python_candidates(
            source_text,
            source_label=source_label,
            default_topic=default_topic,
            split_mode=split_mode,
        )

    notebook = json.loads(source_text)
    cells = notebook.get("cells", [])
    return (
        _infer_notebook_title(cells, source_label),
        sum(1 for cell in cells if cell.get("cell_type") == "markdown"),
        sum(1 for cell in cells if cell.get("cell_type") == "code"),
        parse_notebook_candidates(cells, default_topic=default_topic, split_mode=split_mode),
    )


def parse_notebook_candidates(
    cells: list[dict],
    *,
    default_topic: str = "",
    split_mode: str = "balanced",
) -> list[NotebookCandidate]:
    if split_mode == "aggressive":
        return _parse_aggressive_candidates(cells, default_topic=default_topic)
    return _parse_balanced_candidates(cells, default_topic=default_topic)


def parse_python_candidates(
    source_text: str,
    *,
    source_label: str,
    default_topic: str = "",
    split_mode: str = "balanced",
) -> tuple[str, int, int, list[NotebookCandidate]]:
    title = Path(source_label).stem or "Python Source Import"
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return title, 0, 1 if source_text.strip() else 0, _build_python_fallback_candidate(
            source_text,
            default_topic=default_topic,
            source_label=source_label,
        )

    docstring = (ast.get_docstring(tree) or "").strip()
    body = _python_body_without_docstring(tree)
    if split_mode == "aggressive":
        candidates = _parse_aggressive_python_candidates(
            source_text,
            body,
            default_topic=default_topic,
            source_label=source_label,
            module_notes=[docstring] if docstring else [],
        )
    else:
        candidates = _parse_balanced_python_candidates(
            source_text,
            body,
            default_topic=default_topic,
            source_label=source_label,
            module_notes=[docstring] if docstring else [],
        )
    code_sections = len([node for node in body if _node_source(source_text, node).strip()])
    return title, 1 if docstring else 0, code_sections, candidates


def _parse_balanced_candidates(cells: list[dict], *, default_topic: str = "") -> list[NotebookCandidate]:
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

        title = _infer_code_title(code, len(candidates) + 1) if _infer_top_level_names(code) else (current_title or _infer_code_title(code, len(candidates) + 1))
        cell_spec = _format_cell_spec(current_indexes)
        prompt = _build_prompt(title, current_notes, code, cell_spec, aggressive=False)
        names = _infer_top_level_names(code)
        candidates.append(
            NotebookCandidate(
                title=title,
                prompt=prompt,
                topic=default_topic.strip(),
                tags=[],
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


def _parse_aggressive_candidates(cells: list[dict], *, default_topic: str = "") -> list[NotebookCandidate]:
    candidates: list[NotebookCandidate] = []
    current_title = ""
    current_notes: list[str] = []
    setup_blocks: list[SupportBlock] = []
    prior_blocks: list[SupportBlock] = []

    for index, cell in enumerate(cells, start=1):
        cell_type = str(cell.get("cell_type", ""))
        raw_source = _cell_source(cell)
        if not raw_source:
            continue

        if cell_type == "markdown":
            heading, body = _extract_heading(raw_source)
            if heading:
                current_title = heading
                current_notes = [body] if body else []
                setup_blocks = []
                prior_blocks = []
            else:
                current_notes.append(raw_source)
            continue

        if cell_type != "code":
            continue

        # In aggressive mode, pure import cells are treated as context instead of
        # becoming one-line exercises on their own.
        if _is_setup_only_code(raw_source):
            setup_blocks.append(_build_support_block(raw_source, indexes=[index]))
            continue

        support_blocks = _resolve_support_blocks(raw_source, [*setup_blocks, *prior_blocks])
        support_code = [block.code for block in support_blocks]
        support_indexes = [value for block in support_blocks for value in block.indexes]
        cell_indexes = support_indexes + [index]
        notes = list(current_notes)
        title = _infer_code_title(raw_source, len(candidates) + 1)
        cell_spec = _format_cell_spec(cell_indexes)
        names = _infer_top_level_names(raw_source)
        full_solution = "\n\n".join(block for block in [*support_code, raw_source] if block.strip()).strip()
        candidates.append(
            NotebookCandidate(
                title=title,
                prompt=_build_prompt(
                    title,
                    notes,
                    raw_source,
                    cell_spec,
                    has_support_context=bool(support_code),
                    aggressive=True,
                ),
                topic=default_topic.strip(),
                tags=[],
                solution_code=f"{full_solution}\n",
                answer_template=_build_answer_template(title, names, cell_spec, support_code=support_code),
                tests_template=_build_tests_template(names, cell_spec),
                source_cell_spec=cell_spec,
                cell_indexes=cell_indexes,
            )
        )
        prior_blocks.append(_build_support_block(raw_source, indexes=[index]))

    return candidates


def _parse_balanced_python_candidates(
    source_text: str,
    body: list[ast.stmt],
    *,
    default_topic: str,
    source_label: str,
    module_notes: list[str],
) -> list[NotebookCandidate]:
    code = source_text.strip()
    if not code:
        return []

    names = _infer_top_level_names(code)
    title = _infer_python_title(code, source_label, fallback_index=1)
    line_spec = _format_line_spec(1, len(source_text.splitlines()))
    return [
        NotebookCandidate(
            title=title,
            prompt=_build_prompt(title, module_notes, code, line_spec, source_kind="py"),
            topic=default_topic.strip(),
            tags=[],
            solution_code=f"{code}\n",
            answer_template=_build_answer_template(title, names, line_spec),
            tests_template=_build_tests_template(names, line_spec),
            source_cell_spec=line_spec,
            cell_indexes=[],
        )
    ]


def _parse_aggressive_python_candidates(
    source_text: str,
    body: list[ast.stmt],
    *,
    default_topic: str,
    source_label: str,
    module_notes: list[str],
) -> list[NotebookCandidate]:
    candidates: list[NotebookCandidate] = []
    prior_blocks: list[SupportBlock] = []

    for node in body:
        node_source = _node_source(source_text, node).strip()
        if not node_source:
            continue

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            support_blocks = _resolve_support_blocks(node_source, prior_blocks)
            support_code = [block.code for block in support_blocks]
            title = _infer_python_title(node_source, source_label, fallback_index=len(candidates) + 1)
            line_spec = _format_line_spec(node.lineno, getattr(node, "end_lineno", node.lineno))
            names = _infer_top_level_names(node_source)
            candidates.append(
                NotebookCandidate(
                    title=title,
                    prompt=_build_prompt(
                        title,
                        module_notes,
                        node_source,
                        line_spec,
                        source_kind="py",
                        has_support_context=bool(support_code),
                    ),
                    topic=default_topic.strip(),
                    tags=[],
                    solution_code=_join_code_blocks([*support_code, node_source]),
                    answer_template=_build_answer_template(title, names, line_spec, support_code=support_code),
                    tests_template=_build_tests_template(names, line_spec),
                    source_cell_spec=line_spec,
                    cell_indexes=[],
                )
            )

        prior_blocks.append(
            _build_support_block(
                node_source,
                indexes=[getattr(node, "lineno", 1)],
            )
        )

    if candidates:
        return candidates
    return _build_python_fallback_candidate(
        source_text,
        default_topic=default_topic,
        source_label=source_label,
        module_notes=module_notes,
    )


def _build_python_fallback_candidate(
    source_text: str,
    *,
    default_topic: str,
    source_label: str,
    module_notes: list[str] | None = None,
) -> list[NotebookCandidate]:
    code = source_text.strip()
    if not code:
        return []

    title = _infer_python_title(code, source_label, fallback_index=1)
    names = _infer_top_level_names(code)
    line_spec = _format_line_spec(1, len(source_text.splitlines()))
    return [
        NotebookCandidate(
            title=title,
            prompt=_build_prompt(title, module_notes or [], code, line_spec, source_kind="py"),
            topic=default_topic.strip(),
            tags=[],
            solution_code=f"{code}\n",
            answer_template=_build_answer_template(title, names, line_spec),
            tests_template=_build_tests_template(names, line_spec),
            source_cell_spec=line_spec,
            cell_indexes=[],
        )
    ]


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


def _infer_python_title(code: str, source_label: str, *, fallback_index: int) -> str:
    names = _infer_top_level_names(code)
    if names:
        return f"Reimplement {names[0]}"
    return Path(source_label).stem or f"Python Exercise {fallback_index}"


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


def _build_support_block(code: str, *, indexes: list[int]) -> SupportBlock:
    return SupportBlock(
        code=code,
        indexes=indexes,
        provided_names=_provided_names(code),
        referenced_names=_referenced_names(code),
    )


def _resolve_support_blocks(current_code: str, prior_blocks: list[SupportBlock]) -> list[SupportBlock]:
    if not prior_blocks:
        return []

    required_names = set(_referenced_names(current_code))
    selected_indexes: set[int] = set()
    changed = True
    while changed:
        changed = False
        for index, block in enumerate(prior_blocks):
            if index in selected_indexes:
                continue
            if not block.provided_names.intersection(required_names):
                continue
            selected_indexes.add(index)
            required_names.update(block.referenced_names)
            changed = True

    return [block for index, block in enumerate(prior_blocks) if index in selected_indexes]


def _provided_names(code: str) -> set[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    provided: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            provided.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                provided.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    provided.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                provided.update(_extract_assigned_names(target))
        elif isinstance(node, ast.AnnAssign):
            provided.update(_extract_assigned_names(node.target))
    return provided


def _extract_assigned_names(target: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def _referenced_names(code: str) -> set[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    loaded: set[str] = set()
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
            elif isinstance(node.ctx, ast.Store):
                bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    return loaded - bound


def _is_setup_only_code(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    if not tree.body:
        return True
    return all(isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body)


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


def _format_line_spec(start_line: int, end_line: int) -> str:
    if start_line <= 0 or end_line <= 0:
        return "lines unknown"
    if start_line == end_line:
        return f"lines {start_line}"
    return f"lines {start_line}-{end_line}"


def _join_code_blocks(blocks: list[str]) -> str:
    return "\n\n".join(block.strip() for block in blocks if block.strip()).strip() + "\n"


def _normalized_source_kind(source_kind: str) -> str:
    normalized = source_kind.strip().lower().lstrip(".")
    if normalized not in {"ipynb", "py"}:
        raise ValueError("Import only supports .ipynb and .py sources.")
    return normalized


def _infer_source_kind_from_path(path: Path) -> str | None:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"ipynb", "py"}:
        return suffix
    return None


def _python_body_without_docstring(tree: ast.Module) -> list[ast.stmt]:
    body = list(tree.body)
    if not body:
        return []
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return body[1:]
    return body


def _node_source(source_text: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source_text, node)
    if segment is not None:
        return segment.strip()
    lines = source_text.splitlines()
    start = getattr(node, "lineno", 1) - 1
    end = getattr(node, "end_lineno", getattr(node, "lineno", 1))
    return "\n".join(lines[start:end]).strip()


def _clean_note_block(note: str) -> str:
    cleaned = re.sub(r"<img[^>]*>", "", note, flags=re.IGNORECASE)
    lines: list[str] = []
    skipping_troubleshooting = False
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if re.match(r"^#{1,6}\s+", line) and "troubleshooting" not in lowered and "ssl" not in lowered:
            skipping_troubleshooting = False
        if "troubleshooting" in lowered or "ssl" in lowered and "error" in lowered:
            skipping_troubleshooting = True
            continue
        if skipping_troubleshooting:
            continue
        if not line or line in {"---", "***", "<br>", "&nbsp;"}:
            continue
        if line.startswith("<img") or line.startswith("!["):
            continue
        if "pip install --upgrade certifi" in lowered or "uv pip install --upgrade certifi" in lowered:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _select_context_notes(notes: list[str], *, aggressive: bool = False) -> list[str]:
    cleaned_notes = [_clean_note_block(note) for note in notes]
    useful = [note for note in cleaned_notes if note]
    if not useful:
        return []

    selected = useful[-1:] if aggressive else useful[:2]
    trimmed: list[str] = []
    for note in selected:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", note) if part.strip()]
        if aggressive:
            paragraphs = paragraphs[:1]
        trimmed.append("\n\n".join(paragraphs[:2]).strip())
    return [note for note in trimmed if note]


def _build_prompt(
    title: str,
    notes: list[str],
    code: str,
    cell_spec: str,
    *,
    source_kind: str = "ipynb",
    has_support_context: bool = False,
    aggressive: bool = False,
) -> str:
    names = _infer_top_level_names(code)
    focus = ", ".join(f"`{name}`" for name in names[:2])
    source_label = "source section" if source_kind == "py" else "notebook section"
    prompt_lines = [
        f"Reimplement {focus or f'`{title}`'} as a standalone Python script.",
    ]
    note_text = "\n\n".join(_select_context_notes(notes, aggressive=aggressive)).strip()
    if note_text:
        prompt_lines.extend(["", "Context:", note_text])
    if has_support_context:
        prompt_lines.extend(["", "Supporting runtime context is already preserved in the exercise files."])
    prompt_lines.extend(
        [
            "",
            "Focus on reproducing the core logic from memory.",
            f"Source {source_label}: {cell_spec or 'unknown section'}",
            "",
            "The reference implementation is preserved in `solution.py`.",
        ]
    )
    return "\n".join(prompt_lines).strip()


def _build_answer_template(
    title: str,
    names: list[str],
    cell_spec: str,
    *,
    support_code: list[str] | None = None,
) -> str:
    lines = [
        f'"""Reimplement the imported exercise: {title}."""',
        "",
        f"# Source section: {cell_spec or 'unknown cells'}",
    ]
    if support_code:
        lines.extend(
            [
                "# Supporting context preserved so this exercise stays standalone.",
                "",
                "\n\n".join(support_code).rstrip(),
                "",
            ]
        )
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
            f"        self.fail('Replace the placeholder tests for {focus} extracted from {cell_spec or 'the imported source'}.')",
            "",
            "",
            "if __name__ == '__main__':",
            "    unittest.main()",
            "",
        ]
    )


def _build_metadata_prompt(candidates: list[NotebookCandidate], *, default_topic: str) -> str:
    sections: list[str] = [f"Default topic: {default_topic or 'none'}", "", "Candidates:"]
    for index, candidate in enumerate(candidates, start=1):
        sections.extend(
            [
                f"{index}.",
                f"Title: {candidate.title}",
                f"Prompt: {candidate.prompt}",
                f"Source cells: {candidate.source_cell_spec}",
                "",
            ]
        )
    sections.append(
        "Return JSON only in the form "
        '{"candidates":[{"topic":"...","tags":["..."]}]}'
    )
    return "\n".join(sections)
