from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from study.config import load_config
from study.scheduler import fallback_schedule
from study.storage import (
    add_concept_card,
    complete_concept_attempt,
    dashboard_stats,
    due_cards,
    ensure_storage,
    get_card_detail,
    get_review_attempt,
    start_review_attempt,
)
from study.web import StudyWebApp


def call_app(app: StudyWebApp, *, method: str, path: str, body: str = "") -> tuple[str, dict[str, str], str]:
    environ: dict[str, object] = {}
    setup_testing_defaults(environ)
    environ["REQUEST_METHOD"] = method
    environ["PATH_INFO"] = path
    encoded = body.encode("utf-8")
    environ["CONTENT_LENGTH"] = str(len(encoded))
    environ["wsgi.input"] = io.BytesIO(encoded)

    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    body_bytes = b"".join(app(environ, start_response))
    return str(captured["status"]), dict(captured["headers"]), body_bytes.decode("utf-8")


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
                    'cards_dir = "cards"',
                    'workspaces_dir = ".barsky/workspaces"',
                    'scheduler = "leitner_fallback"',
                    'concept_scheduler = "leitner_fallback"',
                    'exercise_scheduler = "leitner_fallback"',
                    "box_intervals = [1, 2, 4, 8, 16]",
                    'review_order = "oldest-first"',
                    'llm_validator = "openai"',
                ]
            ),
            encoding="utf-8",
        )
        templates = self.root / "templates"
        static = self.root / "static"
        templates.mkdir()
        static.mkdir()
        (templates / "layout.html").write_text(
            "<!DOCTYPE html><html><head><title>$title</title></head><body>$content</body></html>",
            encoding="utf-8",
        )
        (static / "app.css").write_text("body { color: #111; }", encoding="utf-8")
        self.config = load_config(self.root)
        ensure_storage(self.config)
        self.app = StudyWebApp(self.config)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_add_concept_card_is_due_immediately(self) -> None:
        add_concept_card(
            self.config,
            title="Mutex",
            prompt="What does a mutex do?",
            answer="It serializes access to shared state.",
            topic="python",
            tags=["threading", "concurrency"],
        )

        due = due_cards(self.config)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["box"], 1)
        self.assertEqual(due[0]["type"], "concept")
        self.assertEqual(due[0]["topic"], "python")

    def test_fallback_schedule_explains_incomplete_reviews(self) -> None:
        decision = fallback_schedule(self.config, prior_box=3, result="incomplete")

        self.assertEqual(decision.new_box, 3)
        self.assertEqual(decision.new_interval_days, 1)
        self.assertIn("Incomplete review", decision.reason_summary)

    def test_complete_attempt_records_pass_and_updates_box(self) -> None:
        card_id = add_concept_card(
            self.config,
            title="Idempotency",
            prompt="What is idempotency?",
            answer="Repeating the same operation yields the same result.",
        )
        attempt = start_review_attempt(self.config, card_type="concept")
        self.assertEqual(int(attempt["card_id"]), card_id)

        outcome = complete_concept_attempt(self.config, attempt_id=int(attempt["id"]), result="pass")

        self.assertEqual(outcome.schedule.prior_box, 1)
        self.assertEqual(outcome.schedule.new_box, 2)
        self.assertIn("Passed review promoted", outcome.schedule.reason_summary)
        self.assertFalse(due_cards(self.config))

    def test_dashboard_stats_include_recent_failures(self) -> None:
        add_concept_card(self.config, title="Q1", prompt="Q1", answer="A1", topic="algorithms")
        attempt = start_review_attempt(self.config, card_type="concept")
        complete_concept_attempt(self.config, attempt_id=int(attempt["id"]), result="fail")

        snapshot = dashboard_stats(self.config)

        self.assertEqual(snapshot.total_cards, 1)
        self.assertEqual(snapshot.recent_results["fail"], 1)
        self.assertEqual(snapshot.weak_topics[0][0], "algorithms")

    def test_web_dashboard_and_review_flow_render(self) -> None:
        add_concept_card(
            self.config,
            title="Race condition",
            prompt="What is a race condition?",
            answer="A bug caused by timing-dependent access to shared state.",
            topic="python",
        )

        status, _, dashboard_html = call_app(self.app, method="GET", path="/")
        self.assertEqual(status, "200 OK")
        self.assertIn("Start Review", dashboard_html)
        self.assertIn("Due now", dashboard_html)

        status, headers, _ = call_app(self.app, method="GET", path="/review")
        self.assertEqual(status, "303 See Other")
        review_path = headers["Location"]
        self.assertRegex(review_path, r"^/review/\d+$")

        status, _, review_html = call_app(self.app, method="GET", path=review_path)
        self.assertEqual(status, "200 OK")
        self.assertIn("Concept Review", review_html)
        self.assertIn("Reveal answer", review_html)

        status, _, result_html = call_app(
            self.app,
            method="POST",
            path=f"{review_path}/result",
            body="result=fail",
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Result: fail", result_html)
        self.assertIn("Scheduler", result_html)
        self.assertIn("reset the card to box 1", result_html)

    def test_completed_attempt_page_is_stable(self) -> None:
        add_concept_card(self.config, title="Mutex", prompt="Q", answer="A")
        attempt = start_review_attempt(self.config, card_type="concept")
        complete_concept_attempt(self.config, attempt_id=int(attempt["id"]), result="pass")

        status, _, body = call_app(self.app, method="GET", path=f"/review/{int(attempt['id'])}")
        self.assertEqual(status, "200 OK")
        self.assertIn("Review Completed", body)

    def test_cards_page_and_card_detail_render(self) -> None:
        card_id = add_concept_card(
            self.config,
            title="Binary search",
            prompt="How does binary search narrow the search window?",
            answer="It discards half the remaining interval each step.",
            topic="algorithms",
            tags=["python", "search"],
        )
        detail = get_card_detail(self.config, card_id)
        self.assertEqual(detail.title, "Binary search")
        self.assertEqual(detail.tags, ["python", "search"])

        status, _, cards_html = call_app(self.app, method="GET", path="/cards")
        self.assertEqual(status, "200 OK")
        self.assertIn("/cards/1", cards_html)
        self.assertIn("Binary search", cards_html)

        status, _, detail_html = call_app(self.app, method="GET", path=f"/cards/{card_id}")
        self.assertEqual(status, "200 OK")
        self.assertIn("Recent Reviews", detail_html)
        self.assertIn("Reveal answer", detail_html)

    def test_patterns_page_surfaces_failures_and_incompletes(self) -> None:
        add_concept_card(
            self.config,
            title="Mutex",
            prompt="What does a mutex do?",
            answer="It serializes access to shared state.",
            topic="python",
        )
        attempt = start_review_attempt(self.config, card_type="concept")
        complete_concept_attempt(self.config, attempt_id=int(attempt["id"]), result="fail")

        add_concept_card(
            self.config,
            title="Idempotency",
            prompt="What is idempotency?",
            answer="Repeating the same operation yields the same result.",
            topic="api",
        )
        attempt = start_review_attempt(self.config, card_type="concept")
        complete_concept_attempt(self.config, attempt_id=int(attempt["id"]), result="incomplete")

        status, _, body = call_app(self.app, method="GET", path="/patterns")
        self.assertEqual(status, "200 OK")
        self.assertIn("Weak Topics", body)
        self.assertIn("High-Lapse Cards", body)
        self.assertIn("Repeated Incompletes", body)


if __name__ == "__main__":
    unittest.main()
