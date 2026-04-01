from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from study.config import load_config
from study.storage import add_card, due_cards, ensure_storage, recent_cards, review_card, stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="BarskyProtocol",
        description="A local CLI for Leitner-style spaced repetition study.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize storage and schema.")
    init_parser.set_defaults(handler=handle_init)

    add_parser = subparsers.add_parser("add", help="Add a study card.")
    add_parser.add_argument("--prompt", help="Question or recall prompt.")
    add_parser.add_argument("--answer", help="Expected answer.")
    add_parser.add_argument("--topic", default="", help="Topic name for filtering.")
    add_parser.add_argument("--tags", default="", help="Comma-separated tags.")
    add_parser.add_argument("--source", default="", help="Where this card came from.")
    add_parser.set_defaults(handler=handle_add)

    due_parser = subparsers.add_parser("due", help="List due cards.")
    due_parser.add_argument("--limit", type=int, default=20, help="Max cards to show.")
    due_parser.add_argument("--topic", help="Only show cards in one topic.")
    due_parser.set_defaults(handler=handle_due)

    review_parser = subparsers.add_parser("review", help="Review due cards interactively.")
    review_parser.add_argument("--limit", type=int, default=20, help="Max cards in this session.")
    review_parser.add_argument("--topic", help="Only review cards in one topic.")
    review_parser.add_argument("--shuffle", action="store_true", help="Shuffle the due queue.")
    review_parser.set_defaults(handler=handle_review)

    list_parser = subparsers.add_parser("list", help="List recently added cards.")
    list_parser.add_argument("--limit", type=int, default=20, help="Max cards to show.")
    list_parser.set_defaults(handler=handle_list)

    stats_parser = subparsers.add_parser("stats", help="Show queue statistics.")
    stats_parser.set_defaults(handler=handle_stats)

    return parser


def resolve_config() -> object:
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


def handle_add(args: argparse.Namespace) -> int:
    config = resolve_config()
    ensure_storage(config)

    prompt = prompt_if_missing(args.prompt, "Prompt")
    answer = prompt_if_missing(args.answer, "Answer")
    card_id = add_card(
        config,
        prompt=prompt,
        answer=answer,
        topic=args.topic,
        tags=split_tags(args.tags),
        source=args.source,
    )
    print(f"Added card {card_id}")
    return 0


def format_card_line(card: object) -> str:
    topic = card["topic"] or "-"
    tags = ",".join(json.loads(card["tags"])) or "-"
    return (
        f"[{card['id']}] box={card['box']} topic={topic} tags={tags} "
        f"due={card['next_review_at']} prompt={card['prompt']}"
    )


def handle_due(args: argparse.Namespace) -> int:
    config = resolve_config()
    ensure_storage(config)
    cards = due_cards(config, limit=args.limit, topic=args.topic)
    if not cards:
        print("No cards are due.")
        return 0

    for card in cards:
        print(format_card_line(card))
    return 0


def handle_review(args: argparse.Namespace) -> int:
    config = resolve_config()
    ensure_storage(config)
    cards = due_cards(config, limit=args.limit, topic=args.topic)
    if not cards:
        print("No cards are due.")
        return 0

    if args.shuffle:
        random.shuffle(cards)

    correct_count = 0
    wrong_count = 0

    for index, card in enumerate(cards, start=1):
        print()
        print(f"Card {index}/{len(cards)} | id={card['id']} | box={card['box']} | topic={card['topic'] or '-'}")
        print(f"Prompt: {card['prompt']}")
        input("Press Enter to reveal the answer...")
        print(f"Answer: {card['answer']}")

        while True:
            choice = input("[g]ood / [a]gain / [q]uit: ").strip().lower()
            if choice in {"g", "a", "q"}:
                break
            print("Enter `g`, `a`, or `q`.")

        if choice == "q":
            break

        result = "correct" if choice == "g" else "wrong"
        outcome = review_card(config, card_id=int(card["id"]), result=result)

        if result == "correct":
            correct_count += 1
        else:
            wrong_count += 1

        print(
            f"Updated: box {outcome.prior_box} -> {outcome.new_box}; "
            f"next due {outcome.next_review_at}"
        )

    print()
    print(f"Session complete. correct={correct_count} wrong={wrong_count}")
    return 0


def handle_list(args: argparse.Namespace) -> int:
    config = resolve_config()
    ensure_storage(config)
    cards = recent_cards(config, limit=args.limit)
    if not cards:
        print("No cards found.")
        return 0

    for card in cards:
        print(format_card_line(card))
    return 0


def handle_stats(args: argparse.Namespace) -> int:
    config = resolve_config()
    ensure_storage(config)
    snapshot = stats(config)
    print(f"Total cards: {snapshot['total_cards']}")
    print(f"Due now: {snapshot['due_now']}")
    print(f"Reviewed today: {snapshot['reviewed_today']}")
    print("Cards by box:")
    for box, count in snapshot["by_box"].items():
        print(f"  box {box}: {count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))
