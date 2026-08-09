from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from radar.collectors.common import parse_datetime
from radar.models import PaperCandidate
from radar.text import normalize_arxiv_id


def _query_terms(query: str) -> list[str]:
    matches = re.findall(r'"([^"]+)"|([A-Za-z0-9]+)', query)
    return [
        first or second
        for first, second in matches
        if len(first or second) > 1 and (first or second).lower() not in {"and", "or", "not"}
    ]


class HuggingFaceCollector:
    name = "huggingface"
    endpoint = "https://huggingface.co/api/daily_papers"

    def __init__(
        self,
        user_agent: str,
        token: str | None = None,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
    ):
        headers = {"User-Agent": user_agent}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(timeout=30, headers=headers)
        self.max_attempts = max_attempts
        self.backoff_base_seconds = backoff_base_seconds
        self._feed_cache: dict[str, list[dict[str, Any]]] = {}
        self._failed_dates: set[str] = set()

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        terms = [term.lower() for term in _query_terms(query)]
        results: list[PaperCandidate] = []
        cursor = since.astimezone(UTC).date()
        today = datetime.now(UTC).date()
        while cursor <= today:
            for row in self._daily_feed(cursor.isoformat()):
                paper = row.get("paper") or row
                haystack = " ".join(
                    str(paper.get(key) or "")
                    for key in ("title", "summary", "ai_summary", "ai_keywords")
                ).lower()
                if terms and not all(term in haystack for term in terms):
                    continue
                candidate = self._candidate(row)
                if candidate.published_at and candidate.published_at < since:
                    continue
                results.append(candidate)
            cursor += timedelta(days=1)
        results.sort(key=lambda item: item.published_at or since, reverse=True)
        return results[:limit]

    def _daily_feed(self, date_key: str) -> list[dict[str, Any]]:
        if date_key in self._feed_cache:
            return self._feed_cache[date_key]
        if date_key in self._failed_dates:
            return []

        for attempt in range(self.max_attempts):
            try:
                response = self.client.get(
                    self.endpoint,
                    params={"date": date_key, "limit": 100, "sort": "publishedAt"},
                )
            except httpx.TimeoutException:
                if attempt + 1 >= self.max_attempts:
                    self._failed_dates.add(date_key)
                    raise
            else:
                retryable = response.status_code in {400, 429} or response.status_code >= 500
                if not retryable:
                    response.raise_for_status()
                    data = response.json()
                    items = data if isinstance(data, list) else data.get("items", [])
                    self._feed_cache[date_key] = items
                    return items
                if attempt + 1 >= self.max_attempts:
                    self._failed_dates.add(date_key)
                    response.raise_for_status()

            time.sleep(self.backoff_base_seconds * (2**attempt))

        return []

    def _candidate(self, row: dict[str, Any]) -> PaperCandidate:
        paper = row.get("paper") or row
        arxiv_id = normalize_arxiv_id(paper.get("id") or paper.get("arxiv_id"))
        authors = paper.get("authors") or []
        author_names = [
            author.get("name", "") if isinstance(author, dict) else str(author)
            for author in authors
        ]
        published = parse_datetime(row.get("publishedAt") or paper.get("publishedAt"))
        code_url = paper.get("githubRepo") or paper.get("github")
        return PaperCandidate(
            source=self.name,
            source_id=arxiv_id or str(paper.get("id") or ""),
            arxiv_id=arxiv_id,
            title=paper.get("title") or "",
            abstract=paper.get("summary") or paper.get("ai_summary"),
            authors=author_names,
            published_at=published,
            venue="Hugging Face Daily Papers",
            url=f"https://huggingface.co/papers/{arxiv_id}",
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None,
            code_url=code_url,
            external_ids={"arxiv": arxiv_id or "", "huggingface": arxiv_id or ""},
            raw=row,
        )
