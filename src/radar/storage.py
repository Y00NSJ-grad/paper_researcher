from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from radar.models import PaperCandidate, PaperSummary, ScoredPaper
from radar.text import content_hash, normalize_arxiv_id, normalize_doi, normalize_title

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT UNIQUE,
    arxiv_id TEXT UNIQUE,
    normalized_title TEXT NOT NULL,
    first_author TEXT NOT NULL DEFAULT '',
    publication_year INTEGER,
    title TEXT NOT NULL,
    abstract TEXT,
    abstract_hash TEXT,
    authors_json TEXT NOT NULL DEFAULT '[]',
    affiliations_json TEXT NOT NULL DEFAULT '[]',
    published_at TEXT,
    updated_at TEXT,
    venue TEXT,
    primary_url TEXT NOT NULL,
    pdf_url TEXT,
    citation_count INTEGER,
    code_url TEXT,
    tags_json TEXT NOT NULL DEFAULT '{}',
    score REAL NOT NULL DEFAULT 0,
    score_reasons_json TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(normalized_title, first_author, publication_year)
);

CREATE TABLE IF NOT EXISTS paper_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    seen_at TEXT NOT NULL,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    stats_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS run_papers (
    run_id INTEGER NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    query_ids_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(run_id, paper_id)
);

CREATE TABLE IF NOT EXISTS collector_state (
    source TEXT PRIMARY KEY,
    last_success_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
    paper_id INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    abstract_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    value TEXT NOT NULL CHECK(value IN ('keep', 'maybe', 'reject', 'read')),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_first_seen ON papers(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_papers_score ON papers(score DESC);
CREATE INDEX IF NOT EXISTS idx_versions_paper ON paper_versions(paper_id);
CREATE INDEX IF NOT EXISTS idx_feedback_paper ON feedback(paper_id, id DESC);
"""

# Mirrors the CHECK constraint on feedback.value.
FEEDBACK_VALUES = ("keep", "maybe", "reject", "read")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


class PaperStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def start_run(self, kind: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO pipeline_runs(kind, started_at) VALUES (?, ?)",
                (kind, _iso(datetime.now(UTC))),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self, run_id: int, status: str, stats: dict[str, int], error: str | None = None
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_runs
                SET finished_at = ?, status = ?, stats_json = ?, error = ?
                WHERE id = ?
                """,
                (
                    _iso(datetime.now(UTC)),
                    status,
                    json.dumps(stats, ensure_ascii=False),
                    error,
                    run_id,
                ),
            )

    def last_collected_at(self, source: str) -> datetime | None:
        """When this source last completed a fetch without error, if ever."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT last_success_at FROM collector_state WHERE source = ?", (source,)
            ).fetchone()
        return datetime.fromisoformat(row["last_success_at"]) if row else None

    def mark_collected(self, source: str, at: datetime) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO collector_state(source, last_success_at) VALUES (?, ?)
                ON CONFLICT(source) DO UPDATE SET last_success_at = excluded.last_success_at
                """,
                (source, _iso(at)),
            )

    def _find_paper_id(self, connection: sqlite3.Connection, paper: PaperCandidate) -> int | None:
        doi = normalize_doi(paper.doi)
        arxiv_id = normalize_arxiv_id(paper.arxiv_id)
        if doi:
            row = connection.execute("SELECT id FROM papers WHERE doi = ?", (doi,)).fetchone()
            if row:
                return int(row["id"])
        if arxiv_id:
            row = connection.execute(
                "SELECT id FROM papers WHERE arxiv_id = ?", (arxiv_id,)
            ).fetchone()
            if row:
                return int(row["id"])

        normalized = normalize_title(paper.title)
        first_author = normalize_title(paper.authors[0]) if paper.authors else ""
        year = paper.published_at.year if paper.published_at else None
        row = connection.execute(
            """
            SELECT id FROM papers
            WHERE normalized_title = ? AND first_author = ?
              AND publication_year IS ?
            """,
            (normalized, first_author, year),
        ).fetchone()
        return int(row["id"]) if row else None

    def upsert_scored(self, scored: ScoredPaper, run_id: int) -> tuple[int, bool]:
        paper = scored.candidate
        now = _iso(datetime.now(UTC))
        doi = normalize_doi(paper.doi)
        arxiv_id = normalize_arxiv_id(paper.arxiv_id)
        normalized = normalize_title(paper.title)
        first_author = normalize_title(paper.authors[0]) if paper.authors else ""
        year = paper.published_at.year if paper.published_at else None
        abstract_hash = content_hash(paper.abstract) if paper.abstract else None

        with self.connect() as connection:
            paper_id = self._find_paper_id(connection, paper)
            created = paper_id is None
            if created:
                cursor = connection.execute(
                    """
                    INSERT INTO papers(
                        doi, arxiv_id, normalized_title, first_author, publication_year,
                        title, abstract, abstract_hash, authors_json, affiliations_json,
                        published_at, updated_at, venue, primary_url, pdf_url,
                        citation_count, code_url, tags_json, score, score_reasons_json,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doi,
                        arxiv_id,
                        normalized,
                        first_author,
                        year,
                        paper.title,
                        paper.abstract,
                        abstract_hash,
                        json.dumps(paper.authors, ensure_ascii=False),
                        json.dumps(paper.affiliations, ensure_ascii=False),
                        _iso(paper.published_at),
                        _iso(paper.updated_at),
                        paper.venue,
                        paper.url,
                        paper.pdf_url,
                        paper.citation_count,
                        paper.code_url,
                        json.dumps(scored.tags, ensure_ascii=False),
                        scored.score,
                        json.dumps(scored.reasons, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                paper_id = int(cursor.lastrowid)
            else:
                connection.execute(
                    """
                    UPDATE papers SET
                        doi = COALESCE(doi, ?), arxiv_id = COALESCE(arxiv_id, ?),
                        abstract = COALESCE(?, abstract),
                        abstract_hash = COALESCE(?, abstract_hash),
                        venue = COALESCE(?, venue), pdf_url = COALESCE(?, pdf_url),
                        citation_count = MAX(COALESCE(citation_count, 0), COALESCE(?, 0)),
                        code_url = COALESCE(?, code_url), tags_json = ?,
                        score = MAX(score, ?), score_reasons_json = ?, last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        doi,
                        arxiv_id,
                        paper.abstract,
                        abstract_hash,
                        paper.venue,
                        paper.pdf_url,
                        paper.citation_count,
                        paper.code_url,
                        json.dumps(scored.tags, ensure_ascii=False),
                        scored.score,
                        json.dumps(scored.reasons, ensure_ascii=False),
                        now,
                        paper_id,
                    ),
                )

            connection.execute(
                """
                INSERT INTO paper_versions(paper_id, source, source_id, url, raw_json, seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    paper_id = excluded.paper_id, url = excluded.url,
                    raw_json = excluded.raw_json, seen_at = excluded.seen_at
                """,
                (
                    paper_id,
                    paper.source,
                    paper.source_id,
                    paper.url,
                    json.dumps(paper.raw, ensure_ascii=False, default=str),
                    now,
                ),
            )
            existing_run_paper = connection.execute(
                "SELECT query_ids_json FROM run_papers WHERE run_id = ? AND paper_id = ?",
                (run_id, paper_id),
            ).fetchone()
            query_ids = set(paper.query_ids)
            if existing_run_paper:
                query_ids.update(json.loads(existing_run_paper["query_ids_json"]))
            connection.execute(
                """
                INSERT INTO run_papers(run_id, paper_id, query_ids_json)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id, paper_id) DO UPDATE SET query_ids_json = excluded.query_ids_json
                """,
                (run_id, paper_id, json.dumps(sorted(query_ids))),
            )
            return paper_id, created

    def papers_for_run(self, run_id: int, limit: int = 10) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT p.*, s.summary_json, rp.query_ids_json
                FROM run_papers rp
                JOIN papers p ON p.id = rp.paper_id
                LEFT JOIN summaries s ON s.paper_id = p.id
                WHERE rp.run_id = ?
                ORDER BY p.score DESC, p.published_at DESC
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_papers(self, days: int, limit: int = 200) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT p.*, s.summary_json
                FROM papers p LEFT JOIN summaries s ON s.paper_id = p.id
                WHERE p.first_seen_at >= ?
                ORDER BY p.score DESC, p.first_seen_at DESC
                LIMIT ?
                """,
                (_iso(since), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def collection_health(self, days: int) -> dict:
        since = datetime.now(UTC) - timedelta(days=days)
        with self.connect() as connection:
            runs = connection.execute(
                """
                SELECT stats_json, error FROM pipeline_runs
                WHERE started_at >= ? AND status != 'running'
                """,
                (_iso(since),),
            ).fetchall()
            sources = connection.execute(
                """
                SELECT source, COUNT(DISTINCT paper_id) AS paper_count
                FROM paper_versions WHERE seen_at >= ?
                GROUP BY source ORDER BY source
                """,
                (_iso(since),),
            ).fetchall()

        source_failures: dict[str, int] = {}
        source_errors = 0
        for run in runs:
            try:
                stats = json.loads(run["stats_json"] or "{}")
            except json.JSONDecodeError:
                stats = {}
            source_errors += int(stats.get("source_errors", 0))
            for line in (run["error"] or "").splitlines():
                if " failed" not in line:
                    continue
                source = line.split(" ", 1)[0]
                source_failures[source] = source_failures.get(source, 0) + 1
        return {
            "window_days": days,
            "runs": len(runs),
            "source_errors": source_errors,
            "source_failures": source_failures,
            "source_papers": {row["source"]: row["paper_count"] for row in sources},
        }

    def record_feedback(self, paper_id: int, value: str, source: str = "dashboard") -> dict:
        """Append a verdict. History is kept; the newest row is the current one."""
        if value not in FEEDBACK_VALUES:
            raise ValueError(f"Unknown feedback value: {value!r}")
        now = _iso(datetime.now(UTC))
        with self.connect() as connection:
            paper = connection.execute(
                "SELECT 1 FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if paper is None:
                raise LookupError(f"No paper with id {paper_id}")
            connection.execute(
                "INSERT INTO feedback(paper_id, value, source, created_at) VALUES (?, ?, ?, ?)",
                (paper_id, value, source, now),
            )
        return {"paper_id": paper_id, "value": value, "source": source, "created_at": now}

    def clear_feedback(self, paper_id: int) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM feedback WHERE paper_id = ?", (paper_id,)
            )
            return int(cursor.rowcount)

    def feedback_history(self, paper_id: int) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT value, source, created_at FROM feedback
                WHERE paper_id = ? ORDER BY id DESC
                """,
                (paper_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary_is_current(self, paper_id: int, abstract: str) -> bool:
        digest = content_hash(abstract)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM summaries WHERE paper_id = ? AND abstract_hash = ?",
                (paper_id, digest),
            ).fetchone()
        return row is not None

    def save_summary(
        self, paper_id: int, abstract: str, model: str, summary: PaperSummary
    ) -> None:
        payload = {
            "paper": summary.paper,
            "problem": summary.problem,
            "method": summary.method,
            "benchmark": summary.benchmark,
            "why_it_matters": summary.why_it_matters,
            "can_i_use_it": summary.can_i_use_it,
        }
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO summaries(paper_id, abstract_hash, model, summary_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    abstract_hash = excluded.abstract_hash, model = excluded.model,
                    summary_json = excluded.summary_json, created_at = excluded.created_at
                """,
                (
                    paper_id,
                    content_hash(abstract),
                    model,
                    json.dumps(payload, ensure_ascii=False),
                    _iso(datetime.now(UTC)),
                ),
            )
