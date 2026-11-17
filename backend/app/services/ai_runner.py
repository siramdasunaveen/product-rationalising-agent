"""Governed AI task execution (CLAUDE.md rule 11).

Output must parse as JSON, match the task's JSON schema and pass a citation check (every cited
evidence/lineage id must exist). Otherwise the errors are sent back to the model (max
``max_retries`` retries); after that, or if AI is unavailable, the deterministic fallback is used.
"""

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from jsonschema import Draft202012Validator

from app.ports.ai import AIProvider, AIRequest, AIUnavailable

log = logging.getLogger("pra.ai")

CitationCheck = Callable[[dict[str, Any]], list[str]]


@dataclass(frozen=True)
class AITaskResult:
    output: dict[str, Any]
    source: Literal["llm", "fallback"]
    attempts: int
    model: str | None = None
    errors: list[str] = field(default_factory=list)  # rejection reasons from failed attempts
    deployment: str | None = None  # registry ref of the deployment that answered (runtime routing)
    needs_human_review: bool = False  # failure treatment HUMAN_REVIEW and the template was used


def schema_errors(schema: dict[str, Any], output: Any) -> list[str]:
    validator = Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(output), key=lambda e: list(e.absolute_path))
    ]


def _collect(value: Any, keys: frozenset[str]) -> Iterable[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            if k in keys:
                items = v if isinstance(v, list) else [v]
                yield from (str(i) for i in items)
            else:
                yield from _collect(v, keys)
    elif isinstance(value, list):
        for v in value:
            yield from _collect(v, keys)


def citations_must_exist(valid_ids: Iterable[str],
                         keys: Iterable[str] = ("citations", "evidence_ids", "lineage_ids")) -> CitationCheck:
    """Every value under any of ``keys`` (at any depth) must be a known evidence/lineage id."""
    known = frozenset(valid_ids)
    key_set = frozenset(keys)

    def check(output: dict[str, Any]) -> list[str]:
        unknown = sorted({c for c in _collect(output, key_set) if c not in known})
        return [f"cited id does not exist: {c}" for c in unknown]

    return check


def _try_provider(provider: AIProvider, request: AIRequest, citation_check: CitationCheck | None,
                  max_retries: int, errors: list[str]) -> tuple[dict[str, Any] | None, int, str | None]:
    """Up to 1 + ``max_retries`` attempts on one provider. Returns (accepted output or None, attempts, model)."""
    current = request
    attempts = 0
    for _ in range(max_retries + 1):
        attempts += 1
        try:
            raw = provider.complete_json(current)
        except AIUnavailable as exc:
            errors.append(f"unavailable: {exc}")
            return None, attempts, None

        try:
            output = json.loads(raw.text)
        except json.JSONDecodeError as exc:
            problems = [f"output is not valid JSON: {exc.msg}"]
        else:
            problems = schema_errors(request.schema, output)
            if not problems and citation_check is not None:
                problems = citation_check(output)
            if not problems:
                return output, attempts, raw.model

        errors.extend(problems)
        log.info("AI task %s attempt %d rejected: %s", request.task, attempts, problems)
        current = replace(
            request,
            prompt=(
                f"{request.prompt}\n\nYour previous answer was rejected for these reasons:\n- "
                + "\n- ".join(problems)
                + "\nReturn a corrected answer. Cite only ids that appear in the evidence provided."
            ),
        )
    return None, attempts, None


def _fallback_output(request: AIRequest, fallback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    output = fallback()
    fallback_problems = schema_errors(request.schema, output)
    if fallback_problems:
        raise ValueError(f"Fallback output for task {request.task!r} violates its schema: {fallback_problems}")
    return output


def run_ai_chain(
    providers: list[tuple[str, AIProvider]],
    request: AIRequest,
    *,
    fallback: Callable[[], dict[str, Any]],
    citation_check: CitationCheck | None = None,
    max_retries: int = 2,
) -> AITaskResult:
    """Try each (label, provider) in order; the first accepted output wins, else the deterministic fallback."""
    errors: list[str] = []
    attempts = 0
    for label, provider in providers:
        output, n, model = _try_provider(provider, request, citation_check, max_retries, errors)
        attempts += n
        if output is not None:
            return AITaskResult(output=output, source="llm", attempts=attempts, model=model, errors=errors,
                                deployment=label)
        errors.append(f"{label}: gave no acceptable answer")
    return AITaskResult(output=_fallback_output(request, fallback), source="fallback", attempts=attempts,
                        errors=errors)


def run_ai_task(
    provider: AIProvider,
    request: AIRequest,
    *,
    fallback: Callable[[], dict[str, Any]],
    citation_check: CitationCheck | None = None,
    max_retries: int = 2,
) -> AITaskResult:
    """One provider with retries, then the deterministic fallback."""
    errors: list[str] = []
    output, attempts, model = _try_provider(provider, request, citation_check, max_retries, errors)
    if output is not None:
        return AITaskResult(output=output, source="llm", attempts=attempts, model=model, errors=errors)
    return AITaskResult(output=_fallback_output(request, fallback), source="fallback", attempts=attempts,
                        errors=errors)
