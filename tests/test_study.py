from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib import error
from urllib.parse import quote_plus, urlencode, urlsplit
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults

from study.config import load_config
from study.exercises import scaffold_exercise_assets
from study.grading import grade_concept_answer
from study.notebooks import load_import_draft
from study.scheduler import fallback_schedule
from study.storage import (
    add_concept_card,
    add_exercise_card,
    complete_concept_attempt,
    dashboard_stats,
    delete_card,
    due_cards,
    ensure_storage,
    get_card_detail,
    get_exercise_attempt_view,
    get_review_attempt,
    list_cards,
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


def split_location(location: str) -> tuple[str, str]:
    parsed = urlsplit(location)
    return parsed.path, parsed.query


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
                    'llm_model = "gpt-5-codex"',
                    'llm_base_url = "https://chatgpt.com/backend-api"',
                    'llm_api = "openai-codex-responses"',
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

    def test_start_review_attempt_can_shuffle_due_cards(self) -> None:
        first_id = add_concept_card(self.config, title="First", prompt="Q1", answer="A1")
        second_id = add_concept_card(self.config, title="Second", prompt="Q2", answer="A2")

        with patch("study.storage.random.choice") as mocked_choice:
            mocked_choice.side_effect = lambda rows: rows[-1]
            attempt = start_review_attempt(self.config, card_type="concept", review_order="random")

        self.assertEqual(int(attempt["card_id"]), second_id)
        self.assertNotEqual(int(attempt["card_id"]), first_id)
        mocked_choice.assert_called_once()

    def test_grade_concept_answer_uses_responses_api_with_codex_model(self) -> None:
        config = replace(self.config, llm_model="gpt-5-codex")

        class FakeHTTPResponse:
            def __init__(self, payload: dict) -> None:
                self._payload = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._payload

            def __enter__(self) -> "FakeHTTPResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        captured: dict[str, object] = {}

        def fake_urlopen(http_request, timeout: int = 30):
            captured["url"] = http_request.full_url
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return FakeHTTPResponse(
                {
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"result":"pass","summary":"The answer captured the core concurrency guarantee."}',
                                }
                            ]
                        }
                    ]
                }
            )

        with patch("study.grading._resolve_auth_header", return_value="Bearer test-token"), patch(
            "study.grading.request.urlopen",
            side_effect=fake_urlopen,
        ):
            grade = grade_concept_answer(
                config,
                prompt="What does a mutex do?",
                reference_answer="It serializes access to shared state.",
                user_answer="It prevents concurrent access to shared state.",
            )

        self.assertEqual(captured["url"], "https://chatgpt.com/backend-api/codex/responses")
        self.assertEqual(captured["body"]["model"], "gpt-5-codex")
        self.assertEqual(captured["body"]["text"]["format"]["type"], "json_object")
        self.assertFalse(captured["body"]["store"])
        self.assertIsInstance(captured["body"]["input"], list)
        self.assertEqual(captured["body"]["input"][0]["content"][0]["type"], "input_text")
        self.assertEqual(grade.result, "pass")
        self.assertEqual(grade.model, "gpt-5-codex")

    def test_grade_concept_answer_refreshes_codex_token_after_unauthorized(self) -> None:
        auth_file = self.root / "auth.json"
        auth_file.write_text(
            json.dumps(
                {
                    "auth_mode": "codex_oauth",
                    "tokens": {
                        "access_token": "expired.header.payload",
                        "refresh_token": "refresh-token",
                        "id_token": "id-token",
                        "account_id": "acct_123",
                    },
                }
            ),
            encoding="utf-8",
        )
        config = replace(
            self.config,
            llm_model="gpt-5-codex",
            llm_base_url="https://chatgpt.com/backend-api",
            llm_api="openai-codex-responses",
            llm_auth_file=auth_file,
        )

        class FakeHTTPResponse:
            def __init__(self, payload: dict) -> None:
                self._payload = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._payload

            def __enter__(self) -> "FakeHTTPResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        seen_auth_headers: list[str] = []

        def fake_urlopen(http_request, timeout: int = 30):
            if http_request.full_url == "https://auth.openai.com/oauth/token":
                return FakeHTTPResponse(
                    {
                        "access_token": "fresh.header.payload",
                        "refresh_token": "refresh-token-2",
                        "id_token": "id-token-2",
                    }
                )

            seen_auth_headers.append(http_request.headers["Authorization"])
            if len(seen_auth_headers) == 1:
                raise error.HTTPError(
                    http_request.full_url,
                    401,
                    "Unauthorized",
                    hdrs=None,
                    fp=io.BytesIO(
                        b'{"error":{"message":"Missing scopes: api.responses.write","type":"invalid_request_error"}}'
                    ),
                )
            return FakeHTTPResponse(
                {
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"result":"pass","summary":"The refreshed token worked."}',
                                }
                            ]
                        }
                    ]
                }
            )

        with patch("study.grading.request.urlopen", side_effect=fake_urlopen):
            grade = grade_concept_answer(
                config,
                prompt="What does a mutex do?",
                reference_answer="It serializes access to shared state.",
                user_answer="It prevents concurrent access to shared state.",
            )

        self.assertEqual(seen_auth_headers, ["Bearer expired.header.payload", "Bearer fresh.header.payload"])
        updated_auth = json.loads(auth_file.read_text(encoding="utf-8"))
        self.assertEqual(updated_auth["tokens"]["access_token"], "fresh.header.payload")
        self.assertEqual(updated_auth["tokens"]["refresh_token"], "refresh-token-2")
        self.assertEqual(grade.summary, "The refreshed token worked.")

    def test_grade_concept_answer_parses_crlf_streaming_responses(self) -> None:
        config = replace(
            self.config,
            llm_model="gpt-5-codex",
            llm_base_url="https://chatgpt.com/backend-api",
            llm_api="openai-codex-responses",
        )

        class FakeHTTPResponse:
            def __init__(self, payload: str) -> None:
                self._payload = payload.encode("utf-8")

            def read(self) -> bytes:
                return self._payload

            def __enter__(self) -> "FakeHTTPResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        streaming_payload = (
            'data: {"type":"response.output_text.delta","delta":"{\\"result\\":\\"pass\\","}\r\n\r\n'
            'data: {"type":"response.output_text.delta","delta":"\\"summary\\":\\"CRLF streaming worked.\\"}"}\r\n\r\n'
            "data: [DONE]\r\n\r\n"
        )

        with patch("study.grading._resolve_auth_header", return_value="Bearer test-token"), patch(
            "study.grading.request.urlopen",
            return_value=FakeHTTPResponse(streaming_payload),
        ):
            grade = grade_concept_answer(
                config,
                prompt="What does a mutex do?",
                reference_answer="It serializes access to shared state.",
                user_answer="It prevents concurrent access to shared state.",
            )

        self.assertEqual(grade.result, "pass")
        self.assertEqual(grade.summary, "CRLF streaming worked.")

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
        self.assertIn("Shuffle Eligible", dashboard_html)
        self.assertIn("Due now", dashboard_html)

        status, headers, _ = call_app(self.app, method="GET", path="/review", query_string="mode=mixed")
        self.assertEqual(status, "303 See Other")
        review_path, review_query = split_location(headers["Location"])
        self.assertRegex(review_path, r"^/review/\d+$")
        self.assertEqual(review_query, "mode=mixed")

        status, _, review_html = call_app(self.app, method="GET", path=review_path, query_string=review_query)
        self.assertEqual(status, "200 OK")
        self.assertIn("Concept Review", review_html)
        self.assertIn("Type your answer before grading", review_html)
        self.assertRegex(review_html, r"Due: \d{4}-\d{2}-\d{2}")
        self.assertNotIn("+00:00", review_html)

        status, headers, _ = call_app(self.app, method="GET", path="/review", query_string="mode=mixed&order=random")
        self.assertEqual(status, "303 See Other")
        review_path, review_query = split_location(headers["Location"])
        self.assertRegex(review_path, r"^/review/\d+$")
        self.assertEqual(review_query, "mode=mixed&order=random")

        with patch("study.web.grade_concept_answer") as mocked_grade:
            mocked_grade.return_value.result = "fail"
            mocked_grade.return_value.summary = "Your answer missed the timing-dependent part of the definition."
            mocked_grade.return_value.model = "test-model"
            status, _, result_html = call_app(
                self.app,
                method="POST",
                path=f"{review_path}/result",
                body="action=grade&mode=mixed&user_answer=It+is+a+shared+state+bug",
            )
        self.assertEqual(status, "200 OK")
        self.assertIn("Result: fail", result_html)
        self.assertIn("test-model", result_html)
        self.assertIn("Your answer", result_html)
        self.assertIn("Scheduler", result_html)
        self.assertIn("reset the card to box 1", result_html)

    def test_concept_review_renders_card_bound_source_links(self) -> None:
        source_file = self.root / "bpe_openai_gpt2.py"
        source_file.write_text(
            "\n".join(
                [f"# line {index}" for index in range(1, 58)]
                + [
                    "def get_pairs(word):",
                    "    pairs = set()",
                    "    prev_char = word[0]",
                    "    for char in word[1:]:",
                    "        pairs.add((prev_char, char))",
                    "    return pairs",
                ]
            ),
            encoding="utf-8",
        )
        prompt = (
            "What does "
            f"[get_pairs(word)](cci:1://file://{source_file}:58:0-63:16)"
            " return, and why is it needed?"
        )
        add_concept_card(
            self.config,
            title="BPE pairs",
            prompt=prompt,
            answer="It returns adjacent symbol pairs.",
            topic="bpe_tokenizer",
            source=str(source_file),
            source_path=str(source_file),
            source_label=source_file.name,
            source_kind="py",
        )

        attempt = start_review_attempt(self.config, card_type="concept")
        status, _, review_html = call_app(self.app, method="GET", path=f"/review/{int(attempt['id'])}")
        self.assertEqual(status, "200 OK")
        self.assertIn(">get_pairs(word)</a>", review_html)
        self.assertIn(f"/review/{int(attempt['id'])}/source?", review_html)
        self.assertNotIn("cci:1://", review_html)

        status, _, source_html = call_app(
            self.app,
            method="GET",
            path=f"/review/{int(attempt['id'])}/source",
            query_string=urlencode({"path": str(source_file), "start": "58", "end": "63"}),
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Source View", source_html)
        self.assertIn("def get_pairs(word):", source_html)
        self.assertIn("source-line-target", source_html)

    def test_review_page_can_navigate_to_adjacent_cards_in_queue(self) -> None:
        add_concept_card(
            self.config,
            title="Card One",
            prompt="First prompt",
            answer="First answer",
            topic="python",
        )
        add_concept_card(
            self.config,
            title="Card Two",
            prompt="Second prompt",
            answer="Second answer",
            topic="python",
        )

        status, headers, _ = call_app(self.app, method="GET", path="/review", query_string="mode=concept")
        self.assertEqual(status, "303 See Other")
        review_path, review_query = split_location(headers["Location"])

        status, _, review_html = call_app(self.app, method="GET", path=review_path, query_string=review_query)
        self.assertEqual(status, "200 OK")
        self.assertIn("Previous Card", review_html)
        self.assertIn("button-disabled", review_html)
        self.assertIn(f"{review_path}/navigate?mode=concept&amp;direction=next", review_html)

        status, headers, _ = call_app(
            self.app,
            method="GET",
            path=f"{review_path}/navigate",
            query_string="mode=concept&direction=next",
        )
        self.assertEqual(status, "303 See Other")
        next_path, next_query = split_location(headers["Location"])
        self.assertRegex(next_path, r"^/review/\d+$")
        self.assertEqual(next_query, "mode=concept")

        status, _, next_html = call_app(self.app, method="GET", path=next_path, query_string=next_query)
        self.assertEqual(status, "200 OK")
        self.assertIn("Card Two", next_html)
        self.assertIn(f"{next_path}/navigate?mode=concept&amp;direction=previous", next_html)

    def test_source_view_rejects_paths_outside_card_bound_sources(self) -> None:
        source_file = self.root / "allowed.py"
        source_file.write_text("def allowed():\n    return True\n", encoding="utf-8")
        other_file = self.root / "other.py"
        other_file.write_text("def blocked():\n    return False\n", encoding="utf-8")
        add_concept_card(
            self.config,
            title="Bound source",
            prompt="See [allowed](file:///tmp/placeholder.py)",
            answer="A",
            source=str(source_file),
            source_path=str(source_file),
            source_label=source_file.name,
            source_kind="py",
        )

        attempt = start_review_attempt(self.config, card_type="concept")
        status, _, _ = call_app(
            self.app,
            method="GET",
            path=f"/review/{int(attempt['id'])}/source",
            query_string=urlencode({"path": str(other_file)}),
        )
        self.assertEqual(status, "404 Not Found")

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
        self.assertRegex(cards_html, r"box 1 · created \d{4}-\d{2}-\d{2} \d{2}:\d{2}")

        status, _, detail_html = call_app(self.app, method="GET", path=f"/cards/{card_id}")
        self.assertEqual(status, "200 OK")
        self.assertIn("Recent Reviews", detail_html)
        self.assertIn("Reveal answer", detail_html)
        self.assertIn("Delete Card", detail_html)

    def test_delete_concept_card_removes_it_from_the_study_set(self) -> None:
        card_id = add_concept_card(
            self.config,
            title="Delete Me",
            prompt="What should happen?",
            answer="The card should be deleted.",
            topic="testing",
        )

        status, headers, _ = call_app(
            self.app,
            method="POST",
            path=f"/cards/{card_id}/delete",
            body="",
        )
        self.assertEqual(status, "303 See Other")
        self.assertEqual(headers["Location"], "/cards")
        self.assertIsNone(get_card_detail(self.config, card_id))

    def test_delete_exercise_card_removes_assets_and_workspaces(self) -> None:
        files = scaffold_exercise_assets(
            self.config,
            title="Delete Exercise",
            topic="python",
            prompt="Implement delete_me().",
        )
        card_id = add_exercise_card(
            self.config,
            title="Delete Exercise",
            topic="python",
            tags=["exercise"],
            source="",
            files=files,
        )

        attempt = start_review_attempt(self.config, card_type="code_exercise")
        self.assertIsNotNone(attempt)
        status, headers, _ = call_app(
            self.app,
            method="POST",
            path=f"/review/{int(attempt['id'])}/workspace",
            body="action=create",
        )
        self.assertEqual(status, "303 See Other")
        attempt_view = get_exercise_attempt_view(self.config, int(attempt["id"]))
        self.assertIsNotNone(attempt_view.workspace_path)
        asset_dir = Path(files.asset_dir)
        workspace_path = Path(attempt_view.workspace_path)

        deleted = delete_card(self.config, card_id)
        self.assertTrue(deleted)
        self.assertIsNone(get_card_detail(self.config, card_id))
        self.assertFalse(asset_dir.exists())
        self.assertFalse(workspace_path.exists())

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
        review_path, review_query = split_location(headers["Location"])
        status, _, body = call_app(self.app, method="GET", path=review_path, query_string=review_query)
        self.assertEqual(status, "200 OK")
        self.assertIn("Coding", body)

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

    def test_import_text_contract_creates_concept_and_exercise_cards(self) -> None:
        contract = (
            'version = 1\n'
            '\n'
            '[[cards]]\n'
            'type = "concept"\n'
            'title = "Mutex"\n'
            'topic = "python"\n'
            'tags = ["threading", "concurrency"]\n'
            'prompt = """\n'
            'What does a mutex do?\n'
            '"""\n'
            'answer = """\n'
            'It serializes access to shared state.\n'
            '"""\n'
            '\n'
            '[[cards]]\n'
            'type = "code_exercise"\n'
            'title = "Adder"\n'
            'topic = "python"\n'
            'tags = "exercise, math"\n'
            'prompt = """\n'
            'Implement add(a, b).\n'
            '"""\n'
            'answer_py = """\n'
            'def add(a: int, b: int) -> int:\n'
            '    raise NotImplementedError\n'
            '"""\n'
            'solution_py = """\n'
            'def add(a: int, b: int) -> int:\n'
            '    return a + b\n'
            '"""\n'
            'tests_py = """\n'
            'import unittest\n'
            'import answer\n'
            '\n'
            'class ExerciseTests(unittest.TestCase):\n'
            '    def test_add(self) -> None:\n'
            '        self.assertEqual(answer.add(2, 3), 5)\n'
            '\n'
            'if __name__ == "__main__":\n'
            '    unittest.main()\n'
            '"""\n'
        )

        status, headers, _ = call_app(
            self.app,
            method="POST",
            path="/cards/import-text",
            body="contract_text=" + quote_plus(contract),
        )
        self.assertEqual(status, "303 See Other")
        self.assertEqual(headers["Location"], "/cards/1")

        concept = get_card_detail(self.config, 1)
        exercise = get_card_detail(self.config, 2)
        self.assertEqual(concept.type, "concept")
        self.assertEqual(concept.tags, ["threading", "concurrency"])
        self.assertEqual(exercise.type, "code_exercise")
        self.assertEqual(exercise.tags, ["exercise", "math"])
        self.assertTrue((Path(exercise.asset_path) / "solution.py").is_file())

    def test_import_text_contract_shows_validation_errors(self) -> None:
        status, _, body = call_app(
            self.app,
            method="POST",
            path="/cards/import-text",
            body="contract_text=" + quote_plus('version = 1\n\n[[cards]]\ntype = "concept"\ntitle = "Broken"\n'),
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Cannot import this contract yet", body)
        self.assertIn("requires a non-empty `prompt` field", body)

    def test_import_text_contract_is_atomic_when_a_later_card_fails(self) -> None:
        contract = (
            'version = 1\n'
            '\n'
            '[[cards]]\n'
            'type = "concept"\n'
            'title = "Concept One"\n'
            'prompt = """\n'
            'Question?\n'
            '"""\n'
            'answer = """\n'
            'Answer.\n'
            '"""\n'
            '\n'
            '[[cards]]\n'
            'type = "code_exercise"\n'
            'title = "Adder"\n'
            'topic = "python"\n'
            'slug = "shared-slug"\n'
            'prompt = """\n'
            'Implement add(a, b).\n'
            '"""\n'
            'answer_py = """\n'
            'raise NotImplementedError\n'
            '"""\n'
            'solution_py = """\n'
            'def add(a, b):\n'
            '    return a + b\n'
            '"""\n'
            'tests_py = """\n'
            'import unittest\n'
            '"""\n'
            '\n'
            '[[cards]]\n'
            'type = "code_exercise"\n'
            'title = "Adder Again"\n'
            'topic = "python"\n'
            'slug = "shared-slug"\n'
            'prompt = """\n'
            'Implement add(a, b) again.\n'
            '"""\n'
            'answer_py = """\n'
            'raise NotImplementedError\n'
            '"""\n'
            'solution_py = """\n'
            'def add(a, b):\n'
            '    return a + b\n'
            '"""\n'
            'tests_py = """\n'
            'import unittest\n'
            '"""\n'
        )

        status, _, body = call_app(
            self.app,
            method="POST",
            path="/cards/import-text",
            body="contract_text=" + quote_plus(contract),
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("already exists", body)
        self.assertEqual(list_cards(self.config), [])

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
        review_path, review_query = split_location(headers["Location"])

        status, _, review_html = call_app(self.app, method="GET", path=review_path, query_string=review_query)
        self.assertEqual(status, "200 OK")
        self.assertIn("Coding", review_html)
        self.assertIn("Create Workspace", review_html)

        status, headers, _ = call_app(
            self.app,
            method="POST",
            path=f"{review_path}/workspace",
            body="action=create&mode=exercise",
        )
        self.assertEqual(status, "303 See Other")
        attempt_id = int(review_path.rsplit("/", 1)[-1])
        attempt = get_exercise_attempt_view(self.config, attempt_id)
        self.assertIsNotNone(attempt.workspace_path)

        status, _, fail_html = call_app(
            self.app,
            method="POST",
            path=f"{review_path}/validate",
            body="mode=exercise",
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
        review_path, _ = split_location(headers["Location"])
        call_app(
            self.app,
            method="POST",
            path=f"{review_path}/workspace",
            body="action=create&mode=exercise",
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
            body="mode=exercise",
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
        self.assertIn("Review Imported Source", review_html)
        self.assertIn("external_path", review_html)
        self.assertIn("ipynb", review_html)
        self.assertIn("Candidate 1", review_html)
        self.assertIn("Candidate 2", review_html)
        self.assertIn("Delete Candidate", review_html)

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
        source_text = json.dumps(
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
            body="topic=math&source_label=tiny.ipynb&source_kind=ipynb&source_text=" + quote_plus(source_text),
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
        self.assertEqual(detail.source_kind, "ipynb")

    def test_import_preview_clamps_invalid_configured_split_mode(self) -> None:
        broken_config = replace(self.config, notebook_split_mode="agresssive")
        app = StudyWebApp(broken_config)
        notebook_path = self.root / "split.ipynb"
        notebook_path.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "markdown", "source": ["# Split\n", "Small exercise.\n"]},
                        {"cell_type": "code", "source": ["def one() -> int:\n", "    return 1\n"]},
                    ]
                }
            ),
            encoding="utf-8",
        )

        status, _, body = call_app(
            app,
            method="POST",
            path="/cards/import-notebook/preview",
            body=f"source_path={quote_plus(str(notebook_path))}&topic=test&source_label=split.ipynb",
        )
        self.assertEqual(status, "200 OK")
        self.assertIn(">balanced<", body)

    def test_import_notebook_prefills_llm_suggested_topic_and_tags(self) -> None:
        notebook_path = self.root / "metadata.ipynb"
        notebook_path.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "markdown", "source": ["# Tokenizer\n", "Tokenizer exercise.\n"]},
                        {"cell_type": "code", "source": ["def encode(text: str) -> list[str]:\n", "    return text.split()\n"]},
                    ]
                }
            ),
            encoding="utf-8",
        )

        with patch("study.notebooks._call_json_llm") as mocked_llm:
            mocked_llm.return_value.content = {
                "candidates": [
                    {
                        "topic": "nlp",
                        "tags": ["tokenizer", "python", "regex"],
                    }
                ]
            }
            mocked_llm.return_value.model = "test-model"
            status, _, review_html = call_app(
                self.app,
                method="POST",
                path="/cards/import-notebook/preview",
                body=f"source_path={quote_plus(str(notebook_path))}&topic=llm&source_label=metadata.ipynb&split_mode=balanced",
            )

        self.assertEqual(status, "200 OK")
        self.assertIn('name="topic_0" value="nlp"', review_html)
        self.assertIn('name="tags_0" value="tokenizer, python, regex"', review_html)

    def test_load_import_draft_backfills_missing_candidate_tags(self) -> None:
        draft_path = self.config.imports_dir / "legacy.json"
        draft_path.write_text(
            json.dumps(
                {
                    "draft_id": "legacy",
                    "source_mode": "external_path",
                    "source_path": "/tmp/example.ipynb",
                    "source_label": "example.ipynb",
                    "topic": "legacy",
                    "split_mode": "balanced",
                    "notebook_title": "Example",
                    "markdown_cells": 1,
                    "code_cells": 1,
                    "candidates": [
                        {
                            "title": "Legacy Candidate",
                            "prompt": "Prompt",
                            "topic": "legacy",
                            "solution_code": "def one():\n    return 1\n",
                            "answer_template": "raise NotImplementedError\n",
                            "tests_template": "import unittest\n",
                            "source_cell_spec": "cells 1-2",
                            "cell_indexes": [1, 2],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        draft = load_import_draft(self.config, "legacy")
        self.assertEqual(draft.candidates[0].tags, [])

    def test_import_notebook_can_regenerate_with_more_aggressive_split(self) -> None:
        notebook_path = self.root / "chapter.ipynb"
        notebook_path.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "markdown", "source": ["# Tokenizer\n", "Build the tokenizer pieces.\n"]},
                        {"cell_type": "code", "source": ["import re\n"]},
                        {"cell_type": "code", "source": ["def split_words(text: str) -> list[str]:\n", "    return re.findall(r\"\\w+\", text)\n"]},
                        {"cell_type": "code", "source": ["def encode_text(text: str) -> dict[str, int]:\n", "    return {token: index for index, token in enumerate(split_words(text))}\n"]},
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

        draft_id = re.search(r'name="draft_id" value="([^"]+)"', aggressive_html)
        self.assertIsNotNone(draft_id)
        status, headers, _ = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/create",
            body=(
                f"draft_id={draft_id.group(1)}"
                "&keep_0=yes&title_0=Split+Words&topic_0=llm&tags_0=python"
                "&keep_1=yes&title_1=Encode+Text&topic_1=llm&tags_1=python"
            ),
        )
        self.assertEqual(status, "303 See Other")
        first = get_card_detail(self.config, 1)
        second = get_card_detail(self.config, 2)
        second_solution = (Path(second.asset_path) / "solution.py").read_text(encoding="utf-8")
        second_answer = (Path(second.asset_path) / "answer.py").read_text(encoding="utf-8")
        self.assertIn("import re", second_solution)
        self.assertIn("def split_words", second_solution)
        self.assertIn("def encode_text", second_solution)
        self.assertIn("Supporting context preserved", second_answer)
        self.assertIn("def split_words", second_answer)

    def test_import_notebook_balanced_titles_keep_section_headings(self) -> None:
        notebook_path = self.root / "balanced-titles.ipynb"
        notebook_path.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "markdown", "source": ["# Tokenizer A\n", "First section.\n"]},
                        {"cell_type": "code", "source": ["def encode(text: str) -> list[str]:\n", "    return text.split()\n"]},
                        {"cell_type": "markdown", "source": ["# Tokenizer B\n", "Second section.\n"]},
                        {"cell_type": "code", "source": ["def encode(text: str) -> list[str]:\n", "    return text.split('-')\n"]},
                    ]
                }
            ),
            encoding="utf-8",
        )

        status, _, review_html = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/preview",
            body=f"source_path={quote_plus(str(notebook_path))}&topic=llm&source_label=balanced-titles.ipynb&split_mode=balanced",
        )
        self.assertEqual(status, "200 OK")
        self.assertIn('name="title_0" value="Tokenizer A · Reimplement encode"', review_html)
        self.assertIn('name="title_1" value="Tokenizer B · Reimplement encode"', review_html)

    def test_import_notebook_aggressive_titles_are_deduplicated_without_headings(self) -> None:
        notebook_path = self.root / "aggressive-titles.ipynb"
        notebook_path.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "code", "source": ["def encode(text: str) -> list[str]:\n", "    return text.split()\n"]},
                        {"cell_type": "code", "source": ["def encode(text: str) -> list[str]:\n", "    return text.split('-')\n"]},
                    ]
                }
            ),
            encoding="utf-8",
        )

        status, _, review_html = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/preview",
            body=f"source_path={quote_plus(str(notebook_path))}&topic=llm&source_label=aggressive-titles.ipynb&split_mode=aggressive",
        )
        self.assertEqual(status, "200 OK")
        self.assertIn('name="title_0" value="Reimplement encode"', review_html)
        self.assertIn('name="title_1" value="Reimplement encode · Part 2"', review_html)

    def test_aggressive_notebook_split_does_not_carry_independent_previous_code(self) -> None:
        notebook_path = self.root / "independent.ipynb"
        notebook_path.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "markdown", "source": ["# Utilities\n", "Independent helpers.\n"]},
                        {"cell_type": "code", "source": ["def normalize(text: str) -> str:\n", "    return text.strip().lower()\n"]},
                        {"cell_type": "code", "source": ["def count_words(text: str) -> int:\n", "    return len(text.split())\n"]},
                    ]
                }
            ),
            encoding="utf-8",
        )

        status, _, review_html = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/preview",
            body=f"source_path={quote_plus(str(notebook_path))}&topic=nlp&source_label=independent.ipynb&split_mode=aggressive",
        )
        self.assertEqual(status, "200 OK")

        draft_id = re.search(r'name="draft_id" value="([^"]+)"', review_html)
        self.assertIsNotNone(draft_id)
        status, headers, _ = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/create",
            body=(
                f"draft_id={draft_id.group(1)}"
                "&keep_0=yes&title_0=Normalize&topic_0=nlp&tags_0=python"
                "&keep_1=yes&title_1=Count+Words&topic_1=nlp&tags_1=python"
            ),
        )
        self.assertEqual(status, "303 See Other")

        second = get_card_detail(self.config, 2)
        second_solution = (Path(second.asset_path) / "solution.py").read_text(encoding="utf-8")
        self.assertIn("def count_words", second_solution)
        self.assertNotIn("def normalize", second_solution)

    def test_import_python_from_external_path_creates_standalone_candidates(self) -> None:
        source_path = self.root / "tokenizer.py"
        source_path.write_text(
            "\n".join(
                [
                    '"""Tokenizer helpers."""',
                    "",
                    "import re",
                    "",
                    "def split_words(text: str) -> list[str]:",
                    '    return re.findall(r"\\w+", text)',
                    "",
                    "def encode_text(text: str) -> dict[str, int]:",
                    "    return {token: index for index, token in enumerate(split_words(text))}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        status, _, review_html = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/preview",
            body=f"source_path={quote_plus(str(source_path))}&topic=llm&source_label=tokenizer.py&split_mode=aggressive",
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Review Imported Source", review_html)
        self.assertIn("<dd>py</dd>", review_html)
        self.assertIn("Reimplement `split_words`", review_html)
        self.assertIn("Reimplement `encode_text`", review_html)

        draft_id = re.search(r'name="draft_id" value="([^"]+)"', review_html)
        self.assertIsNotNone(draft_id)
        status, headers, _ = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/create",
            body=(
                f"draft_id={draft_id.group(1)}"
                "&keep_0=yes&title_0=Split+Words&topic_0=nlp&tags_0=python%2Ctokenizer"
                "&keep_1=yes&title_1=Encode+Text&topic_1=nlp&tags_1=python%2Ctokenizer"
            ),
        )
        self.assertEqual(status, "303 See Other")

        second = get_card_detail(self.config, 2)
        second_solution = (Path(second.asset_path) / "solution.py").read_text(encoding="utf-8")
        self.assertEqual(second.source_kind, "py")
        self.assertIn("import re", second_solution)
        self.assertIn("def split_words", second_solution)
        self.assertIn("def encode_text", second_solution)

    def test_import_python_does_not_carry_independent_previous_functions(self) -> None:
        source_path = self.root / "independent.py"
        source_path.write_text(
            "\n".join(
                [
                    "def normalize(text: str) -> str:",
                    "    return text.strip().lower()",
                    "",
                    "def count_words(text: str) -> int:",
                    "    return len(text.split())",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        status, _, review_html = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/preview",
            body=f"source_path={quote_plus(str(source_path))}&topic=nlp&source_label=independent.py&split_mode=aggressive",
        )
        self.assertEqual(status, "200 OK")

        draft_id = re.search(r'name="draft_id" value="([^"]+)"', review_html)
        self.assertIsNotNone(draft_id)
        status, headers, _ = call_app(
            self.app,
            method="POST",
            path="/cards/import-notebook/create",
            body=(
                f"draft_id={draft_id.group(1)}"
                "&keep_0=yes&title_0=Normalize&topic_0=nlp&tags_0=python"
                "&keep_1=yes&title_1=Count+Words&topic_1=nlp&tags_1=python"
            ),
        )
        self.assertEqual(status, "303 See Other")

        second = get_card_detail(self.config, 2)
        second_solution = (Path(second.asset_path) / "solution.py").read_text(encoding="utf-8")
        self.assertIn("def count_words", second_solution)
        self.assertNotIn("def normalize", second_solution)


if __name__ == "__main__":
    unittest.main()
