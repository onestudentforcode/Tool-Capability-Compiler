"""A real LLM-backed capability resolver backed by a local Ollama server.

This is the Step 8 concrete implementation of :class:`CapabilityResolver`.
It consumes only the declared capabilities available in the topology and maps a
user query into structured capability requirements. It never inspects the
topology graph, validates edges, searches routes or computes coverage by itself
--- those remain deterministic (phase2.md section 19).

Configuration is read from environment variables, optionally loaded from a
``.env`` file:

    LLM_BASE_URL           default http://localhost:11434
    LLM_MODEL              default qwen3:1.7b
    LLM_TIMEOUT_SECONDS    default 60

Every option can be overridden explicitly in the constructor.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import AbstractSet, Any, Mapping, Sequence

from ..core.capability import validate_capability_name
from ..core.errors import CapabilityResolutionError, InvalidCapabilityError
from .resolver import CapabilityResolution, CapabilityResolver


DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:1.7b"
DEFAULT_TIMEOUT_SECONDS = 60.0


def _load_dotenv(path: str | Path | None = None) -> None:
    """Minimal ``.env`` loader that populates missing environment variables."""
    dotenv_path = (
        Path(path) if path is not None else Path(os.getcwd()) / ".env"
    )
    if not dotenv_path.is_file():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class OllamaCapabilityResolver:
    """Resolve a user query into capabilities using a local Ollama model.

    Implements :class:`CapabilityResolver` so it can be injected into
    :class:`capability_runtime.regression.report.FastRegressionRunner` for
    Discovery Mode regression.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        dotenv_path: str | Path | None = None,
        _http: Any = None,
    ) -> None:
        if dotenv_path is not None:
            _load_dotenv(dotenv_path)
        else:
            _load_dotenv()
        self._base_url = (base_url or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        self._timeout = timeout_seconds or _env_float(
            "LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
        )
        if not isinstance(self._model, str) or not self._model.strip():
            raise CapabilityResolutionError("LLM_MODEL must be a non-empty string")
        self._http = _http

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model(self) -> str:
        return self._model

    async def resolve(
        self,
        query: str,
        available_capabilities: AbstractSet[str],
    ) -> CapabilityResolution:
        if not isinstance(query, str) or not query.strip():
            raise CapabilityResolutionError(
                "Resolver query must be a non-empty string"
            )
        available = self._normalize_capabilities(available_capabilities)
        payload = self._build_payload(query, available)
        response_text = await asyncio.to_thread(self._complete, payload)
        return self._parse_response(response_text, available)

    def _complete(self, payload: dict[str, Any]) -> str:
        if self._http is not None:
            return self._http(payload)
        return self._chat(payload)

    def _build_payload(
        self, query: str, available: tuple[str, ...]
    ) -> dict[str, Any]:
        available_block = "\n".join(f"- {name}" for name in available)
        system = (
            "You map a user request into business capability names. Capability "
            "names use lowercase dot-separated format, e.g. order.read, "
            "refund.policy.check. You must ONLY pick capabilities from the "
            "provided available set. If the request needs a capability that is "
            "NOT in the available set, list it under missing_capability_hints "
            "instead. Respond ONLY with a single JSON object, no prose, having "
            "exactly these keys: required (array of strings), optional (array "
            "of strings), missing_capability_hints (array of strings), "
            "confidence (number between 0 and 1), reasoning (short string)."
        )
        user = (
            "Available capabilities:\n"
            + (available_block if available_block else "(none)")
            + "\n\nUser request:\n"
            + query
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
            raise CapabilityResolutionError(
                f"Ollama HTTP {exc.code} for model {self._model!r}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise CapabilityResolutionError(
                f"Ollama request failed for model {self._model!r}: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise CapabilityResolutionError(
                f"Ollama returned non-JSON response: {exc}"
            ) from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise CapabilityResolutionError(
                "Ollama response missing choices[0].message.content"
            ) from exc
        if not isinstance(content, str):
            raise CapabilityResolutionError("Ollama message content is not a string")
        return content

    def _parse_response(
        self, text: str, available: tuple[str, ...]
    ) -> CapabilityResolution:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[len("json") :].strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CapabilityResolutionError(
                f"Ollama model returned non-JSON capabilities: {exc}"
            ) from exc
        if not isinstance(obj, Mapping):
            raise CapabilityResolutionError(
                "Ollama model returned JSON that is not an object"
            )
        required = self._extract_list(obj, "required")
        optional = self._extract_list(obj, "optional")
        hints = self._extract_list(obj, "missing_capability_hints")
        confidence = self._extract_confidence(obj)
        reasoning_raw = obj.get("reasoning")
        reasoning = (
            reasoning_raw.strip()
            if isinstance(reasoning_raw, str) and reasoning_raw.strip()
            else None
        )

        unavailable = sorted(
            ((set(required) | set(optional)) - set(available))
        )
        if unavailable:
            raise CapabilityResolutionError(
                "Resolver selected unavailable capabilities instead of returning "
                "hints: " + ", ".join(unavailable)
            )
        invalid_hints = sorted(set(hints) & set(available))
        if invalid_hints:
            raise CapabilityResolutionError(
                "Missing capability hints are already available: "
                + ", ".join(invalid_hints)
            )
        try:
            return CapabilityResolution(
                required=required,
                optional=optional,
                missing_capability_hints=hints,
                confidence=confidence,
                reasoning=reasoning,
            )
        except InvalidCapabilityError as exc:
            raise CapabilityResolutionError(
                f"Ollama model produced an invalid capability: {exc}"
            ) from exc

    @staticmethod
    def _extract_list(obj: Mapping[str, Any], key: str) -> tuple[str, ...]:
        raw = obj.get(key, [])
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise CapabilityResolutionError(
                f"Ollama model field {key!r} must be a JSON array"
            )
        items = [item for item in raw if isinstance(item, str) and item.strip()]
        lowered = {item.lower() for item in items}
        if len(lowered) != len(items):
            raise CapabilityResolutionError(
                f"Ollama model field {key!r} contains duplicates case-insensitively"
            )
        try:
            return tuple(
                sorted(validate_capability_name(item.strip()) for item in items)
            )
        except InvalidCapabilityError as exc:
            raise CapabilityResolutionError(
                f"Ollama model field {key!r} contains an invalid capability"
            ) from exc

    @staticmethod
    def _extract_confidence(obj: Mapping[str, Any]) -> float:
        raw = obj.get("confidence", 1.0)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise CapabilityResolutionError(
                "Ollama model field 'confidence' must be a number between 0 and 1"
            )
        value = float(raw)
        if not 0.0 <= value <= 1.0:
            raise CapabilityResolutionError(
                f"Ollama model confidence {value} is outside [0, 1]"
            )
        return value

    @staticmethod
    def _normalize_capabilities(
        available_capabilities: AbstractSet[str],
    ) -> tuple[str, ...]:
        try:
            return tuple(
                sorted(validate_capability_name(item) for item in available_capabilities)
            )
        except InvalidCapabilityError as exc:
            raise CapabilityResolutionError(
                f"available_capabilities contains an invalid capability: {exc}"
            ) from exc