# Data Model and Storage

## Storage Choice

Use SQLite for study metadata and the filesystem for exercise assets.

Why:

- local-first
- zero service setup
- reliable filtering and history queries
- Python exercises are better represented as files than long text blobs

## Core Tables

### `cards`

Shared study item table.

Important fields:

- `id`
- `type`
- `title`
- `topic`
- `tags`
- `source_path`
- `source_mode`
- `source_label`
- `source_kind`
- `source_cell_spec`
- `source_import_options`
- `asset_path`
- `box`
- `lapse_count`
- `next_review_at`
- `last_result`

### `concept_cards`

Concept-specific content:

- `card_id`
- `prompt`
- `answer`

### `exercise_cards`

Exercise-specific metadata:

- `card_id`
- `prompt_path`
- `answer_path`
- `solution_path`
- `tests_path`
- `entrypoint`

### `reviews`

Append-only review history.

Important fields:

- `card_id`
- `reviewed_at`
- `result`
- `prior_box`
- `new_box`
- `next_review_at`
- `validator_summary`
- `failing_tests`
- `workspace_path`
- `reason_codes`
- `reason_summary`

### `review_attempts`

Tracks active and completed review attempts.

Important fields:

- `card_id`
- `card_type`
- `status`
- `started_at`
- `completed_at`
- `result`
- `workspace_path`
- `validator_summary`
- `failing_tests`

## Exercise Filesystem Layout

Recommended layout:

```text
cards/
  algorithms/
    binary-search/
      prompt.md
      answer.py
      solution.py
      tests.py
```

Runtime data lives under `.barsky/`, including:

- SQLite database
- temp workspaces
- saved notebook sources
- saved Python source snapshots
- notebook import drafts

## Provenance

Imported cards should keep source provenance explicitly, including:

- where the source came from
- which cells or code sections produced the candidate
- which import options were used

That provenance exists to help debugging, tracing, and future re-imports.

## Deletion Rules

When a card is deleted:

- delete the `cards` row
- rely on foreign-key cascades for dependent concept, exercise, review, and attempt rows
- delete the owned exercise asset directory if the card is a `code_exercise`
- delete retained workspace directories referenced by that card
- do not delete external source files or shared managed source snapshots
