from __future__ import annotations

import time
from datetime import datetime

import httpx

from radar.collectors.common import RetryPolicy, parse_datetime, send_with_retry
from radar.models import PaperCandidate
from radar.text import normalize_arxiv_id, normalize_doi


class SemanticScholarCollector:
    name = "semantic_scholar"
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
    fields = (
        "paperId,corpusId,externalIds,url,title,abstract,venue,publicationVenue,"
        "publicationDate,authors,citationCount,openAccessPdf"
    )

    def __init__(
        self,
        api_key: str | None,
        user_agent: str,
        min_interval_seconds: float = 1.0,
        max_attempts: int = 4,
        backoff_base_seconds: float = 5.0,
        max_backoff_seconds: float = 60.0,
    ):
        headers = {"User-Agent": user_agent}
        if api_key:
            headers["x-api-key"] = api_key
        self.client = httpx.Client(timeout=30, headers=headers)
        self.min_interval_seconds = min_interval_seconds
        self.retry = RetryPolicy(max_attempts, backoff_base_seconds, max_backoff_seconds)
        self._last_request_at = 0.0

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

    def _mark_request(self) -> None:
        self._last_request_at = time.monotonic()

    def _get_with_retry(self, params: dict[str, str]) -> httpx.Response:
        return send_with_retry(
            lambda: self.client.get(self.endpoint, params=params),
            self.retry,
            before=self._respect_rate_limit,
            after=self._mark_request,
        )

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        response = self._get_with_retry(
            {
                "query": query.replace("-", " "),
                "publicationDateOrYear": f"{since.date().isoformat()}:",
                "sort": "publicationDate:desc",
                "fields": self.fields,
            }
        )
        results: list[PaperCandidate] = []
        # The bulk endpoint returns a server-sized batch and does not honor the
        # relevance endpoint's `limit` parameter. It is sorted newest-first above.
        for paper in response.json().get("data", [])[:limit]:
            published = parse_datetime(paper.get("publicationDate"))
            if published and published < since:
                continue
            external_ids = paper.get("externalIds") or {}
            arxiv_id = normalize_arxiv_id(external_ids.get("ArXiv"))
            doi = normalize_doi(external_ids.get("DOI"))
            oa_pdf = paper.get("openAccessPdf") or {}
            venue_data = paper.get("publicationVenue") or {}
            paper_id = str(paper.get("paperId") or paper.get("corpusId") or "")
            results.append(
                PaperCandidate(
                    source=self.name,
                    source_id=paper_id,
                    doi=doi,
                    arxiv_id=arxiv_id,
                    title=paper.get("title") or "",
                    abstract=paper.get("abstract"),
                    authors=[author.get("name", "") for author in paper.get("authors") or []],
                    published_at=published,
                    venue=venue_data.get("name") or paper.get("venue"),
                    url=paper.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}",
                    pdf_url=oa_pdf.get("url"),
                    citation_count=paper.get("citationCount"),
                    external_ids={str(k).lower(): str(v) for k, v in external_ids.items() if v},
                    raw=paper,
                )
            )
        return results
