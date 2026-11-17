"""Assess and Recommend stages of the analysis workflow (filled in by BUILD_PLAN step 11)."""

from typing import Any


def assess(session, run, analysis, manifest, features, snapshot) -> dict[str, Any]:
    return {"note": "Scoring arrives in step 11"}


def recommend(session, run, analysis, manifest, features, snapshot) -> dict[str, Any]:
    return {"note": "Recommendations arrive in step 11"}


def result_digest(session, analysis) -> list:
    return []
