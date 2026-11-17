"""AI providers: Anthropic (structured outputs) and "none" (no-LLM mode)."""

import json

import anthropic
import httpx

from app.ports.ai import AIRawResult, AIRequest, AIUnavailable


class NoLLMProvider:
    """No-LLM mode: always unavailable, so every AI task uses its template/rule-based fallback."""

    name = "none"

    def complete_json(self, request: AIRequest) -> AIRawResult:
        raise AIUnavailable("AI is disabled (no-LLM mode)")


class AnthropicProvider:
    """Claude via the Anthropic SDK, constrained to the task's JSON schema.

    Server-side refusal fallback is enabled (``fallbacks="default"``): if the model declines on a
    safety policy, the API re-runs the request on a fallback model in the same call. If the whole
    chain still refuses, we raise ``AIUnavailable`` and the caller uses its no-LLM fallback.
    """

    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, effort: str = "medium",
                 client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic(api_key=api_key or None, max_retries=2)
        self._model = model
        self._effort = effort

    def complete_json(self, request: AIRequest) -> AIRawResult:
        try:
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.prompt}],
                output_config={
                    "effort": self._effort,
                    "format": {"type": "json_schema", "schema": request.schema},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIConnectionError as exc:
            raise AIUnavailable(f"Cannot reach Anthropic: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise AIUnavailable("Anthropic rate limit") from exc
        except anthropic.APIStatusError as exc:
            raise AIUnavailable(f"Anthropic error {exc.status_code}") from exc

        if response.stop_reason == "refusal":
            raise AIUnavailable("Model declined the request")
        # On max_tokens the JSON is truncated; the runner rejects it as invalid and retries.
        text = next((b.text for b in response.content if b.type == "text"), "")
        return AIRawResult(text=text, model=response.model, provider=self.name)


class HttpContractProvider:
    """A customer-hosted model endpoint implementing the governed AI contract (BYO_ENDPOINT strategy).

    Request: ``POST {endpoint}`` with JSON ``{"task", "system", "prompt", "schema", "max_tokens", "model"}`` and
    ``Authorization: Bearer <secret>`` when a secret is configured. Response: JSON ``{"output": {...}}`` (preferred)
    or ``{"text": "<json>"}``, optionally with ``"model"``. Any error, timeout or non-2xx status raises
    ``AIUnavailable`` so the runtime moves to the next deployment or the deterministic template.
    """

    name = "byo_http"

    def __init__(self, endpoint: str, secret: str | None, model: str | None = None, *, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._endpoint = endpoint
        self._model = model
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        self._client = httpx.Client(timeout=timeout, headers=headers, transport=transport)

    def complete_json(self, request: AIRequest) -> AIRawResult:
        body = {"task": request.task, "system": request.system, "prompt": request.prompt, "schema": request.schema,
                "max_tokens": request.max_tokens, "model": self._model}
        try:
            r = self._client.post(self._endpoint, json=body)
        except httpx.HTTPError as exc:
            raise AIUnavailable(f"Cannot reach the endpoint: {type(exc).__name__}") from exc
        if r.status_code >= 300:
            raise AIUnavailable(f"Endpoint returned HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as exc:
            raise AIUnavailable("Endpoint did not return JSON") from exc
        if isinstance(data, dict) and isinstance(data.get("output"), dict):
            text = json.dumps(data["output"])
        elif isinstance(data, dict) and isinstance(data.get("text"), str):
            text = data["text"]
        else:
            raise AIUnavailable("Endpoint response has neither 'output' nor 'text'")
        model = data.get("model") if isinstance(data.get("model"), str) else (self._model or "customer-endpoint")
        return AIRawResult(text=text, model=model, provider=self.name)
