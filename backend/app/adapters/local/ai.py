"""AI providers: Anthropic (structured outputs) and "none" (no-LLM mode)."""

import anthropic

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
