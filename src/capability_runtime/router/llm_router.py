"""A real LLM-backed :class:`LayerRouter` powered by a local Ollama server.

Step 12 implementation: it calls Ollama's OpenAI-compatible endpoint once per
layer to decide which available tools to run (or to finish), strictly scoped to
the reachable tools the runtime exposes. It only decides ``execute`` vs
``finish`` and which tools; it never judges the final business outcome
(phase3 §63).

Configuration mirrors the Step 8 resolver and is read from environment
variables (optionally from a ``.env`` file), except model/temperature/explore
which come from the injected :class:`RouterConfig`:

    LLM_BASE_URL          default http://localhost:11434
    LLM_TIMEOUT_SECONDS   default 60

Every decision is validated against the layer-contract (available tools, max
tools, duplicates) and records the router model + prompt version + exploration
mode via exposed properties so a Trial can bind them (phase3 §128).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..core.env import env_float, env_string
from ..core.errors import RoutingError
from .models import (
    RoutingAction,
    RouterConfig,
    RoutingContext,
    RoutingDecision,
    validate_decision,
)
from .prompts import build_router_prompt

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 60.0


class LLMRouter:
    """Pick tools for one layer via a local Ollama model."""

    def __init__(
        self,
        *,
        router_config: RouterConfig,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        dotenv_path: str | Path | None = None,
        _http: Any = None,
    ) -> None:
        self._config = router_config
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
    def router_config(self) -> RouterConfig:
        return self._config

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def prompt_version(self) -> str:
        return self._config.prompt_version

    @property
    def exploration_mode(self) -> str:
        return self._config.exploration_mode

    @property
    def base_url(self) -> str:
        return self._base_url

    async def route(self, context: RoutingContext) -> RoutingDecision:
        available_names = tuple(summary.name for summary in context.available_tools)
        if not available_names:
            return RoutingDecision(action=RoutingAction.FINISH, reason="no tools available")

        prompt = build_router_prompt(
            query=context.query,
            layer=context.current_layer,
            available_tools=context.available_tools,
            state_summary=context.state_summary,
            previous_layers=tuple(layer.layer for layer in context.previous_layers),
            max_tools_per_layer=self._config.max_tools_per_layer,
            exploration_mode=self._config.exploration_mode,
        )
        payload = {
            "model": self._config.model,
            "temperature": self._config.temperature,
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }
        response_text = await asyncio.to_thread(self._complete, payload)
        return self._parse_decision(response_text, available_names)

    def _complete(self, payload: dict[str, Any]) -> str:
        if self._http is not None:
            return self._http(payload)
        return self._chat(payload)

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
            raise RoutingError(
                f"Ollama HTTP {exc.code} for model {self._config.model!r}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RoutingError(
                f"Ollama request failed for model {self._config.model!r}: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise RoutingError(f"Ollama returned non-JSON response: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RoutingError(
                "Ollama response missing choices[0].message.content"
            ) from exc
        if not isinstance(content, str):
            raise RoutingError("Ollama message content is not a string")
        return content

    def _parse_decision(
        self, text: str, available_names: tuple[str, ...]
    ) -> RoutingDecision:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[len("json") :].strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RoutingError(
                f"Ollama router returned non-JSON decision: {exc}"
            ) from exc
        if not isinstance(obj, Mapping):
            raise RoutingError("Ollama router returned JSON that is not an object")

        action_raw = obj.get("action")
        if action_raw not in (RoutingAction.EXECUTE.value, RoutingAction.FINISH.value):
            raise RoutingError(f"Ollama router returned invalid action: {action_raw!r}")

        reason_raw = obj.get("reason")
        reason = (
            reason_raw.strip() if isinstance(reason_raw, str) and reason_raw.strip() else None
        )

        if action_raw == RoutingAction.FINISH.value:
            return RoutingDecision(action=RoutingAction.FINISH, reason=reason)

        tools_raw = obj.get("selected_tools")
        if not isinstance(tools_raw, Sequence) or isinstance(tools_raw, (str, bytes)):
            raise RoutingError(
                "Ollama router field 'selected_tools' must be a JSON array"
            )
        # de-duplicate while preserving order so a noisy model is tolerated
        selected = tuple(
            dict.fromkeys(
                tool for tool in tools_raw if isinstance(tool, str) and tool.strip()
            )
        )
        decision = RoutingDecision(
            action=RoutingAction.EXECUTE, selected_tools=selected, reason=reason
        )
        validate_decision(decision, available_names, self._config.max_tools_per_layer)
        return decision