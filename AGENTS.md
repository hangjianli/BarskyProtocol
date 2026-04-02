Start every response with "Hi good sir,"

# Repository Guidelines

## Project Structure & Module Organization
`study/` contains the application code: `web.py` for the local web UI, `app.py` for admin CLI commands, `storage.py` for SQLite access, `scheduler.py` for review scheduling, and `grading.py` / `validators.py` for answer evaluation. HTML templates live in `templates/`, and minimal CSS lives in `static/`. Runtime data stays under `.barsky/`; treat it as local state, not source. Card assets are scaffolded under `cards/`. Tests live in `tests/test_study.py`. Product and architecture decisions are documented in `DESIGN.md`.

## Build, Test, and Development Commands
Create or activate the local environment with `source .venv/bin/activate`.

- `python cli.py init`: initialize local storage and directories.
- `python cli.py serve`: run the local web app on `localhost`.
- `python cli.py add-concept --title "Mutex" --topic "python" --prompt "..." --answer "..."`: seed a concept card for manual testing.
- `python -m unittest discover -s tests -v`: run the test suite.
- `python -m py_compile cli.py study/*.py tests/*.py`: catch syntax/import issues quickly.

## Coding Style & Naming Conventions
Use Python with 4-space indentation and standard library-first solutions unless there is a clear dependency need. Prefer small functions, explicit data flow, and brief inline comments whenever they improve clarity, especially around non-obvious logic, parsing heuristics, storage mutations, and scheduler behavior. Use `snake_case` for functions, variables, and modules; use clear nouns for dataclasses such as `ScheduleDecision`. Keep HTML and CSS minimal and text-forward to match the product UI.

## Testing Guidelines
Tests use `unittest`. Add or update tests for any behavior change in scheduling, grading, storage, or web routes. Name test methods descriptively, for example `test_exercise_review_pass_path`. Prefer deterministic tests with temp directories and mocked grader calls instead of networked validation.

## Commit & Pull Request Guidelines
Follow the existing commit style: concise, imperative, and scoped, e.g. `feat: add phase 1 web study workflow` or `chore: bootstrap v1 study CLI`. Keep commits focused. For pull requests, include a short summary, user-visible behavior changes, verification steps, and screenshots for UI changes. Link the relevant design section in `DESIGN.md` when the change implements or revises architecture.

Future feature work should not be developed directly on `main`. Start from `main`, create a dedicated feature branch, implement the work there, then raise a pull request and conduct code review before merging.

## Design Discipline
If you make a major design decision, update `DESIGN.md` before implementing it. Treat the design doc as the source of truth for architecture, workflows, and product behavior changes, especially for scheduling, grading, import pipelines, and UI flow.

## Delivery Discipline
After a major implementation milestone, create a commit and check in the change. Keep the commit focused and use the repository's existing commit style so the history stays readable.

## Security & Configuration Tips
Do not commit secrets, auth files, or `.barsky/study.db`. Keep local auth in user-level config such as `~/.codex/auth.json`, and keep repository config in `config.toml` free of sensitive values.
