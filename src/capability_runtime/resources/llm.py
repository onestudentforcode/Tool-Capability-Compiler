"""LLM resource handle: precise tokens/cost for tools with internal LLM calls.

The tool does not report anything — it calls the model through this handle
and metering settles at the tool-call boundary (resource-metering §5).
Ollama-compatible endpoint, stdlib-only, fake ``_http`` injectable for tests
(mirrors :class:`~capability_runtime.router.llm_router.LLMRouter`).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.env import env_float, env_string
from ..core.metrics import TokenUsage
from .metering import current_collector

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    usage: TokenUsage | None
    cost: float | None


class LLMResource:
    """A metered completion client; use it inside a tool's body."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        input_cost_per_1k: float | None = None,
        output_cost_per_1k: float | None = None,
        timeout_seconds: float | None = None,
        dotenv_path: str | Path | None = None,
        _http: Any = None,
    ) -> None:
        from ..core.errors import ResourceHandleError

        if not isinstance(model, str) or not model.strip():
            raise ResourceHandleError("LLMResource needs a non-empty model")
        for name, price in (
            ("input_cost_per_1k", input_cost_per_1k),
            ("output_cost_per_1k", output_cost_per_1k),
        ):
            if price is not None and (isinstance(price, bool) or price < 0):
                raise ResourceHandleError(
                    f"LLMResource {name} must be a non-negative number"
                )
        self._model = model
        self._base_url = (
            env_string("LLM_BASE_URL", DEFAULT_BASE_URL, dotenv_path=dotenv_path)
            if base_url is None
            else base_url
        ).rstrip("/")
        self._input_price = input_cost_per_1k
        self._output_price = output_cost_per_1k
        self._timeout = (
            env_float(
                "LLM_TIMEOUT_SECONDS",
                DEFAULT_TIMEOUT_SECONDS,
                dotenv_path=dotenv_path,
            )
            if timeout_seconds is None
            else timeout_seconds
        )
        self._http = _http

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, prompt: str, *, system: str | None = None) -> LLMResponse:
        """One completion; usage and cost are metered onto the current tool."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self._model,
            "stream": False,
            "messages": messages,
        }
        body = await asyncio.to_thread(self._request, payload)
        from ..router.llm_router import LLMRouter

        text = LLMRouter._extract_content(body)
        usage = LLMRouter._extract_usage(body)
        cost = self._cost(usage)

        collector = current_collector()
        if usage is not None:
            collector.record_tokens(usage)
        if cost is not None:
            collector.record_measured_cost(cost)
        return LLMResponse(text=text, usage=usage, cost=cost)

    def _request(self, payload: dict[str, Any]) -> Any:
        if self._http is not None:
            response = self._http(payload)
            # test fakes may return the inner content string directly
            if isinstance(response, str):
                return {"choices": [{"message": {"content": response}}]}
            return response
        request = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OSError(f"LLM HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OSError(f"LLM request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise OSError(f"LLM returned non-JSON: {exc}") from exc

    def _cost(self, usage: TokenUsage | None) -> float | None:
        if usage is None:
            return None
        if self._input_price is None and self._output_price is None:
            return None
        per_input = self._input_price or 0.0
        per_output = self._output_price or 0.0
        return (
            usage.input_tokens / 1000.0 * per_input
            + usage.output_tokens / 1000.0 * per_output
        )
