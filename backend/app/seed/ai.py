"""Seed content for the AI runtime registry (step 8): two approved deployments and the tenant's runtime profile.

PLATFORM-DEFAULT is "whatever model the platform is configured with": in standalone mode PRA_AI_PROVIDER
(none = no-LLM, or anthropic), in AI Studio the studio's AI service. Customers add their own approved models
and endpoints as AI deployments in the registry and switch strategy on the AI Runtime screen.
"""

from app.domain.ai_runtime import Task

AI_RUNTIME_KEY = "TELCO-AI-RUNTIME"
PLATFORM_DEPLOYMENT = "PLATFORM-DEFAULT"
ANTHROPIC_DEPLOYMENT = "ANTHROPIC-CLAUDE-OPUS"

AI_DEPLOYMENTS: list[tuple[str, str, dict]] = [
    (PLATFORM_DEPLOYMENT, "Platform-managed model", {
        "description": "The platform's configured model (standalone: PRA_AI_PROVIDER; AI Studio: the studio AI "
                       "service). With PRA_AI_PROVIDER=none every task uses its deterministic template.",
        "provider": "platform", "displayName": "Platform-managed model", "hosting": "PLATFORM", "region": "GLOBAL",
        "dataClassifications": ["METADATA", "AGGREGATES"], "capabilities": {"structuredOutput": True},
        "qualityTier": 4, "costTier": 3, "latencyMsP95": 30000, "maxOutputTokens": 4096,
    }),
    (ANTHROPIC_DEPLOYMENT, "Claude Opus 5.5 (customer registry)", {
        "description": "Claude via the Anthropic API with JSON-schema structured output. The API key is read from "
                       "the AI secret 'anthropic-api-key' (AI Runtime screen), else PRA_ANTHROPIC_API_KEY.",
        "provider": "anthropic", "model": "claude-opus-5-5", "displayName": "Claude Opus 5.5",
        "hosting": "CUSTOMER_REGISTRY", "secretRef": "anthropic-api-key", "region": "US",
        "dataClassifications": ["METADATA", "AGGREGATES"], "capabilities": {"structuredOutput": True},
        "qualityTier": 5, "costTier": 4, "latencyMsP95": 60000, "maxOutputTokens": 8192,
    }),
]

TELCO_AI_RUNTIME = {
    "description": "Default AI runtime: the platform-managed model for every task, deterministic templates as "
                   "fallback. AI only explains and drafts; it never scores, validates, counts or approves.",
    "strategy": "PLATFORM_MANAGED",
    "routingPolicy": "TASK_SPECIFIC",
    "failureTreatment": "DETERMINISTIC_FALLBACK",
    "aiConfidenceThreshold": 80,
    "maxRetries": 2,
    "constraints": {"dataClassification": "AGGREGATES", "allowedRegions": [], "allowToolUse": False},
    "routes": [{"task": t.value, "mode": "LLM", "primary": PLATFORM_DEPLOYMENT} for t in Task],
}
