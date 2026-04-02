from __future__ import annotations

from dataclasses import dataclass

from study.config import StudyConfig
from study.storage import connect
from study.scheduler import to_iso, utc_now


@dataclass(frozen=True)
class PatternSnapshot:
    weak_topics: list[tuple[str, int]]
    high_lapse_cards: list[tuple[int, str, int]]
    incomplete_cards: list[tuple[int, str, int]]


def build_pattern_snapshot(config: StudyConfig) -> PatternSnapshot:
    recent_cutoff = to_iso(utc_now())

    with connect(config) as connection:
        # Topic-level failures are the simplest reliable starting signal.
        weak_topics = [
            (str(row["topic"]), int(row["count"]))
            for row in connection.execute(
                """
                SELECT cards.topic AS topic, COUNT(*) AS count
                FROM reviews
                JOIN cards ON cards.id = reviews.card_id
                WHERE reviews.result = 'fail'
                  AND cards.topic != ''
                GROUP BY cards.topic
                ORDER BY count DESC, cards.topic ASC
                LIMIT 5
                """
            )
        ]

        # Lapse-heavy cards are candidates for splitting or simplifying later.
        high_lapse_cards = [
            (int(row["id"]), str(row["title"]), int(row["lapse_count"]))
            for row in connection.execute(
                """
                SELECT id, title, lapse_count
                FROM cards
                WHERE lapse_count > 0
                ORDER BY lapse_count DESC, updated_at DESC, id DESC
                LIMIT 8
                """
            )
        ]

        # Repeated incompletes are a different signal than actual failures.
        incomplete_cards = [
            (int(row["id"]), str(row["title"]), int(row["count"]))
            for row in connection.execute(
                """
                SELECT cards.id AS id, cards.title AS title, COUNT(*) AS count
                FROM reviews
                JOIN cards ON cards.id = reviews.card_id
                WHERE reviews.result = 'incomplete'
                GROUP BY cards.id, cards.title
                ORDER BY count DESC, cards.title ASC
                LIMIT 8
                """
            )
        ]

    return PatternSnapshot(
        weak_topics=weak_topics,
        high_lapse_cards=high_lapse_cards,
        incomplete_cards=incomplete_cards,
    )
