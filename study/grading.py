from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib import error, parse, request

from study.config import StudyConfig

DEFAULT_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


class GradingError(RuntimeError):
    """Raised when the concept grader cannot produce a usable result."""


@dataclass(frozen=True)
class ConceptGrade:
    result: str
    summary: str
    model: str


@dataclass(frozen=True)
class LLMResult:
    content: dict
    model: str


@dataclass(frozen=True)
class CodexTokens:
    access_token: str
    refresh_token: str
    id_token: str
    account_id: str


def _load_codex_tokens(auth_file: Path) -> CodexTokens:
    if not auth_file.is_file():
        raise GradingError(f"Codex auth file not found: {auth_file}")

    try:
        payload = json.loads(auth_file.read_text(encoding="utf-8"))
        tokens = payload["tokens"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise GradingError(f"Codex auth file is malformed: {auth_file}") from exc

    access_token = str(tokens.get("access_token", "")).strip()
    refresh_token = str(tokens.get("refresh_token", "")).strip()
    id_token = str(tokens.get("id_token", "")).strip()
    account_id = str(tokens.get("account_id", "")).strip()
    if not access_token:
        raise GradingError(f"Codex auth file does not contain an access token: {auth_file}")
    return CodexTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        account_id=account_id,
    )


def _write_codex_tokens(auth_file: Path, tokens: CodexTokens) -> None:
    payload = json.loads(auth_file.read_text(encoding="utf-8"))
    payload["last_refresh"] = datetime.now(UTC).replace(microsecond=0).isoformat()
    payload["tokens"] = {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "id_token": tokens.id_token,
        "account_id": tokens.account_id,
    }
    auth_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _jwt_expiry(access_token: str) -> datetime | None:
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        expiry = int(payload["exp"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
    return datetime.fromtimestamp(expiry, tz=UTC)


def _token_should_refresh(access_token: str) -> bool:
    expiry = _jwt_expiry(access_token)
    if expiry is None:
        return False
    # Refresh slightly early so grading does not fail in the middle of a request.
    return expiry <= datetime.now(UTC).replace(microsecond=0)


def _refresh_codex_tokens(auth_file: Path, tokens: CodexTokens) -> CodexTokens:
    if not tokens.refresh_token:
        raise GradingError("Codex auth file does not contain a refresh token.")

    payload = parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "client_id": os.environ.get("CODEX_CLIENT_ID", DEFAULT_CODEX_CLIENT_ID),
        }
    ).encode("utf-8")
    http_request = request.Request(
        "https://auth.openai.com/oauth/token",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with request.urlopen(http_request, timeout=30) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise GradingError(f"Codex token refresh failed: {details}") from exc
    except error.URLError as exc:
        raise GradingError(f"Codex token refresh failed: {exc.reason}") from exc

    access_token = str(raw_response.get("access_token", "")).strip()
    if not access_token:
        raise GradingError("Codex token refresh response did not include an access token.")

    refreshed = CodexTokens(
        access_token=access_token,
        refresh_token=str(raw_response.get("refresh_token") or tokens.refresh_token).strip(),
        id_token=str(raw_response.get("id_token") or tokens.id_token).strip(),
        account_id=str(raw_response.get("account_id") or tokens.account_id).strip(),
    )
    _write_codex_tokens(auth_file, refreshed)
    return refreshed


def _resolve_auth_header(config: StudyConfig) -> str:
    # Keep API-key auth available, but default to the local Codex auth store.
    if config.llm_validator == "codex_oauth":
        tokens = _load_codex_tokens(config.llm_auth_file)
        if _token_should_refresh(tokens.access_token):
            tokens = _refresh_codex_tokens(config.llm_auth_file, tokens)
        return f"Bearer {tokens.access_token}"

    if config.llm_validator == "openai_api_key":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise GradingError("Set OPENAI_API_KEY before grading concept answers with the LLM.")
        return f"Bearer {api_key}"

    raise GradingError(f"Unsupported llm_validator: {config.llm_validator}")


def _base_url(config: StudyConfig) -> str:
    override = os.environ.get("OPENAI_BASE_URL", "").strip()
    if override:
        return override.rstrip("/")
    if config.llm_base_url.strip():
        return config.llm_base_url.strip().rstrip("/")
    if config.llm_validator == "codex_oauth":
        return "https://chatgpt.com/backend-api"
    return "https://api.openai.com/v1"


def _api_mode(config: StudyConfig) -> str:
    override = os.environ.get("OPENAI_API", "").strip()
    if override:
        return override
    if config.llm_api.strip():
        return config.llm_api.strip()
    if config.llm_validator == "codex_oauth":
        return "openai-codex-responses"
    return "responses"


def _responses_endpoint(config: StudyConfig) -> str:
    base_url = _base_url(config)
    api_mode = _api_mode(config)
    if api_mode == "openai-codex-responses":
        return f"{base_url}/codex/responses"
    if api_mode == "responses":
        return f"{base_url}/responses"
    raise GradingError(f"Unsupported llm_api: {api_mode}")


def _responses_input(config: StudyConfig, *, user_prompt: str) -> object:
    if _api_mode(config) == "openai-codex-responses":
        return [{"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}]
    return user_prompt


def _should_stream(config: StudyConfig) -> bool:
    return _api_mode(config) == "openai-codex-responses"


def _call_json_llm(
    config: StudyConfig,
    *,
    system_prompt: str,
    user_prompt: str,
) -> LLMResult:
    model = os.environ.get("OPENAI_MODEL", config.llm_model)
    body = {
        "model": model,
        "instructions": system_prompt,
        # Both OpenAI and ChatGPT Codex backends expose Responses-compatible payloads.
        "input": _responses_input(config, user_prompt=user_prompt),
        "text": {"format": {"type": "json_object"}},
        "store": False,
    }
    if _should_stream(config):
        body["stream"] = True
    payload = json.dumps(body).encode("utf-8")
    raw_response = None
    last_error: Exception | None = None
    endpoint = _responses_endpoint(config)
    for refresh_on_unauthorized in (False, True):
        http_request = request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": _resolve_auth_header(config) if not refresh_on_unauthorized else _refreshed_auth_header(config),
                "Content-Type": "application/json",
            },
        )

        try:
            with request.urlopen(http_request, timeout=30) as response:
                response_body = response.read().decode("utf-8")
                if _should_stream(config):
                    raw_response = _streaming_response_payload(response_body)
                else:
                    raw_response = json.loads(response_body)
                break
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            last_error = GradingError(f"OpenAI grading request failed: {details}")
            if exc.code == 401 and not refresh_on_unauthorized and config.llm_validator == "codex_oauth":
                continue
            raise last_error from exc
        except error.URLError as exc:
            raise GradingError(f"OpenAI grading request failed: {exc.reason}") from exc

    if raw_response is None:
        raise last_error or GradingError("OpenAI grading request failed.")

    try:
        parsed = json.loads(_response_text(raw_response))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise GradingError("The grading response could not be parsed as JSON.") from exc

    return LLMResult(content=parsed, model=model)


def _refreshed_auth_header(config: StudyConfig) -> str:
    if config.llm_validator != "codex_oauth":
        return _resolve_auth_header(config)
    tokens = _refresh_codex_tokens(config.llm_auth_file, _load_codex_tokens(config.llm_auth_file))
    return f"Bearer {tokens.access_token}"


def _streaming_response_payload(response_body: str) -> dict:
    # Some compatible backends return a single JSON payload even when `stream`
    # is requested, so accept that shape before parsing SSE chunks.
    stripped = response_body.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    deltas: list[str] = []
    completed_response: dict | None = None
    # Normalize CRLF-delimited SSE chunks so both standard and LF-only streams
    # are decoded the same way.
    normalized = response_body.replace("\r\n", "\n")
    for raw_event in normalized.split("\n\n"):
        lines = [line for line in raw_event.splitlines() if line.startswith("data:")]
        if not lines:
            continue
        data = "\n".join(line[5:].strip() for line in lines).strip()
        if not data or data == "[DONE]":
            continue
        event = json.loads(data)
        event_type = str(event.get("type", ""))
        if event_type == "response.output_text.delta":
            deltas.append(str(event.get("delta", "")))
            continue
        if event_type in {"response.completed", "response.done"} and isinstance(event.get("response"), dict):
            completed_response = event["response"]
            continue
        if event_type == "error":
            raise GradingError(f"OpenAI grading request failed: {json.dumps(event)}")

    if completed_response is not None:
        return completed_response
    return {"output_text": "".join(deltas)}


def _response_text(raw_response: dict) -> str:
    direct_text = raw_response.get("output_text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text

    for output_item in raw_response.get("output", []):
        if not isinstance(output_item, dict):
            continue
        for content_item in output_item.get("content", []):
            if not isinstance(content_item, dict):
                continue
            text_value = content_item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                return text_value
            if isinstance(text_value, dict):
                nested = text_value.get("value")
                if isinstance(nested, str) and nested.strip():
                    return nested

    raise KeyError("No text content in Responses API payload.")


def grade_concept_answer(
    config: StudyConfig,
    *,
    prompt: str,
    reference_answer: str,
    user_answer: str,
) -> ConceptGrade:
    response = _call_json_llm(
        config,
        system_prompt=(
            "You grade study answers for a spaced-repetition system. "
            "Return strict JSON with keys `result` and `summary`. "
            "`result` must be `pass` or `fail`. "
            "Grade semantic correctness rather than exact wording, but fail answers "
            "that omit core ideas or introduce contradictions. "
            "`summary` must be concise and explain the grading decision."
        ),
        user_prompt=(
            f"Prompt:\n{prompt}\n\n"
            f"Reference answer:\n{reference_answer}\n\n"
            f"User answer:\n{user_answer}\n\n"
            "Return JSON only."
        ),
    )

    try:
        result = str(response.content["result"]).strip().lower()
        summary = str(response.content["summary"]).strip()
    except (KeyError, TypeError) as exc:
        raise GradingError("The grading response did not include the expected keys.") from exc

    if result not in {"pass", "fail"}:
        raise GradingError("The grading response returned an invalid result.")
    if not summary:
        raise GradingError("The grading response did not include a summary.")

    return ConceptGrade(result=result, summary=summary, model=response.model)
