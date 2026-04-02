from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Callable
from urllib.parse import parse_qs
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
    complete_concept_attempt,
    complete_exercise_attempt,
    dashboard_stats,
    get_card_detail,
    get_exercise_attempt_view,
    get_review_attempt,
    list_cards,
    recent_reviews_for_card,
    start_review_attempt,
    update_attempt_workspace,
)
from study.validators import run_exercise_tests


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
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)

        if method == "GET" and path == "/":
            response = self.handle_dashboard()
        elif method == "GET" and path == "/cards":
            response = self.handle_cards()
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
        elif method == "GET" and re.fullmatch(r"/review/\d+", path):
            response = self.handle_review_page(int(path.rsplit("/", 1)[-1]))
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
            <label class="hero-select">
              <span>Quick actions</span>
              <select onchange="if (this.value) window.location = this.value;">
                <option value="">Choose an action</option>
                <optgroup label="Review">
                  <option value="/review?mode=concept">Concept queue</option>
                  <option value="/review?mode=exercise">Exercise queue</option>
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
        elif card.type == "code_exercise":
            prompt_block = f"""
            <section class="panel">
              <h2>Exercise Files</h2>
              <dl class="stats">
                <div><dt>Asset path</dt><dd>{html.escape(card.asset_path)}</dd></div>
                <div><dt>Entry point</dt><dd>{html.escape(card.entrypoint or '-')}</dd></div>
                <div><dt>Tests</dt><dd>{html.escape(card.tests_path or '-')}</dd></div>
              </dl>
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
        <section class="panel">
          <h2>Card Actions</h2>
          <p class="muted">Delete this card if it should no longer be part of the study set.</p>
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
        except (CardContractError, FileExistsError) as exc:
            return self.handle_import_text_form(errors=[str(exc)], values={"contract_text": contract_text})

        if not created_ids:
            return self.handle_import_text_form(
                errors=["The contract did not create any cards."],
                values={"contract_text": contract_text},
            )
        return self.redirect(f"/cards/{created_ids[0]}")

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

    def handle_new_exercise_submit(self, environ: dict) -> Response:
        form = self._parse_form(environ)
        values = {key: self._first(form, key) for key in ("title", "topic", "tags", "prompt", "source")}
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
        mode = query.get("mode", ["mixed"])[0]
        if mode not in {"mixed", "concept", "exercise"}:
            mode = "mixed"
        card_type = None if mode == "mixed" else ("concept" if mode == "concept" else "code_exercise")
        attempt = start_review_attempt(self.config, card_type=card_type)
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
        return self.redirect(f"/review/{int(attempt['id'])}")

    def handle_review_page(self, attempt_id: int) -> Response:
        attempt = get_review_attempt(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")

        if str(attempt["card_type"]) == "code_exercise":
            return self.render_exercise_review_page(attempt_id)
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
              {self._select("action", "Review action", "grade", [("grade", "Grade with LLM"), ("incomplete", "Mark Incomplete")])}
              <button class="button" type="submit">Submit Action</button>
            </div>
          </form>
        </section>
        """
        return self.html_page("Concept Review", content)

    def render_exercise_review_page(self, attempt_id: int, errors: list[str] | None = None) -> Response:
        attempt = get_exercise_attempt_view(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")
        if attempt.status == "completed":
            content = f"""
            <section class="panel">
              <h1>Review Completed</h1>
              <p class="muted">Attempt {attempt.attempt_id} has already been recorded as {html.escape(attempt.result or '-')}.</p>
              <div class="actions">
                <a class="button" href="/review?mode=exercise">Continue Review</a>
                <a class="button button-secondary" href="/">Dashboard</a>
              </div>
            </section>
            """
            return self.html_page("Exercise Review", content)

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
          <p class="eyebrow">Exercise Review</p>
          <h1>{html.escape(attempt.title)}</h1>
          <p class="meta">Topic: {html.escape(attempt.topic or '-')} · Box: {attempt.box} · Due: {html.escape(attempt.next_review_at)}</p>
        </section>
        <section class="panel">
          <h2>Prompt</h2>
          <pre class="code-block">{html.escape(attempt.prompt)}</pre>
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
            <button class="button" type="submit">Run Tests</button>
          </form>
          <p>{html.escape(attempt.validator_summary or 'No validation run yet.')}</p>
          <ul class="list">{failing_tests}</ul>
        </section>
        """
        return self.html_page("Exercise Review", content)

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
          <a class="button" href="/review?mode=mixed">Continue Review</a>
          <a class="button button-secondary" href="/">Dashboard</a>
        </section>
        """
        return self.html_page("Review Result", content)

    def handle_exercise_workspace(self, environ: dict, attempt_id: int) -> Response:
        form = self._parse_form(environ)
        action = self._first(form, "action")
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
            return self.render_exercise_result_page(outcome, validator_summary="The exercise attempt was marked incomplete.", failing_tests=[], workspace_path=attempt.workspace_path)

        workspace_dir = create_workspace(self.config, attempt_id=attempt_id, asset_dir=Path(attempt.asset_path))
        update_attempt_workspace(self.config, attempt_id=attempt_id, workspace_path=str(workspace_dir))
        return self.redirect(f"/review/{attempt_id}")

    def handle_exercise_validate(self, environ: dict, attempt_id: int) -> Response:
        attempt = get_exercise_attempt_view(self.config, attempt_id)
        if attempt is None:
            return self.text("404 Not Found", status="404 Not Found")
        if not attempt.workspace_path:
            return self.render_exercise_review_page(attempt_id, errors=["Create a workspace before running tests."])

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
        )

    def render_exercise_result_page(
        self,
        outcome: object,
        *,
        validator_summary: str,
        failing_tests: list[str],
        workspace_path: str | None,
    ) -> Response:
        schedule = outcome.schedule
        failing_html = "".join(
            f"<li><span>{html.escape(name)}</span><strong>fail</strong></li>" for name in failing_tests
        ) or "<li><span>No failing tests</span><strong>-</strong></li>"

        content = f"""
        <section class="panel">
          <p class="eyebrow">Exercise Result</p>
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
            <div><dt>Next review</dt><dd>{html.escape(schedule.next_review_at)}</dd></div>
          </dl>
          <p>{html.escape(schedule.reason_summary)}</p>
        </section>
        <section class="actions">
          <a class="button" href="/review?mode=exercise">Continue Review</a>
          <a class="button button-secondary" href="/">Dashboard</a>
        </section>
        """
        return self.html_page("Exercise Result", content)

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
            '\n'
            '[[cards]]\n'
            'type = "code_exercise"\n'
            'title = "Split Words"\n'
            'topic = "nlp"\n'
            'tags = ["python", "tokenizer"]\n'
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
        return raw_value if raw_value in {"balanced", "aggressive"} else self.config.notebook_split_mode

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
