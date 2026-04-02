from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable

from study.config import StudyConfig
from study.scheduler import ScheduleDecision, fallback_schedule, initial_card_state, to_iso, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('concept', 'code_exercise')),
    title TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '',
    asset_path TEXT NOT NULL DEFAULT '',
    box INTEGER NOT NULL DEFAULT 1 CHECK(box BETWEEN 1 AND 5),
    lapse_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_reviewed_at TEXT,
    next_review_at TEXT NOT NULL,
    last_result TEXT CHECK(last_result IN ('pass', 'fail', 'incomplete') OR last_result IS NULL),
    scheduler_name TEXT NOT NULL,
    last_interval_days INTEGER,
    last_schedule_reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS concept_cards (
    card_id INTEGER PRIMARY KEY,
    prompt TEXT NOT NULL,
    answer TEXT NOT NULL,
    FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL,
    reviewed_at TEXT NOT NULL,
    result TEXT NOT NULL CHECK(result IN ('pass', 'fail', 'incomplete')),
    prior_box INTEGER NOT NULL,
    new_box INTEGER NOT NULL,
    next_review_at TEXT NOT NULL,
    review_duration_seconds INTEGER,
    failure_reason TEXT,
    validator_summary TEXT,
    failing_tests TEXT NOT NULL DEFAULT '[]',
    recommendation_snapshot TEXT,
    workspace_path TEXT,
    scheduler_name TEXT NOT NULL,
    reason_codes TEXT NOT NULL DEFAULT '[]',
    reason_summary TEXT NOT NULL DEFAULT '',
    previous_interval_days INTEGER,
    new_interval_days INTEGER NOT NULL,
    FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL,
    card_type TEXT NOT NULL CHECK(card_type IN ('concept', 'code_exercise')),
    status TEXT NOT NULL CHECK(status IN ('active', 'completed')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    result TEXT CHECK(result IN ('pass', 'fail', 'incomplete') OR result IS NULL),
    FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cards_next_review_at ON cards(next_review_at);
CREATE INDEX IF NOT EXISTS idx_reviews_reviewed_at ON reviews(reviewed_at);
CREATE INDEX IF NOT EXISTS idx_attempts_status ON review_attempts(status, card_type);
"""


@dataclass(frozen=True)
class ReviewOutcome:
    card_id: int
    attempt_id: int
    result: str
    title: str
    topic: str
    prompt: str
    answer: str
    schedule: ScheduleDecision


@dataclass(frozen=True)
class DashboardStats:
    total_cards: int
    due_now: int
    overdue: int
    recent_results: dict[str, int]
    weak_topics: list[tuple[str, int]]


@dataclass(frozen=True)
class CardDetail:
    id: int
    type: str
    title: str
    topic: str
    tags: list[str]
    source: str
    box: int
    lapse_count: int
    created_at: str
    updated_at: str
    next_review_at: str
    last_result: str | None
    scheduler_name: str
    last_interval_days: int | None
    last_schedule_reason: str
    prompt: str | None
    answer: str | None


def connect(config: StudyConfig) -> sqlite3.Connection:
    connection = sqlite3.connect(config.database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")}


def _migrate_legacy_schema(connection: sqlite3.Connection, config: StudyConfig) -> None:
    columns = _table_columns(connection, "cards")
    if not columns or "type" in columns:
        return

    connection.execute("ALTER TABLE cards RENAME TO cards_legacy")
    if _table_columns(connection, "reviews"):
        connection.execute("ALTER TABLE reviews RENAME TO reviews_legacy")

    connection.executescript(SCHEMA)

    connection.execute(
        """
        INSERT INTO cards (
            id, type, title, topic, tags, source, asset_path, box, lapse_count,
            created_at, updated_at, last_reviewed_at, next_review_at, last_result,
            scheduler_name, last_interval_days, last_schedule_reason
        )
        SELECT
            id,
            'concept',
            substr(prompt, 1, 80),
            topic,
            tags,
            source,
            '',
            box,
            lapse_count,
            created_at,
            updated_at,
            last_reviewed_at,
            next_review_at,
            CASE last_result
                WHEN 'correct' THEN 'pass'
                WHEN 'wrong' THEN 'fail'
                ELSE NULL
            END,
            ?,
            NULL,
            'Migrated from the legacy concept-card schema.'
        FROM cards_legacy
        """,
        (config.scheduler,),
    )
    connection.execute(
        """
        INSERT INTO concept_cards (card_id, prompt, answer)
        SELECT id, prompt, answer
        FROM cards_legacy
        """
    )

    if "reviews_legacy" in {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'reviews_legacy'"
        )
    }:
        connection.execute(
            """
            INSERT INTO reviews (
                card_id, reviewed_at, result, prior_box, new_box, next_review_at,
                scheduler_name, reason_codes, reason_summary, previous_interval_days, new_interval_days
            )
            SELECT
                card_id,
                reviewed_at,
                CASE result
                    WHEN 'correct' THEN 'pass'
                    WHEN 'wrong' THEN 'fail'
                END,
                prior_box,
                new_box,
                next_review_at,
                ?,
                '[]',
                'Migrated from the legacy review history.',
                NULL,
                NULL
            FROM reviews_legacy
            """,
            (config.scheduler,),
        )
        connection.execute("DROP TABLE reviews_legacy")

    connection.execute("DROP TABLE cards_legacy")


def ensure_storage(config: StudyConfig) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.database.parent.mkdir(parents=True, exist_ok=True)
    config.cards_dir.mkdir(parents=True, exist_ok=True)
    config.workspaces_dir.mkdir(parents=True, exist_ok=True)

    with connect(config) as connection:
        _migrate_legacy_schema(connection, config)
        connection.executescript(SCHEMA)


def _json_tags(tags: Iterable[str]) -> str:
    return json.dumps([tag for tag in (value.strip() for value in tags) if tag])


def add_concept_card(
    config: StudyConfig,
    *,
    title: str,
    prompt: str,
    answer: str,
    topic: str = "",
    tags: Iterable[str] = (),
    source: str = "",
) -> int:
    created_at = utc_now()
    schedule = initial_card_state(config, now=created_at)

    with connect(config) as connection:
        cursor = connection.execute(
            """
            INSERT INTO cards (
                type, title, topic, tags, source, asset_path, box, lapse_count,
                created_at, updated_at, next_review_at, scheduler_name,
                last_interval_days, last_schedule_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "concept",
                title.strip(),
                topic.strip(),
                _json_tags(tags),
                source.strip(),
                "",
                schedule.new_box,
                0,
                to_iso(created_at),
                to_iso(created_at),
                schedule.next_review_at,
                schedule.scheduler_name,
                schedule.new_interval_days,
                schedule.reason_summary,
            ),
        )
        card_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO concept_cards (card_id, prompt, answer) VALUES (?, ?, ?)",
            (card_id, prompt.strip(), answer.strip()),
        )
        return card_id


