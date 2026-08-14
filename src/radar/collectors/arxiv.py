from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from urllib.parse import quote_plus

import httpx

from radar.collectors.common import RetryPolicy, net_terms, route_to_query, send_with_retry
from radar.models import PaperCandidate
from radar.text import normalize_arxiv_id

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"

# arXiv's boolean parser ANDs whatever clauses it is handed, so one clause per
# word ("UAV AND edge AND computing AND reinforcement AND learning AND
# offloading") matches 45 papers in the whole archive and none inside a 48-hour
# window. The collector instead casts a single date-bounded OR net over the topic
# anchors and lets `radar.scoring` decide what is actually relevant.
MAX_FEED_RESULTS = 200
DATE_FORMAT = "%Y%m%d%H%M"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def _term_clause(term: str) -> str:
    """Bare tokens go through as-is; anything else has to be quoted as a phrase."""
    term = term.strip()
    if not term:
        return ""
    return f"all:{term}" if term.isalnum() else f'all:"{term}"'


def _net_expression(terms: Iterable[str]) -> str:
    clauses: list[str] = []
    seen: set[str] = set()
    for term in terms:
        clause = _term_clause(term)
        key = clause.lower()
        if clause and key not in seen:
            seen.add(key)
            clauses.append(clause)
    return " OR ".join(clauses)


def _date_range(since: datetime, until: datetime) -> str:
    start = since.astimezone(UTC).strftime(DATE_FORMAT)
    end = until.astimezone(UTC).strftime(DATE_FORMAT)
    return f"submittedDate:[{start} TO {end}]"


def search_expression(
    anchors: Sequence[str],
    queries: Sequence[str],
    since: datetime,
    until: datetime | None = None,
) -> str:
    """The `search_query` sent to arXiv.

    The individual queries are not sent; they only route the results afterwards.
    Kept module-level so the dashboard can preview it without a client.
    """
    return (
        f"({_net_expression(net_terms(anchors, list(queries)))})"
        f" AND {_date_range(since, until or datetime.now(UTC))}"
    )


class ArxivCollector:
    name = "arxiv"
    endpoint = "https://export.arxiv.org/api/query"

    def __init__(
        self,
        user_agent: str,
        anchors: Sequence[str] | None = None,
        min_interval_seconds: float = 3.1,
        max_attempts: int = 5,
        backoff_base_seconds: float = 20.0,
        max_backoff_seconds: float = 180.0,
    ):
        self.client = httpx.Client(
            timeout=60,
            headers={"User-Agent": user_agent, "Accept": "application/atom+xml"},
        )
        self.anchors = list(anchors or [])
        self.min_interval_seconds = min_interval_seconds
        self.retry = RetryPolicy(max_attempts, backoff_base_seconds, max_backoff_seconds)
        self._last_request_at = 0.0

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

    def _mark_request(self) -> None:
        self._last_request_at = time.monotonic()

    def _get_with_retry(self, url: str) -> httpx.Response:
        return send_with_retry(
            lambda: self.client.get(url),
            self.retry,
            before=self._respect_rate_limit,
            after=self._mark_request,
        )

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
        expression = search_expression(self.anchors, queries, since)
        url = (
            f"{self.endpoint}?search_query={quote_plus(expression)}"
            f"&start=0&max_results={min(limit * len(queries), MAX_FEED_RESULTS)}"
            "&sortBy=submittedDate&sortOrder=descending"
        )
        response = self._get_with_retry(url)

        results: dict[str, list[PaperCandidate]] = {query: [] for query in queries}
        for candidate in self._parse(response.text, since):
            results[route_to_query(candidate, queries)].append(candidate)
        return results

    def _parse(self, payload: str, since: datetime) -> list[PaperCandidate]:
        root = ET.fromstring(payload)
        candidates: list[PaperCandidate] = []
        for entry in root.findall(f"{ATOM}entry"):
            published = _parse_datetime(entry.findtext(f"{ATOM}published"))
            updated = _parse_datetime(entry.findtext(f"{ATOM}updated"))
            # The window is enforced server-side; this only guards a feed that
            # ignored it. Keyed on the newest date so a revision inside the window
            # keeps a paper whose v1 predates it.
            newest = max([date for date in (published, updated) if date], default=None)
            if newest and newest < since:
                continue
            source_url = entry.findtext(f"{ATOM}id") or ""
            arxiv_id = normalize_arxiv_id(source_url)
            links = {
                link.attrib.get("title") or link.attrib.get("rel", ""): link.attrib.get("href", "")
                for link in entry.findall(f"{ATOM}link")
            }
            candidates.append(
                PaperCandidate(
                    source=self.name,
                    source_id=arxiv_id or source_url,
                    arxiv_id=arxiv_id,
                    doi=entry.findtext(f"{ARXIV}doi"),
                    title=" ".join((entry.findtext(f"{ATOM}title") or "").split()),
                    abstract=" ".join((entry.findtext(f"{ATOM}summary") or "").split()),
                    authors=[
                        author.findtext(f"{ATOM}name") or ""
                        for author in entry.findall(f"{ATOM}author")
                    ],
                    published_at=published,
                    updated_at=updated,
                    venue=entry.findtext(f"{ARXIV}journal_ref"),
                    url=source_url,
                    pdf_url=links.get("pdf"),
                    external_ids={"arxiv": arxiv_id or ""},
                )
            )
        return candidates
