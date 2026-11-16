from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Notification:
    tenant_id: str
    recipients: tuple[str, ...]  # user ids
    subject: str
    body: str
    link: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


class Notifier(Protocol):
    def send(self, notification: Notification) -> None: ...
