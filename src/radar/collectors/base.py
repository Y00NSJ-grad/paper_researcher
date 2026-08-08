from __future__ import annotations

from datetime import datetime
from typing import Protocol

from radar.models import PaperCandidate


class Collector(Protocol):
    name: str

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]: ...

