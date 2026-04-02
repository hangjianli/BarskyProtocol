from __future__ import annotations

import json
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from study.config import StudyConfig
from study.exercises import scaffold_exercise_assets
from study.storage import add_concept_card, add_exercise_card, delete_card


class CardContractError(ValueError):
    """Raised when a pasted card contract is malformed."""


@dataclass(frozen=True)
class ConceptContractCard:
    title: str
    prompt: str
    answer: str
    topic: str
    tags: list[str]
    source: str
    source_path: str
    source_mode: str
    source_label: str
    source_kind: str
    source_cell_spec: str
    source_import_options: str


@dataclass(frozen=True)
class ExerciseContractCard:
    title: str
    prompt: str
    answer_py: str
    solution_py: str
    tests_py: str
    topic: str
    tags: list[str]
    source: str
    source_path: str
    source_mode: str
    source_label: str
    source_kind: str
    source_cell_spec: str
    source_import_options: str
    slug: str


def import_cards_from_contract(config: StudyConfig, contract_text: str) -> list[int]:
    cards = parse_card_contract(contract_text)
    created_ids: list[int] = []
    created_asset_dirs: list[Path] = []
    try:
        for card in cards:
            if isinstance(card, ConceptContractCard):
                created_ids.append(
                    add_concept_card(
                        config,
                        title=card.title,
                        prompt=card.prompt,
                        answer=card.answer,
                        topic=card.topic,
                        tags=card.tags,
                        source=card.source,
                        source_path=card.source_path,
                        source_mode=card.source_mode,
                        source_label=card.source_label,
                        source_kind=card.source_kind,
                        source_cell_spec=card.source_cell_spec,
                        source_import_options=card.source_import_options,
                    )
                )
                continue

            files = scaffold_exercise_assets(
                config,
                title=card.title,
                topic=card.topic,
                prompt=card.prompt,
                slug=card.slug or None,
                answer_body=card.answer_py,
                solution_body=card.solution_py,
                tests_body=card.tests_py,
            )
            created_asset_dirs.append(files.asset_dir)
            created_ids.append(
                add_exercise_card(
                    config,
                    title=card.title,
                    topic=card.topic,
                    tags=card.tags,
                    source=card.source,
                    source_path=card.source_path,
                    source_mode=card.source_mode,
                    source_label=card.source_label,
                    source_kind=card.source_kind,
                    source_cell_spec=card.source_cell_spec,
                    source_import_options=card.source_import_options,
                    files=files,
                )
            )
    except Exception:
        # Multi-card imports should not leave a partially created deck behind.
        for card_id in reversed(created_ids):
            delete_card(config, card_id)
        for asset_dir in created_asset_dirs:
            shutil.rmtree(asset_dir, ignore_errors=True)
        raise
    return created_ids


def parse_card_contract(contract_text: str) -> list[ConceptContractCard | ExerciseContractCard]:
    try:
        payload = tomllib.loads(contract_text)
    except tomllib.TOMLDecodeError as exc:
        raise CardContractError(f"Contract is not valid TOML: {exc}") from exc

    if not isinstance(payload, dict):
        raise CardContractError("Contract root must be a TOML table.")

    allowed_root_keys = {"version", "cards"}
    unknown_root = set(payload) - allowed_root_keys
    if unknown_root:
        raise CardContractError(f"Unknown root keys: {', '.join(sorted(unknown_root))}")

    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list) or not raw_cards:
        raise CardContractError("Contract must contain at least one [[cards]] entry.")

    parsed_cards: list[ConceptContractCard | ExerciseContractCard] = []
    for index, raw_card in enumerate(raw_cards, start=1):
        if not isinstance(raw_card, dict):
            raise CardContractError(f"Card {index} must be a TOML table.")
        parsed_cards.append(_parse_contract_card(raw_card, index=index))
    return parsed_cards


