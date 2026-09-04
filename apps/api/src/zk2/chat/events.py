"""The event a chat turn streams to the client.

Its own module so pipeline nodes can emit events without importing the chat
orchestration that consumes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class StreamEvent:
    kind: str  # conversation | sources | token | citations | done | error
    payload: dict[str, Any]
