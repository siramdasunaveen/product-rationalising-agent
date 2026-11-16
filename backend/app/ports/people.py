from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Person:
    user_id: str  # same id as the JWT ``sub``
    name: str
    email: str | None
    roles: tuple[str, ...]


class PeopleDirectory(Protocol):
    """Who can be picked as owner/assignee. Locally backed by ``app_user``; in AI Studio by its directory."""

    def search(self, tenant_id: str, *, roles: tuple[str, ...] = (), query: str = "",
               limit: int = 50, session: Any = None) -> list[Person]: ...

    def get(self, tenant_id: str, user_id: str, *, session: Any = None) -> Person | None: ...
