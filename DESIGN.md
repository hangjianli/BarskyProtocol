# BarskyProtocol Design

## Goal

Build a local-first CLI study system for retaining anything worth revisiting:

- technical concepts
- code patterns
- vocabulary
- quiz misses
- debugging heuristics
- definitions and tradeoffs
- implementation exercises

The system should be simple enough to use daily, predictable enough to trust, and
structured enough to extend later.

## Product Direction

The initial version should optimize for:

- low friction capture
- a clear daily review queue
- deterministic scheduling
- local storage
- reproducible code exercise review
- minimal operational overhead

The initial version should not optimize for:

- mobile sync
- AI-generated cards
- a complex adaptive scheduler
- rich note-taking
- notebook-specific storage formats

## Core Study Model

The system uses typed cards with a shared 5-box Leitner schedule.

Supported v1 card types:

- `concept`
- `code_exercise`

Rules:

- New cards start in box 1
- New cards are due immediately
- Correct review: move up one box, capped at box 5
- Failed review: reset to box 1

Default intervals:

| Box | Interval |
| --- | --- |
| 1 | 1 day |
| 2 | 2 days |
| 3 | 4 days |
| 4 | 8 days |
| 5 | 16 days |

This keeps the scheduling logic transparent. It also matches the intended
`Barsky/Leitner-style` workflow without introducing a more opaque algorithm like
SM-2 or FSRS too early.

## Card Types

### `concept`

This is the classic flashcard form.

Structure:

- prompt
- answer
- optional topic
- optional tags
- optional source

Use it for:

- definitions
- tradeoffs
- API recall
- debugging heuristics
- short conceptual explanations

### `code_exercise`

This is a reimplementation task rather than a simple text recall task.

Structure:

- metadata in the database
- exercise assets on disk

Use it for:

- reimplementing a function, module, or class
- practicing algorithmic patterns
- reproducing a technique from memory
- rebuilding a small utility without copying the original

The review task is:

1. read the prompt
2. implement the required Python code
3. run validation
4. record the result

This avoids the false simplicity of forcing code-learning into a plain
question-answer format.

## CLI Scope

Proposed commands for v1:

### `init`

Bootstrap local storage and schema.

Responsibilities:

- create storage directories
- initialize SQLite schema
- validate config

### `add-concept`

Create a concept card.

Inputs:

- prompt
- answer
- optional topic
- optional tags
- optional source

Behavior:

- store the card
- mark it due immediately

### `add-exercise`

Create a code exercise card and scaffold its files.

Inputs:

- id or slug
- title
- topic
- tags
- prompt

Generated assets:

- metadata file
- prompt file
- starter module
- reference solution
- tests
- optional rubric

### `due`

List cards due for review.

Useful for:

- checking daily workload
- filtering by topic
- scripting around the queue

### `review`

Run an interactive review session.

Loop for concept cards:

1. Show prompt
2. Wait for user recall
3. Reveal answer
4. User marks result
5. System updates box and next review date

Loop for code exercise cards:

1. Show the exercise prompt and workspace path
2. Let the user implement the target Python file
3. Run deterministic validation first
4. If needed, run LLM-assisted validation
5. Record the final result and update scheduling

### `list`

Inspect recently added cards for sanity checking and cleanup.

### `stats`

Show queue health.

Examples:

- total cards
- cards due now
- cards reviewed today
- card counts by box

## Data Model

### `cards`

Primary study item table.

Fields:

- `id`
- `type`
- `title`
- `topic`
- `tags`
- `source`
- `asset_path`
- `box`
- `lapse_count`
- `created_at`
- `updated_at`
- `last_reviewed_at`
- `next_review_at`
- `last_result`

Rationale:

- `type` allows different review flows under one scheduler
- `title` is a stable summary across card types
- `asset_path` points to exercise assets for non-text cards
- `box` is the active Leitner position
- `lapse_count` tracks instability over time
- `next_review_at` drives the daily queue
- `topic` and `tags` enable filtering without overcomplicating structure

