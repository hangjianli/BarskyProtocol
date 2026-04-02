from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Callable
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from study.analytics import build_pattern_snapshot
from study.config import StudyConfig
from study.grading import GradingError, grade_concept_answer
from study.storage import (
    add_concept_card,
    complete_concept_attempt,
    dashboard_stats,
    get_card_detail,
    get_review_attempt,
    list_cards,
    recent_reviews_for_card,
    start_review_attempt,
)


@dataclass(frozen=True)
class Response:
    status: str
    headers: list[tuple[str, str]]
    body: bytes


class StudyWebApp:
    def __init__(self, config: StudyConfig) -> None:
        self.config = config
        self.templates_dir = config.config_path.parent / "templates"
        self.static_dir = config.config_path.parent / "static"

    def __call__(self, environ: dict, start_response: Callable) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")

        if method == "GET" and path == "/":
            response = self.handle_dashboard()
        elif method == "GET" and path == "/cards":
            response = self.handle_cards()
        elif method == "GET" and re.fullmatch(r"/cards/\d+", path):
            response = self.handle_card_detail(int(path.rsplit("/", 1)[-1]))
        elif method == "GET" and path == "/cards/new/concept":
            response = self.handle_new_concept_form()
        elif method == "POST" and path == "/cards/new/concept":
            response = self.handle_new_concept_submit(environ)
        elif method == "GET" and path == "/patterns":
            response = self.handle_patterns()
        elif method == "GET" and path == "/review":
            response = self.handle_start_review()
        elif method == "GET" and re.fullmatch(r"/review/\d+", path):
            response = self.handle_review_page(int(path.rsplit("/", 1)[-1]))
        elif method == "POST" and re.fullmatch(r"/review/\d+/result", path):
            attempt_id = int(path.split("/")[-2])
            response = self.handle_review_result(environ, attempt_id)
        elif method == "GET" and path == "/static/app.css":
            response = self.handle_static_css()
        else:
            response = self.text("404 Not Found", status="404 Not Found")

        start_response(response.status, response.headers)
        return [response.body]

    def handle_dashboard(self) -> Response:
        snapshot = dashboard_stats(self.config)
        weak_topics = "".join(
            f"<li><span>{html.escape(topic)}</span><strong>{count}</strong></li>"
            for topic, count in snapshot.weak_topics
        ) or "<li><span>No failure clusters yet</span><strong>-</strong></li>"

        content = f"""
        <section class="hero">
          <h1>BarskyProtocol</h1>
          <p class="muted">A local-first study loop for concept recall and coding drills.</p>
          <div class="actions">
            <a class="button" href="/review">Start Review</a>
            <a class="button button-secondary" href="/cards/new/concept">Add Concept Card</a>
          </div>
        </section>
        <section class="grid">
          <article class="panel">
            <h2>Queue</h2>
            <dl class="stats">
              <div><dt>Total cards</dt><dd>{snapshot.total_cards}</dd></div>
              <div><dt>Due now</dt><dd>{snapshot.due_now}</dd></div>
              <div><dt>Overdue</dt><dd>{snapshot.overdue}</dd></div>
            </dl>
          </article>
          <article class="panel">
            <h2>Recent Reviews</h2>
            <dl class="stats">
              <div><dt>Pass</dt><dd>{snapshot.recent_results['pass']}</dd></div>
              <div><dt>Fail</dt><dd>{snapshot.recent_results['fail']}</dd></div>
              <div><dt>Incomplete</dt><dd>{snapshot.recent_results['incomplete']}</dd></div>
            </dl>
          </article>
        </section>
        <section class="panel">
          <h2>Weak Topics</h2>
          <ul class="list">{weak_topics}</ul>
        </section>
        """
        return self.html_page("Dashboard", content)

    def handle_cards(self) -> Response:
        cards = list_cards(self.config)
        rows = "".join(
            f"""
            <li>
              <span>
                <a href="/cards/{int(card['id'])}">{html.escape(str(card['title']))}</a>
                <small class="muted">· {html.escape(str(card['type']))} · {html.escape(str(card['topic'] or '-'))}</small>
              </span>
              <strong>box {int(card['box'])}</strong>
            </li>
            """
            for card in cards
        ) or "<li><span>No cards yet</span><strong>-</strong></li>"

        content = f"""
        <section class="panel">
          <h1>Cards</h1>
          <p class="muted">A compact view of the study set, ordered by recent activity.</p>
          <ul class="list">{rows}</ul>
        </section>
        """
        return self.html_page("Cards", content)

    def handle_card_detail(self, card_id: int) -> Response:
        card = get_card_detail(self.config, card_id)
        if card is None:
            return self.text("404 Not Found", status="404 Not Found")

        reviews = recent_reviews_for_card(self.config, card_id)
        review_rows = "".join(
            f"""
            <li>
              <span>{html.escape(str(review['reviewed_at']))} · {html.escape(str(review['result']))}</span>
              <strong>{html.escape(str(review['reason_summary']))}</strong>
            </li>
            """
            for review in reviews
        ) or "<li><span>No reviews yet</span><strong>-</strong></li>"
        tags = ", ".join(card.tags) if card.tags else "-"

        prompt_block = ""
        if card.type == "concept":
            prompt_block = f"""
            <section class="panel">
              <h2>Prompt</h2>
              <p>{html.escape(card.prompt or '')}</p>
              <details class="answer-block">
                <summary>Reveal answer</summary>
                <p>{html.escape(card.answer or '')}</p>
              </details>
            </section>
            """

        content = f"""
        <section class="panel">
          <p class="eyebrow">{html.escape(card.type)}</p>
          <h1>{html.escape(card.title)}</h1>
          <dl class="stats">
            <div><dt>Topic</dt><dd>{html.escape(card.topic or '-')}</dd></div>
            <div><dt>Tags</dt><dd>{html.escape(tags)}</dd></div>
            <div><dt>Box</dt><dd>{card.box}</dd></div>
            <div><dt>Next review</dt><dd>{html.escape(card.next_review_at)}</dd></div>
            <div><dt>Scheduler</dt><dd>{html.escape(card.scheduler_name)}</dd></div>
            <div><dt>Last result</dt><dd>{html.escape(card.last_result or '-')}</dd></div>
          </dl>
          <p>{html.escape(card.last_schedule_reason)}</p>
        </section>
        {prompt_block}
        <section class="panel">
          <h2>Recent Reviews</h2>
          <ul class="list">{review_rows}</ul>
        </section>
        """
        return self.html_page(card.title, content)

    def handle_patterns(self) -> Response:
        snapshot = build_pattern_snapshot(self.config)

        weak_topics = "".join(
            f"<li><span>{html.escape(topic)}</span><strong>{count}</strong></li>"
            for topic, count in snapshot.weak_topics
        ) or "<li><span>No topic-level failures yet</span><strong>-</strong></li>"
        high_lapse_cards = "".join(
            f'<li><span><a href="/cards/{card_id}">{html.escape(title)}</a></span><strong>{count} lapse(s)</strong></li>'
            for card_id, title, count in snapshot.high_lapse_cards
        ) or "<li><span>No lapse-heavy cards yet</span><strong>-</strong></li>"
        incomplete_cards = "".join(
            f'<li><span><a href="/cards/{card_id}">{html.escape(title)}</a></span><strong>{count} incomplete</strong></li>'
            for card_id, title, count in snapshot.incomplete_cards
        ) or "<li><span>No incomplete patterns yet</span><strong>-</strong></li>"

        content = f"""
        <section class="panel">
          <h1>Patterns</h1>
          <p class="muted">A minimal evidence view of where retention is breaking down.</p>
        </section>
        <section class="grid">
          <article class="panel">
            <h2>Weak Topics</h2>
            <ul class="list">{weak_topics}</ul>
          </article>
          <article class="panel">
            <h2>High-Lapse Cards</h2>
            <ul class="list">{high_lapse_cards}</ul>
          </article>
        </section>
        <section class="panel">
          <h2>Repeated Incompletes</h2>
          <ul class="list">{incomplete_cards}</ul>
        </section>
        """
        return self.html_page("Patterns", content)

    def handle_new_concept_form(self, errors: list[str] | None = None, values: dict[str, str] | None = None) -> Response:
        values = values or {}
        error_html = ""
        if errors:
            error_items = "".join(f"<li>{html.escape(error)}</li>" for error in errors)
            error_html = f'<section class="panel panel-error"><h2>Fix these fields</h2><ul class="errors">{error_items}</ul></section>'

        content = f"""
        {error_html}
        <section class="panel">
          <h1>New Concept Card</h1>
          <form method="post" action="/cards/new/concept" class="form-stack">
            {self._input("title", "Title", values.get("title", ""))}
            {self._input("topic", "Topic", values.get("topic", ""))}
            {self._input("tags", "Tags", values.get("tags", ""))}
            {self._textarea("prompt", "Prompt", values.get("prompt", ""))}
            {self._textarea("answer", "Answer", values.get("answer", ""))}
            {self._input("source", "Source", values.get("source", ""))}
            <div class="actions">
              <button class="button" type="submit">Create Card</button>
              <a class="button button-secondary" href="/">Cancel</a>
            </div>
          </form>
        </section>
        """
        return self.html_page("New Concept Card", content)

    def handle_new_concept_submit(self, environ: dict) -> Response:
        form = self._parse_form(environ)
        values = {key: self._first(form, key) for key in ("title", "topic", "tags", "prompt", "answer", "source")}
        errors: list[str] = []
        if not values["title"].strip():
            errors.append("Title is required.")
        if not values["prompt"].strip():
            errors.append("Prompt is required.")
        if not values["answer"].strip():
            errors.append("Answer is required.")
        if errors:
            return self.handle_new_concept_form(errors=errors, values=values)

        add_concept_card(
            self.config,
            title=values["title"],
            prompt=values["prompt"],
            answer=values["answer"],
            topic=values["topic"],
            tags=[tag.strip() for tag in values["tags"].split(",") if tag.strip()],
            source=values["source"],
        )
        return self.redirect("/")

    def handle_start_review(self) -> Response:
        attempt = start_review_attempt(self.config, card_type="concept")
        if attempt is None:
            content = """
            <section class="panel">
              <h1>No Cards Due</h1>
              <p class="muted">The concept review queue is clear for now.</p>
              <div class="actions">
                <a class="button" href="/">Back to Dashboard</a>
              </div>
            </section>
            """
            return self.html_page("No Cards Due", content)
        return self.redirect(f"/review/{int(attempt['id'])}")

    def handle_review_page(self, attempt_id: int) -> Response:
        attempt = get_review_attempt(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")

        return self.render_review_page(attempt)

    def render_review_page(
        self,
        attempt: object,
        *,
        errors: list[str] | None = None,
        user_answer: str = "",
    ) -> Response:
        if str(attempt["status"]) == "completed":
            content = f"""
            <section class="panel">
              <h1>Review Completed</h1>
              <p class="muted">Attempt {int(attempt['id'])} has already been recorded as {html.escape(str(attempt['result']))}.</p>
              <div class="actions">
                <a class="button" href="/review">Continue Review</a>
                <a class="button button-secondary" href="/">Dashboard</a>
              </div>
            </section>
            """
            return self.html_page("Review Completed", content)

        error_html = ""
        if errors:
            error_items = "".join(f"<li>{html.escape(error)}</li>" for error in errors)
            error_html = (
                f'<section class="panel panel-error"><h2>Cannot grade this answer yet</h2>'
                f'<ul class="errors">{error_items}</ul></section>'
            )

        content = f"""
        {error_html}
        <section class="panel">
          <p class="eyebrow">Concept Review</p>
          <h1>{html.escape(str(attempt['title']))}</h1>
          <p class="meta">Topic: {html.escape(str(attempt['topic'] or '-'))} · Box: {attempt['box']} · Due: {html.escape(str(attempt['next_review_at']))}</p>
        </section>
        <section class="panel">
          <h2>Prompt</h2>
          <p>{html.escape(str(attempt['prompt']))}</p>
        </section>
        <section class="panel">
          <h2>Your Answer</h2>
          <form method="post" action="/review/{int(attempt['id'])}/result" class="form-stack">
            {self._textarea("user_answer", "Type your answer before grading", user_answer, rows=8)}
            <div class="actions">
              <button class="button" type="submit" name="action" value="grade">Grade with LLM</button>
              <button class="button button-secondary" type="submit" name="action" value="incomplete">Mark Incomplete</button>
            </div>
          </form>
        </section>
        """
        return self.html_page("Concept Review", content)

    def handle_review_result(self, environ: dict, attempt_id: int) -> Response:
        form = self._parse_form(environ)
        action = self._first(form, "action")
        attempt = get_review_attempt(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")

        if action == "incomplete":
            outcome = complete_concept_attempt(self.config, attempt_id=attempt_id, result="incomplete")
            grader_summary = "The attempt was marked incomplete without grading."
            submitted_answer = ""
            model_name = "manual"
        else:
            submitted_answer = self._first(form, "user_answer").strip()
            if not submitted_answer:
                return self.render_review_page(
                    attempt,
                    errors=["Type an answer before requesting grading."],
                    user_answer=submitted_answer,
                )
            try:
                grade = grade_concept_answer(
                    self.config,
                    prompt=str(attempt["prompt"]),
                    reference_answer=str(attempt["answer"]),
                    user_answer=submitted_answer,
                )
            except GradingError as exc:
                return self.render_review_page(
                    attempt,
                    errors=[str(exc)],
                    user_answer=submitted_answer,
                )
            outcome = complete_concept_attempt(
                self.config,
                attempt_id=attempt_id,
                result=grade.result,
                validator_summary=grade.summary,
                failure_reason=grade.summary if grade.result == "fail" else None,
            )
            grader_summary = grade.summary
            model_name = grade.model

        schedule = outcome.schedule

        content = f"""
        <section class="panel">
          <p class="eyebrow">Review Result</p>
          <h1>{html.escape(outcome.title)}</h1>
          <p class="status status-{html.escape(outcome.result)}">Result: {html.escape(outcome.result)}</p>
        </section>
        <section class="panel">
          <h2>Grading</h2>
          <dl class="stats">
            <div><dt>Grader</dt><dd>{html.escape(model_name)}</dd></div>
          </dl>
          <p>{html.escape(grader_summary)}</p>
          <details class="answer-block">
            <summary>Your answer</summary>
            <p>{html.escape(submitted_answer) or "<em>No answer submitted.</em>"}</p>
          </details>
          <details class="answer-block">
            <summary>Reference answer</summary>
            <p>{html.escape(outcome.answer)}</p>
          </details>
        </section>
        <section class="panel">
          <h2>Scheduling</h2>
          <dl class="stats">
            <div><dt>Scheduler</dt><dd>{html.escape(schedule.scheduler_name)}</dd></div>
            <div><dt>Previous box</dt><dd>{schedule.prior_box}</dd></div>
            <div><dt>New box</dt><dd>{schedule.new_box}</dd></div>
            <div><dt>Previous interval</dt><dd>{schedule.previous_interval_days if schedule.previous_interval_days is not None else "new"}</dd></div>
            <div><dt>New interval</dt><dd>{schedule.new_interval_days} day(s)</dd></div>
            <div><dt>Next review</dt><dd>{html.escape(schedule.next_review_at)}</dd></div>
          </dl>
          <p>{html.escape(schedule.reason_summary)}</p>
        </section>
        <section class="actions">
          <a class="button" href="/review">Continue Review</a>
          <a class="button button-secondary" href="/">Dashboard</a>
        </section>
        """
        return self.html_page("Review Result", content)

    def handle_static_css(self) -> Response:
        css_path = self.static_dir / "app.css"
        body = css_path.read_bytes()
        return Response(
            status="200 OK",
            headers=[("Content-Type", "text/css; charset=utf-8"), ("Content-Length", str(len(body)))],
            body=body,
        )

    def html_page(self, title: str, content: str) -> Response:
        template = Template((self.templates_dir / "layout.html").read_text(encoding="utf-8"))
        body = template.substitute(title=html.escape(title), content=content)
        payload = body.encode("utf-8")
        return Response(
            status="200 OK",
            headers=[("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(payload)))],
            body=payload,
        )

    def text(self, text: str, *, status: str = "200 OK") -> Response:
        payload = text.encode("utf-8")
        return Response(
            status=status,
            headers=[("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(payload)))],
            body=payload,
        )

    def redirect(self, location: str) -> Response:
        return Response(status="303 See Other", headers=[("Location", location)], body=b"")

    def _parse_form(self, environ: dict) -> dict[str, list[str]]:
        length = int(environ.get("CONTENT_LENGTH") or "0")
        raw_body = environ["wsgi.input"].read(length).decode("utf-8")
        return parse_qs(raw_body, keep_blank_values=True)

    def _first(self, form: dict[str, list[str]], key: str) -> str:
        return form.get(key, [""])[0]

    def _input(self, name: str, label: str, value: str) -> str:
        safe_value = html.escape(value)
        return (
            f'<label><span>{html.escape(label)}</span>'
            f'<input type="text" name="{html.escape(name)}" value="{safe_value}"></label>'
        )

    def _textarea(self, name: str, label: str, value: str, *, rows: int = 6) -> str:
        safe_value = html.escape(value)
        return (
            f'<label><span>{html.escape(label)}</span>'
            f'<textarea name="{html.escape(name)}" rows="{rows}">{safe_value}</textarea></label>'
        )


def serve_app(*, config: StudyConfig, host: str, port: int) -> None:
    app = StudyWebApp(config)
    print(f"BarskyProtocol listening on http://{host}:{port}")
    with make_server(host, port, app) as server:
        server.serve_forever()
