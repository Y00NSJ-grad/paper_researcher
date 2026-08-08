from __future__ import annotations

from datetime import UTC, datetime

import httpx

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

    def __init__(self, api_key: str | None, email: str | None, user_agent: str):
        self.api_key = api_key
        self.email = email
        self.client = httpx.Client(timeout=30, headers={"User-Agent": user_agent})

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        params: dict[str, str | int] = {
            "search": query,
            "filter": f"from_publication_date:{since.date().isoformat()},has_abstract:true",
            "sort": "publication_date:desc",
            "per-page": min(limit, 100),
            "select": (
                "id,doi,title,display_name,publication_date,primary_location,authorships,"
                "abstract_inverted_index,cited_by_count,ids,updated_date"
            ),
        }
        if self.api_key:
            params["api_key"] = self.api_key
        if self.email:
            params["mailto"] = self.email
        response = self.client.get(self.endpoint, params=params)
        response.raise_for_status()
        results: list[PaperCandidate] = []
        for work in response.json().get("results", []):
            ids = work.get("ids") or {}
            location = work.get("primary_location") or {}
            source = location.get("source") or {}
            authorships = work.get("authorships") or []
            authors = [
                item.get("author", {}).get("display_name", "") for item in authorships
            ]
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

