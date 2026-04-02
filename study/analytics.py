from __future__ import annotations

import json
from dataclasses import dataclass

from study.config import StudyConfig
from study.storage import managed_connection
from study.scheduler import to_iso, utc_now


@dataclass(frozen=True)
class PatternSnapshot:
    weak_topics: list[tuple[str, int]]
    high_lapse_cards: list[tuple[int, str, int]]
    incomplete_cards: list[tuple[int, str, int]]


@dataclass(frozen=True)
class Recommendation:
    category: str
    action: str
    evidence: str


def build_pattern_snapshot(config: StudyConfig) -> PatternSnapshot:
    recent_cutoff = to_iso(utc_now())

    with managed_connection(config) as connection:
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


def build_recommendations(config: StudyConfig) -> list[Recommendation]:
    recommendations: list[Recommendation] = []

    with managed_connection(config) as connection:
        weak_topics = list(
            connection.execute(
                """
                SELECT cards.topic AS topic, COUNT(*) AS count
                FROM reviews
                JOIN cards ON cards.id = reviews.card_id
                WHERE reviews.result = 'fail'
                  AND cards.topic != ''
                GROUP BY cards.topic
                HAVING COUNT(*) >= 2
                ORDER BY count DESC, cards.topic ASC
                LIMIT 5
                """
            )
        )
        for row in weak_topics:
            topic = str(row["topic"])
            count = int(row["count"])
            recommendations.append(
                Recommendation(
                    category="Topic Load",
                    action=f"Pause new `{topic}` cards until the due backlog is stable.",
                    evidence=f"{count} failed reviews have accumulated in `{topic}`.",
                )
            )

        lapse_heavy = list(
            connection.execute(
                """
                SELECT id, title, type, lapse_count
                FROM cards
                WHERE lapse_count >= 2
                ORDER BY lapse_count DESC, updated_at DESC, id DESC
                LIMIT 5
                """
            )
        )
        for row in lapse_heavy:
            title = str(row["title"])
            lapse_count = int(row["lapse_count"])
            card_type = str(row["type"])
            action = (
                f"Split `{title}` into a smaller exercise or add supporting concept cards."
                if card_type == "code_exercise"
                else f"Rewrite `{title}` as a smaller concept card or a short concept set."
            )
            recommendations.append(
                Recommendation(
                    category="Repeated Lapses",
                    action=action,
                    evidence=f"`{title}` has reset {lapse_count} time(s).",
                )
            )

        repeated_incompletes = list(
            connection.execute(
                """
                SELECT cards.title AS title, COUNT(*) AS count
                FROM reviews
                JOIN cards ON cards.id = reviews.card_id
                WHERE reviews.result = 'incomplete'
                GROUP BY cards.id, cards.title
                HAVING COUNT(*) >= 2
                ORDER BY count DESC, cards.title ASC
                LIMIT 5
                """
            )
        )
        for row in repeated_incompletes:
            title = str(row["title"])
            count = int(row["count"])
            recommendations.append(
                Recommendation(
                    category="Repeated Incomplete",
                    action=f"Shrink `{title}` so it can be completed in one sitting.",
                    evidence=f"`{title}` has been marked incomplete {count} time(s).",
                )
            )

        failing_tests: dict[str, int] = {}
        for row in connection.execute(
            """
            SELECT failing_tests
            FROM reviews
            WHERE result = 'fail'
              AND failing_tests != '[]'
            ORDER BY reviewed_at DESC
            """
        ):
            for name in json.loads(str(row["failing_tests"])):
                failing_tests[str(name)] = failing_tests.get(str(name), 0) + 1

        for test_name, count in sorted(failing_tests.items(), key=lambda item: (-item[1], item[0]))[:3]:
            if count < 2:
                continue
            recommendations.append(
                Recommendation(
                    category="Edge Cases",
                    action=f"Add a focused drill covering `{test_name}`.",
                    evidence=f"`{test_name}` has appeared in {count} failed validation runs.",
                )
            )

    return recommendations
