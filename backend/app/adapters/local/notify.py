import logging

from app.ports.notify import Notification

log = logging.getLogger("pra.notify")


class LogNotifier:
    """Writes notifications to the log. Studio will route them to its own channels."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        self.sent.append(notification)
        log.info("notify tenant=%s to=%s subject=%r link=%s", notification.tenant_id,
                 ",".join(notification.recipients), notification.subject, notification.link)
