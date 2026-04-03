# Changelog

This changelog is maintained retrospectively from the repository commit history.
Commit hashes below reflect the rewritten history with the corrected git author
identity.

## `41cd4d5` - `2026-04-02` - `feat: use codex responses api for grading`

- Switched LLM grading to the Responses API with `gpt-5-codex` as the default model.
- Added ChatGPT backend compatibility, including Codex OAuth refresh/retry handling.
- Added streaming-response parsing so concept grading works against the Codex backend.

## `90d87b6` - `2026-04-02` - `feat: add review queue navigation`

- Added previous/next navigation across the active review queue.
- Preserved queue mode across grading, validation, and source-view routes.
- Added route and storage coverage for adjacent review navigation.

## `b522b53` - `2026-04-02` - `feat: render prompt markdown with source viewer`

- Rendered card prompts as markdown instead of escaped plain text.
- Added read-only in-app source viewing for card-bound local source references.
- Restricted source-view access to bound card files and updated the related workflow docs.

## `731ac20` - `2026-04-01` - `refactor: format card timestamps`

- Reformatted due dates and next-review labels to concise date strings.
- Reformatted created/review timestamps to a short local date-time format.
- Updated UI tests to assert the cleaner timestamp presentation.

## `89cc0cb` - `2026-04-01` - `docs: refresh repository readme`

- Reworked the README with a more polished header, badges, and quick links.
- Updated the README to reflect the web-first workflow and current import options.

## `a7e02d7` - `2026-04-01` - `refactor: show card creation dates`

- Changed the cards list to show creation timestamps instead of low-value box labels alone.
- Improved card-list scanning by moving useful metadata into the right-hand summary.

## `383d5e4` - `2026-04-01` - `fix: harden import workflows`

- Tightened import workflow handling around candidate cleanup and metadata persistence.
- Reduced importer fragility after the notebook and text-contract feature work.

## `a744f24` - `2026-04-01` - `content: add bpe merge step exercise`

- Checked in the BPE merge-step exercise assets under `cards/` as tracked study content.

## `df046f7` - `2026-04-01` - `feat: add card deletion flow`

- Added UI support for deleting cards from the card detail page.
- Removed owned review history, exercise assets, and retained workspaces on delete.
- Preserved external or managed source files instead of deleting provenance copies blindly.

## `2f5cd55` - `2026-04-01` - `feat: add contract-based card import`

- Added a TOML card contract for concept and code-exercise imports.
- Added a text-import UI that can ingest pasted card definitions directly.
- Tightened aggressive imports so independent exercises stop inheriting unrelated prior code.

## `33c9e36` - `2026-04-01` - `refactor: simplify top-level navigation`

- Reduced top-level nav clutter and moved secondary actions into compact dropdowns.
- Kept the dashboard hero focused on a single primary `Start Review` action.

## `a9a8cb4` - `2026-04-01` - `feat: support python source imports`

- Added `.py` source import alongside `.ipynb` import.
- Split Python files into exercise candidates using AST-based top-level parsing.
- Reduced button clutter in the import UI and moved secondary controls into dropdowns.

## `c9bcabb` - `2026-04-01` - `docs: split design reference`

- Split the monolithic design doc into focused files under `docs/design/`.
- Turned `DESIGN.md` into a shorter index with links into the detailed docs.

## `02526fd` - `2026-04-01` - `fix: keep aggressive notebook splits standalone`

- Fixed aggressive notebook imports so later candidates keep earlier support code they depend on.
- Preserved support context in both `solution.py` and `answer.py` for aggressively split exercise cards.
- Added regression coverage to ensure later imported candidates remain standalone.

## `58cc824` - `2026-04-01` - `docs: add retrospective changelog`

- Added `CHANGELOG.md` to track the repository's milestone commits in plain language.
- Captured the rewritten commit history so later contributors can understand what each shipped milestone introduced.

## `84f1a8f` - `2026-04-01` - `feat: add configurable notebook import splitting`

- Added configurable notebook split modes for import: `balanced` and `aggressive`.
- Added a regenerate flow in the notebook import UI so users can rebuild the draft after changing split mode.
- Stored import options in card provenance and documented the behavior in the design docs.

## `374d1a6` - `2026-04-01` - `feat: add recommendations and mixed review`

- Added a deterministic recommendations page driven by stored failures and incompletes.
- Added mixed review mode so `/review` can choose across both concept and exercise queues.
- Expanded test coverage for recommendations and mixed queue selection.

## `bbaa9ee` - `2026-04-01` - `feat: add notebook import workflow`

- Added notebook import from either an external path or a managed copy.
- Added notebook parsing and candidate exercise generation with a review-before-create workflow.
- Added exercise scaffolding, validation helpers, provenance fields, and corresponding web UI routes.

## `d723afa` - `2026-04-01` - `feat: grade concept answers with typed input`

- Changed concept review to require a typed answer before grading.
- Added LLM-based grading for concept answers and showed grading details in the result page.
- Wired runtime model auth configuration into the local app config.

## `99ed37b` - `2026-04-01` - `feat: add phase 1 web study workflow`

- Added the first web-based study workflow on `localhost`.
- Introduced the fallback scheduler with transparent explanations.
- Added cards and patterns pages, minimal styling, and storage changes to support web review attempts.

## `e27afce` - `2026-04-01` - `chore: bootstrap v1 study CLI`

- Bootstrapped the repository with config loading, SQLite storage, and the initial CLI.
- Added the first design document and README.
- Tagged this initial milestone as `v1`.
