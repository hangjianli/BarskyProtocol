# Changelog

This changelog is maintained retrospectively from the repository commit history.
Commit hashes below reflect the rewritten history with the corrected git author
identity.

## `84f1a8f` - `feat: add configurable notebook import splitting`

- Added configurable notebook split modes for import: `balanced` and `aggressive`.
- Added a regenerate flow in the notebook import UI so users can rebuild the draft after changing split mode.
- Stored import options in card provenance and documented the behavior in `DESIGN.md`.

## `374d1a6` - `feat: add recommendations and mixed review`

- Added a deterministic recommendations page driven by stored failures and incompletes.
- Added mixed review mode so `/review` can choose across both concept and exercise queues.
- Expanded test coverage for recommendations and mixed queue selection.

## `bbaa9ee` - `feat: add notebook import workflow`

- Added notebook import from either an external path or a managed copy.
- Added notebook parsing and candidate exercise generation with a review-before-create workflow.
- Added exercise scaffolding, validation helpers, provenance fields, and corresponding web UI routes.

## `d723afa` - `feat: grade concept answers with typed input`

- Changed concept review to require a typed answer before grading.
- Added LLM-based grading for concept answers and showed grading details in the result page.
- Wired runtime model auth configuration into the local app config.

## `99ed37b` - `feat: add phase 1 web study workflow`

- Added the first web-based study workflow on `localhost`.
- Introduced the fallback scheduler with transparent explanations.
- Added cards and patterns pages, minimal styling, and storage changes to support web review attempts.

## `e27afce` - `chore: bootstrap v1 study CLI`

- Bootstrapped the repository with config loading, SQLite storage, and the initial CLI.
- Added the first design document and README.
- Tagged this initial milestone as `v1`.