def _parse_contract_card(raw_card: dict, *, index: int) -> ConceptContractCard | ExerciseContractCard:
    card_type = _require_str(raw_card, "type", index=index)
    if card_type == "concept":
        allowed = {
            "type",
            "title",
            "prompt",
            "answer",
            "topic",
            "tags",
            "source",
            "source_path",
            "source_mode",
            "source_label",
            "source_kind",
            "source_cell_spec",
            "source_import_options",
        }
        _reject_unknown_fields(raw_card, allowed, index=index)
        return ConceptContractCard(
            title=_require_str(raw_card, "title", index=index),
            prompt=_require_str(raw_card, "prompt", index=index),
            answer=_require_str(raw_card, "answer", index=index),
            topic=_optional_str(raw_card, "topic"),
            tags=_parse_tags(raw_card.get("tags"), index=index),
            source=_optional_str(raw_card, "source"),
            source_path=_optional_str(raw_card, "source_path"),
            source_mode=_optional_str(raw_card, "source_mode"),
            source_label=_optional_str(raw_card, "source_label"),
            source_kind=_optional_str(raw_card, "source_kind"),
            source_cell_spec=_optional_str(raw_card, "source_cell_spec"),
            source_import_options=_parse_import_options(raw_card.get("source_import_options")),
        )

    if card_type == "code_exercise":
        allowed = {
            "type",
            "title",
            "prompt",
            "answer_py",
            "solution_py",
            "tests_py",
            "topic",
            "tags",
            "source",
            "source_path",
            "source_mode",
            "source_label",
            "source_kind",
            "source_cell_spec",
            "source_import_options",
            "slug",
        }
        _reject_unknown_fields(raw_card, allowed, index=index)
        return ExerciseContractCard(
            title=_require_str(raw_card, "title", index=index),
            prompt=_require_str(raw_card, "prompt", index=index),
            answer_py=_require_str(raw_card, "answer_py", index=index),
            solution_py=_require_str(raw_card, "solution_py", index=index),
            tests_py=_require_str(raw_card, "tests_py", index=index),
            topic=_optional_str(raw_card, "topic"),
            tags=_parse_tags(raw_card.get("tags"), index=index),
            source=_optional_str(raw_card, "source"),
            source_path=_optional_str(raw_card, "source_path"),
            source_mode=_optional_str(raw_card, "source_mode"),
            source_label=_optional_str(raw_card, "source_label"),
            source_kind=_optional_str(raw_card, "source_kind"),
            source_cell_spec=_optional_str(raw_card, "source_cell_spec"),
            source_import_options=_parse_import_options(raw_card.get("source_import_options")),
            slug=_optional_str(raw_card, "slug"),
        )

    raise CardContractError(f"Card {index} has unsupported type: {card_type}")


def _reject_unknown_fields(raw_card: dict, allowed: set[str], *, index: int) -> None:
    unknown = set(raw_card) - allowed
    if unknown:
        raise CardContractError(f"Card {index} has unknown fields: {', '.join(sorted(unknown))}")


def _require_str(raw_card: dict, key: str, *, index: int) -> str:
    value = raw_card.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CardContractError(f"Card {index} requires a non-empty `{key}` field.")
    return value.strip()


def _optional_str(raw_card: dict, key: str) -> str:
    value = raw_card.get(key, "")
    if value == "":
        return ""
    if not isinstance(value, str):
        raise CardContractError(f"`{key}` must be a string when provided.")
    return value.strip()


def _parse_tags(raw_tags: object, *, index: int) -> list[str]:
    if raw_tags in (None, ""):
        return []
    if isinstance(raw_tags, str):
        return [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
    if isinstance(raw_tags, list):
        cleaned = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
        if not cleaned:
            return []
        return cleaned
    raise CardContractError(f"Card {index} has an invalid `tags` field.")


def _parse_import_options(raw_value: object) -> str:
    if raw_value in (None, ""):
        return ""
    if isinstance(raw_value, str):
        return raw_value.strip()
    return json.dumps(raw_value, sort_keys=True)
