from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from radar.collectors.common import content_value, parse_datetime
from radar.models import PaperCandidate
from radar.text import normalize_arxiv_id, normalize_doi


def _author_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for author in value:
        if isinstance(author, str):
            name = author
        elif isinstance(author, dict):
            name = author.get("fullname") or author.get("name") or author.get("username") or ""
        else:
            name = str(author)
        if name:
            names.append(name)
    return names


class OpenReviewCollector:
    name = "openreview"
    endpoint = "https://api2.openreview.net/notes/search"

    def __init__(self, user_agent: str, token: str | None = None):
        headers = {"User-Agent": user_agent}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(timeout=30, headers=headers)

    @staticmethod
    def search_params(query: str, since: datetime, limit: int = 25) -> dict[str, str | int]:
        """The request this collector sends. Shared with the dashboard preview."""
        return {
            "query": query,
            "source": "forum",
            "sort": "tmdate:desc",
            "limit": min(limit, 1000),
        }

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        response = self.client.get(self.endpoint, params=self.search_params(query, since, limit))
        response.raise_for_status()
        results: list[PaperCandidate] = []
        for note in response.json().get("notes", []):
            updated = parse_datetime(note.get("tmdate") or note.get("mdate"))
            published = parse_datetime(note.get("pdate") or note.get("cdate") or note.get("tcdate"))
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
                    authors=_author_names(content_value(content, "authors", [])),
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
