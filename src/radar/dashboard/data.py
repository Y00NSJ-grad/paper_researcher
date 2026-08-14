"""Aggregations behind the dashboard API.

Every function here opens the database through `PaperStore.connect` and returns
plain JSON-serialisable structures. The only writes are the feedback verdicts in
`record_feedback`/`clear_feedback`, which touch the `feedback` table and nothing
else — collected papers, runs and scores are never modified from the dashboard.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from radar.collectors.common import anchors_from_config
from radar.collectors.plan import query_plan
from radar.config import QUERY_GROUPS, Settings, config_token, load_config, write_queries
from radar.reports import KST, trend_counts
from radar.scoring import explain_score, is_survey
from radar.storage import FEEDBACK_VALUES, PaperStore

AXES = ("methods", "domains", "tasks")
# Feedback is append-only, so "current" means the newest row for that paper.
LATEST_FEEDBACK = (
    "SELECT f.value FROM feedback f WHERE f.paper_id = p.id ORDER BY f.id DESC LIMIT 1"
)
# Papers without the sort key sink to the bottom: SQLite orders NULLs last on
# DESC, and the second key keeps the ordering stable within ties.
SORTS = {
    "score": "p.score DESC, p.first_seen_at DESC",
    "first_seen": "p.first_seen_at DESC, p.score DESC",
    "published": "p.published_at DESC, p.score DESC",
}
SCORE_BUCKETS = ((0, 20), (20, 40), (40, 60), (60, 80), (80, 101))
REPORT_PERIODS = ("daily", "weekly", "monthly")


class ConfigChanged(RuntimeError):
    """keywords.yml moved under us; the caller must reload before saving."""


def _json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _kst_date(value: str | None) -> str | None:
    """Bucket an ISO timestamp into the local calendar date the reports use."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(KST).strftime("%Y-%m-%d")


def _tag_list(tags_json: str | None) -> list[tuple[str, str]]:
    tags = _json(tags_json, {})
    return [(axis, tag) for axis in AXES for tag in tags.get(axis, [])]


def report_period(kind: str) -> str:
    """Group a report directory under its schedule.

    The pipeline writes `daily`, `weekly`, `weekly-trends` and `monthly-trends`,
    so the trend maps belong to the schedule that produced them.
    """
    for period in REPORT_PERIODS:
        if kind == period or kind.startswith(f"{period}-"):
            return period
    return "other"


def parse_query_id(query_id: str) -> dict[str, str]:
    """`query_ids` are stored as ``kind:index:query text``."""
    kind, _, remainder = query_id.partition(":")
    index, _, text = remainder.partition(":")
    if not text:
        return {"kind": kind, "index": "", "text": remainder or query_id}
    return {"kind": kind, "index": index, "text": text}


