from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from radar.collectors.common import content_value, parse_datetime
from radar.models import PaperCandidate
from radar.text import normalize_arxiv_id, normalize_doi


class OpenReviewCollector:
    name = "openreview"
    endpoint = "https://api2.openreview.net/notes/search"

    def __init__(self, user_agent: str, token: str | None = None):
        headers = {"User-Agent": user_agent}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(timeout=30, headers=headers)

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        response = self.client.get(
            self.endpoint,
            params={
                "query": query,
                "source": "forum",
                "sort": "tmdate:desc",
                "limit": min(limit, 1000),
            },
        )
        response.raise_for_status()
        results: list[PaperCandidate] = []
        for note in response.json().get("notes", []):
            updated = parse_datetime(note.get("tmdate") or note.get("mdate"))
            published = parse_datetime(
                note.get("pdate") or note.get("cdate") or note.get("tcdate")
            )
            if (published or updated) and (published or updated) < since:
                continue
            content: dict[str, Any] = note.get("content") or {}
            note_id = str(note.get("forum") or note.get("id") or "")
            venue = content_value(content, "venue") or content_value(content, "venueid")
            arxiv_id = normalize_arxiv_id(content_value(content, "arxiv_id"))
            doi = normalize_doi(content_value(content, "doi"))
            pdf_value = content_value(content, "pdf")
            results.append(
                PaperCandidate(
                    source=self.name,
                    source_id=note_id,
                    doi=doi,
                    arxiv_id=arxiv_id,
                    title=content_value(content, "title", "") or "",
                    abstract=content_value(content, "abstract"),
                    authors=list(content_value(content, "authors", []) or []),
                    published_at=published,
                    updated_at=updated,
                    venue=venue,
                    url=f"https://openreview.net/forum?id={note_id}",
                    pdf_url=self._absolute_pdf(pdf_value, note_id),
                    code_url=content_value(content, "code"),
                    external_ids={"openreview": note_id},
                    raw=note,
                )
            )
        return results

    @staticmethod
    def _absolute_pdf(value: Any, note_id: str) -> str:
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, str) and value.startswith("/"):
            return f"https://openreview.net{value}"
        return f"https://openreview.net/pdf?id={note_id}"
