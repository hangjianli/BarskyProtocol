from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable

from study.config import StudyConfig


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt TEXT NOT NULL,
    answer TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '',
    box INTEGER NOT NULL DEFAULT 1 CHECK(box BETWEEN 1 AND 5),
    lapse_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_reviewed_at TEXT,
    next_review_at TEXT NOT NULL,
    last_result TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL,
    reviewed_at TEXT NOT NULL,
    result TEXT NOT NULL CHECK(result IN ('correct', 'wrong')),
    prior_box INTEGER NOT NULL,
    new_box INTEGER NOT NULL,
    next_review_at TEXT NOT NULL,
    FOREIGN KEY(card_id) REFERENCES cards(id)
);

CREATE INDEX IF NOT EXISTS idx_cards_next_review_at ON cards(next_review_at);
CREATE INDEX IF NOT EXISTS idx_reviews_reviewed_at ON reviews(reviewed_at);
"""


@dataclass(frozen=True)
class ReviewOutcome:
    card_id: int
    prompt: str
    answer: str
    topic: str
    prior_box: int
    new_box: int
    next_review_at: str
    result: str


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def connect(config: StudyConfig) -> sqlite3.Connection:
    connection = sqlite3.connect(config.database)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_storage(config: StudyConfig) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.database.parent.mkdir(parents=True, exist_ok=True)
    with connect(config) as connection:
        connection.executescript(SCHEMA)


def add_card(
    config: StudyConfig,
    *,
    prompt: str,
    answer: str,
    topic: str = "",
    tags: Iterable[str] = (),
    source: str = "",
) -> int:
    now = utc_now()
    payload = {
        "prompt": prompt.strip(),
        "answer": answer.strip(),
        "topic": topic.strip(),
        "tags": json.dumps([tag for tag in (value.strip() for value in tags) if tag]),
        "source": source.strip(),
        "created_at": to_iso(now),
        "updated_at": to_iso(now),
        "next_review_at": to_iso(now),
    }

    with connect(config) as connection:
        cursor = connection.execute(
            """
            INSERT INTO cards (
                prompt, answer, topic, tags, source,
                created_at, updated_at, next_review_at
            ) VALUES (
                :prompt, :answer, :topic, :tags, :source,
                :created_at, :updated_at, :next_review_at
            )
            """,
            payload,
        )
        return int(cursor.lastrowid)


def due_cards(config: StudyConfig, *, limit: int | None = None, topic: str | None = None) -> list[sqlite3.Row]:
    now = to_iso(utc_now())
    order_by = "next_review_at ASC, box ASC, id ASC"
    params: list[object] = [now]
    query = """
        SELECT *
        FROM cards
        WHERE next_review_at <= ?
    """

    if topic:
        query += " AND topic = ?"
        params.append(topic)

    query += f" ORDER BY {order_by}"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    with connect(config) as connection:
        return list(connection.execute(query, params))


def recent_cards(config: StudyConfig, *, limit: int = 20) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                """
                SELECT *
                FROM cards
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )


def review_card(config: StudyConfig, *, card_id: int, result: str) -> ReviewOutcome:
    if result not in {"correct", "wrong"}:
        raise ValueError("result must be either 'correct' or 'wrong'")

    reviewed_at = utc_now()

    with connect(config) as connection:
        card = connection.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if card is None:
            raise ValueError(f"Card {card_id} does not exist.")

        prior_box = int(card["box"])
        if result == "correct":
            new_box = min(5, prior_box + 1)
            lapse_count = int(card["lapse_count"])
        else:
            new_box = 1
            lapse_count = int(card["lapse_count"]) + 1

        next_review_at = reviewed_at + timedelta(days=config.box_intervals[new_box])
        next_review_iso = to_iso(next_review_at)
        reviewed_at_iso = to_iso(reviewed_at)

        connection.execute(
            """
            UPDATE cards
            SET box = ?,
                lapse_count = ?,
                updated_at = ?,
                last_reviewed_at = ?,
                next_review_at = ?,
                last_result = ?
            WHERE id = ?
            """,
            (
                new_box,
                lapse_count,
                reviewed_at_iso,
                reviewed_at_iso,
                next_review_iso,
                result,
                card_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO reviews (
                card_id, reviewed_at, result,
                prior_box, new_box, next_review_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                card_id,
                reviewed_at_iso,
                result,
                prior_box,
                new_box,
                next_review_iso,
            ),
        )

    return ReviewOutcome(
        card_id=card_id,
        prompt=str(card["prompt"]),
        answer=str(card["answer"]),
        topic=str(card["topic"]),
        prior_box=prior_box,
        new_box=new_box,
        next_review_at=next_review_iso,
        result=result,
    )


def stats(config: StudyConfig) -> dict[str, object]:
    now_iso = to_iso(utc_now())
    today = utc_now().date().isoformat()

    with connect(config) as connection:
        total_cards = connection.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        due_now = connection.execute(
            "SELECT COUNT(*) FROM cards WHERE next_review_at <= ?",
            (now_iso,),
        ).fetchone()[0]
        reviewed_today = connection.execute(
            "SELECT COUNT(*) FROM reviews WHERE reviewed_at >= ?",
            (f"{today}T00:00:00+00:00",),
        ).fetchone()[0]
        by_box = {
            int(row["box"]): int(row["count"])
            for row in connection.execute(
                "SELECT box, COUNT(*) AS count FROM cards GROUP BY box ORDER BY box ASC"
            )
        }

    return {
        "total_cards": int(total_cards),
        "due_now": int(due_now),
        "reviewed_today": int(reviewed_today),
        "by_box": {box: by_box.get(box, 0) for box in range(1, 6)},
    }
