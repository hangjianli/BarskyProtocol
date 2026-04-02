# Card Contract

## Purpose

BarskyProtocol should publish a plain-text contract that any model or user can
generate and paste into the UI to create cards.

The contract should be:

- machine-parseable
- easy for humans to edit
- stable across LLM providers
- able to represent both supported card categories

## Format Choice

Use TOML.

Why:

- Python can parse it with the standard library
- multiline strings are easy for prompts and code blocks
- arrays work well for tags
- multiple cards can be represented cleanly with `[[cards]]`

## Root Shape

```toml
version = 1

[[cards]]
type = "concept"
...

[[cards]]
type = "code_exercise"
...
```

Rules:

- `version` is optional but recommended
- `[[cards]]` may appear one or more times
- unknown fields should be rejected in v1 so contract mistakes are obvious

## `concept` Card Contract

Required fields:

- `type = "concept"`
- `title`
- `prompt`
- `answer`

Optional fields:

- `topic`
- `tags`
- `source`
- `source_path`
- `source_mode`
- `source_label`
- `source_kind`
- `source_cell_spec`
- `source_import_options`

## `code_exercise` Card Contract

Required fields:

- `type = "code_exercise"`
- `title`
- `prompt`
- `answer_py`
- `solution_py`
- `tests_py`

Optional fields:

- `topic`
- `tags`
- `source`
- `source_path`
- `source_mode`
- `source_label`
- `source_kind`
- `source_cell_spec`
- `source_import_options`
- `slug`

`answer_py`, `solution_py`, and `tests_py` become the generated exercise files.

## Tags

Preferred form:

```toml
tags = ["python", "tokenizer"]
```

Acceptable fallback:

```toml
tags = "python, tokenizer"
```

## Example

```toml
version = 1

[[cards]]
type = "concept"
title = "Mutex"
topic = "python"
tags = ["threading", "concurrency"]
prompt = """
What does a mutex do?
"""
answer = """
It serializes access to shared state.
"""

[[cards]]
type = "code_exercise"
title = "Split Words"
topic = "nlp"
tags = ["python", "tokenizer"]
source = "LLMs-from-scratch ch02"
source_kind = "py"
prompt = """
Implement `split_words(text)` and return a token list.
"""
answer_py = """
\"\"\"Reimplement the imported exercise: Split Words.\"\"\"

raise NotImplementedError("Implement the exercise in answer.py")
"""
solution_py = """
import re

def split_words(text: str) -> list[str]:
    return re.findall(r"\\w+", text)
"""
tests_py = """
import unittest
import answer

class ExerciseTests(unittest.TestCase):
    def test_split_words(self) -> None:
        self.assertEqual(answer.split_words("a b"), ["a", "b"])

if __name__ == "__main__":
    unittest.main()
"""
```

## UI Flow

The web app should expose a text import page where the user can:

1. paste one or more TOML cards
2. submit the payload
3. see validation errors inline if the contract is malformed
4. create all valid cards in one action

This flow complements source import. It does not replace notebook or Python
source parsing.

Multi-card contract imports should be atomic. If any later card fails to
create, the importer should roll back earlier cards from the same payload so
the user does not end up with a half-imported deck.
