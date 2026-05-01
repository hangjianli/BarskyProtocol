from __future__ import annotations

import html
import json
import re
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Callable
from urllib.parse import parse_qs, unquote, urlencode
from wsgiref.simple_server import make_server

from study.analytics import build_pattern_snapshot, build_recommendations
from study.card_contract import CardContractError, import_cards_from_contract
from study.config import StudyConfig
from study.exercises import cleanup_workspace, create_workspace, scaffold_exercise_assets
from study.grading import GradingError, grade_concept_answer
from study.notebooks import (
    build_import_draft,
    delete_import_draft,
    load_import_draft,
    load_source_text_from_path,
    save_managed_source,
)
from study.storage import (
    add_concept_card,
    delete_card,
    add_exercise_card,
    adjacent_review_card_id,
    complete_concept_attempt,
    complete_exercise_attempt,
    dashboard_stats,
    get_card_detail,
    get_exercise_attempt_view,
    get_or_create_review_attempt_for_card,
    get_review_attempt,
    list_cards,
    recent_reviews_for_card,
    start_review_attempt,
    update_card,
    update_attempt_workspace,
)
from study.validators import run_exercise_tests


@dataclass(frozen=True)
class Response:
    status: str
    headers: list[tuple[str, str]]
    body: bytes


@dataclass(frozen=True)
class SourceReference:
    path: Path
    start_line: int | None
    end_line: int | None


