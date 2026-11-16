"""Base class for service errors. The API turns them into
``{"detail": {"code", "message", "errors": [{field, message}]}}`` with ``status_code``."""

from app.domain.common import FieldError


class ServiceError(Exception):
    status_code = 400
    code = "error"

    def __init__(self, message: str, errors: list[FieldError] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or []