class DashboardData:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = PaperStore(settings.db_path)
        self._config_mtime: float | None = None
        self._config: dict[str, Any] = {}

    @property
    def config(self) -> dict[str, Any]:
        """Reload keywords.yml whenever it changes, so edits show up on refresh."""
        path = self.settings.config_path
        mtime = path.stat().st_mtime if path.exists() else None
        if mtime != self._config_mtime:
            self._config = load_config(path) if mtime is not None else {}
            self._config_mtime = mtime
        return self._config

    # ------------------------------------------------------------------ overview

    def overview(self, days: int = 30) -> dict[str, Any]:
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self.store.connect() as connection:
            counts = {
                name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("papers", "paper_versions", "pipeline_runs", "summaries", "feedback")
            }
            recent_papers = connection.execute(
                "SELECT COUNT(*) FROM papers WHERE first_seen_at >= ?", (since,)
            ).fetchone()[0]
            score_row = connection.execute(
                "SELECT AVG(score) AS mean, MAX(score) AS best FROM papers"
            ).fetchone()
            sources = [
                {"source": row["source"], "versions": row["versions"], "papers": row["papers"]}
                for row in connection.execute(
                    """
                    SELECT source, COUNT(*) AS versions, COUNT(DISTINCT paper_id) AS papers
                    FROM paper_versions GROUP BY source ORDER BY papers DESC
                    """
                )
            ]
            runs = [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "stats": _json(row["stats_json"], {}),
                    "error": row["error"],
                    "papers": row["papers"],
                }
                for row in connection.execute(
                    """
                    SELECT r.*, (
                        SELECT COUNT(*) FROM run_papers rp WHERE rp.run_id = r.id
                    ) AS papers
                    FROM pipeline_runs r ORDER BY r.started_at DESC LIMIT 30
                    """
                )
            ]
            seen_rows = connection.execute(
                "SELECT first_seen_at, score, tags_json FROM papers WHERE first_seen_at >= ?",
                (since,),
            ).fetchall()
            all_scores = [row["score"] for row in connection.execute("SELECT score FROM papers")]
            verdicts = dict(
                connection.execute(
                    """
                    SELECT value, COUNT(*) FROM (
                        SELECT f.paper_id, f.value FROM feedback f
                        JOIN (SELECT paper_id, MAX(id) AS id FROM feedback GROUP BY paper_id)
                          latest ON latest.id = f.id
                    ) GROUP BY value
                    """
                )
            )

        per_day: Counter[str] = Counter()
        for row in seen_rows:
            date = _kst_date(row["first_seen_at"])
            if date:
                per_day[date] += 1
        today = datetime.now(KST).date()
        timeline = []
        for offset in range(days - 1, -1, -1):
            date = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            timeline.append({"date": date, "papers": per_day.get(date, 0)})

        histogram = [
            {
                "label": f"{low}–{min(high, 100)}",
                "low": low,
                "papers": sum(1 for score in all_scores if low <= score < high),
            }
            for low, high in SCORE_BUCKETS
        ]

        run_stats = [
            {
                "id": run["id"],
                "kind": run["kind"],
                "status": run["status"],
                "date": _kst_date(run["started_at"]),
                "collected": run["stats"].get("collected", 0),
                "relevant": run["stats"].get("relevant", 0),
                "new": run["stats"].get("new", 0),
                "source_errors": run["stats"].get("source_errors", 0),
            }
            for run in reversed(runs)
        ]

        judged = sum(verdicts.values())
        feedback = [
            {"value": value, "papers": verdicts.get(value, 0)} for value in FEEDBACK_VALUES
        ]
        feedback.append({"value": "none", "papers": counts["papers"] - judged})

        return {
            "counts": counts,
            "recent_papers": recent_papers,
            "days": days,
            "feedback": feedback,
            "judged_papers": judged,
            "mean_score": round(score_row["mean"] or 0, 1),
            "best_score": score_row["best"] or 0,
            "sources": sources,
            "runs": runs,
            "run_stats": run_stats,
            "timeline": timeline,
            "histogram": histogram,
            "db_path": str(self.settings.db_path),
        }

    # -------------------------------------------------------------------- papers

    def papers(
        self,
        search: str = "",
        source: str = "",
        tag: str = "",
        min_score: float = 0.0,
        days: int = 0,
        sort: str = "score",
        feedback: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["p.score >= ?"]
        params: list[Any] = [min_score]
        if search:
            where.append("(p.title LIKE ? OR p.abstract LIKE ? OR p.venue LIKE ?)")
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern])
        if source:
            where.append(
                "EXISTS (SELECT 1 FROM paper_versions v "
                "WHERE v.paper_id = p.id AND v.source = ?)"
            )
            params.append(source)
        if tag:
            where.append("p.tags_json LIKE ?")
            params.append(f'%"{tag}"%')
        if days > 0:
            where.append("p.first_seen_at >= ?")
            params.append((datetime.now(UTC) - timedelta(days=days)).isoformat())
        if feedback == "none":
            where.append("NOT EXISTS (SELECT 1 FROM feedback f WHERE f.paper_id = p.id)")
        elif feedback in FEEDBACK_VALUES:
            where.append(f"({LATEST_FEEDBACK}) = ?")
            params.append(feedback)
        clause = " AND ".join(where)
        order = SORTS.get(sort, SORTS["score"])

        with self.store.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM papers p WHERE {clause}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT p.id, p.title, p.venue, p.score, p.tags_json, p.published_at,
                       p.first_seen_at, p.citation_count, p.primary_url, p.pdf_url,
                       p.code_url, p.abstract,
                       (SELECT GROUP_CONCAT(DISTINCT v.source) FROM paper_versions v
                        WHERE v.paper_id = p.id) AS sources,
                       (SELECT COUNT(*) FROM summaries s WHERE s.paper_id = p.id) AS has_summary,
                       ({LATEST_FEEDBACK}) AS feedback
                FROM papers p WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        items = []
        for row in rows:
            tags = _json(row["tags_json"], {})
            abstract = (row["abstract"] or "").strip()
            items.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "venue": row["venue"],
                    "score": row["score"],
                    "tags": {axis: tags.get(axis, []) for axis in AXES},
                    "published_at": row["published_at"],
                    "first_seen_at": row["first_seen_at"],
                    "citation_count": row["citation_count"],
                    "url": row["primary_url"],
                    "pdf_url": row["pdf_url"],
                    "code_url": row["code_url"],
                    "sources": sorted((row["sources"] or "").split(",")) if row["sources"] else [],
                    "has_summary": bool(row["has_summary"]),
                    # Same predicate the scorer uses, so the badge cannot
                    # disagree with the survey bonus in the score breakdown.
                    "is_survey": is_survey(row["title"] or ""),
                    "feedback": row["feedback"],
                    "excerpt": abstract[:240] + ("…" if len(abstract) > 240 else ""),
                }
            )
        return {"total": total, "limit": limit, "offset": offset, "items": items}

    def filters(self) -> dict[str, Any]:
        with self.store.connect() as connection:
            sources = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT source FROM paper_versions ORDER BY source"
                )
            ]
            rows = connection.execute("SELECT tags_json FROM papers").fetchall()
        counts: Counter[str] = Counter()
        axis_of: dict[str, str] = {}
        for row in rows:
            for axis, tag in _tag_list(row["tags_json"]):
                counts[tag] += 1
                axis_of[tag] = axis
        tags = [
            {"tag": tag, "axis": axis_of[tag], "papers": count}
            for tag, count in counts.most_common()
        ]
        return {"sources": sources, "tags": tags}

    def paper(self, paper_id: int) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if row is None:
                return None
            versions = [
                {
                    "source": version["source"],
                    "source_id": version["source_id"],
                    "url": version["url"],
                    "seen_at": version["seen_at"],
                    "raw": _json(version["raw_json"], {}),
                }
                for version in connection.execute(
                    "SELECT * FROM paper_versions WHERE paper_id = ? ORDER BY seen_at DESC",
                    (paper_id,),
                )
            ]
            run_rows = connection.execute(
                """
                SELECT r.id, r.kind, r.status, r.started_at, rp.query_ids_json
                FROM run_papers rp JOIN pipeline_runs r ON r.id = rp.run_id
                WHERE rp.paper_id = ? ORDER BY r.started_at DESC
                """,
                (paper_id,),
            ).fetchall()
            summary_row = connection.execute(
                "SELECT * FROM summaries WHERE paper_id = ?", (paper_id,)
            ).fetchone()
        feedback = self.store.feedback_history(paper_id)

        runs = []
        matched_queries: dict[str, dict[str, Any]] = {}
        for run in run_rows:
            queries = [parse_query_id(qid) for qid in _json(run["query_ids_json"], [])]
            runs.append(
                {
                    "id": run["id"],
                    "kind": run["kind"],
                    "status": run["status"],
                    "started_at": run["started_at"],
                    "queries": queries,
                }
            )
            for query in queries:
                matched_queries.setdefault(query["text"], {"text": query["text"], "runs": 0})
                matched_queries[query["text"]]["runs"] += 1

        breakdown = explain_score(
            row["title"], row["abstract"], self.config, has_code=bool(row["code_url"])
        )
        stored_score = row["score"]
        return {
            "id": row["id"],
            "title": row["title"],
            "abstract": row["abstract"],
            "authors": _json(row["authors_json"], []),
            "affiliations": _json(row["affiliations_json"], []),
            "venue": row["venue"],
            "doi": row["doi"],
            "arxiv_id": row["arxiv_id"],
            "url": row["primary_url"],
            "pdf_url": row["pdf_url"],
            "code_url": row["code_url"],
            "citation_count": row["citation_count"],
            "published_at": row["published_at"],
            "updated_at": row["updated_at"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "score": stored_score,
            "tags": {axis: _json(row["tags_json"], {}).get(axis, []) for axis in AXES},
            "stored_reasons": _json(row["score_reasons_json"], []),
            "breakdown": {
                "score": breakdown.score,
                "raw_score": breakdown.raw_score,
                "capped": breakdown.capped,
                "relevant": breakdown.relevant,
                "minimum_relevant": breakdown.minimum_relevant,
                "max_score": breakdown.max_score,
                "stored_score": stored_score,
                "matches_stored": abs(breakdown.score - stored_score) < 0.01,
                "matches": [
                    {
                        "axis": match.axis,
                        "tag": match.tag,
                        "weight": match.weight,
                        "terms": match.terms,
                        "title_terms": match.title_terms,
                        "multiplier": match.multiplier,
                        "contribution": round(match.contribution, 2),
                    }
                    for match in breakdown.matches
                ],
                "bonuses": [
                    {"name": bonus.name, "amount": bonus.amount} for bonus in breakdown.bonuses
                ],
            },
            "feedback": feedback[0]["value"] if feedback else None,
            "feedback_history": feedback,
            "versions": versions,
            "runs": runs,
            "queries": sorted(matched_queries.values(), key=lambda item: item["text"]),
            "summary": (
                {
                    "model": summary_row["model"],
                    "created_at": summary_row["created_at"],
                    "fields": _json(summary_row["summary_json"], {}),
                }
                if summary_row
                else None
            ),
        }

    # ------------------------------------------------------------------ feedback

    def record_feedback(self, paper_id: int, value: str) -> dict[str, Any]:
        """Append a verdict. Raises ValueError/LookupError for bad input."""
        self.store.record_feedback(paper_id, value, source="dashboard")
        return self.feedback(paper_id)

    def clear_feedback(self, paper_id: int) -> dict[str, Any]:
        self.store.clear_feedback(paper_id)
        return self.feedback(paper_id)

    def feedback(self, paper_id: int) -> dict[str, Any]:
        history = self.store.feedback_history(paper_id)
        return {
            "paper_id": paper_id,
            "feedback": history[0]["value"] if history else None,
            "feedback_history": history,
        }

    # ------------------------------------------------------------------- queries

    def queries(self) -> dict[str, Any]:
        configured = self.config.get("queries", {}) or {}
        known: dict[str, dict[str, Any]] = {}
        for group, entries in configured.items():
            for index, text in enumerate(entries or []):
                known[text] = {
                    "text": text,
                    "group": group,
                    "index": index,
                    "configured": True,
                    "papers": 0,
                    "runs": 0,
                    "scores": [],
                    "last_seen": None,
                }

        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT rp.query_ids_json, rp.paper_id, p.score, r.started_at, r.id AS run_id
                FROM run_papers rp
                JOIN papers p ON p.id = rp.paper_id
                JOIN pipeline_runs r ON r.id = rp.run_id
                """
            ).fetchall()

        papers_by_query: dict[str, set[int]] = defaultdict(set)
        runs_by_query: dict[str, set[int]] = defaultdict(set)
        for row in rows:
            for query_id in _json(row["query_ids_json"], []):
                parsed = parse_query_id(query_id)
                text = parsed["text"]
                entry = known.setdefault(
                    text,
                    {
                        "text": text,
                        "group": parsed["kind"],
                        "index": None,
                        "configured": False,
                        "papers": 0,
                        "runs": 0,
                        "scores": [],
                        "last_seen": None,
                    },
                )
                papers_by_query[text].add(row["paper_id"])
                runs_by_query[text].add(row["run_id"])
                entry["scores"].append(row["score"])
                started = row["started_at"]
                if started and (entry["last_seen"] is None or started > entry["last_seen"]):
                    entry["last_seen"] = started

        items = []
        for entry in known.values():
            scores = entry.pop("scores")
            entry["papers"] = len(papers_by_query.get(entry["text"], ()))
            entry["runs"] = len(runs_by_query.get(entry["text"], ()))
            entry["mean_score"] = round(sum(scores) / len(scores), 1) if scores else 0
            entry["best_score"] = max(scores) if scores else 0
            items.append(entry)
        items.sort(key=lambda item: (-item["papers"], item["text"]))
        return {
            "items": items,
            "groups": list(configured.keys()),
            "editable": {
                group: list(configured.get(group) or []) for group in QUERY_GROUPS
            },
            "token": config_token(self.settings.config_path),
            "config_path": str(self.settings.config_path),
        }

    def save_queries(self, groups: dict[str, list[str]], token: str) -> dict[str, Any]:
        """Persist query edits to keywords.yml, refusing to overwrite a newer file."""
        current = config_token(self.settings.config_path)
        if token != current:
            raise ConfigChanged(
                "keywords.yml이 대시보드 밖에서 수정되었습니다. "
                "새로고침한 뒤 다시 저장하세요."
            )
        write_queries(self.settings.config_path, groups)
        return self.queries()

    def plan(self, since_hours: int = 48, limit_per_query: int = 25) -> dict[str, Any]:
        """What each source would be asked, for the queries a daily run uses."""
        configured = self.config.get("queries", {}) or {}
        since = datetime.now(UTC) - timedelta(hours=since_hours)
        groups = {
            group: list(configured.get(group) or [])
            for group in QUERY_GROUPS
        }
        plans = {
            group: query_plan(
                self.config,
                queries,
                since,
                limit_per_query=limit_per_query,
                ieee_enabled=bool(
                    self.settings.ieee_xplore_enabled and self.settings.ieee_xplore_api_key
                ),
            )
            for group, queries in groups.items()
        }
        return {
            "since": since.isoformat(),
            "since_hours": since_hours,
            "limit_per_query": limit_per_query,
            "groups": groups,
            "plans": plans,
            "anchors": anchors_from_config(self.config),
            "config_path": str(self.settings.config_path),
        }

    # ------------------------------------------------------------------- scoring

    def scoring(self) -> dict[str, Any]:
        with self.store.connect() as connection:
            rows = connection.execute("SELECT tags_json, score FROM papers").fetchall()

        tag_papers: Counter[str] = Counter()
        tag_scores: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            for _axis, tag in _tag_list(row["tags_json"]):
                tag_papers[tag] += 1
                tag_scores[tag].append(row["score"])

        axes = []
        for axis in AXES:
            entries = []
            for tag, definition in (self.config.get(axis, {}) or {}).items():
                scores = tag_scores.get(tag, [])
                entries.append(
                    {
                        "tag": tag,
                        "weight": float(definition.get("weight", 0)),
                        "terms": list(definition.get("terms", [])),
                        "papers": tag_papers.get(tag, 0),
                        "mean_score": round(sum(scores) / len(scores), 1) if scores else 0,
                    }
                )
            entries.sort(key=lambda item: (-item["weight"], item["tag"]))
            axes.append({"axis": axis, "tags": entries})

        unused = sorted(
            tag for tag in tag_papers if not any(tag in (self.config.get(a, {}) or {}) for a in AXES)
        )
        return {
            "axes": axes,
            "params": self.config.get("scoring", {}) or {},
            "orphan_tags": unused,
            "config_path": str(self.settings.config_path),
        }

    def simulate(self, title: str, abstract: str, has_code: bool = False) -> dict[str, Any]:
        breakdown = explain_score(title, abstract, self.config, has_code=has_code)
        return {
            "score": breakdown.score,
            "raw_score": breakdown.raw_score,
            "capped": breakdown.capped,
            "relevant": breakdown.relevant,
            "minimum_relevant": breakdown.minimum_relevant,
            "max_score": breakdown.max_score,
            "tags": breakdown.tags,
            "matches": [
                {
                    "axis": match.axis,
                    "tag": match.tag,
                    "weight": match.weight,
                    "terms": match.terms,
                    "title_terms": match.title_terms,
                    "multiplier": match.multiplier,
                    "contribution": round(match.contribution, 2),
                }
                for match in breakdown.matches
            ],
            "bonuses": [{"name": b.name, "amount": b.amount} for b in breakdown.bonuses],
        }

    # -------------------------------------------------------------------- trends

    def trends(self, days: int = 30) -> dict[str, Any]:
        rows = self.store.recent_papers(days=days, limit=2000)
        tag_counts, pair_counts = trend_counts(rows)

        axis_of: dict[str, str] = {}
        for axis in AXES:
            for tag in self.config.get(axis, {}) or {}:
                axis_of[tag] = axis

        matrix_counts: dict[tuple[str, str], int] = Counter()
        column_axis: dict[str, str] = {}
        for row in rows:
            tags = _json(row.get("tags_json"), {})
            domains = tags.get("domains", [])
            for domain in domains:
                for method in tags.get("methods", []):
                    matrix_counts[(domain, method)] += 1
                    column_axis[method] = "methods"
                for task in tags.get("tasks", []):
                    matrix_counts[(domain, task)] += 1
                    column_axis[task] = "tasks"

        row_labels = sorted({key[0] for key in matrix_counts})
        column_labels = sorted({key[1] for key in matrix_counts}, key=lambda c: column_axis[c])
        matrix = {
            "rows": row_labels,
            "columns": [{"label": label, "axis": column_axis[label]} for label in column_labels],
            "cells": [
                {"row": row, "column": column, "papers": matrix_counts.get((row, column), 0)}
                for row in row_labels
                for column in column_labels
            ],
            "max": max(matrix_counts.values(), default=0),
        }

        per_day: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            date = _kst_date(row.get("first_seen_at"))
            if not date:
                continue
            tags = _json(row.get("tags_json"), {})
            for tag in set(tags.get("domains", [])):
                per_day[date][tag] += 1
        domain_totals: Counter[str] = Counter()
        for counter in per_day.values():
            domain_totals.update(counter)
        top_domains = [tag for tag, _ in domain_totals.most_common(3)]
        today = datetime.now(KST).date()
        dates = [
            (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in range(days - 1, -1, -1)
        ]
        series = [
            {
                "label": domain,
                "points": [per_day.get(date, Counter()).get(domain, 0) for date in dates],
            }
            for domain in top_domains
        ]

        return {
            "days": days,
            "papers": len(rows),
            "tags": [
                {"tag": tag, "axis": axis_of.get(tag, "unknown"), "papers": count}
                for tag, count in tag_counts.most_common(20)
            ],
            "pairs": [
                {"pair": name, "papers": count} for name, count in pair_counts.most_common(15)
            ],
            "matrix": matrix,
            "timeline": {"dates": dates, "series": series},
            "top_papers": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "score": row["score"],
                    "url": row["primary_url"],
                }
                for row in rows[:10]
            ],
        }

    # ------------------------------------------------------------------- reports

    def reports(self) -> dict[str, Any]:
        base = self.settings.output_dir
        items = []
        if base.is_dir():
            for path in base.glob("*/*.md"):
                kind = path.parent.name
                items.append(
                    {
                        "kind": kind,
                        "period": report_period(kind),
                        "name": path.stem,
                        "size": path.stat().st_size,
                        "modified_at": datetime.fromtimestamp(
                            path.stat().st_mtime, UTC
                        ).isoformat(),
                    }
                )
        # Newest report date first. Globbing yields directory order, which would
        # otherwise sort by kind and bury today's digest under `weekly-trends`.
        items.sort(key=lambda item: (item["name"], item["modified_at"]), reverse=True)
        kinds = sorted({item["kind"] for item in items})
        counts = Counter(item["period"] for item in items)
        periods = [
            {"period": period, "reports": counts.get(period, 0)}
            for period in (*REPORT_PERIODS, "other")
            if period != "other" or counts.get("other", 0)
        ]
        return {
            "items": items,
            "kinds": kinds,
            "periods": periods,
            "output_dir": str(base),
        }

    def report(self, kind: str, name: str) -> str | None:
        base = self.settings.output_dir.resolve()
        candidate = (base / kind / f"{name}.md").resolve()
        if not candidate.is_file() or base not in candidate.parents:
            return None
        return candidate.read_text(encoding="utf-8")
