"""Seed content for the scoring and policy registries (step 9).

Values follow docs/SPEC_NOTES.md (candidate guides and Scoring & AI guide): Opportunity Score weights
35/20/20/15/10, recommend >= 80, review >= 60; detector weights and thresholds; priority index weights, bands and
overrides. Weights are percentages, detector thresholds per mille (900 = 0.90).
"""

from app.domain.runs import Objective
from app.seed.telco import CANONICAL_KEY

ALL_OBJECTIVES = [o.value for o in Objective]
ALL_DIMENSIONS = ["COMMERCIAL", "TECHNICAL", "EXECUTION"]

DETECTOR_PACK_KEY = "TELCO-RATIONALISATION-DETECTORS"
SCORING_PROFILE_KEY = "TELCO-PORTFOLIO-SCORING"
PRIORITY_POLICY_KEY = "TELCO-PRIORITY"
UK_POLICY_KEY = "UK-CONSUMER-PORTFOLIO"
DEFAULT_POLICY_KEY = "TELCO-DEFAULT-POLICY"


def _applies(markets=("GLOBAL",), families=("ALL",), priority=10) -> dict:
    return {"markets": list(markets), "productFamilies": list(families), "objectives": ALL_OBJECTIVES,
            "dimensions": ALL_DIMENSIONS, "canonicalSchema": CANONICAL_KEY, "priority": priority}


TELCO_DETECTORS = {
    "description": "Telco rationalisation detectors: duplicate, overlap, legacy, complexity, operational burden.",
    "applicability": _applies(),
    "detectors": [
        {"type": "DUPLICATE", "version": "1.0", "enabled": True,
         "factors": {"service": 30, "cpq": 20, "eligibility": 15, "pricingStructure": 10, "technical": 15,
                     "lifecycle": 10},
         "thresholds": {"strong": 900, "probable": 800}},
        {"type": "OVERLAP", "version": "1.0", "enabled": True,
         "factors": {"capability": 40, "segment": 25, "channelMarket": 15, "useCase": 15, "substitution": 5},
         "thresholds": {"match": 700}},
        {"type": "LEGACY", "version": "1.0", "enabled": True, "rules": {"sunsetHorizonDays": 365}},
        {"type": "COMPLEXITY", "version": "1.0", "enabled": True,
         "factors": {"variant": 25, "rule": 25, "bundleDepth": 15, "attributeEntropy": 15, "manualException": 20},
         "thresholds": {"match": 750}, "hardLimits": {"variants": 50, "rules": 200}},
        {"type": "OPERATIONAL_BURDEN", "version": "1.0", "enabled": True,
         "factors": {"fallout": 30, "manualTouch": 20, "incident": 20, "provisioningException": 15, "handling": 15},
         "thresholds": {"match": 700}},
    ],
    "primaryTypePrecedence": ["DUPLICATE", "OVERLAP", "LEGACY", "COMPLEXITY", "OPERATIONAL_BURDEN"],
    "normalisation": "FAMILY_P95",
    "grouping": {"scope": "PRODUCT_SET"},
}

TELCO_SCORING = {
    "description": "Telco Portfolio: transparent Opportunity Score weights. Detector signal 35%, commercial value "
                   "20%, technical feasibility 20%, operational benefit 15%, strategic alignment 10%.",
    "applicability": _applies(),
    "weights": {"detectorSignal": 35, "commercialValue": 20, "technicalFeasibility": 20, "operationalBenefit": 15,
                "strategicAlignment": 10},
    "thresholds": {"recommended": 80, "review": 60},
    "belowReviewTreatment": "NO_RECOMMENDATION",
    "mandatoryDimensions": ALL_DIMENSIONS,
}

TELCO_PRIORITY = {
    "description": "Priority index (separate from the Opportunity Score) with policy overrides.",
    "applicability": _applies(),
    "weights": {"businessImpact": 25, "exposure": 20, "operationalBurden": 15, "legacyUrgency": 15,
                "regulatoryRisk": 10, "dependencyCriticality": 10, "strategicAlignment": 5},
    "bands": [{"band": "LOW", "min": 0}, {"band": "MEDIUM", "min": 40}, {"band": "HIGH", "min": 65},
              {"band": "CRITICAL", "min": 85}],
    "overrides": [
        {"kind": "REGULATORY", "minimumBand": "HIGH"},
        {"kind": "SUNSET", "minimumBand": "CRITICAL", "withinDays": 90},
        {"kind": "DEPENDENCY", "minimumBand": "HIGH"},
        {"kind": "CUSTOMER_IMPACT", "minimumBand": "HIGH", "minSubscriptions": 50000},
    ],
    "sunsetUrgency": [{"withinDays": 90, "value": 100}, {"withinDays": 180, "value": 80},
                      {"withinDays": 365, "value": 60}],
}

_APPROVAL_ROUTES = [
    {"dispositions": ["RETAIN", "INVESTIGATE"], "scopes": ["product"], "reason": "Low-impact outcome"},
    {"dispositions": ["SIMPLIFY"], "scopes": ["product", "commercial"], "reason": "Offer change"},
    {"dispositions": ["CONSOLIDATE", "STANDARDISE", "MIGRATE", "RETIRE"],
     "scopes": ["product", "commercial", "architecture"], "reason": "Customer and technical impact"},
]
_GATES = {
    "minimumEvidenceCoverage": 80, "missingEvidenceTreatment": "NOT_ASSESSED", "criticalConflictTreatment": "BLOCK",
    "staleEvidenceTreatment": "REQUIRE_REFRESH", "technicalEquivalenceGate": "REQUIRED_FOR_CONSOLIDATE",
    "executionReadinessGate": "REQUIRED_FOR_DECISION", "humanReviewRequired": True,
}

UK_CONSUMER_POLICY = {
    "description": "UK Consumer Portfolio policy: evidence gates, guardrails, approval routing, decision audit "
                   "kept 7 years.",
    "applicability": _applies(markets=("UK_CONSUMER",), priority=50),
    "gates": {**_GATES, "minimumEvidenceCoverage": 85},
    "overridable": ["recommendedThreshold", "reviewThreshold", "belowReviewTreatment", "staleEvidenceTreatment",
                    "minimumEvidenceCoverage"],
    "approvalRoutes": _APPROVAL_ROUTES,
    "allowSelfApproval": False,
    "auditRetentionYears": 7,
}

DEFAULT_POLICY = {
    "description": "Telco default policy for markets without a specific policy pack.",
    "applicability": _applies(priority=10),
    "gates": _GATES,
    "overridable": ["recommendedThreshold", "reviewThreshold", "belowReviewTreatment", "staleEvidenceTreatment"],
    "approvalRoutes": _APPROVAL_ROUTES,
    "allowSelfApproval": False,
    "auditRetentionYears": 7,
}

CONFIGURATION_ITEMS: list[tuple[str, str, str, dict]] = [
    ("DETECTOR_PACK", DETECTOR_PACK_KEY, "Telco rationalisation detectors", TELCO_DETECTORS),
    ("SCORING_PROFILE", SCORING_PROFILE_KEY, "Telco Portfolio scoring", TELCO_SCORING),
    ("PRIORITY_POLICY", PRIORITY_POLICY_KEY, "Telco priority policy", TELCO_PRIORITY),
    ("POLICY_PACK", UK_POLICY_KEY, "UK Consumer Portfolio policy", UK_CONSUMER_POLICY),
    ("POLICY_PACK", DEFAULT_POLICY_KEY, "Telco default policy", DEFAULT_POLICY),
]
