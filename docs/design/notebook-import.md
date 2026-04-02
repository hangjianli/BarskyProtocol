# Source Import

## Purpose

Source import turns a source notebook or Python module into one or more
standalone `code_exercise` cards.

The source file is provenance, not the runtime study artifact.

## Source Modes

- `external_path`
  The card points to the original source path and no copy is created.
- `managed_copy`
  The app stores its own source copy and points the card at that saved copy.

## Import Inputs

The import page should support:

- pasting a source path
- dropping plain text that contains a source path
- uploading or dragging a `.ipynb` file
- uploading or dragging a `.py` file

If a readable path is provided, prefer `external_path`.
If file contents are uploaded, use `managed_copy`.

## Import Pipeline

1. Read source text.
2. Detect source kind from the path or label.
3. Parse ordered notebook cells or Python top-level definitions.
4. Segment the source into candidate exercise units.
5. Enrich candidate metadata with LLM topic/tag suggestions.
6. Show a review screen before creating cards.
7. Let the user keep, delete, or edit candidates.
8. Create standalone `code_exercise` cards.

Supported source kinds:

- `.ipynb`
- `.py`

The importer should never create source-derived cards silently.

## Source-Specific Parsing

### `.ipynb`

Notebook imports should:

- parse markdown and code cells
- use heading-aware grouping in `balanced` mode
- split substantive code cells more readily in `aggressive` mode
- preserve enough support code to keep each generated exercise standalone

### `.py`

Python imports should:

- parse the module with `ast`
- detect top-level imports, assignments, functions, async functions, and classes
- treat imports and module constants as support context
- derive candidates from top-level functions and classes
- fall back to a single module-level candidate when there are no clear function or class units

In `aggressive` mode, each candidate should focus on one top-level function or
class while carrying forward the support code it depends on.

In `balanced` mode, related top-level definitions may stay together as one
candidate when the module is small or tightly coupled.

## Candidate Quality Rules

Candidate quality matters more than raw split count.

The importer should:

- trim noisy markdown in prompt text
- drop irrelevant notebook boilerplate such as images and troubleshooting prose
- keep only nearby explanatory context for narrow candidates
- generate task-focused prompts from the extracted code intent
- separate runtime support context from visible study prompt context
- preserve dependencies so generated exercises remain standalone

The generated prompt should describe the implementation task, not just repeat a
chapter heading or file name.

## Metadata Suggestions

Candidate metadata should be enriched with the LLM:

- topic
- tags

Rules:

- suggestions are best-effort, not required for import success
- the user can edit them before creation
- if the LLM call fails, fallback metadata should still render

## Split Modes

Supported split modes:

- `balanced`
- `aggressive`

`balanced` keeps related code together under a broader candidate.

`aggressive` splits more readily into smaller candidates, but still preserves
enough support context to keep each generated exercise standalone.

## Regeneration

The review screen should support regenerating the draft after changing split
mode.

Regeneration rules:

1. Keep the same source reference.
2. Re-run segmentation with the selected split mode.
3. Replace the candidate list in the draft.
4. Preserve the review-first workflow.

## Provenance

Imported cards should keep:

- source path
- source mode
- source label
- source kind
- source section spec
- import options such as split mode

## Generated Assets

Each approved candidate produces:

- `prompt.md`
- `answer.py`
- `solution.py`
- `tests.py`

The resulting study artifact is always `.py`, even when the source came from a
notebook.
