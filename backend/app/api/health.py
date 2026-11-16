"""Liveness and readiness probes (outside the /api/rationalisation prefix)."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness: the process is up")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness: dependencies (database, Redis) respond")
def readyz(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}
    for name, check in request.app.state.readiness_checks.items():
        try:
            check()
            checks[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - any failure means not ready
            checks[name] = f"error: {type(exc).__name__}"
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