def due_cards(config: StudyConfig, *, card_type: str | None = None, limit: int | None = None) -> list[sqlite3.Row]:
    params: list[object] = [to_iso(utc_now())]
    query = """
        SELECT cards.*, concept_cards.prompt, concept_cards.answer
        FROM cards
        LEFT JOIN concept_cards ON concept_cards.card_id = cards.id
        WHERE cards.next_review_at <= ?
    """

    if card_type:
        query += " AND cards.type = ?"
        params.append(card_type)

    query += " ORDER BY cards.next_review_at ASC, cards.id ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    with connect(config) as connection:
        return list(connection.execute(query, params))


def list_cards(config: StudyConfig, *, limit: int = 50) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                """
                SELECT cards.*, concept_cards.prompt
                FROM cards
                LEFT JOIN concept_cards ON concept_cards.card_id = cards.id
                ORDER BY cards.updated_at DESC, cards.id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )


def get_card_detail(config: StudyConfig, card_id: int) -> CardDetail | None:
    with connect(config) as connection:
        row = connection.execute(
            """
            SELECT cards.*, concept_cards.prompt, concept_cards.answer
            FROM cards
            LEFT JOIN concept_cards ON concept_cards.card_id = cards.id
            WHERE cards.id = ?
            """,
            (card_id,),
        ).fetchone()
        if row is None:
            return None

    return CardDetail(
        id=int(row["id"]),
        type=str(row["type"]),
        title=str(row["title"]),
        topic=str(row["topic"]),
        tags=json.loads(str(row["tags"])),
        source=str(row["source"]),
        box=int(row["box"]),
        lapse_count=int(row["lapse_count"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        next_review_at=str(row["next_review_at"]),
        last_result=str(row["last_result"]) if row["last_result"] is not None else None,
        scheduler_name=str(row["scheduler_name"]),
        last_interval_days=int(row["last_interval_days"]) if row["last_interval_days"] is not None else None,
        last_schedule_reason=str(row["last_schedule_reason"]),
        prompt=str(row["prompt"]) if row["prompt"] is not None else None,
        answer=str(row["answer"]) if row["answer"] is not None else None,
    )


def recent_reviews_for_card(config: StudyConfig, card_id: int, *, limit: int = 10) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                """
                SELECT *
                FROM reviews
                WHERE card_id = ?
                ORDER BY reviewed_at DESC, id DESC
                LIMIT ?
                """,
                (card_id, limit),
            )
        )


def dashboard_stats(config: StudyConfig) -> DashboardStats:
    now = utc_now()
    start_of_day = datetime(now.year, now.month, now.day, tzinfo=UTC)
    recent_cutoff = now - timedelta(days=7)
    weak_cutoff = now - timedelta(days=30)

    with connect(config) as connection:
        total_cards = int(connection.execute("SELECT COUNT(*) FROM cards").fetchone()[0])
        due_now = int(
            connection.execute(
                "SELECT COUNT(*) FROM cards WHERE next_review_at <= ?",
                (to_iso(now),),
            ).fetchone()[0]
        )
        overdue = int(
            connection.execute(
                "SELECT COUNT(*) FROM cards WHERE next_review_at < ?",
                (to_iso(start_of_day),),
            ).fetchone()[0]
        )
        recent_results = {key: 0 for key in ("pass", "fail", "incomplete")}
        for row in connection.execute(
            """
            SELECT result, COUNT(*) AS count
            FROM reviews
            WHERE reviewed_at >= ?
            GROUP BY result
            """,
            (to_iso(recent_cutoff),),
        ):
            recent_results[str(row["result"])] = int(row["count"])

        weak_topics = [
            (str(row["topic"]), int(row["count"]))
            for row in connection.execute(
                """
                SELECT cards.topic AS topic, COUNT(*) AS count
                FROM reviews
                JOIN cards ON cards.id = reviews.card_id
                WHERE reviews.reviewed_at >= ?
                  AND reviews.result = 'fail'
                  AND cards.topic != ''
                GROUP BY cards.topic
                ORDER BY count DESC, cards.topic ASC
                LIMIT 5
                """,
                (to_iso(weak_cutoff),),
            )
        ]

    return DashboardStats(
        total_cards=total_cards,
        due_now=due_now,
        overdue=overdue,
        recent_results=recent_results,
        weak_topics=weak_topics,
    )


def start_review_attempt(config: StudyConfig, *, card_type: str = "concept") -> sqlite3.Row | None:
    with connect(config) as connection:
        active = connection.execute(
            """
            SELECT review_attempts.*, cards.title, cards.topic, cards.box, cards.next_review_at,
                   concept_cards.prompt, concept_cards.answer
            FROM review_attempts
            JOIN cards ON cards.id = review_attempts.card_id
            LEFT JOIN concept_cards ON concept_cards.card_id = cards.id
            WHERE review_attempts.status = 'active'
              AND review_attempts.card_type = ?
            ORDER BY review_attempts.started_at ASC
            LIMIT 1
            """,
            (card_type,),
        ).fetchone()
        if active is not None:
            return active

        next_card = connection.execute(
            """
            SELECT cards.*, concept_cards.prompt, concept_cards.answer
            FROM cards
            LEFT JOIN concept_cards ON concept_cards.card_id = cards.id
            WHERE cards.type = ?
              AND cards.next_review_at <= ?
            ORDER BY cards.next_review_at ASC, cards.id ASC
            LIMIT 1
            """,
            (card_type, to_iso(utc_now())),
        ).fetchone()
        if next_card is None:
            return None

        started_at = to_iso(utc_now())
        cursor = connection.execute(
            """
            INSERT INTO review_attempts (card_id, card_type, status, started_at)
            VALUES (?, ?, 'active', ?)
            """,
            (int(next_card["id"]), card_type, started_at),
        )
        attempt_id = int(cursor.lastrowid)
        return connection.execute(
            """
            SELECT review_attempts.*, cards.title, cards.topic, cards.box, cards.next_review_at,
                   concept_cards.prompt, concept_cards.answer
            FROM review_attempts
            JOIN cards ON cards.id = review_attempts.card_id
            LEFT JOIN concept_cards ON concept_cards.card_id = cards.id
            WHERE review_attempts.id = ?
            """,
            (attempt_id,),
        ).fetchone()


def get_review_attempt(config: StudyConfig, attempt_id: int) -> sqlite3.Row | None:
    with connect(config) as connection:
        return connection.execute(
            """
            SELECT review_attempts.*, cards.title, cards.topic, cards.box, cards.next_review_at,
                   cards.scheduler_name, cards.last_schedule_reason,
                   concept_cards.prompt, concept_cards.answer
            FROM review_attempts
            JOIN cards ON cards.id = review_attempts.card_id
            LEFT JOIN concept_cards ON concept_cards.card_id = cards.id
            WHERE review_attempts.id = ?
            """,
            (attempt_id,),
        ).fetchone()


def complete_concept_attempt(
    config: StudyConfig,
    *,
    attempt_id: int,
    result: str,
    review_duration_seconds: int | None = None,
    validator_summary: str | None = None,
    failure_reason: str | None = None,
) -> ReviewOutcome:
    if result not in {"pass", "fail", "incomplete"}:
        raise ValueError("result must be `pass`, `fail`, or `incomplete`")

    completed_at = utc_now()

    with connect(config) as connection:
        attempt = connection.execute(
            """
            SELECT review_attempts.*, cards.id AS card_id, cards.title, cards.topic, cards.box,
                   concept_cards.prompt, concept_cards.answer
            FROM review_attempts
            JOIN cards ON cards.id = review_attempts.card_id
            JOIN concept_cards ON concept_cards.card_id = cards.id
            WHERE review_attempts.id = ?
            """,
            (attempt_id,),
        ).fetchone()
        if attempt is None:
            raise ValueError(f"Review attempt {attempt_id} does not exist.")
        if str(attempt["status"]) != "active":
            raise ValueError(f"Review attempt {attempt_id} is already completed.")

        prior_box = int(attempt["box"])
        schedule = fallback_schedule(config, prior_box=prior_box, result=result, now=completed_at)
        lapse_count = 0 if result != "fail" else 1
        if result == "fail":
            lapse_count = int(
                connection.execute("SELECT lapse_count FROM cards WHERE id = ?", (attempt["card_id"],)).fetchone()[0]
            ) + 1
        else:
            lapse_count = int(
                connection.execute("SELECT lapse_count FROM cards WHERE id = ?", (attempt["card_id"],)).fetchone()[0]
            )

        connection.execute(
            """
            UPDATE cards
            SET box = ?,
                lapse_count = ?,
                updated_at = ?,
                last_reviewed_at = ?,
                next_review_at = ?,
                last_result = ?,
                scheduler_name = ?,
                last_interval_days = ?,
                last_schedule_reason = ?
            WHERE id = ?
            """,
            (
                schedule.new_box,
                lapse_count,
                to_iso(completed_at),
                to_iso(completed_at),
                schedule.next_review_at,
                result,
                schedule.scheduler_name,
                schedule.new_interval_days,
                schedule.reason_summary,
                int(attempt["card_id"]),
            ),
        )
        connection.execute(
            """
            INSERT INTO reviews (
                card_id, reviewed_at, result, prior_box, new_box, next_review_at,
                review_duration_seconds, failure_reason, validator_summary,
                scheduler_name, reason_codes, reason_summary,
                previous_interval_days, new_interval_days
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(attempt["card_id"]),
                to_iso(completed_at),
                result,
                prior_box,
                schedule.new_box,
                schedule.next_review_at,
                review_duration_seconds,
                failure_reason,
                validator_summary,
                schedule.scheduler_name,
                json.dumps(list(schedule.reason_codes)),
                schedule.reason_summary,
                schedule.previous_interval_days,
                schedule.new_interval_days,
            ),
        )
        connection.execute(
            """
            UPDATE review_attempts
            SET status = 'completed',
                completed_at = ?,
                result = ?
            WHERE id = ?
            """,
            (to_iso(completed_at), result, attempt_id),
        )

    return ReviewOutcome(
        card_id=int(attempt["card_id"]),
        attempt_id=attempt_id,
        result=result,
        title=str(attempt["title"]),
        topic=str(attempt["topic"]),
        prompt=str(attempt["prompt"]),
        answer=str(attempt["answer"]),
        schedule=schedule,
    )
