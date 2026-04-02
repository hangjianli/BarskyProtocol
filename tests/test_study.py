from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote_plus
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults

from study.config import load_config
from study.exercises import scaffold_exercise_assets
from study.scheduler import fallback_schedule
from study.storage import (
    add_concept_card,
    add_exercise_card,
    complete_concept_attempt,
    dashboard_stats,
    due_cards,
    ensure_storage,
    get_card_detail,
    get_exercise_attempt_view,
    get_review_attempt,
    start_review_attempt,
)
from study.web import StudyWebApp


def call_app(
    app: StudyWebApp,
    *,
    method: str,
    path: str,
    body: str = "",
    query_string: str = "",
) -> tuple[str, dict[str, str], str]:
    environ: dict[str, object] = {}
    setup_testing_defaults(environ)
    environ["REQUEST_METHOD"] = method
    environ["PATH_INFO"] = path
    environ["QUERY_STRING"] = query_string
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
                    'notebook_split_mode = "balanced"',
                    'scheduler = "leitner_fallback"',
                    'concept_scheduler = "leitner_fallback"',
                    'exercise_scheduler = "leitner_fallback"',
                    "box_intervals = [1, 2, 4, 8, 16]",
                    'review_order = "oldest-first"',
                    'llm_validator = "codex_oauth"',
                    'llm_model = "gpt-4.1-mini"',
                    'llm_auth_file = "~/.codex/auth.json"',
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

        status, headers, _ = call_app(self.app, method="GET", path="/review", query_string="mode=mixed")
        self.assertEqual(status, "303 See Other")
        review_path = headers["Location"]
        self.assertRegex(review_path, r"^/review/\d+$")

        status, _, review_html = call_app(self.app, method="GET", path=review_path)
        self.assertEqual(status, "200 OK")
        self.assertIn("Concept Review", review_html)
        self.assertIn("Type your answer before grading", review_html)

        with patch("study.web.grade_concept_answer") as mocked_grade:
            mocked_grade.return_value.result = "fail"
            mocked_grade.return_value.summary = "Your answer missed the timing-dependent part of the definition."
            mocked_grade.return_value.model = "test-model"
            status, _, result_html = call_app(
                self.app,
                method="POST",
                path=f"{review_path}/result",
                body="action=grade&user_answer=It+is+a+shared+state+bug",
            )
        self.assertEqual(status, "200 OK")
        self.assertIn("Result: fail", result_html)
        self.assertIn("test-model", result_html)
        self.assertIn("Your answer", result_html)
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

    def test_recommendations_page_surfaces_actions_from_failures(self) -> None:
        for title in ("Async cancellation", "Async shielding"):
            add_concept_card(
                self.config,
                title=title,
                prompt="What happens when a task is cancelled?",
                answer="It raises CancelledError at the next await point.",
                topic="asyncio",
            )
            attempt = start_review_attempt(self.config, card_type="concept")
            complete_concept_attempt(self.config, attempt_id=int(attempt["id"]), result="fail")

        status, _, body = call_app(self.app, method="GET", path="/recommendations")
        self.assertEqual(status, "200 OK")
        self.assertIn("Recommendations", body)
        self.assertIn("Pause new", body)
        self.assertIn("asyncio", body)

    def test_mixed_review_can_select_exercise_card(self) -> None:
        files = scaffold_exercise_assets(
            self.config,
            title="Binary Search Drill",
            topic="algorithms",
            prompt="Implement binary search.",
        )
        add_exercise_card(
            self.config,
            title="Binary Search Drill",
            topic="algorithms",
            tags=["exercise"],
            source="",
            files=files,
        )

        status, headers, _ = call_app(self.app, method="GET", path="/review", query_string="mode=mixed")
        self.assertEqual(status, "303 See Other")
        status, _, body = call_app(self.app, method="GET", path=headers["Location"])
        self.assertEqual(status, "200 OK")
        self.assertIn("Exercise Review", body)

    def test_new_exercise_page_creates_card_and_assets(self) -> None:
        status, _, body = call_app(
            self.app,
            method="POST",
            path="/cards/new/exercise",
            body="title=Binary+Search&topic=algorithms&tags=python%2Csearch&prompt=Implement+binary+search",
        )
        self.assertEqual(status, "303 See Other")

        detail = get_card_detail(self.config, 1)
        self.assertEqual(detail.type, "code_exercise")
        self.assertTrue(Path(detail.asset_path).is_dir())
        self.assertTrue((Path(detail.asset_path) / "tests.py").is_file())

    def test_exercise_review_creates_workspace_and_records_validation(self) -> None:
        files = scaffold_exercise_assets(
            self.config,
            title="Adder",
            topic="python",
            prompt="Implement add(a, b) and return the sum.",
        )
        add_exercise_card(
            self.config,
            title="Adder",
            topic="python",
            tags=["exercise"],
            source="",
            files=files,
        )

        status, headers, _ = call_app(self.app, method="GET", path="/review", query_string="mode=exercise")
        self.assertEqual(status, "303 See Other")
        review_path = headers["Location"]

        status, _, review_html = call_app(self.app, method="GET", path=review_path)
        self.assertEqual(status, "200 OK")
        self.assertIn("Exercise Review", review_html)
        self.assertIn("Create Workspace", review_html)

        status, headers, _ = call_app(
            self.app,
            method="POST",
            path=f"{review_path}/workspace",
            body="action=create",
        )
        self.assertEqual(status, "303 See Other")
        attempt_id = int(review_path.rsplit("/", 1)[-1])
        attempt = get_exercise_attempt_view(self.config, attempt_id)
        self.assertIsNotNone(attempt.workspace_path)

        status, _, fail_html = call_app(
            self.app,
            method="POST",
            path=f"{review_path}/validate",
            body="",
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Result: fail", fail_html)
        self.assertIn("test_placeholder", fail_html)

    def test_exercise_review_records_successful_validation(self) -> None:
        files = scaffold_exercise_assets(
            self.config,
            title="Adder Pass",
            topic="python",
            prompt="Implement add(a, b) and return the sum.",
        )
        add_exercise_card(
            self.config,
            title="Adder Pass",
            topic="python",
            tags=["exercise"],
            source="",
            files=files,
        )

        status, headers, _ = call_app(self.app, method="GET", path="/review", query_string="mode=exercise")
        self.assertEqual(status, "303 See Other")
        review_path = headers["Location"]
        call_app(
            self.app,
            method="POST",
            path=f"{review_path}/workspace",
            body="action=create",
        )
        attempt_id = int(review_path.rsplit("/", 1)[-1])
        attempt = get_exercise_attempt_view(self.config, attempt_id)
        workspace = Path(attempt.workspace_path)
        (workspace / "answer.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
        (workspace / "tests.py").write_text(
            "import unittest\nimport answer\n\nclass ExerciseTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(answer.add(2, 3), 5)\n\nif __name__ == '__main__':\n    unittest.main()\n",
            encoding="utf-8",
        )

        status, _, pass_html = call_app(
            self.app,
            method="POST",
            path=f"{review_path}/validate",
            body="",
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Result: pass", pass_html)
        self.assertIn("All exercise tests passed.", pass_html)

    def test_import_notebook_from_external_path_creates_multiple_cards(self) -> None:
        notebook_path = self.root / "tokenizer.ipynb"
        notebook_path.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "markdown", "source": ["# Tokenizer\n", "Build a tiny tokenizer.\n"]},
                        {"cell_type": "code", "source": ["def tokenize(text: str) -> list[str]:\n", "    return text.split()\n"]},
                        {"cell_type": "markdown", "source": ["# Dataloader\n", "Create batches.\n"]},
                        {"cell_type": "code", "source": ["def batch(items: list[int], size: int) -> list[list[int]]:\n", "    return [items[i:i + size] for i in range(0, len(items), size)]\n"]},
                    ]
                }
            ),
            encoding="utf-8",
        )

        status, _, review_html = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/preview",
            body=f"source_path={notebook_path}&topic=llm-from-scratch&source_label=Chapter+2",
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Review Imported Notebook", review_html)
        self.assertIn("external_path", review_html)
        self.assertIn("Candidate 1", review_html)
        self.assertIn("Candidate 2", review_html)

        draft_id = re.search(r'name="draft_id" value="([^"]+)"', review_html)
        self.assertIsNotNone(draft_id)

        status, headers, _ = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/create",
            body=(
                f"draft_id={draft_id.group(1)}"
                "&keep_0=yes&title_0=Tokenizer&topic_0=llm-from-scratch&tags_0=python%2Ctokenizer"
                "&keep_1=yes&title_1=Dataloader&topic_1=llm-from-scratch&tags_1=python%2Cloader"
            ),
        )
        self.assertEqual(status, "303 See Other")
        self.assertEqual(headers["Location"], "/cards/1")

        first = get_card_detail(self.config, 1)
        second = get_card_detail(self.config, 2)
        self.assertEqual(first.source_mode, "external_path")
        self.assertEqual(first.source_label, "Chapter 2")
        self.assertEqual(Path(first.source_path), notebook_path.resolve())
        self.assertIn("cells", first.source_cell_spec)
        self.assertEqual(second.source_mode, "external_path")
        self.assertTrue((Path(first.asset_path) / "solution.py").read_text(encoding="utf-8").startswith("def tokenize"))

    def test_import_notebook_upload_creates_managed_copy(self) -> None:
        notebook_json = json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["# Tiny Exercise\n", "Short section.\n"]},
                    {"cell_type": "code", "source": ["def square(value: int) -> int:\n", "    return value * value\n"]},
                ]
            }
        )

        status, _, review_html = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/preview",
            body="topic=math&source_label=tiny.ipynb&notebook_json=" + quote_plus(notebook_json),
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("managed_copy", review_html)

        draft_id = re.search(r'name="draft_id" value="([^"]+)"', review_html)
        self.assertIsNotNone(draft_id)
        status, headers, _ = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/create",
            body=(
                f"draft_id={draft_id.group(1)}"
                "&keep_0=yes&title_0=Square&topic_0=math&tags_0=python"
            ),
        )
        self.assertEqual(status, "303 See Other")
        detail = get_card_detail(self.config, 1)
        self.assertEqual(detail.source_mode, "managed_copy")
        self.assertTrue(Path(detail.source_path).is_file())

    def test_import_notebook_can_regenerate_with_more_aggressive_split(self) -> None:
        notebook_path = self.root / "chapter.ipynb"
        notebook_path.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "markdown", "source": ["# Tokenizer\n", "Build the tokenizer pieces.\n"]},
                        {"cell_type": "code", "source": ["import re\n"]},
                        {"cell_type": "code", "source": ["def split_words(text: str) -> list[str]:\n", "    return re.findall(r\"\\w+\", text)\n"]},
                        {"cell_type": "code", "source": ["def encode(tokens: list[str]) -> dict[str, int]:\n", "    return {token: index for index, token in enumerate(tokens)}\n"]},
                    ]
                }
            ),
            encoding="utf-8",
        )

        status, _, balanced_html = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/preview",
            body=f"source_path={quote_plus(str(notebook_path))}&topic=llm&source_label=chapter.ipynb&split_mode=balanced",
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Split mode", balanced_html)
        self.assertIn(">balanced<", balanced_html)
        self.assertIn("<dd>1</dd>", balanced_html)

        draft_id = re.search(r'name="draft_id" value="([^"]+)"', balanced_html)
        self.assertIsNotNone(draft_id)
        status, _, aggressive_html = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/regenerate",
            body=f"draft_id={draft_id.group(1)}&split_mode=aggressive",
        )
        self.assertEqual(status, "200 OK")
        self.assertIn(">aggressive<", aggressive_html)
        self.assertIn("<dd>2</dd>", aggressive_html)


if __name__ == "__main__":
    unittest.main()
