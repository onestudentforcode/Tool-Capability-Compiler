"""An LLM-backed evaluator for aspects that cannot be asserted deterministically.

Step 13 implements the :class:`Evaluator` variant for natural-language answers,
summary quality, information completeness and semantic correctness (phase3 §71,
§2183). It asks a local Ollama judge to return ``success``, ``quality_score``
and ``reason``.

Two contracts matter:

* Regression must not rely on an LLM judge alone (phase3 §72); deterministic
  scenarios should keep using :class:`StructuredEvaluator`.
* When the evaluator itself fails it raises :class:`EvaluationError`, which is
  distinct from a business failure (phase3 §74) -- the caller can tell
  ``execution failed`` / ``evaluation failed`` / ``business failed`` apart.

Configuration mirrors the Step 8 resolver / Step 12 router and reads from the
environment (optionally a ``.env`` file), overridable per constructor:

    LLM_BASE_URL          default http://localhost:11434
    LLM_MODEL             default qwen3:1.7b
    LLM_TIMEOUT_SECONDS   default 60

Unit tests inject a fake ``_http`` so they never touch a real model.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..core.env import env_float, env_string
from ..core.errors import EvaluationError
from ..scenario.models import Scenario
from .base import CriterionResult, EvaluationResult, Evaluator, FinalResult

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:1.7b"
DEFAULT_TIMEOUT_SECONDS = 60.0


class LLMJudgeEvaluator:
    """Judge business success of free-form answers via a local Ollama model."""

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        dotenv_path: str | Path | None = None,
        _http: Any = None,
    ) -> None:
        self._model = (
            env_string("LLM_MODEL", DEFAULT_MODEL, dotenv_path=dotenv_path)
            if model is None
            else model
        )
        if not isinstance(self._model, str) or not self._model.strip():
            raise EvaluationError("LLM_MODEL must be a non-empty string")
        self._base_url = (
            env_string("LLM_BASE_URL", DEFAULT_BASE_URL, dotenv_path=dotenv_path)
            if base_url is None
            else base_url
        ).rstrip("/")
        self._timeout = (
            env_float(
                "LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, dotenv_path=dotenv_path
            )
            if timeout_seconds is None
            else timeout_seconds
        )
        self._http = _http

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    async def evaluate(
        self,
        scenario: Scenario,
        result: FinalResult,
        trace: "ExecutionTrace",
    ) -> EvaluationResult:
        payload = self._build_payload(scenario, result)
        response_text = await asyncio.to_thread(self._complete, payload)
        return self._parse_response(response_text)

    def _complete(self, payload: dict[str, Any]) -> str:
        if self._http is not None:
            return self._http(payload)
        return self._chat(payload)

    def _build_payload(
        self, scenario: Scenario, result: FinalResult
    ) -> dict[str, Any]:
        goal = scenario.query if scenario is not None else ""
        response = result.response
        response_block = (
            json.dumps(response, ensure_ascii=False, default=str)
            if response is not None
            else "(no answer produced)"
        )
        system = (
            "You are an answer-quality judge. You evaluate whether the produced "
            "answer satisfies the business goal. Be strict: an answer that omits "
            "a required fact or is not semantically correct must be marked not "
            "successful. Respond ONLY with a single JSON object and no prose, "
            "having exactly these keys: success (boolean), quality_score "
            "(number between 0 and 1), reason (short string)."
        )
        user = (
            f"Business goal:\n{goal}\n\nProduced answer:\n{response_block}"
        )
        return {
            "model": self._model,
            "temperature": 0.0,
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

    def _chat(self, payload: dict[str, Any]) -> str:
        request = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise EvaluationError(
                f"Ollama HTTP {exc.code} for model {self._model!r}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise EvaluationError(
                f"Ollama request failed for model {self._model!r}: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"Ollama returned non-JSON response: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise EvaluationError(
                "Ollama response missing choices[0].message.content"
            ) from exc
        if not isinstance(content, str):
            raise EvaluationError("Ollama message content is not a string")
        return content

    def _parse_response(self, text: str) -> EvaluationResult:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[len("json") :].strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EvaluationError(
                f"LLM judge returned non-JSON verdict: {exc}"
            ) from exc
        if not isinstance(obj, Mapping):
            raise EvaluationError("LLM judge returned JSON that is not an object")

        success = obj.get("success")
        if not isinstance(success, bool):
            raise EvaluationError("LLM judge field 'success' must be a boolean")

        quality = self._extract_quality(obj)
        reason_raw = obj.get("reason")
        reason = (
            reason_raw.strip() if isinstance(reason_raw, str) and reason_raw.strip() else None
        )
        return EvaluationResult(
            success=success,
            criteria=(
                CriterionResult(name="llm_judge", passed=success, detail=reason),
            ),
            quality_score=quality,
            reason=reason,
        )

    @staticmethod
    def _extract_quality(obj: Mapping[str, Any]) -> float | None:
        raw = obj.get("quality_score")
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise EvaluationError(
                "LLM judge field 'quality_score' must be a number between 0 and 1"
            )
        value = float(raw)
        if not 0.0 <= value <= 1.0:
            raise EvaluationError(
                f"LLM judge quality_score {value} is outside [0, 1]"
            )
        return value