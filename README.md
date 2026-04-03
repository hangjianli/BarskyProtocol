<h1 align="center">BarskyProtocol</h1>

<p align="center">
  <strong>Local-first spaced repetition for concepts and coding drills.</strong>
</p>

<p align="center">
  <a href="#quick-start"><img alt="Quick Start" src="https://img.shields.io/badge/quick%20start-local%20web%20app-0f766e"></a>
  <a href="#study-model"><img alt="Study Model" src="https://img.shields.io/badge/study%20model-concepts%20%2B%20code-1d4ed8"></a>
  <a href="#card-sources"><img alt="Card Sources" src="https://img.shields.io/badge/import-.ipynb%20%2F%20.py%20%2F%20TOML-7c3aed"></a>
  <a href="./DESIGN.md"><img alt="Design Docs" src="https://img.shields.io/badge/docs-design%20index-f59e0b"></a>
</p>

<p align="center">
  <a href="./DESIGN.md">Design</a> ·
  <a href="./CHANGELOG.md">Changelog</a> ·
  <a href="./docs/design/card-contract.md">Card Contract</a>
</p>

BarskyProtocol is a local study tool for retaining technical material through spaced repetition. It supports both short concept cards and standalone Python reimplementation exercises, with a minimal web UI on `localhost`, local SQLite state, filesystem-backed exercise assets, and transparent scheduling explanations.

## Quick Start

```bash
uv venv .venv
source .venv/bin/activate
python cli.py init
python cli.py serve
```

Open `http://127.0.0.1:8427`.

## Study Model

BarskyProtocol is still a spaced repetition system at its core, but it extends a basic flashcard tool in two ways:

- `concept` cards for prompt/answer recall
- `code_exercise` cards for reimplementing Python from memory and validating it with tests

Phase 1 uses a transparent Leitner-style fallback scheduler:

- `pass`: move up one box
- `fail`: reset to box 1
- `incomplete`: keep the current box and reschedule soon

The UI always shows why the next review date was chosen.

## Card Sources

You can create cards from several sources:

- Manual concept and exercise forms in the web UI
- Notebook or Python imports from external paths or managed copies
- Paste-in TOML using the [card contract](./docs/design/card-contract.md)

Imported coding drills become standalone exercise folders under `cards/` with:

- `prompt.md`
- `answer.py`
- `solution.py`
- `tests.py`

## Commands

- `python cli.py init`: initialize local storage and schema
- `python cli.py serve`: run the local web app
- `python cli.py add-concept`: add a concept card from the terminal
- `python cli.py stats`: print a compact queue summary

## Repository Layout

- `study/`: application code
- `templates/`: server-rendered HTML
- `static/`: minimal CSS
- `tests/`: `unittest` coverage
- `cards/`: tracked exercise assets
- `docs/design/`: detailed design references

## Configuration

The app reads [config.toml](./config.toml) from the current directory or one of its parents. Override it with `BARSKY_CONFIG=/path/to/config.toml`.

Important paths:

- `study.database`
- `study.cards_dir`
- `study.sources_dir`
- `study.workspaces_dir`
- `study.llm_auth_file`
- `study.llm_model`
- `study.llm_validator`
- `study.llm_base_url`
- `study.llm_api`

LLM grading defaults to a Codex model. For ChatGPT OAuth, the app uses the
ChatGPT backend Codex Responses endpoint. You can still override the model and
transport at runtime if needed.
