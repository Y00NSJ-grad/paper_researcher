from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

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


IEEE_MIN_INTERVAL_SECONDS = 0.11
IEEE_DAILY_CALL_LIMIT = 200


class IeeeQuotaExceeded(RuntimeError):
    """Raised before an HTTP request when the persisted daily quota is exhausted."""


class IeeeApiGuard:
    """Cross-process request spacing and UTC daily quota backed by SQLite."""

    def __init__(
        self,
        db_path: Path,
        min_interval_seconds: float = IEEE_MIN_INTERVAL_SECONDS,
        daily_call_limit: int = IEEE_DAILY_CALL_LIMIT,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.db_path = db_path
        self.min_interval_seconds = min_interval_seconds
        self.daily_call_limit = daily_call_limit
        self.clock = clock
        self.sleeper = sleeper
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_rate_limits (
                    source TEXT PRIMARY KEY,
                    quota_day TEXT NOT NULL,
                    call_count INTEGER NOT NULL CHECK(call_count >= 0),
                    last_request_at REAL
                )
                """
            )

    def reserve_call(self) -> int:
        """Reserve one call atomically and return today's persisted call count."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT quota_day, call_count, last_request_at
                FROM api_rate_limits WHERE source = ?
                """,
                ("ieee_xplore",),
            ).fetchone()
            now = self.clock()
            quota_day = datetime.fromtimestamp(now, tz=UTC).date().isoformat()
            call_count = int(row[1]) if row and row[0] == quota_day else 0
            if call_count >= self.daily_call_limit:
                raise IeeeQuotaExceeded(
                    f"IEEE Xplore daily API quota exhausted "
                    f"({call_count}/{self.daily_call_limit} calls on {quota_day} UTC)"
                )

            last_request_at = float(row[2]) if row and row[2] is not None else None
            if last_request_at is not None:
                elapsed = max(0.0, now - last_request_at)
                wait_seconds = self.min_interval_seconds - elapsed
                if wait_seconds > 0:
                    self.sleeper(wait_seconds)
                    now = self.clock()

            call_count += 1
            connection.execute(
                """
                INSERT INTO api_rate_limits(source, quota_day, call_count, last_request_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    quota_day = excluded.quota_day,
                    call_count = excluded.call_count,
                    last_request_at = excluded.last_request_at
                """,
                ("ieee_xplore", quota_day, call_count, now),
            )
            connection.commit()
            return call_count
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()


class IeeeXploreCollector:
    name = "ieee_xplore"
    endpoint = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

    def __init__(
        self,
        api_key: str,
        user_agent: str,
        quota_db_path: Path,
        journals: dict[str, str] | None = None,
    ):
        if not api_key:
            raise ValueError("IEEE_XPLORE_API_KEY is required")
        self.api_key = api_key
        self.journals = journals or IEEE_JOURNALS
        self.api_guard = IeeeApiGuard(quota_db_path)
        self.client = httpx.Client(timeout=30, headers={"User-Agent": user_agent})

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        per_journal = max(1, min(200, (limit + len(self.journals) - 1) // len(self.journals)))
        results: list[PaperCandidate] = []
        seen: set[str] = set()
        for short_name, publication_title in self.journals.items():
            self.api_guard.reserve_call()
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
