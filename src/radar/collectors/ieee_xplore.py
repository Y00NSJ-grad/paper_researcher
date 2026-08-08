from __future__ import annotations

from datetime import UTC, datetime

import httpx

from radar.collectors.common import parse_datetime
from radar.models import PaperCandidate
from radar.text import normalize_doi

IEEE_JOURNALS = {
    "JSAC": "IEEE Journal on Selected Areas in Communications",
    "TMC": "IEEE Transactions on Mobile Computing",
    "TIV": "IEEE Transactions on Intelligent Vehicles",
    "ToN": "IEEE/ACM Transactions on Networking",
    "TVT": "IEEE Transactions on Vehicular Technology",
}


class IeeeXploreCollector:
    name = "ieee_xplore"
    endpoint = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

    def __init__(
        self,
        api_key: str,
        user_agent: str,
        journals: dict[str, str] | None = None,
    ):
        if not api_key:
            raise ValueError("IEEE_XPLORE_API_KEY is required")
        self.api_key = api_key
        self.journals = journals or IEEE_JOURNALS
        self.client = httpx.Client(timeout=30, headers={"User-Agent": user_agent})

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        per_journal = max(1, min(200, (limit + len(self.journals) - 1) // len(self.journals)))
        results: list[PaperCandidate] = []
        seen: set[str] = set()
        for short_name, publication_title in self.journals.items():
            response = self.client.get(
                self.endpoint,
                params={
                    "apikey": self.api_key,
                    "format": "json",
                    "querytext": query,
                    "publication_title": publication_title,
                    "start_record": 1,
                    "max_records": per_journal,
                    "sort_field": "article_number",
                    "sort_order": "desc",
                },
            )
            response.raise_for_status()
            for article in response.json().get("articles", []):
                candidate = self._candidate(article, short_name)
                if candidate.published_at and candidate.published_at < since:
                    continue
                if candidate.source_id in seen:
                    continue
                seen.add(candidate.source_id)
                results.append(candidate)
        results.sort(
            key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return results[:limit]

    def _candidate(self, article: dict, short_name: str) -> PaperCandidate:
        author_rows = (article.get("authors") or {}).get("authors") or []
        article_number = str(article.get("article_number") or "")
        published = parse_datetime(
            article.get("publication_date") or article.get("publication_year")
        )
        html_url = article.get("html_url") or article.get("abstract_url")
        return PaperCandidate(
            source=self.name,
            source_id=article_number or str(article.get("doi") or html_url or ""),
            doi=normalize_doi(article.get("doi")),
            title=article.get("title") or "",
            abstract=article.get("abstract"),
            authors=[row.get("full_name", "") for row in author_rows],
            affiliations=sorted(
                {row.get("affiliation", "") for row in author_rows if row.get("affiliation")}
            ),
            published_at=published,
            venue=article.get("publication_title") or IEEE_JOURNALS.get(short_name),
            url=html_url or f"https://ieeexplore.ieee.org/document/{article_number}",
            pdf_url=article.get("pdf_url"),
            citation_count=article.get("citing_paper_count"),
            external_ids={"ieee": article_number, "ieee_journal": short_name},
            raw=article,
        )
