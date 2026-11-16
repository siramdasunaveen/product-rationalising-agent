"""Small types shared by the domain modules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldError:
    """One problem with one input field. ``field`` is a dotted/indexed path, e.g. ``entities[0].name``."""

    field: str
    message: str