class StudyWebApp:
    def __init__(self, config: StudyConfig) -> None:
        self.config = config
        self.templates_dir = config.config_path.parent / "templates"
        self.static_dir = config.config_path.parent / "static"

    def __call__(self, environ: dict, start_response: Callable) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)

        if method == "GET" and path == "/":
            response = self.handle_dashboard()
        elif method == "GET" and path == "/cards":
            response = self.handle_cards()
        elif method == "GET" and re.fullmatch(r"/cards/\d+/source", path):
            response = self.handle_card_source_view(int(path.split("/")[-2]), query)
        elif method == "GET" and re.fullmatch(r"/cards/\d+/edit", path):
            response = self.handle_card_edit_form(int(path.split("/")[-2]))
        elif method == "POST" and re.fullmatch(r"/cards/\d+/edit", path):
            response = self.handle_card_edit_submit(int(path.split("/")[-2]), environ)
        elif method == "GET" and re.fullmatch(r"/cards/\d+", path):
            response = self.handle_card_detail(int(path.rsplit("/", 1)[-1]))
        elif method == "POST" and re.fullmatch(r"/cards/\d+/delete", path):
            response = self.handle_card_delete(int(path.split("/")[-2]))
        elif method == "GET" and path == "/cards/new/concept":
            response = self.handle_new_concept_form()
        elif method == "POST" and path == "/cards/new/concept":
            response = self.handle_new_concept_submit(environ)
        elif method == "GET" and path == "/cards/new/exercise":
            response = self.handle_new_exercise_form()
        elif method == "POST" and path == "/cards/new/exercise":
            response = self.handle_new_exercise_submit(environ)
        elif method == "GET" and path == "/cards/import-text":
            response = self.handle_import_text_form()
        elif method == "POST" and path == "/cards/import-text":
            response = self.handle_import_text_submit(environ)
        elif method == "GET" and path == "/cards/import-text/result":
            response = self.handle_import_text_result(query)
        elif method == "GET" and path == "/cards/import-notebook":
            response = self.handle_import_notebook_form()
        elif method == "POST" and path == "/cards/import-notebook/preview":
            response = self.handle_import_notebook_preview(environ)
        elif method == "POST" and path == "/cards/import-notebook/regenerate":
            response = self.handle_import_notebook_regenerate(environ)
        elif method == "POST" and path == "/cards/import-notebook/create":
            response = self.handle_import_notebook_create(environ)
        elif method == "GET" and path == "/patterns":
            response = self.handle_patterns()
        elif method == "GET" and path == "/recommendations":
            response = self.handle_recommendations()
        elif method == "GET" and path == "/review":
            response = self.handle_start_review(query)
        elif method == "GET" and re.fullmatch(r"/review/\d+/source", path):
            response = self.handle_review_source_view(int(path.split("/")[-2]), query)
        elif method == "GET" and re.fullmatch(r"/review/\d+/navigate", path):
            response = self.handle_review_navigation(int(path.split("/")[-2]), query)
        elif method == "GET" and re.fullmatch(r"/review/\d+", path):
            response = self.handle_review_page(int(path.rsplit("/", 1)[-1]), query)
        elif method == "POST" and re.fullmatch(r"/review/\d+/result", path):
            attempt_id = int(path.split("/")[-2])
            response = self.handle_review_result(environ, attempt_id)
        elif method == "POST" and re.fullmatch(r"/review/\d+/workspace", path):
            attempt_id = int(path.split("/")[-2])
            response = self.handle_exercise_workspace(environ, attempt_id)
        elif method == "POST" and re.fullmatch(r"/review/\d+/validate", path):
            attempt_id = int(path.split("/")[-2])
            response = self.handle_exercise_validate(environ, attempt_id)
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
          <div class="hero-controls">
            <a class="button" href="/review?mode=mixed">Start Review</a>
            <a class="button button-secondary" href="/review?mode=mixed&amp;order=random">Shuffle Eligible</a>
            <label class="hero-select">
              <span>Quick actions</span>
              <select onchange="if (this.value) window.location = this.value;">
                <option value="">Choose an action</option>
                <optgroup label="Review">
                  <option value="/review?mode=concept">Concept queue</option>
                  <option value="/review?mode=exercise">Coding queue</option>
                </optgroup>
                <optgroup label="Create">
                  <option value="/cards/new/concept">New concept card</option>
                  <option value="/cards/new/exercise">New exercise</option>
                  <option value="/cards/import-text">Paste card contract</option>
                  <option value="/cards/import-notebook">Import source</option>
                </optgroup>
              </select>
            </label>
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
              <span class="list-primary">
                <a href="/cards/{int(card['id'])}">{html.escape(str(card['title']))}</a>
                <small class="muted">{self._render_card_badges(str(card['type']), str(card['topic'] or ''))}</small>
              </span>
              <strong>box {int(card['box'])} · created {html.escape(self._format_timestamp(str(card['created_at'])))}</strong>
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
              <span>{html.escape(self._format_timestamp(str(review['reviewed_at'])))} · {html.escape(str(review['result']))}</span>
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
              <div class="markdown-content">{self._render_markdown(card.prompt or '', card_id=card.id)}</div>
              <details class="answer-block">
                <summary>Reveal answer</summary>
                <div class="markdown-content">{self._render_markdown(card.answer or '', card_id=card.id)}</div>
              </details>
            </section>
            """
        elif card.type == "code_exercise":
            exercise_prompt = self._exercise_prompt_text(card)
            prompt_block = f"""
            <section class="panel">
              <h2>Prompt</h2>
              <div class="markdown-content">{self._render_markdown(exercise_prompt, card_id=card.id)}</div>
            </section>
            <section class="panel">
              <h2>Exercise Files</h2>
              <dl class="stats">
                <div><dt>Asset path</dt><dd>{html.escape(card.asset_path)}</dd></div>
                <div><dt>Prompt file</dt><dd>{html.escape(card.prompt_path or '-')}</dd></div>
                <div><dt>Entry point</dt><dd>{html.escape(card.entrypoint or '-')}</dd></div>
                <div><dt>Tests</dt><dd>{html.escape(card.tests_path or '-')}</dd></div>
              </dl>
            </section>
            """

        content = f"""
        <section class="panel">
          <p class="eyebrow">{html.escape(self._card_type_label(card.type))}</p>
          <h1>{html.escape(card.title)}</h1>
          <p class="meta">{self._render_card_badges(card.type, card.topic)}</p>
          <dl class="stats">
            <div><dt>Topic</dt><dd>{html.escape(card.topic or '-')}</dd></div>
            <div><dt>Tags</dt><dd>{html.escape(tags)}</dd></div>
            <div><dt>Box</dt><dd>{card.box}</dd></div>
            <div><dt>Next review</dt><dd>{html.escape(self._format_date(card.next_review_at))}</dd></div>
            <div><dt>Scheduler</dt><dd>{html.escape(card.scheduler_name)}</dd></div>
            <div><dt>Last result</dt><dd>{html.escape(card.last_result or '-')}</dd></div>
            <div><dt>Source label</dt><dd>{html.escape(card.source_label or card.source or '-')}</dd></div>
            <div><dt>Source path</dt><dd>{html.escape(card.source_path or '-')}</dd></div>
            <div><dt>Source mode</dt><dd>{html.escape(card.source_mode or '-')}</dd></div>
            <div><dt>Source kind</dt><dd>{html.escape(card.source_kind or '-')}</dd></div>
            <div><dt>Source cells</dt><dd>{html.escape(card.source_cell_spec or '-')}</dd></div>
            <div><dt>Import options</dt><dd>{html.escape(card.source_import_options or '-')}</dd></div>
          </dl>
          <p>{html.escape(card.last_schedule_reason)}</p>
        </section>
        {prompt_block}
        {self._references_panel(card.references)}
        <section class="panel">
          <h2>Card Actions</h2>
          <p class="muted">Edit this card or delete it if it should no longer be part of the study set.</p>
          <div class="actions">
            <a class="button button-secondary" href="/cards/{card.id}/edit">Edit Card</a>
          </div>
          <form method="post" action="/cards/{card.id}/delete" class="actions">
            <button class="button button-danger" type="submit" onclick="return confirm('Delete this card and its review history?');">Delete Card</button>
          </form>
        </section>
        <section class="panel">
          <h2>Recent Reviews</h2>
          <ul class="list">{review_rows}</ul>
        </section>
        """
        return self.html_page(card.title, content)

    def handle_card_delete(self, card_id: int) -> Response:
        if not delete_card(self.config, card_id):
            return self.text("404 Not Found", status="404 Not Found")
        return self.redirect("/cards")

    def handle_card_edit_form(
        self,
        card_id: int,
        *,
        errors: list[str] | None = None,
        values: dict[str, str] | None = None,
    ) -> Response:
        card = get_card_detail(self.config, card_id)
        if card is None:
            return self.text("404 Not Found", status="404 Not Found")

        values = values or self._card_form_values(card)
        error_html = ""
        if errors:
            error_items = "".join(f"<li>{html.escape(error)}</li>" for error in errors)
            error_html = f'<section class="panel panel-error"><h2>Fix these fields</h2><ul class="errors">{error_items}</ul></section>'

        if card.type == "concept":
            content = f"""
            {error_html}
            <section class="panel">
              <h1>Edit Concept Card</h1>
              <form method="post" action="/cards/{card.id}/edit" class="form-stack">
                {self._input("title", "Title", values.get("title", ""))}
                {self._input("topic", "Topic", values.get("topic", ""))}
                {self._input("tags", "Tags", values.get("tags", ""))}
                {self._input("source", "Source", values.get("source", ""))}
                {self._textarea("prompt", "Prompt", values.get("prompt", ""), rows=10)}
                {self._textarea("answer", "Answer", values.get("answer", ""), rows=8)}
                <div class="actions">
                  <button class="button" type="submit">Save Changes</button>
                  <a class="button button-secondary" href="/cards/{card.id}">Cancel</a>
                </div>
              </form>
            </section>
            """
            return self.html_page(f"Edit · {card.title}", content)

        content = f"""
        {error_html}
        <section class="panel">
          <h1>Edit Exercise Card</h1>
          <form method="post" action="/cards/{card.id}/edit" class="form-stack">
            {self._input("title", "Title", values.get("title", ""))}
            {self._input("topic", "Topic", values.get("topic", ""))}
            {self._input("tags", "Tags", values.get("tags", ""))}
            {self._input("source", "Source", values.get("source", ""))}
            {self._textarea("prompt", "Prompt", values.get("prompt", ""), rows=12)}
            {self._textarea("answer_py", "answer.py", values.get("answer_py", ""), rows=12)}
            {self._textarea("solution_py", "solution.py", values.get("solution_py", ""), rows=12)}
            {self._textarea("tests_py", "tests.py", values.get("tests_py", ""), rows=14)}
            <div class="actions">
              <button class="button" type="submit">Save Changes</button>
              <a class="button button-secondary" href="/cards/{card.id}">Cancel</a>
            </div>
          </form>
        </section>
        """
        return self.html_page(f"Edit · {card.title}", content)

    def handle_card_edit_submit(self, card_id: int, environ: dict) -> Response:
        card = get_card_detail(self.config, card_id)
        if card is None:
            return self.text("404 Not Found", status="404 Not Found")

        form = self._parse_form(environ)
        values = {key: self._first(form, key) for key in ("title", "topic", "tags", "source", "prompt", "answer")}
        if card.type == "code_exercise":
            values["answer_py"] = self._first(form, "answer_py")
            values["solution_py"] = self._first(form, "solution_py")
            values["tests_py"] = self._first(form, "tests_py")

        errors: list[str] = []
        if not values["title"].strip():
            errors.append("Title is required.")
        if not values["prompt"].strip():
            errors.append("Prompt is required.")
        if card.type == "concept" and not values["answer"].strip():
            errors.append("Answer is required.")
        if card.type == "code_exercise":
            for field_name in ("answer_py", "solution_py", "tests_py"):
                if not values[field_name].strip():
                    errors.append(f"{field_name} is required.")
        if errors:
            return self.handle_card_edit_form(card_id, errors=errors, values=values)

        update_card(
            self.config,
            card_id=card_id,
            title=values["title"],
            topic=values["topic"],
            tags=[tag.strip() for tag in values["tags"].split(",") if tag.strip()],
            source=values["source"],
            prompt=values["prompt"],
            answer=values.get("answer"),
            answer_body=values.get("answer_py"),
            solution_body=values.get("solution_py"),
            tests_body=values.get("tests_py"),
        )
        return self.redirect(f"/cards/{card_id}")

    def handle_card_source_view(self, card_id: int, query: dict[str, list[str]]) -> Response:
        card = get_card_detail(self.config, card_id)
        if card is None:
            return self.text("404 Not Found", status="404 Not Found")
        return self._render_source_view(
            card=card,
            query=query,
            back_href=f"/cards/{card_id}",
            source_title=card.title,
        )

    def handle_review_source_view(self, attempt_id: int, query: dict[str, list[str]]) -> Response:
        attempt = get_review_attempt(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")
        card = get_card_detail(self.config, int(attempt["card_id"]))
        if card is None:
            return self.text("404 Not Found", status="404 Not Found")
        queue_mode = self._review_mode(query)
        review_order = self._review_order(query)
        return self._render_source_view(
            card=card,
            query=query,
            back_href=self._review_href(attempt_id, queue_mode=queue_mode, review_order=review_order),
            source_title=str(attempt["title"]),
        )

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

    def handle_recommendations(self) -> Response:
        recommendations = build_recommendations(self.config)
        items = "".join(
            f"""
            <li>
              <span>
                <strong>{html.escape(recommendation.category)}</strong><br>
                {html.escape(recommendation.action)}
                <small class="muted"><br>{html.escape(recommendation.evidence)}</small>
              </span>
            </li>
            """
            for recommendation in recommendations
        ) or "<li><span>No recommendations yet. Review more cards to generate evidence.</span></li>"

        content = f"""
        <section class="panel">
          <h1>Recommendations</h1>
          <p class="muted">Direct next steps derived from observed review failures and incompletes.</p>
          <ul class="list">{items}</ul>
        </section>
        """
        return self.html_page("Recommendations", content)

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
            {self._textarea("references", "References", values.get("references", ""), rows=8)}
            {self._input("source", "Source", values.get("source", ""))}
            <div class="actions">
              <button class="button" type="submit">Create Card</button>
              <a class="button button-secondary" href="/">Cancel</a>
            </div>
          </form>
        </section>
        """
        return self.html_page("New Concept Card", content)

    def handle_new_exercise_form(self, errors: list[str] | None = None, values: dict[str, str] | None = None) -> Response:
        values = values or {}
        error_html = ""
        if errors:
            error_items = "".join(f"<li>{html.escape(error)}</li>" for error in errors)
            error_html = f'<section class="panel panel-error"><h2>Fix these fields</h2><ul class="errors">{error_items}</ul></section>'

        content = f"""
        {error_html}
        <section class="panel">
          <h1>New Exercise</h1>
          <form method="post" action="/cards/new/exercise" class="form-stack">
            {self._input("title", "Title", values.get("title", ""))}
            {self._input("topic", "Topic", values.get("topic", ""))}
            {self._input("tags", "Tags", values.get("tags", ""))}
            {self._textarea("prompt", "Prompt", values.get("prompt", ""), rows=10)}
            {self._textarea("references", "References", values.get("references", ""), rows=8)}
            {self._input("source", "Source", values.get("source", ""))}
            <div class="actions">
              <button class="button" type="submit">Create Exercise</button>
              <a class="button button-secondary" href="/">Cancel</a>
            </div>
          </form>
        </section>
        """
        return self.html_page("New Exercise", content)

    def handle_import_text_form(
        self,
        errors: list[str] | None = None,
        values: dict[str, str] | None = None,
    ) -> Response:
        values = values or {}
        error_html = ""
        if errors:
            error_items = "".join(f"<li>{html.escape(error)}</li>" for error in errors)
            error_html = f'<section class="panel panel-error"><h2>Cannot import this contract yet</h2><ul class="errors">{error_items}</ul></section>'

        content = f"""
        {error_html}
        <section class="panel">
          <h1>Import Text Cards</h1>
          <p class="muted">Paste a TOML card contract generated by any model or written by hand.</p>
          <form method="post" action="/cards/import-text" class="form-stack">
            {self._textarea("contract_text", "Card contract", values.get("contract_text", self._contract_example()), rows=28)}
            <div class="actions">
              <button class="button" type="submit">Import Cards</button>
              <a class="button button-secondary" href="/">Cancel</a>
            </div>
          </form>
        </section>
        """
        return self.html_page("Import Text Cards", content)

    def handle_import_notebook_form(
        self,
        errors: list[str] | None = None,
        values: dict[str, str] | None = None,
    ) -> Response:
        values = values or {}
        error_html = ""
        if errors:
            error_items = "".join(f"<li>{html.escape(error)}</li>" for error in errors)
            error_html = f'<section class="panel panel-error"><h2>Cannot parse this source yet</h2><ul class="errors">{error_items}</ul></section>'

        content = f"""
        {error_html}
        <section class="panel">
          <h1>Import Source</h1>
          <p class="muted">Provide a `.ipynb` or `.py` path for no-copy import, or drop a file to create a managed source snapshot.</p>
          <form method="post" action="/cards/import-notebook/preview" class="form-stack" id="notebook-import-form">
            {self._input("source_path", "Source path", values.get("source_path", ""))}
            {self._input("topic", "Default topic", values.get("topic", ""))}
            {self._input("source_label", "Source label", values.get("source_label", ""))}
            {self._select("split_mode", "Split mode", values.get("split_mode", self.config.notebook_split_mode), [("balanced", "Balanced"), ("aggressive", "Aggressive")])}
            <input type="hidden" name="source_text" id="source-text" value="">
            <input type="hidden" name="source_kind" id="source-kind" value="">
            <section class="dropzone" id="notebook-dropzone">
              <h2>Drop `.ipynb`, `.py`, or a path</h2>
              <p class="muted">Drop a supported source file, paste a path, or click to choose a local file.</p>
              <input type="file" id="notebook-file" accept=".ipynb,.py">
            </section>
            <div class="actions">
              <button class="button" type="submit">Preview Candidates</button>
              <a class="button button-secondary" href="/">Cancel</a>
            </div>
          </form>
        </section>
        {self._import_notebook_script()}
        """
        return self.html_page("Import Source", content)

    def handle_import_text_submit(self, environ: dict) -> Response:
        form = self._parse_form(environ)
        contract_text = self._first(form, "contract_text")
        try:
            created_ids = import_cards_from_contract(self.config, contract_text)
        except CardContractError as exc:
            return self.handle_import_text_form(errors=[str(exc)], values={"contract_text": contract_text})
        except FileExistsError as exc:
            return self.handle_import_text_form(
                errors=[f"An exercise asset already exists for this card import: {exc.filename or exc}"],
                values={"contract_text": contract_text},
            )

        if not created_ids:
            return self.handle_import_text_form(
                errors=["The contract did not create any cards."],
                values={"contract_text": contract_text},
            )
        query = urlencode([("ids", str(card_id)) for card_id in created_ids])
        return self.redirect(f"/cards/import-text/result?{query}")

    def handle_import_text_result(self, query: dict[str, list[str]]) -> Response:
        raw_ids = query.get("ids", [])
        if not raw_ids:
            return self.redirect("/cards")

        created_cards = []
        for raw_id in raw_ids:
            try:
                card_id = int(raw_id)
            except ValueError:
                continue
            card = get_card_detail(self.config, card_id)
            if card is not None:
                created_cards.append(card)

        if not created_cards:
            return self.redirect("/cards")

        rows = "".join(
            f"""
            <li>
              <span>
                <a href="/cards/{card.id}">{html.escape(card.title)}</a>
                <small class="muted">· {html.escape(card.type)} · {html.escape(card.topic or '-')}</small>
              </span>
              <strong>box {card.box} · created {html.escape(self._format_timestamp(card.created_at))}</strong>
            </li>
            """
            for card in created_cards
        )

        content = f"""
        <section class="panel">
          <h1>Imported Cards</h1>
          <p class="muted">Created {len(created_cards)} card(s) from the pasted contract.</p>
          <ul class="list">{rows}</ul>
          <div class="actions">
            <a class="button" href="/cards">View All Cards</a>
            <a class="button button-secondary" href="/cards/import-text">Import Another Contract</a>
          </div>
        </section>
        """
        return self.html_page("Imported Cards", content)

    def handle_new_concept_submit(self, environ: dict) -> Response:
        form = self._parse_form(environ)
        values = {key: self._first(form, key) for key in ("title", "topic", "tags", "prompt", "answer", "references", "source")}
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
            references=values["references"],
        )
        return self.redirect("/")

    def handle_new_exercise_submit(self, environ: dict) -> Response:
        form = self._parse_form(environ)
        values = {key: self._first(form, key) for key in ("title", "topic", "tags", "prompt", "references", "source")}
        errors: list[str] = []
        if not values["title"].strip():
            errors.append("Title is required.")
        if not values["prompt"].strip():
            errors.append("Prompt is required.")
        if errors:
            return self.handle_new_exercise_form(errors=errors, values=values)

        try:
            files = scaffold_exercise_assets(
                self.config,
                title=values["title"],
                topic=values["topic"],
                prompt=values["prompt"],
            )
        except FileExistsError:
            return self.handle_new_exercise_form(
                errors=["An exercise with the same generated slug already exists."],
                values=values,
            )

        card_id = add_exercise_card(
            self.config,
            title=values["title"],
            topic=values["topic"],
            tags=[tag.strip() for tag in values["tags"].split(",") if tag.strip()],
            source=values["source"],
            references=values["references"],
            files=files,
        )
        return self.redirect(f"/cards/{card_id}")

    def handle_import_notebook_preview(self, environ: dict) -> Response:
        form = self._parse_form(environ)
        values = {
            key: self._first(form, key)
            for key in ("source_path", "topic", "source_label", "source_text", "source_kind", "split_mode")
        }
        try:
            source_path, source_mode, source_label, source_kind, source_text = self._resolve_source(values)
            draft = build_import_draft(
                self.config,
                source_path=source_path,
                source_mode=source_mode,
                source_label=source_label,
                source_kind=source_kind,
                topic=values["topic"],
                split_mode=self._normalized_split_mode(values.get("split_mode", "")),
                source_text=source_text,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            return self.handle_import_notebook_form(errors=[str(exc)], values=values)

        if not draft.candidates:
            return self.handle_import_notebook_form(
                errors=["No code exercise candidates were found in this source."],
                values=values,
            )
        return self.render_import_review_page(draft)

    def render_import_review_page(self, draft: object, errors: list[str] | None = None) -> Response:
        error_html = ""
        if errors:
            error_items = "".join(f"<li>{html.escape(error)}</li>" for error in errors)
            error_html = f'<section class="panel panel-error"><h2>Cannot create cards yet</h2><ul class="errors">{error_items}</ul></section>'

        candidate_sections = []
        for index, candidate in enumerate(draft.candidates):
            topic_value = candidate.topic or draft.topic
            tags_value = ", ".join(candidate.tags)
            candidate_sections.append(
                f"""
                <article class="panel import-candidate" data-candidate-index="{index}">
                  <h2>Candidate {index + 1}</h2>
                  <div class="candidate-actions">
                    <div class="checkbox-row">
                    <input type="checkbox" id="keep_{index}" name="keep_{index}" value="yes" checked>
                    <label for="keep_{index}">Create this exercise card</label>
                    </div>
                    <button class="button button-danger" type="button" data-delete-candidate="{index}">Delete Candidate</button>
                  </div>
                  {self._input(f"title_{index}", "Title", candidate.title)}
                  {self._input(f"topic_{index}", "Topic", topic_value)}
                  {self._input(f"tags_{index}", "Tags", tags_value)}
                  <p class="muted">Source section: {html.escape(candidate.source_cell_spec)}</p>
                  <details class="answer-block" open>
                    <summary>Prompt preview</summary>
                    <pre class="code-block">{html.escape(candidate.prompt)}</pre>
                  </details>
                  <details class="answer-block">
                    <summary>Solution preview</summary>
                    <pre class="code-block">{html.escape(candidate.solution_code)}</pre>
                  </details>
                </article>
                """
            )

        content = f"""
        {error_html}
        <section class="panel">
          <h1>Review Imported Source</h1>
          <dl class="stats">
            <div><dt>Source</dt><dd>{html.escape(draft.source_label)}</dd></div>
            <div><dt>Source path</dt><dd>{html.escape(draft.source_path)}</dd></div>
            <div><dt>Source mode</dt><dd>{html.escape(draft.source_mode)}</dd></div>
            <div><dt>Source kind</dt><dd>{html.escape(draft.source_kind)}</dd></div>
            <div><dt>Split mode</dt><dd>{html.escape(draft.split_mode)}</dd></div>
            <div><dt>Source title</dt><dd>{html.escape(draft.source_title)}</dd></div>
            <div><dt>Prose sections</dt><dd>{draft.prose_sections}</dd></div>
            <div><dt>Code sections</dt><dd>{draft.code_sections}</dd></div>
            <div><dt>Candidates</dt><dd>{len(draft.candidates)}</dd></div>
          </dl>
        </section>
        <section class="panel">
          <h2>Regenerate Draft</h2>
          <p class="muted">Change split aggressiveness and rebuild the draft from the same source.</p>
          <form method="post" action="/cards/import-notebook/regenerate" class="form-stack">
            <input type="hidden" name="draft_id" value="{html.escape(draft.draft_id)}">
            {self._select("split_mode", "Split mode", draft.split_mode, [("balanced", "Balanced"), ("aggressive", "Aggressive")])}
            <div class="actions">
              <button class="button button-secondary" type="submit">Regenerate Draft</button>
            </div>
          </form>
        </section>
        <form method="post" action="/cards/import-notebook/create" class="form-stack">
          <input type="hidden" name="draft_id" value="{html.escape(draft.draft_id)}">
          {''.join(candidate_sections)}
          <div class="actions">
            <button class="button" type="submit">Create Selected Cards</button>
            <a class="button button-secondary" href="/cards/import-notebook">Start Over</a>
          </div>
        </form>
        {self._import_review_script()}
        """
        return self.html_page("Review Source Import", content)

    def handle_import_notebook_regenerate(self, environ: dict) -> Response:
        form = self._parse_form(environ)
        draft_id = self._first(form, "draft_id")
        try:
            existing_draft = load_import_draft(self.config, draft_id)
            source_path, source_mode, source_label, source_kind, source_text = self._resolve_draft_source(existing_draft)
            regenerated = build_import_draft(
                self.config,
                source_path=source_path,
                source_mode=source_mode,
                source_label=source_label,
                source_kind=source_kind,
                topic=existing_draft.topic,
                split_mode=self._normalized_split_mode(self._first(form, "split_mode")),
                source_text=source_text,
                draft_id=existing_draft.draft_id,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            return self.handle_import_notebook_form(errors=[str(exc)])

        if not regenerated.candidates:
            return self.render_import_review_page(
                regenerated,
                errors=["No code exercise candidates were found with the selected split mode."],
            )
        return self.render_import_review_page(regenerated)

    def handle_import_notebook_create(self, environ: dict) -> Response:
        form = self._parse_form(environ)
        draft_id = self._first(form, "draft_id")
        try:
            draft = load_import_draft(self.config, draft_id)
        except ValueError as exc:
            return self.handle_import_notebook_form(errors=[str(exc)])

        created_ids: list[int] = []
        for index, candidate in enumerate(draft.candidates):
            if self._first(form, f"keep_{index}") != "yes":
                continue

            title = self._first(form, f"title_{index}").strip() or candidate.title
            topic = self._first(form, f"topic_{index}").strip() or candidate.topic or draft.topic
            tags = [tag.strip() for tag in self._first(form, f"tags_{index}").split(",") if tag.strip()]
            try:
                files = scaffold_exercise_assets(
                    self.config,
                    title=title,
                    topic=topic,
                    prompt=candidate.prompt,
                    answer_body=candidate.answer_template,
                    solution_body=candidate.solution_code,
                    tests_body=candidate.tests_template,
                )
            except FileExistsError:
                return self.render_import_review_page(
                    draft,
                    errors=[f"An exercise with the generated slug for '{title}' already exists."],
                )

            card_id = add_exercise_card(
                self.config,
                title=title,
                topic=topic,
                tags=tags,
                source=draft.source_label,
                source_path=draft.source_path,
                source_mode=draft.source_mode,
                source_label=draft.source_label,
                source_kind=draft.source_kind,
                source_cell_spec=candidate.source_cell_spec,
                source_import_options=json.dumps({"split_mode": draft.split_mode, "source_kind": draft.source_kind}),
                files=files,
            )
            created_ids.append(card_id)

        if not created_ids:
            return self.render_import_review_page(draft, errors=["Select at least one candidate to create cards."])
        delete_import_draft(self.config, draft.draft_id)
        return self.redirect(f"/cards/{created_ids[0]}")

    def handle_start_review(self, query: dict[str, list[str]]) -> Response:
        mode = self._review_mode(query)
        review_order = self._review_order(query)
        card_type = None if mode == "mixed" else ("concept" if mode == "concept" else "code_exercise")
        attempt = start_review_attempt(self.config, card_type=card_type, review_order=review_order)
        if attempt is None:
            content = """
            <section class="panel">
              <h1>No Cards Due</h1>
              <p class="muted">The selected review queue is clear for now.</p>
              <div class="actions">
                <a class="button" href="/">Back to Dashboard</a>
              </div>
            </section>
            """
            return self.html_page("No Cards Due", content)
        return self.redirect(self._review_href(int(attempt["id"]), queue_mode=mode, review_order=review_order))

    def handle_review_navigation(self, attempt_id: int, query: dict[str, list[str]]) -> Response:
        attempt = get_review_attempt(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")

        queue_mode = self._review_mode(query)
        review_order = self._review_order(query)
        adjacent_card_id = adjacent_review_card_id(
            self.config,
            current_card_id=int(attempt["card_id"]),
            queue_mode=queue_mode,
            direction=query.get("direction", [""])[0],
        )
        if adjacent_card_id is None:
            return self.redirect(self._review_href(attempt_id, queue_mode=queue_mode, review_order=review_order))

        adjacent_attempt = get_or_create_review_attempt_for_card(self.config, card_id=adjacent_card_id)
        if adjacent_attempt is None:
            return self.redirect(self._review_href(attempt_id, queue_mode=queue_mode, review_order=review_order))
        return self.redirect(
            self._review_href(int(adjacent_attempt["id"]), queue_mode=queue_mode, review_order=review_order)
        )

    def handle_review_page(self, attempt_id: int, query: dict[str, list[str]]) -> Response:
        attempt = get_review_attempt(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")
        queue_mode = self._review_mode(query)
        review_order = self._review_order(query)

        if str(attempt["card_type"]) == "code_exercise":
            return self.render_exercise_review_page(attempt_id, queue_mode=queue_mode, review_order=review_order)
        return self.render_review_page(attempt, queue_mode=queue_mode, review_order=review_order)

    def render_review_page(
        self,
        attempt: object,
        *,
        queue_mode: str = "mixed",
        review_order: str = "oldest-first",
        errors: list[str] | None = None,
        user_answer: str = "",
    ) -> Response:
        if str(attempt["status"]) == "completed":
            content = f"""
            <section class="panel">
              <h1>Review Completed</h1>
              <p class="muted">Attempt {int(attempt['id'])} has already been recorded as {html.escape(str(attempt['result']))}.</p>
              <div class="actions">
                <a class="button" href="/review?mode={html.escape(queue_mode)}{self._review_order_suffix(review_order)}">Continue Review</a>
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
          <p class="meta">{self._render_card_badges("concept", str(attempt['topic'] or ''))} · Box: {attempt['box']} · Due: {html.escape(self._format_date(str(attempt['next_review_at'])))}</p>
          {self._render_review_navigation(int(attempt['id']), int(attempt['card_id']), queue_mode=queue_mode, review_order=review_order)}
        </section>
        <section class="panel">
          <h2>Prompt</h2>
          <div class="markdown-content">{self._render_markdown(str(attempt['prompt']), card_id=int(attempt['card_id']), attempt_id=int(attempt['id']), queue_mode=queue_mode)}</div>
        </section>
        <section class="panel">
          <h2>Your Answer</h2>
          <form method="post" action="/review/{int(attempt['id'])}/result" class="form-stack">
            <input type="hidden" name="mode" value="{html.escape(queue_mode)}">
            <input type="hidden" name="order" value="{html.escape(review_order)}">
            {self._textarea("user_answer", "Type your answer before grading", user_answer, rows=8)}
            <div class="actions">
              {self._select("action", "Review action", "grade", [("grade", "Grade with LLM"), ("incomplete", "Mark Incomplete")])}
              <button class="button" type="submit">Submit Action</button>
            </div>
          </form>
        </section>
        """
        return self.html_page("Concept Review", content)

    def render_exercise_review_page(
        self,
        attempt_id: int,
        errors: list[str] | None = None,
        *,
        queue_mode: str = "mixed",
        review_order: str = "oldest-first",
    ) -> Response:
        attempt = get_exercise_attempt_view(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")
        if attempt.status == "completed":
            content = f"""
            <section class="panel">
              <h1>Coding Completed</h1>
              <p class="muted">Attempt {attempt.attempt_id} has already been recorded as {html.escape(attempt.result or '-')}.</p>
              <div class="actions">
                <a class="button" href="/review?mode={html.escape(queue_mode)}{self._review_order_suffix(review_order)}">Continue Review</a>
                <a class="button button-secondary" href="/">Dashboard</a>
              </div>
            </section>
            """
            return self.html_page("Coding", content)

        error_html = ""
        if errors:
            error_items = "".join(f"<li>{html.escape(error)}</li>" for error in errors)
            error_html = f'<section class="panel panel-error"><h2>Cannot validate this exercise yet</h2><ul class="errors">{error_items}</ul></section>'

        workspace_html = html.escape(attempt.workspace_path) if attempt.workspace_path else "Not created yet."
        failing_tests = "".join(
            f"<li><span>{html.escape(name)}</span><strong>fail</strong></li>" for name in attempt.failing_tests
        ) or "<li><span>No failing tests recorded yet</span><strong>-</strong></li>"

        content = f"""
        {error_html}
        <section class="panel">
          <p class="eyebrow">Coding</p>
          <h1>{html.escape(attempt.title)}</h1>
          <p class="meta">{self._render_card_badges("code_exercise", attempt.topic or '')} · Box: {attempt.box} · Due: {html.escape(self._format_date(attempt.next_review_at))}</p>
          {self._render_review_navigation(attempt_id, attempt.card_id, queue_mode=queue_mode, review_order=review_order)}
        </section>
        <section class="panel">
          <h2>Prompt</h2>
          <div class="markdown-content">{self._render_markdown(attempt.prompt, card_id=attempt.card_id, attempt_id=attempt.attempt_id, queue_mode=queue_mode)}</div>
        </section>
        <section class="panel">
          <h2>Workspace</h2>
          <dl class="stats">
            <div><dt>Asset path</dt><dd>{html.escape(attempt.asset_path)}</dd></div>
            <div><dt>Workspace</dt><dd>{workspace_html}</dd></div>
            <div><dt>Edit file</dt><dd>{html.escape(attempt.entrypoint)}</dd></div>
            <div><dt>Tests</dt><dd>{html.escape(Path(attempt.tests_path).name)}</dd></div>
          </dl>
          <form method="post" action="/review/{attempt_id}/workspace" class="form-stack">
            <input type="hidden" name="mode" value="{html.escape(queue_mode)}">
            <input type="hidden" name="order" value="{html.escape(review_order)}">
            {self._select("action", "Workspace action", "create", [("create", "Reset Workspace" if attempt.workspace_path else "Create Workspace"), ("incomplete", "Mark Incomplete")])}
            <div class="actions">
              <button class="button" type="submit">Apply Workspace Action</button>
            </div>
          </form>
        </section>
        <section class="panel">
          <h2>Validation</h2>
          <p class="muted">Edit the workspace locally, then run the test suite from the browser.</p>
          <form method="post" action="/review/{attempt_id}/validate" class="actions">
            <input type="hidden" name="mode" value="{html.escape(queue_mode)}">
            <input type="hidden" name="order" value="{html.escape(review_order)}">
            <button class="button" type="submit">Run Tests</button>
          </form>
          <p>{html.escape(attempt.validator_summary or 'No validation run yet.')}</p>
          <ul class="list">{failing_tests}</ul>
        </section>
        """
        return self.html_page("Coding", content)

    def handle_review_result(self, environ: dict, attempt_id: int) -> Response:
        form = self._parse_form(environ)
        action = self._first(form, "action")
        queue_mode = self._review_mode({"mode": [self._first(form, "mode")]})
        review_order = self._review_order({"order": [self._first(form, "order")]})
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
                    queue_mode=queue_mode,
                    review_order=review_order,
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
                    queue_mode=queue_mode,
                    review_order=review_order,
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
        card = get_card_detail(self.config, outcome.card_id)

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
            <div><dt>Next review</dt><dd>{html.escape(self._format_date(schedule.next_review_at))}</dd></div>
          </dl>
          <p>{html.escape(schedule.reason_summary)}</p>
        </section>
        {self._references_panel(card.references if card is not None else "")}
        <section class="actions">
          <a class="button" href="/review?mode={html.escape(queue_mode)}{self._review_order_suffix(review_order)}">Continue Review</a>
          <a class="button button-secondary" href="/">Dashboard</a>
        </section>
        """
        return self.html_page("Review Result", content)

    def handle_exercise_workspace(self, environ: dict, attempt_id: int) -> Response:
        form = self._parse_form(environ)
        action = self._first(form, "action")
        queue_mode = self._review_mode({"mode": [self._first(form, "mode")]})
        review_order = self._review_order({"order": [self._first(form, "order")]})
        attempt = get_exercise_attempt_view(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")

        if action == "incomplete":
            outcome = complete_exercise_attempt(
                self.config,
                attempt_id=attempt_id,
                result="incomplete",
                validator_summary="The exercise attempt was marked incomplete.",
                failing_tests=[],
                workspace_path=attempt.workspace_path,
            )
            return self.render_exercise_result_page(
                outcome,
                validator_summary="The exercise attempt was marked incomplete.",
                failing_tests=[],
                workspace_path=attempt.workspace_path,
                queue_mode=queue_mode,
                review_order=review_order,
            )

        workspace_dir = create_workspace(self.config, attempt_id=attempt_id, asset_dir=Path(attempt.asset_path))
        update_attempt_workspace(self.config, attempt_id=attempt_id, workspace_path=str(workspace_dir))
        return self.redirect(self._review_href(attempt_id, queue_mode=queue_mode, review_order=review_order))

    def handle_exercise_validate(self, environ: dict, attempt_id: int) -> Response:
        form = self._parse_form(environ)
        queue_mode = self._review_mode({"mode": [self._first(form, "mode")]})
        review_order = self._review_order({"order": [self._first(form, "order")]})
        attempt = get_exercise_attempt_view(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")
        if not attempt.workspace_path:
            return self.render_exercise_review_page(
                attempt_id,
                errors=["Create a workspace before running tests."],
                queue_mode=queue_mode,
                review_order=review_order,
            )

        validation = run_exercise_tests(Path(attempt.workspace_path))
        outcome = complete_exercise_attempt(
            self.config,
            attempt_id=attempt_id,
            result=validation.result,
            validator_summary=validation.summary,
            failing_tests=validation.failing_tests,
            workspace_path=attempt.workspace_path,
        )

        if validation.result == "pass":
            cleanup_workspace(Path(attempt.workspace_path))

        return self.render_exercise_result_page(
            outcome,
            validator_summary=validation.summary,
            failing_tests=validation.failing_tests,
            workspace_path=attempt.workspace_path,
            queue_mode=queue_mode,
            review_order=review_order,
        )

    def render_exercise_result_page(
        self,
        outcome: object,
        *,
        validator_summary: str,
        failing_tests: list[str],
        workspace_path: str | None,
        queue_mode: str = "mixed",
        review_order: str = "oldest-first",
    ) -> Response:
        schedule = outcome.schedule
        card = get_card_detail(self.config, outcome.card_id)
        failing_html = "".join(
            f"<li><span>{html.escape(name)}</span><strong>fail</strong></li>" for name in failing_tests
        ) or "<li><span>No failing tests</span><strong>-</strong></li>"

        content = f"""
        <section class="panel">
          <p class="eyebrow">Coding Result</p>
          <h1>{html.escape(outcome.title)}</h1>
          <p class="status status-{html.escape(outcome.result)}">Result: {html.escape(outcome.result)}</p>
        </section>
        <section class="panel">
          <h2>Validation</h2>
          <dl class="stats">
            <div><dt>Workspace</dt><dd>{html.escape(workspace_path or '-')}</dd></div>
          </dl>
          <p>{html.escape(validator_summary)}</p>
          <ul class="list">{failing_html}</ul>
        </section>
        <section class="panel">
          <h2>Scheduling</h2>
          <dl class="stats">
            <div><dt>Scheduler</dt><dd>{html.escape(schedule.scheduler_name)}</dd></div>
            <div><dt>Previous box</dt><dd>{schedule.prior_box}</dd></div>
            <div><dt>New box</dt><dd>{schedule.new_box}</dd></div>
            <div><dt>Previous interval</dt><dd>{schedule.previous_interval_days if schedule.previous_interval_days is not None else "new"}</dd></div>
            <div><dt>New interval</dt><dd>{schedule.new_interval_days} day(s)</dd></div>
            <div><dt>Next review</dt><dd>{html.escape(self._format_date(schedule.next_review_at))}</dd></div>
          </dl>
          <p>{html.escape(schedule.reason_summary)}</p>
        </section>
        {self._references_panel(card.references if card is not None else "")}
        <section class="actions">
          <a class="button" href="/review?mode={html.escape(queue_mode)}{self._review_order_suffix(review_order)}">Continue Review</a>
          <a class="button button-secondary" href="/">Dashboard</a>
        </section>
        """
        return self.html_page("Coding Result", content)

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

    def _format_timestamp(self, raw_timestamp: str) -> str:
        try:
            parsed = datetime.fromisoformat(raw_timestamp)
        except ValueError:
            # Show the stored value if an older row is not ISO-formatted.
            return raw_timestamp

        # Convert to local wall-clock time so the list view is easier to scan quickly.
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")

    def _format_date(self, raw_timestamp: str) -> str:
        try:
            parsed = datetime.fromisoformat(raw_timestamp)
        except ValueError:
            return raw_timestamp

        # Due dates only need the calendar day in the study UI.
        return parsed.astimezone().strftime("%Y-%m-%d")

    def _references_panel(self, references: str) -> str:
        if not references.strip():
            return ""
        return (
            '<section class="panel">'
            "<h2>References</h2>"
            '<div class="markdown-content">'
            f"{self._render_markdown(references)}"
            "</div>"
            "</section>"
        )

    def _review_mode(self, query: dict[str, list[str]]) -> str:
        mode = query.get("mode", ["mixed"])[0]
        if mode not in {"mixed", "concept", "exercise"}:
            return "mixed"
        return mode

    def _review_order(self, query: dict[str, list[str]]) -> str:
        review_order = query.get("order", [self.config.review_order])[0]
        if review_order not in {"oldest-first", "random"}:
            return self.config.review_order
        return review_order

    def _review_order_suffix(self, review_order: str) -> str:
        if review_order == "random":
            return "&order=random"
        return ""

    def _review_href(self, attempt_id: int, *, queue_mode: str, review_order: str = "oldest-first") -> str:
        return f"/review/{attempt_id}?mode={queue_mode}{self._review_order_suffix(review_order)}"

    def _render_review_navigation(
        self,
        attempt_id: int,
        card_id: int,
        *,
        queue_mode: str,
        review_order: str,
    ) -> str:
        previous_card = adjacent_review_card_id(
            self.config,
            current_card_id=card_id,
            queue_mode=queue_mode,
            direction="previous",
        )
        next_card = adjacent_review_card_id(
            self.config,
            current_card_id=card_id,
            queue_mode=queue_mode,
            direction="next",
        )
        previous_link = self._navigation_link(
            attempt_id,
            queue_mode=queue_mode,
            review_order=review_order,
            direction="previous",
            label="Previous Card",
            enabled=previous_card is not None,
        )
        next_link = self._navigation_link(
            attempt_id,
            queue_mode=queue_mode,
            review_order=review_order,
            direction="next",
            label="Next Card",
            enabled=next_card is not None,
        )
        return f'<div class="review-nav">{previous_link}{next_link}</div>'

    def _navigation_link(
        self,
        attempt_id: int,
        *,
        queue_mode: str,
        review_order: str,
        direction: str,
        label: str,
        enabled: bool,
    ) -> str:
        if not enabled:
            return f'<span class="button button-secondary button-disabled">{html.escape(label)}</span>'
        href = f"/review/{attempt_id}/navigate?mode={queue_mode}&direction={direction}{self._review_order_suffix(review_order)}"
        return f'<a class="button button-secondary" href="{html.escape(href)}">{html.escape(label)}</a>'

    def _card_type_label(self, card_type: str) -> str:
        if card_type == "code_exercise":
            return "coding"
        return card_type

    def _card_type_chip_class(self, card_type: str) -> str:
        if card_type == "code_exercise":
            return "chip-coding"
        return "chip-concept"

    def _topic_chip_style(self, topic: str) -> str:
        if not topic:
            return ""

        # A deterministic tint helps the same topic read as the same cluster across pages.
        hue = sum(ord(char) for char in topic) % 360
        return (
            f"background-color:hsl({hue} 62% 94%);"
            f"border-color:hsl({hue} 48% 74%);"
            f"color:hsl({hue} 44% 28%);"
        )

    def _render_card_badges(self, card_type: str, topic: str) -> str:
        type_badge = (
            f'<span class="chip {self._card_type_chip_class(card_type)}">'
            f"{html.escape(self._card_type_label(card_type))}</span>"
        )
        if not topic:
            return type_badge

        topic_badge = (
            f'<span class="chip chip-topic" style="{html.escape(self._topic_chip_style(topic))}">'
            f"{html.escape(topic)}</span>"
        )
        return f"{type_badge} {topic_badge}"

    def _render_source_view(
        self,
        *,
        card: object,
        query: dict[str, list[str]],
        back_href: str,
        source_title: str,
    ) -> Response:
        raw_path = query.get("path", [""])[0].strip()
        if not raw_path:
            return self.text("404 Not Found", status="404 Not Found")

        resolved_path = Path(unquote(raw_path)).expanduser().resolve()
        source_path = self._resolve_bound_source_path(card, resolved_path)
        if source_path is None or not source_path.is_file():
            return self.text("404 Not Found", status="404 Not Found")

        start_line = self._optional_int(query.get("start", [""])[0])
        end_line = self._optional_int(query.get("end", [""])[0]) or start_line
        file_lines = source_path.read_text(encoding="utf-8").splitlines()
        total_lines = max(len(file_lines), 1)
        if start_line is not None:
            start_line = max(1, min(start_line, total_lines))
        if end_line is not None:
            end_line = max(start_line or 1, min(end_line, total_lines))

        view_start = 1
        view_end = total_lines
        if start_line is not None and end_line is not None:
            # Show a focused excerpt around the linked source range instead of dumping the full file.
            view_start = max(1, start_line - 3)
            view_end = min(total_lines, end_line + 3)

        line_rows = []
        for line_number in range(view_start, view_end + 1):
            line_text = file_lines[line_number - 1] if line_number - 1 < len(file_lines) else ""
            target_class = ""
            if start_line is not None and end_line is not None and start_line <= line_number <= end_line:
                target_class = " source-line-target"
            line_rows.append(
                f'<li class="source-line{target_class}"><span class="source-gutter">{line_number}</span><code>{html.escape(line_text)}</code></li>'
            )

        range_label = "-"
        if start_line is not None:
            range_label = f"lines {start_line}" if start_line == end_line else f"lines {start_line}-{end_line}"

        content = f"""
        <section class="panel">
          <p class="eyebrow">Source View</p>
          <h1>{html.escape(source_title)}</h1>
          <dl class="stats">
            <div><dt>File</dt><dd>{html.escape(str(source_path))}</dd></div>
            <div><dt>Highlighted</dt><dd>{html.escape(range_label)}</dd></div>
          </dl>
          <div class="actions">
            <a class="button button-secondary" href="{html.escape(back_href)}">Back</a>
          </div>
        </section>
        <section class="panel">
          <ol class="source-lines" start="{view_start}">
            {''.join(line_rows)}
          </ol>
        </section>
        """
        return self.html_page(f"Source · {source_path.name}", content)

    def _card_form_values(self, card: object) -> dict[str, str]:
        values = {
            "title": str(getattr(card, "title", "")),
            "topic": str(getattr(card, "topic", "")),
            "tags": ", ".join(getattr(card, "tags", []) or []),
            "source": str(getattr(card, "source", "")),
            "prompt": str(getattr(card, "prompt", "") or ""),
            "answer": str(getattr(card, "answer", "") or ""),
        }
        if str(getattr(card, "type", "")) == "code_exercise":
            values["prompt"] = self._exercise_prompt_text(card)
            values["answer_py"] = self._read_text_file(getattr(card, "answer_path", None))
            values["solution_py"] = self._read_text_file(getattr(card, "solution_path", None))
            values["tests_py"] = self._read_text_file(getattr(card, "tests_path", None))
        return values

    def _exercise_prompt_text(self, card: object) -> str:
        return self._read_prompt_file(getattr(card, "prompt_path", None), fallback=str(getattr(card, "title", "")))

    def _read_prompt_file(self, raw_path: str | None, *, fallback: str) -> str:
        content = self._read_text_file(raw_path)
        if not content:
            return fallback

        lines = content.splitlines()
        if lines and lines[0].startswith("# "):
            return "\n".join(lines[2:]).strip() or fallback
        return content

    def _read_text_file(self, raw_path: str | None) -> str:
        if not raw_path:
            return ""
        path = Path(str(raw_path))
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def _render_markdown(
        self,
        raw_text: str,
        *,
        card_id: int | None = None,
        attempt_id: int | None = None,
        queue_mode: str = "mixed",
    ) -> str:
        text = re.sub(r"<img[^>]*>", "", raw_text, flags=re.IGNORECASE).replace("\r\n", "\n").strip()
        if not text:
            return "<p></p>"

        blocks: list[str] = []
        paragraph_lines: list[str] = []
        list_items: list[str] = []
        list_kind: str | None = None
        in_code_block = False
        code_lines: list[str] = []

        def flush_paragraph() -> None:
            if not paragraph_lines:
                return
            paragraph = " ".join(line.strip() for line in paragraph_lines if line.strip())
            blocks.append(
                f"<p>{self._render_inline_markdown(paragraph, card_id=card_id, attempt_id=attempt_id, queue_mode=queue_mode)}</p>"
            )
            paragraph_lines.clear()

        def flush_list() -> None:
            nonlocal list_kind
            if not list_items or list_kind is None:
                return
            items = "".join(f"<li>{item}</li>" for item in list_items)
            blocks.append(f"<{list_kind}>{items}</{list_kind}>")
            list_items.clear()
            list_kind = None

        for raw_line in text.split("\n"):
            stripped = raw_line.rstrip()
            if stripped.startswith("```"):
                flush_paragraph()
                flush_list()
                if in_code_block:
                    blocks.append(f"<pre class=\"code-block\"><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                    code_lines.clear()
                    in_code_block = False
                else:
                    in_code_block = True
                continue

            if in_code_block:
                code_lines.append(raw_line)
                continue

            heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            unordered_match = re.match(r"^[-*]\s+(.*)$", stripped)
            ordered_match = re.match(r"^\d+\.\s+(.*)$", stripped)

            if not stripped:
                flush_paragraph()
                flush_list()
                continue
            if heading_match:
                flush_paragraph()
                flush_list()
                level = len(heading_match.group(1))
                heading_html = self._render_inline_markdown(
                    heading_match.group(2).strip(),
                    card_id=card_id,
                    attempt_id=attempt_id,
                    queue_mode=queue_mode,
                )
                blocks.append(f"<h{level}>{heading_html}</h{level}>")
                continue
            if unordered_match:
                flush_paragraph()
                if list_kind not in {None, "ul"}:
                    flush_list()
                list_kind = "ul"
                list_items.append(
                    self._render_inline_markdown(
                        unordered_match.group(1).strip(),
                        card_id=card_id,
                        attempt_id=attempt_id,
                        queue_mode=queue_mode,
                    )
                )
                continue
            if ordered_match:
                flush_paragraph()
                if list_kind not in {None, "ol"}:
                    flush_list()
                list_kind = "ol"
                list_items.append(
                    self._render_inline_markdown(
                        ordered_match.group(1).strip(),
                        card_id=card_id,
                        attempt_id=attempt_id,
                        queue_mode=queue_mode,
                    )
                )
                continue
            if list_kind is not None:
                flush_list()
            paragraph_lines.append(raw_line)

        if in_code_block:
            blocks.append(f"<pre class=\"code-block\"><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
        flush_paragraph()
        flush_list()
        return "".join(blocks)

    def _render_inline_markdown(
        self,
        raw_text: str,
        *,
        card_id: int | None,
        attempt_id: int | None,
        queue_mode: str,
    ) -> str:
        text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", raw_text)
        parts = re.split(r"(`[^`]+`)", text)
        rendered_parts: list[str] = []
        for part in parts:
            if not part:
                continue
            if part.startswith("`") and part.endswith("`") and len(part) >= 2:
                rendered_parts.append(f"<code>{html.escape(part[1:-1])}</code>")
                continue
            rendered_parts.append(
                self._render_inline_without_code(
                    part,
                    card_id=card_id,
                    attempt_id=attempt_id,
                    queue_mode=queue_mode,
                )
            )
        return "".join(rendered_parts)

    def _render_inline_without_code(
        self,
        raw_text: str,
        *,
        card_id: int | None,
        attempt_id: int | None,
        queue_mode: str,
    ) -> str:
        pieces: list[str] = []
        cursor = 0
        for match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", raw_text):
            prefix = raw_text[cursor:match.start()]
            if prefix:
                pieces.append(self._apply_inline_emphasis(html.escape(prefix)))

            label = self._apply_inline_emphasis(html.escape(match.group(1)))
            href = self._rewrite_prompt_link(
                match.group(2),
                card_id=card_id,
                attempt_id=attempt_id,
                queue_mode=queue_mode,
            )
            if href is None:
                pieces.append(label)
            else:
                pieces.append(f'<a href="{html.escape(href)}">{label}</a>')
            cursor = match.end()

        suffix = raw_text[cursor:]
        if suffix:
            pieces.append(self._apply_inline_emphasis(html.escape(suffix)))
        return "".join(pieces)

    def _apply_inline_emphasis(self, escaped_text: str) -> str:
        strong = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped_text)
        strong = re.sub(r"__(.+?)__", r"<strong>\1</strong>", strong)
        italic = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", strong)
        return re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<em>\1</em>", italic)

    def _rewrite_prompt_link(
        self,
        target: str,
        *,
        card_id: int | None,
        attempt_id: int | None,
        queue_mode: str,
    ) -> str | None:
        candidate = target.strip()
        if candidate.startswith(("http://", "https://")):
            return candidate

        source_ref = self._parse_source_reference(candidate)
        if source_ref is None:
            return None

        query = {"path": str(source_ref.path)}
        if source_ref.start_line is not None:
            query["start"] = str(source_ref.start_line)
        if source_ref.end_line is not None:
            query["end"] = str(source_ref.end_line)

        if attempt_id is not None:
            query["mode"] = queue_mode
            return f"/review/{attempt_id}/source?{urlencode(query)}"
        if card_id is not None:
            return f"/cards/{card_id}/source?{urlencode(query)}"
        return None

    def _parse_source_reference(self, raw_target: str) -> SourceReference | None:
        candidate = raw_target.strip()
        if candidate.startswith("cci:"):
            file_index = candidate.find("file://")
            if file_index == -1:
                return None
            candidate = candidate[file_index:]

        if candidate.startswith("file://"):
            candidate = unquote(candidate[len("file://"):])
        elif not candidate.startswith("/"):
            return None

        range_match = re.match(r"^(?P<path>.+?):(?P<start>\d+):\d+(?:-(?P<end>\d+):\d+)?$", candidate)
        if range_match:
            return SourceReference(
                path=Path(range_match.group("path")).expanduser().resolve(),
                start_line=int(range_match.group("start")),
                end_line=int(range_match.group("end")) if range_match.group("end") else int(range_match.group("start")),
            )

        return SourceReference(
            path=Path(candidate).expanduser().resolve(),
            start_line=None,
            end_line=None,
        )

    def _resolve_bound_source_path(self, card: object, requested_path: Path) -> Path | None:
        source_path = str(getattr(card, "source_path", "") or "").strip()
        if source_path:
            bound_source = Path(source_path).expanduser().resolve()
            if requested_path == bound_source:
                return requested_path

        asset_path = str(getattr(card, "asset_path", "") or "").strip()
        if asset_path:
            asset_dir = Path(asset_path).expanduser().resolve()
            if requested_path == asset_dir:
                return requested_path
            if requested_path.is_relative_to(asset_dir):
                return requested_path

        return None

    def _optional_int(self, raw_value: str) -> int | None:
        if not raw_value.strip():
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

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

    def _select(self, name: str, label: str, value: str, options: list[tuple[str, str]]) -> str:
        option_html = "".join(
            f'<option value="{html.escape(option_value)}"{" selected" if option_value == value else ""}>{html.escape(option_label)}</option>'
            for option_value, option_label in options
        )
        return (
            f'<label><span>{html.escape(label)}</span>'
            f'<select name="{html.escape(name)}">{option_html}</select></label>'
        )

    def _contract_example(self) -> str:
        return (
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
            'references = """\n'
            '- Python docs: https://docs.python.org/3/library/threading.html\n'
            '"""\n'
            '\n'
            '[[cards]]\n'
            'type = "code_exercise"\n'
            'title = "Split Words"\n'
            'topic = "nlp"\n'
            'tags = ["python", "tokenizer"]\n'
            'references = """\n'
            '- Chapter metadata: tokenizer walkthrough\n'
            '"""\n'
            'prompt = """\n'
            'Implement `split_words(text)` and return a token list.\n'
            '"""\n'
            'answer_py = """\n'
            '\\"\\"\\"Reimplement the imported exercise: Split Words.\\"\\"\\"\n'
            '\n'
            'raise NotImplementedError("Implement the exercise in answer.py")\n'
            '"""\n'
            'solution_py = """\n'
            'import re\n'
            '\n'
            'def split_words(text: str) -> list[str]:\n'
            '    return re.findall(r"\\\\w+", text)\n'
            '"""\n'
            'tests_py = """\n'
            'import unittest\n'
            'import answer\n'
            '\n'
            'class ExerciseTests(unittest.TestCase):\n'
            '    def test_split_words(self) -> None:\n'
            '        self.assertEqual(answer.split_words("a b"), ["a", "b"])\n'
            '\n'
            'if __name__ == "__main__":\n'
            '    unittest.main()\n'
            '"""\n'
        )

    def _resolve_source(self, values: dict[str, str]) -> tuple[str, str, str, str, str]:
        source_path = values["source_path"].strip()
        source_text = values["source_text"].strip()
        source_kind = values["source_kind"].strip()
        source_label = values["source_label"].strip()

        if source_path:
            resolved_path, loaded_text, resolved_kind = load_source_text_from_path(source_path)
            return str(resolved_path), "external_path", source_label or resolved_path.name, resolved_kind, loaded_text
        if source_text:
            normalized_kind = source_kind.strip().lower().lstrip(".") or "ipynb"
            label = source_label or f"imported-source.{normalized_kind}"
            managed_path = save_managed_source(
                self.config,
                source_label=label,
                source_text=source_text,
                source_kind=normalized_kind,
            )
            return str(managed_path), "managed_copy", label, normalized_kind, source_text
        raise ValueError("Provide a source path or drop a .ipynb or .py file first.")

    def _resolve_draft_source(self, draft: object) -> tuple[str, str, str, str, str]:
        resolved_path, source_text, source_kind = load_source_text_from_path(draft.source_path)
        return str(resolved_path), draft.source_mode, draft.source_label, source_kind, source_text

    def _normalized_split_mode(self, raw_value: str) -> str:
        if raw_value in {"balanced", "aggressive"}:
            return raw_value
        if self.config.notebook_split_mode in {"balanced", "aggressive"}:
            return self.config.notebook_split_mode
        return "balanced"

    def _import_notebook_script(self) -> str:
        return """
        <script>
        (() => {
          const dropzone = document.getElementById("notebook-dropzone");
          const fileInput = document.getElementById("notebook-file");
          const sourceInput = document.getElementById("source-text");
          const kindInput = document.getElementById("source-kind");
          const pathInput = document.querySelector('input[name="source_path"]');
          const labelInput = document.querySelector('input[name="source_label"]');

          function inferKind(name) {
            const lower = name.toLowerCase();
            if (lower.endsWith(".py")) return "py";
            return "ipynb";
          }

          async function loadFile(file) {
            if (!file) return;
            const text = await file.text();
            sourceInput.value = text;
            kindInput.value = inferKind(file.name);
            if (!labelInput.value) labelInput.value = file.name;
            pathInput.value = "";
          }

          fileInput.addEventListener("change", async (event) => {
            await loadFile(event.target.files[0]);
          });

          dropzone.addEventListener("dragover", (event) => {
            event.preventDefault();
            dropzone.classList.add("dropzone-active");
          });
          dropzone.addEventListener("dragleave", () => {
            dropzone.classList.remove("dropzone-active");
          });
          dropzone.addEventListener("drop", async (event) => {
            event.preventDefault();
            dropzone.classList.remove("dropzone-active");
            const file = event.dataTransfer.files && event.dataTransfer.files[0];
            if (file) {
              await loadFile(file);
              return;
            }
            const text = event.dataTransfer.getData("text/plain").trim();
            if (text) {
              pathInput.value = text;
              sourceInput.value = "";
              kindInput.value = "";
              if (!labelInput.value) {
                const parts = text.split(/[\\\\/]/);
                labelInput.value = parts[parts.length - 1];
              }
            }
          });
        })();
        </script>
        """

    def _import_review_script(self) -> str:
        return """
        <script>
        (() => {
          document.querySelectorAll("[data-delete-candidate]").forEach((button) => {
            button.addEventListener("click", () => {
              const article = button.closest(".import-candidate");
              if (!article) return;
              // Removing the candidate from the form prevents it from being created.
              article.remove();
            });
          });
        })();
        </script>
        """


def serve_app(*, config: StudyConfig, host: str, port: int) -> None:
    app = StudyWebApp(config)
    print(f"BarskyProtocol listening on http://{host}:{port}")
    with make_server(host, port, app) as server:
        server.serve_forever()