### `concept_cards`

Concept-specific content.

Fields:

- `card_id`
- `prompt`
- `answer`

Rationale:

- keeps the base `cards` table generic
- avoids null-heavy rows once multiple card types exist

### `exercise_cards`

Exercise-specific metadata.

Fields:

- `card_id`
- `instruction_path`
- `starter_path`
- `solution_path`
- `test_path`
- `validator_type`
- `entrypoint`

Rationale:

- the scheduler should not have to know exercise file layout
- exercise review needs deterministic paths
- validator choice should be explicit

### `reviews`

Append-only review history.

Fields:

- `id`
- `card_id`
- `reviewed_at`
- `result`
- `prior_box`
- `new_box`
- `next_review_at`

Rationale:

- preserves history for future analytics
- allows debugging schedule behavior
- supports later features like streaks or retention reports

## Storage Choice

Use SQLite for scheduling metadata and filesystem assets for exercise content.

Why SQLite:

- local-first
- zero service setup
- reliable enough for long-term use
- supports filtering, history, and statistics cleanly
- easier to evolve than flat JSON once review logs exist

Why filesystem assets:

- Python exercises are better represented as files than long text blobs
- tests, starter code, and reference implementations should be diffable
- modules can be executed directly by local tooling

Why not JSON first:

- review history becomes awkward quickly
- concurrent writes and schema changes are messy
- filtering and stats become more manual than necessary

Why not store `.ipynb`:

- the actual study artifact is Python code
- notebooks are noisy in git
- validation is easier against `.py`
- notebook-style exercises can still be expressed as Python scripts

## Configuration

Use a repo-local `config.toml` for the project version.

Initial config fields:

```toml
[study]
data_dir = ".barsky"
database = ".barsky/study.db"
box_intervals = [1, 2, 4, 8, 16]
review_order = "oldest-first"
cards_dir = "cards"
llm_validator = "openai"
```

Configuration goals:

- keep defaults explicit
- make interval tuning easy
- avoid hidden behavior

Future config candidates:

- daily new-card cap
- default review limit
- alternate box schedules
- timezone handling
- LLM model selection
- temp workspace location

## Scheduling Logic

Pseudo-logic:

```text
if result == correct:
    new_box = min(5, old_box + 1)
else:
    new_box = 1

next_review_at = now + interval_for(new_box)
```

Due selection:

- card is due when `next_review_at <= now`
- default order should be oldest due first

Design choice:

- keep reviews binary in v1: `correct` or `wrong`

Why:

- matches the classic Leitner model
- reduces interaction overhead
- easier to reason about than four-button review scoring

Possible v2 expansion:

- `hard`, `good`, `easy`
- partial-credit scheduling

## Exercise Asset Layout

Recommended layout:

```text
cards/
  algorithms/
    binary-search/
      card.toml
      prompt.md
      starter.py
      solution.py
      tests.py
```

Example `card.toml`:

```toml
id = "algorithms.binary-search"
type = "code_exercise"
title = "Implement binary search"
topic = "algorithms"
tags = ["python", "search"]

[review]
validator = "tests_then_llm"
entrypoint = "starter.py"
test_file = "tests.py"
solution_file = "solution.py"
```

The canonical source of truth for executable study material is always `.py`.

## User Workflow

Daily usage:

1. Capture concept cards or scaffold exercises while learning
2. Run `due` or `stats` to see the queue
3. Run `review` once or twice a day
4. Promote or reset cards based on recall

Recommended card style:

- one concept per card
- question/answer instead of paragraph notes
- focus on recall, not recognition
- turn mistakes into cards quickly
- keep exercise scope small enough to complete in one sitting

Examples:

