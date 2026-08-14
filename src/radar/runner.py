from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from radar.collectors.arxiv import ArxivCollector
from radar.collectors.base import Collector
from radar.collectors.common import anchors_from_config
from radar.collectors.huggingface import HuggingFaceCollector
from radar.collectors.ieee_xplore import IeeeXploreCollector
from radar.collectors.openalex import OpenAlexCollector
from radar.collectors.openreview import OpenReviewCollector
from radar.collectors.semantic_scholar import SemanticScholarCollector
from radar.config import Settings, load_config
from radar.delivery import SlackWebhookDelivery
from radar.models import PaperCandidate
from radar.reports import render_digest, render_trend_report, write_report
from radar.scoring import score_paper
from radar.storage import PaperStore
from radar.summarizer import OpenAISummarizer

logger = logging.getLogger(__name__)

# How far a recovering source may reach back to cover runs it missed.
CATCHUP_LIMIT_DAYS = 14


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        return f"HTTP {response.status_code} {response.reason_phrase}".strip()
    return str(exc)


class RadarRunner:
    def __init__(
        self,
        settings: Settings,
        collectors: list[Collector] | None = None,
    ):
        self.settings = settings
        self.config = load_config(settings.config_path)
        self.store = PaperStore(settings.db_path)
        self.store.initialize()
        if collectors is not None:
            self.collectors = collectors
        else:
            anchors = anchors_from_config(self.config)
            self.collectors = [
                ArxivCollector(settings.user_agent, anchors),
                OpenAlexCollector(
                    settings.openalex_api_key,
                    settings.contact_email,
                    settings.user_agent,
                ),
                SemanticScholarCollector(settings.semantic_scholar_api_key, settings.user_agent),
                OpenReviewCollector(settings.user_agent, settings.openreview_token),
                HuggingFaceCollector(settings.user_agent, settings.huggingface_token, anchors),
            ]
            if settings.ieee_xplore_enabled and settings.ieee_xplore_api_key:
                self.collectors.append(
                    IeeeXploreCollector(
                        settings.ieee_xplore_api_key,
                        settings.user_agent,
                        settings.db_path,
                    )
                )

    def collect_and_report(
        self,
        kind: str,
        since_hours: int,
        top_n: int,
        summarize_n: int,
        dry_run: bool,
        limit_per_query: int = 25,
    ) -> Path:
        run_id = self.store.start_run(kind)
        run_started_at = datetime.now(UTC)
        default_since = run_started_at - timedelta(hours=since_hours)
        query_groups = ["daily"] if kind == "daily" else ["daily", "weekly"]
        queries = [
            query
            for group in query_groups
            for query in self.config.get("queries", {}).get(group, [])
        ]
        stats = {"collected": 0, "relevant": 0, "new": 0, "source_errors": 0}
        errors: list[str] = []
        relevant_ids: set[int] = set()
        new_ids: set[int] = set()

        try:
            for collector in self.collectors:
                since = self._catchup_since(collector.name, default_since, run_started_at)
                fetch_failed = False
                batch_search = getattr(collector, "search_many", None)
                candidates_by_query: dict[str, list[PaperCandidate]] = {}
                if callable(batch_search):
                    try:
                        candidates_by_query = batch_search(queries, since, limit_per_query)
                    except Exception as exc:  # noqa: BLE001 - isolate one batch source.
                        stats["source_errors"] += 1
                        message = f"{collector.name} batch failed: {_safe_error(exc)}"
                        logger.error(message)
                        errors.append(message)
                        continue

                for index, query in enumerate(queries):
                    query_id = f"{kind}:{index}:{query}"
                    if callable(batch_search):
                        candidates = candidates_by_query.get(query, [])
                    else:
                        try:
                            candidates = collector.search(query, since, limit_per_query)
                        except Exception as exc:  # noqa: BLE001 - isolate one source/query.
                            fetch_failed = True
                            stats["source_errors"] += 1
                            message = (
                                f"{collector.name} query failed ({query!r}): {_safe_error(exc)}"
                            )
                            logger.error(message)
                            errors.append(message)
                            continue
                    stats["collected"] += len(candidates)
                    for candidate in candidates:
                        try:
                            candidate.query_ids.append(query_id)
                            scored = score_paper(candidate, self.config)
                            minimum = float(
                                self.config.get("scoring", {}).get("minimum_relevant", 20)
                            )
                            if scored.score < minimum:
                                continue
                            paper_id, created = self.store.upsert_scored(scored, run_id)
                            relevant_ids.add(paper_id)
                            if created:
                                new_ids.add(paper_id)
                        except Exception as exc:
                            stats["source_errors"] += 1
                            message = (
                                f"{collector.name} candidate failed "
                                f"({candidate.source_id!r}): {exc}"
                            )
                            logger.exception(message)
                            errors.append(message)

                # A candidate that failed to score still arrived, so only a failed
                # fetch holds the window open for the next run.
                if not fetch_failed:
                    self.store.mark_collected(collector.name, run_started_at)

            stats["relevant"] = len(relevant_ids)
            stats["new"] = len(new_ids)

            row_limit = max(top_n, len(relevant_ids))
            rows = self.store.papers_for_run(run_id, limit=row_limit)
            if kind == "daily":
                rows = [row for row in rows if int(row["id"]) in new_ids]
            rows = rows[:top_n]
            self._summarize_rows(rows[:summarize_n])
            refreshed = {
                int(row["id"]): row for row in self.store.papers_for_run(run_id, row_limit)
            }
            rows = [refreshed[int(row["id"])] for row in rows]
            content = render_digest(kind, rows, stats)
            path = write_report(self.settings.output_dir, kind, content)
            self._deliver(content, dry_run)
            status = "partial" if errors else "success"
            self.store.finish_run(run_id, status, stats, "\n".join(errors) or None)
            return path
        except Exception as exc:
            self.store.finish_run(run_id, "failed", stats, str(exc))
            raise

    def _catchup_since(
        self, source: str, default_since: datetime, now: datetime
    ) -> datetime:
        """Reach back to a source's last good fetch so an outage is not a hole.

        A source that failed yesterday would otherwise never be asked for
        yesterday's papers again: today's window opens after they were published,
        and the digest only reports papers it has never seen. The reach is capped
        so a long silence cannot turn one run into a full-archive crawl.
        """
        last_success = self.store.last_collected_at(source)
        if last_success is None or last_success >= default_since:
            return default_since
        return max(last_success, now - timedelta(days=CATCHUP_LIMIT_DAYS))

    def trend_report(self, kind: str, days: int, dry_run: bool) -> Path:
        rows = self.store.recent_papers(days=days)
        content = render_trend_report(kind, rows, days)
        path = write_report(self.settings.output_dir, kind, content)
        self._deliver(content, dry_run)
        return path

    def _summarize_rows(self, rows: list[dict[str, Any]]) -> None:
        if not self.settings.openai_api_key:
            logger.info("OPENAI_API_KEY is absent; skipping summaries")
            return
        summarizer = OpenAISummarizer(
            api_key=self.settings.openai_api_key,
            model=self.settings.openai_model,
        )
        for row in rows:
            abstract = row.get("abstract")
            if not abstract or self.store.summary_is_current(int(row["id"]), abstract):
                continue
            try:
                summary = summarizer.summarize(row["title"], abstract)
                self.store.save_summary(
                    int(row["id"]), abstract, self.settings.openai_model, summary
                )
            except Exception:
                logger.exception("Failed to summarize paper %s", row["id"])

    def _deliver(self, content: str, dry_run: bool) -> None:
        if dry_run:
            return
        if not self.settings.slack_webhook_url:
            logger.warning("SLACK_WEBHOOK_URL is absent; report was saved locally only")
            return
        SlackWebhookDelivery(self.settings.slack_webhook_url).send(content)


def merge_candidates(candidates: list[PaperCandidate]) -> list[PaperCandidate]:
    """Useful for fixtures and later batch collectors; database dedupe remains authoritative."""
    seen: set[tuple[str, str]] = set()
    merged: list[PaperCandidate] = []
    for candidate in candidates:
        key = (candidate.source, candidate.source_id)
        if key not in seen:
            seen.add(key)
            merged.append(candidate)
    return merged
