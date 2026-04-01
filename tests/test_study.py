from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from study.config import load_config
from study.storage import add_card, due_cards, ensure_storage, review_card, stats


class StudyWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "config.toml").write_text(
            "\n".join(
                [
                    "[study]",
                    'data_dir = ".barsky"',
                    'database = ".barsky/test.db"',
                    "box_intervals = [1, 2, 4, 8, 16]",
                    'review_order = "oldest-first"',
                ]
            ),
            encoding="utf-8",
        )
        self.config = load_config(self.root)
        ensure_storage(self.config)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_add_card_is_due_immediately(self) -> None:
        add_card(
            self.config,
            prompt="What does a mutex do?",
            answer="It serializes access to shared state.",
            topic="python",
            tags=["threading", "concurrency"],
        )

        due = due_cards(self.config)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["box"], 1)
        self.assertEqual(due[0]["topic"], "python")

    def test_correct_review_promotes_box(self) -> None:
        card_id = add_card(
            self.config,
            prompt="What is idempotency?",
            answer="Repeating the same operation yields the same result.",
        )

        outcome = review_card(self.config, card_id=card_id, result="correct")

        self.assertEqual(outcome.prior_box, 1)
        self.assertEqual(outcome.new_box, 2)
        self.assertFalse(due_cards(self.config))

    def test_wrong_review_resets_box_and_increments_lapses(self) -> None:
        card_id = add_card(
            self.config,
            prompt="What is a race condition?",
            answer="A bug caused by timing-dependent access to shared state.",
        )
        review_card(self.config, card_id=card_id, result="correct")
        outcome = review_card(self.config, card_id=card_id, result="wrong")

        self.assertEqual(outcome.new_box, 1)

    def test_stats_report_queue_shape(self) -> None:
        add_card(self.config, prompt="Q1", answer="A1")
        add_card(self.config, prompt="Q2", answer="A2")

        snapshot = stats(self.config)

        self.assertEqual(snapshot["total_cards"], 2)
        self.assertEqual(snapshot["due_now"], 2)
        self.assertEqual(snapshot["by_box"][1], 2)


if __name__ == "__main__":
    unittest.main()
