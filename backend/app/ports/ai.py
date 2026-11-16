from dataclasses import dataclass
from typing import Any, Protocol


class AIUnavailable(Exception):
    """The provider cannot answer (disabled, network, refusal, quota). Triggers the no-LLM fallback."""


@dataclass(frozen=True)
class AIRequest:
    task: str  # e.g. "explanation", "matching", "mapping_suggestion"
    system: str
    prompt: str
    schema: dict[str, Any]  # JSON schema the output must match
    max_tokens: int = 4096


@dataclass(frozen=True)
class AIRawResult:
    text: str  # raw model text, expected to be JSON
    model: str
    provider: str


class AIProvider(Protocol):
    name: str

    def complete_json(self, request: AIRequest) -> AIRawResult:
        """Return raw JSON text for the request. Raise ``AIUnavailable`` if it cannot."""
        ...
