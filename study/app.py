from __future__ import annotations

import argparse
from pathlib import Path

from study.config import StudyConfig, load_config
from study.storage import add_concept_card, dashboard_stats, ensure_storage
from study.web import serve_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="BarskyProtocol",
        description="Local-first spaced repetition for concepts and coding drills.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize storage and schema.")
    init_parser.set_defaults(handler=handle_init)

    add_parser = subparsers.add_parser("add-concept", help="Add a concept card.")
    add_parser.add_argument("--title", help="Short card title shown in the UI.")
    add_parser.add_argument("--topic", default="", help="Topic name for filtering.")
    add_parser.add_argument("--tags", default="", help="Comma-separated tags.")
    add_parser.add_argument("--source", default="", help="Where this card came from.")
    add_parser.add_argument("--prompt", help="Question or recall prompt.")
    add_parser.add_argument("--answer", help="Expected answer.")
    add_parser.set_defaults(handler=handle_add_concept)

    serve_parser = subparsers.add_parser("serve", help="Run the local web app.")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    serve_parser.add_argument("--port", type=int, default=8427, help="Port to bind.")
    serve_parser.set_defaults(handler=handle_serve)

    stats_parser = subparsers.add_parser("stats", help="Show queue statistics.")
    stats_parser.set_defaults(handler=handle_stats)

    return parser


def resolve_config() -> StudyConfig:
    return load_config(Path.cwd())


def split_tags(raw_tags: str) -> list[str]:
    return [tag.strip() for tag in raw_tags.split(",") if tag.strip()]


def prompt_if_missing(value: str | None, label: str) -> str:
    if value:
        return value
    return input(f"{label}: ").strip()


def handle_init(args: argparse.Namespace) -> int:
    config = resolve_config()
    ensure_storage(config)
    print(f"Initialized study database at {config.database}")
    return 0


def handle_add_concept(args: argparse.Namespace) -> int:
    config = resolve_config()
    ensure_storage(config)

    prompt = prompt_if_missing(args.prompt, "Prompt")
    answer = prompt_if_missing(args.answer, "Answer")
    title = args.title or prompt[:80]
    card_id = add_concept_card(
        config,
        title=title,
        prompt=prompt,
        answer=answer,
        topic=args.topic,
        tags=split_tags(args.tags),
        source=args.source,
    )
    print(f"Added concept card {card_id}")
    return 0


def handle_serve(args: argparse.Namespace) -> int:
    config = resolve_config()
    ensure_storage(config)
    serve_app(config=config, host=args.host, port=args.port)
    return 0


def handle_stats(args: argparse.Namespace) -> int:
    config = resolve_config()
    ensure_storage(config)
    snapshot = dashboard_stats(config)
    print(f"Total cards: {snapshot.total_cards}")
    print(f"Due now: {snapshot.due_now}")
    print(f"Overdue: {snapshot.overdue}")
    print("Recent reviews:")
    print(f"  pass: {snapshot.recent_results['pass']}")
    print(f"  fail: {snapshot.recent_results['fail']}")
    print(f"  incomplete: {snapshot.recent_results['incomplete']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))
