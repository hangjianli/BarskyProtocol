from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

from study.config import StudyConfig


class GradingError(RuntimeError):
    """Raised when the concept grader cannot produce a usable result."""


@dataclass(frozen=True)
class ConceptGrade:
    result: str
    summary: str
    model: str


def _load_codex_access_token(auth_file: Path) -> str:
    if not auth_file.is_file():
        raise GradingError(f"Codex auth file not found: {auth_file}")

    try:
        payload = json.loads(auth_file.read_text(encoding="utf-8"))
        token = str(payload["tokens"]["access_token"]).strip()
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise GradingError(f"Codex auth file is malformed: {auth_file}") from exc

    if not token:
        raise GradingError(f"Codex auth file does not contain an access token: {auth_file}")
    return token


def _resolve_auth_header(config: StudyConfig) -> str:
    # Keep API-key auth available, but default to the local Codex auth store.
    if config.llm_validator == "codex_oauth":
        return f"Bearer {_load_codex_access_token(config.llm_auth_file)}"

    if config.llm_validator == "openai_api_key":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise GradingError("Set OPENAI_API_KEY before grading concept answers with the LLM.")
        return f"Bearer {api_key}"

    raise GradingError(f"Unsupported llm_validator: {config.llm_validator}")


def grade_concept_answer(
    config: StudyConfig,
    *,
    prompt: str,
    reference_answer: str,
    user_answer: str,
) -> ConceptGrade:
    endpoint = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", config.llm_model)
    authorization = _resolve_auth_header(config)
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You grade study answers for a spaced-repetition system. "
                    "Return strict JSON with keys `result` and `summary`. "
                    "`result` must be `pass` or `fail`. "
                    "Grade semantic correctness rather than exact wording, but fail answers "
                    "that omit core ideas or introduce contradictions. "
                    "`summary` must be concise and explain the grading decision."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Prompt:\n{prompt}\n\n"
                    f"Reference answer:\n{reference_answer}\n\n"
                    f"User answer:\n{user_answer}\n\n"
                    "Return JSON only."
                ),
            },
        ],
        # JSON mode keeps the response machine-readable without adding a client dependency.
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    payload = json.dumps(body).encode("utf-8")
    http_request = request.Request(
        f"{endpoint}/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(http_request, timeout=30) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise GradingError(f"OpenAI grading request failed: {details}") from exc
    except error.URLError as exc:
        raise GradingError(f"OpenAI grading request failed: {exc.reason}") from exc

    try:
        content = raw_response["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        result = str(parsed["result"]).strip().lower()
        summary = str(parsed["summary"]).strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise GradingError("The grading response could not be parsed as JSON.") from exc

    if result not in {"pass", "fail"}:
        raise GradingError("The grading response returned an invalid result.")
    if not summary:
        raise GradingError("The grading response did not include a summary.")

    return ConceptGrade(result=result, summary=summary, model=model)