- "What bug does a race condition describe?"
- "Why does `git rebase` rewrite commit history?"
- "When should `useEffect` depend on a value?"
- "Reimplement a binary search over a sorted list."
- "Rebuild a tiny LRU cache module from memory."

## Review and Validation Pipeline

### Concept Review

- reveal prompt
- recall answer
- self-grade as correct or wrong
- reschedule

### Code Exercise Review

- reveal prompt and working file path
- user reimplements the target in Python
- CLI runs deterministic checks first
- CLI optionally runs LLM validation second
- final grade is recorded

Validation order:

1. deterministic tests
2. optional reference-output checks
3. LLM review

Design rule:

- LLM is not the primary source of truth when deterministic tests exist

Rationale:

- tests are stricter and reproducible
- LLM can catch conceptual mismatches and incomplete reasoning
- LLM-only grading is too permissive for executable tasks

Expected LLM responsibilities:

- compare implementation intent against the exercise prompt
- identify likely correctness gaps not covered by tests
- explain failures in plain language
- provide actionable hints when requested

Expected deterministic responsibilities:

- verify behavior
- catch regressions
- provide trustable pass/fail signals

## Architecture

Proposed Python layout:

```text
cli.py
study/
  __init__.py
  app.py
  config.py
  storage.py
  exercises.py
  validators.py
tests/
  test_study.py
  test_exercises.py
config.toml
README.md
cards/
```

Module responsibilities:

- `cli.py`: entrypoint
- `study/app.py`: argparse command wiring
- `study/config.py`: config discovery and parsing
- `study/storage.py`: schema, queries, and scheduling mutations
- `study/exercises.py`: scaffold and locate exercise assets
- `study/validators.py`: test runner and LLM validation integration
- `tests/`: core workflow tests

## Constraints

Non-goals for v1:

- editing cards in place
- deleting cards
- importing from CSV or markdown
- sync across machines
- web UI
- attachments or images
- native notebook storage

Reason:

The core risk is not lack of features. The risk is building a system that is too
heavy to use every day. The first version should prove the study loop.

## Testing Plan

Test the workflow, not just parsing.

Required tests:

- add concept card -> card is due immediately
- review correct -> card moves up one box
- review wrong -> card resets to box 1
- stats reflect queue shape
- config loading resolves relative paths correctly
- exercise scaffold creates expected files
- exercise validator runs tests against a temp workspace

Manual checks:

- interactive `review` session feels fast
- `due` output is readable
- exercise review can point the user at a concrete file to implement
- storage initializes cleanly from an empty directory

## Implementation Plan

### Phase 1

Deliver the working core:

- config loading
- SQLite schema
- `init`
- `add-concept`
- `due`
- `review`
- `stats`

### Phase 2

Improve usability:

- better output formatting
- `list` for recent cards
- topic filtering
- shuffling option for review sessions
- `add-exercise`
- exercise scaffolding
- deterministic validation runner

### Phase 3

Add retention tooling if needed:

- import/export
- edit/delete flows
- card templates
- richer review scoring
- advanced reporting
- LLM-assisted grading for exercises

## Open Decisions

These are the main design questions worth settling before the tool grows:

1. Should the v1 review flow stay strictly binary, or should it support `hard`
   and `easy` from the start?
2. Should new cards be due immediately, or should they be queued for end-of-day
   review only?
3. Should exercise grading require deterministic tests for every exercise, or can
   some exercises rely on rubric-plus-LLM review?
4. Should `topic` be a single string with `tags` as secondary metadata, or should
   everything be tag-based?

## Recommended Starting Point

My recommendation is:

- keep v1 binary
- keep the 5-box schedule fixed in config
- use SQLite plus filesystem assets
- support `concept` and `code_exercise`
- require `.py` as the executable source format
- make tests the primary validator for exercises
- add LLM validation as a secondary layer
- prove the daily loop first

That gives you a system you can actually use immediately and critique from real
experience, instead of prematurely optimizing the scheduler.
