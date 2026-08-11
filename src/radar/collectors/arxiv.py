from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import quote_plus

import httpx

from radar.models import PaperCandidate
from radar.text import normalize_arxiv_id, normalize_title

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def _arxiv_expression(query: str) -> str:
    tokens = re.findall(r'"[^"]+"|\S+', query)
    clauses = []
    for token in tokens:
        token = token.strip()
        if token.startswith('"') and token.endswith('"'):
            clauses.append(f'all:"{token[1:-1]}"')
        else:
            clauses.append(f"all:{token}")
    return " AND ".join(clauses)


def _matches_query(candidate: PaperCandidate, query: str) -> bool:
    haystack = normalize_title(f"{candidate.title} {candidate.abstract or ''}")
    terms = re.findall(r'"([^"]+)"|([^\s]+)', query)
    return all(normalize_title(phrase or token) in haystack for phrase, token in terms)


class ArxivCollector:
    name = "arxiv"
    endpoint = "https://export.arxiv.org/api/query"

    def __init__(
        self,
        user_agent: str,
        min_interval_seconds: float = 3.1,
        max_attempts: int = 4,
        backoff_base_seconds: float = 15.0,
    ):
        self.client = httpx.Client(
            timeout=30,
            headers={"User-Agent": user_agent, "Accept": "application/atom+xml"},
        )
        self.min_interval_seconds = min_interval_seconds
        self.max_attempts = max_attempts
        self.backoff_base_seconds = backoff_base_seconds
        self._last_request_at = 0.0

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

    def _get_with_retry(self, url: str) -> httpx.Response:
        for attempt in range(self.max_attempts):
            self._respect_rate_limit()
            try:
                response = self.client.get(url)
            except httpx.TimeoutException:
                self._last_request_at = time.monotonic()
                if attempt + 1 >= self.max_attempts:
                    raise
                time.sleep(self.backoff_base_seconds * (2**attempt))
                continue

            self._last_request_at = time.monotonic()
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable:
                response.raise_for_status()
                return response
            if attempt + 1 >= self.max_attempts:
                response.raise_for_status()

            retry_after = response.headers.get("Retry-After")
            try:
                retry_delay = float(retry_after) if retry_after else 0.0
            except ValueError:
                retry_delay = 0.0
            backoff = self.backoff_base_seconds * (2**attempt)
            time.sleep(max(retry_delay, backoff))

        raise RuntimeError("arXiv retry loop exhausted")

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        return self.search_many([query], since, limit)[query]

    def search_many(
        self,
        queries: list[str],
        since: datetime,
        limit: int = 25,
    ) -> dict[str, list[PaperCandidate]]:
        if not queries:
            return {}
        expression = " OR ".join(f"({_arxiv_expression(query)})" for query in queries)
        combined_limit = min(limit * len(queries), 200)
        url = (
            f"{self.endpoint}?search_query={quote_plus(expression)}"
            f"&start=0&max_results={combined_limit}&sortBy=submittedDate&sortOrder=descending"
        )
        response = self._get_with_retry(url)
        root = ET.fromstring(response.text)
        candidates: list[PaperCandidate] = []
        for entry in root.findall(f"{ATOM}entry"):
            published = _parse_datetime(entry.findtext(f"{ATOM}published"))
            if published and published < since:
                continue
            source_url = entry.findtext(f"{ATOM}id") or ""
            arxiv_id = normalize_arxiv_id(source_url)
            links = {
                link.attrib.get("title") or link.attrib.get("rel", ""): link.attrib.get("href", "")
                for link in entry.findall(f"{ATOM}link")
            }
            doi = entry.findtext(f"{ARXIV}doi")
            candidates.append(
                PaperCandidate(
                    source=self.name,
                    source_id=arxiv_id or source_url,
                    arxiv_id=arxiv_id,
                    doi=doi,
                    title=" ".join((entry.findtext(f"{ATOM}title") or "").split()),
                    abstract=" ".join((entry.findtext(f"{ATOM}summary") or "").split()),
                    authors=[
                        author.findtext(f"{ATOM}name") or ""
                        for author in entry.findall(f"{ATOM}author")
                    ],
                    published_at=published,
                    updated_at=_parse_datetime(entry.findtext(f"{ATOM}updated")),
                    venue=entry.findtext(f"{ARXIV}journal_ref"),
                    url=source_url,
                    pdf_url=links.get("pdf"),
                    external_ids={"arxiv": arxiv_id or ""},
                )
            )
        results = {query: [] for query in queries}
        for candidate in candidates:
            for query in queries:
                if len(results[query]) < limit and _matches_query(candidate, query):
                    results[query].append(candidate)
        return results
