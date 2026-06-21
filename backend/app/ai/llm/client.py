"""HTTP client for the local Qwen orchestrator (OpenAI-compatible vLLM).

Thin wrapper over /v1/chat/completions. The orchestrator only ever returns
structured JSON, so chat_json() enforces a schema via vLLM's guided_json
(guided decoding) and retries on parse / validation failure.

No model weights are loaded in-process; this only speaks HTTP to the server
launched by scripts/serve_qwen.sh.
"""
from __future__ import annotations

import json

import httpx

from app.ai.llm import config


class OrchestratorError(RuntimeError):
    """Server unreachable, HTTP error, or unparseable response."""


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=config.ORCH_CONNECT_TIMEOUT,
        read=config.ORCH_READ_TIMEOUT,
        write=config.ORCH_READ_TIMEOUT,
        pool=config.ORCH_CONNECT_TIMEOUT,
    )


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.ORCH_API_KEY}"}


def health() -> dict:
    """Liveness probe. Returns {"ok": bool, "detail": str, "model": str|None}."""
    url = config.ORCH_BASE_URL.rstrip("/") + "/models"
    try:
        with httpx.Client(timeout=_timeout()) as cli:
            resp = cli.get(url, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
        ids = [m.get("id") for m in data.get("data", [])]
        served = config.ORCH_MODEL in ids
        return {
            "ok": served,
            "detail": "model served" if served else f"model {config.ORCH_MODEL!r} not in {ids}",
            "model": config.ORCH_MODEL if served else None,
        }
    except Exception as exc:  # noqa: BLE001 - report any failure as not-live
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}", "model": None}


def chat(
    system: str,
    user: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    guided_json: dict | None = None,
) -> str:
    """Single-turn chat completion. Returns raw assistant content (str)."""
    url = config.ORCH_BASE_URL.rstrip("/") + "/chat/completions"
    payload: dict = {
        "model": config.ORCH_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": config.ORCH_TEMPERATURE if temperature is None else temperature,
        "max_tokens": config.ORCH_MAX_TOKENS if max_tokens is None else max_tokens,
    }
    # vLLM extension: constrain output to a JSON schema (guided decoding).
    if guided_json is not None:
        payload["guided_json"] = guided_json

    try:
        with httpx.Client(timeout=_timeout()) as cli:
            resp = cli.post(url, headers=_headers(), json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except httpx.HTTPError as exc:
        raise OrchestratorError(f"orchestrator request failed: {exc}") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise OrchestratorError(f"unexpected orchestrator response: {exc}") from exc


def chat_json(
    system: str,
    user: str,
    schema: dict,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Chat completion constrained to `schema`, parsed to a dict.

    Uses guided_json for schema-enforced decoding, then validates the result
    parses as JSON. Retries ORCH_JSON_RETRIES times on parse failure before
    raising OrchestratorError.
    """
    last_err: Exception | None = None
    for attempt in range(config.ORCH_JSON_RETRIES + 1):
        raw = chat(
            system,
            user,
            temperature=temperature,
            max_tokens=max_tokens,
            guided_json=schema,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            last_err = exc
            # Nudge the model on retry; guided_json should already prevent this.
            user = f"{user}\n\n(Previous reply was not valid JSON. Return ONLY JSON.)"
    raise OrchestratorError(
        f"could not parse JSON after {config.ORCH_JSON_RETRIES + 1} attempts: {last_err}"
    )
