from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx

from radar.collectors.common import RetryPolicy, send_with_retry
from radar.models import PaperCandidate
from radar.text import normalize_arxiv_id, normalize_doi


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _abstract_from_index(index: dict[str, list[int]] | None) -> str | None:
    if not index:
        return None
    positions: list[tuple[int, str]] = []
    for word, indexes in index.items():
        positions.extend((position, word) for position in indexes)
    return " ".join(word for _, word in sorted(positions))


class OpenAlexCollector:
    name = "openalex"
    endpoint = "https://api.openalex.org/works"

    def __init__(
        self,
        api_key: str | None,
        email: str | None,
        user_agent: str,
        # Without a `mailto` this client sits in OpenAlex's anonymous pool, which
        # throttles hard; one request a second keeps it out of trouble and costs
        # only a few seconds across a run's queries.
        min_interval_seconds: float = 1.0,
        max_attempts: int = 4,
        backoff_base_seconds: float = 5.0,
        max_backoff_seconds: float = 60.0,
    ):
        self.api_key = api_key
        self.email = email
        self.client = httpx.Client(timeout=30, headers={"User-Agent": user_agent})
        self.min_interval_seconds = min_interval_seconds
        self.retry = RetryPolicy(max_attempts, backoff_base_seconds, max_backoff_seconds)
        self._last_request_at = 0.0

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

    def _mark_request(self) -> None:
        self._last_request_at = time.monotonic()

    def _get_with_retry(self, params: dict[str, str | int]) -> httpx.Response:
        return send_with_retry(
            lambda: self.client.get(self.endpoint, params=params),
            self.retry,
            before=self._respect_rate_limit,
            after=self._mark_request,
        )

    @staticmethod
    def search_params(query: str, since: datetime, limit: int = 25) -> dict[str, str | int]:
        """The request this collector sends. Shared with the dashboard preview."""
        return {
            "search": query,
            "filter": f"from_publication_date:{since.date().isoformat()},has_abstract:true",
            "sort": "publication_date:desc",
            "per-page": min(limit, 100),
            "select": (
                "id,doi,title,display_name,publication_date,primary_location,authorships,"
                "abstract_inverted_index,cited_by_count,ids,updated_date"
            ),
        }

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        params = self.search_params(query, since, limit)
        if self.api_key:
            params["api_key"] = self.api_key
        if self.email:
            params["mailto"] = self.email
        response = self._get_with_retry(params)
        results: list[PaperCandidate] = []
        for work in response.json().get("results", []):
            ids = work.get("ids") or {}
            location = work.get("primary_location") or {}
            source = location.get("source") or {}
            authorships = work.get("authorships") or []
            authors = [item.get("author", {}).get("display_name", "") for item in authorships]
            affiliations = sorted(
                {
                    institution.get("display_name", "")
                    for item in authorships
                    for institution in item.get("institutions", [])
                    if institution.get("display_name")
                }
            )
            arxiv_id = normalize_arxiv_id(ids.get("arxiv"))
            doi = normalize_doi(work.get("doi"))
            best_url = location.get("landing_page_url") or work.get("id")
            results.append(
                PaperCandidate(
                    source=self.name,
                    source_id=work.get("id", ""),
                    doi=doi,
                    arxiv_id=arxiv_id,
                    title=work.get("display_name") or work.get("title") or "",
                    abstract=_abstract_from_index(work.get("abstract_inverted_index")),
                    authors=authors,
                    affiliations=affiliations,
                    published_at=_parse_date(work.get("publication_date")),
                    updated_at=_parse_date((work.get("updated_date") or "")[:10]),
                    venue=source.get("display_name"),
                    url=best_url,
                    pdf_url=location.get("pdf_url"),
                    citation_count=work.get("cited_by_count"),
                    external_ids={key: str(value) for key, value in ids.items() if value},
                )
            )
        return results
