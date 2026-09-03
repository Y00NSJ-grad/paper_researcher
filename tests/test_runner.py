import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

from radar.config import Settings
from radar.models import (
    MonthlyTrendAnalysis,
    PaperCandidate,
    TrendSection,
    WeeklyTrendAnalysis,
)
from radar.runner import CATCHUP_LIMIT_DAYS, WEEKLY_BASELINE_DAYS, RadarRunner


class FixtureCollector:
    name = "fixture"

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        return [
            PaperCandidate(
                source=self.name,
                source_id="fixture-1",
                doi="10.1234/fixture",
                title="MARL Routing for Space-Air-Ground Integrated Networks",
                abstract=(
                    "We use multi-agent reinforcement learning for routing and resource "
                    "allocation in a SAGIN test environment."
                ),
                authors=["A. Researcher"],
                published_at=datetime(2026, 8, 9, tzinfo=UTC),
                url="https://example.test/fixture",
            )
        ]


class MixedCandidateCollector:
    name = "mixed"

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        published = datetime(2026, 8, 9, tzinfo=UTC)
        return [
            PaperCandidate(
                source=self.name,
                source_id="bad-author",
                title="MARL Routing for Space-Air-Ground Integrated Networks",
                abstract="Multi-agent reinforcement learning for SAGIN routing.",
                authors=[{"fullname": "Broken Author"}],
                published_at=published,
                url="https://example.test/bad",
            ),
            PaperCandidate(
                source=self.name,
                source_id="good-paper",
                doi="10.1234/good",
                title="MARL Routing for Space-Air-Ground Integrated Networks",
                abstract="Multi-agent reinforcement learning for SAGIN routing.",
                authors=["Good Author"],
                published_at=published,
                url="https://example.test/good",
            ),
        ]


class HttpFailureCollector:
    name = "protected_source"

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        request = httpx.Request("GET", "https://example.test/search?apikey=secret-value")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("request failed", request=request, response=response)


class WindowRecordingCollector:
    """Records the `since` it was handed, and fails on demand."""

    name = "windowed"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.windows: list[datetime] = []

    def search(self, query: str, since: datetime, limit: int = 25) -> list[PaperCandidate]:
        self.windows.append(since)
        if self.fail:
            raise httpx.ConnectError("source is down")
        return []


class RunnerTest(unittest.TestCase):
    def _settings(self, root: Path) -> Settings:
        return Settings(
            db_path=root / "papers.db",
            output_dir=root / "outputs",
            config_path=Path(__file__).parents[1] / "config" / "keywords.yml",
            slack_webhook_url=None,
            openalex_api_key=None,
            openai_api_key=None,
            openai_model="test-model",
            contact_email=None,
            user_agent="paper-radar-test",
        )

    def _run_daily(self, runner: RadarRunner) -> None:
        runner.collect_and_report(
            kind="daily",
            since_hours=48,
            top_n=10,
            summarize_n=0,
            dry_run=True,
            limit_per_query=5,
        )

    def test_a_failed_source_reaches_back_to_its_last_good_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            collector = WindowRecordingCollector()

            runner = RadarRunner(settings, collectors=[collector])
            self._run_daily(runner)
            first_window = collector.windows[-1]

            # Pretend the last success was a week ago and the source then failed.
            week_ago = datetime.now(UTC) - timedelta(days=7)
            runner.store.mark_collected(collector.name, week_ago)
            collector.fail = True
            with self.assertLogs("radar.runner", level="ERROR"):
                self._run_daily(runner)

            collector.fail = False
            self._run_daily(runner)
            recovered_window = collector.windows[-1]

            # The 48-hour window would have skipped straight over the outage.
            self.assertLess(recovered_window, first_window)
            self.assertAlmostEqual(recovered_window.timestamp(), week_ago.timestamp(), delta=2)

    def test_catchup_reach_is_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            collector = WindowRecordingCollector()
            runner = RadarRunner(settings, collectors=[collector])
            runner.store.mark_collected(collector.name, datetime.now(UTC) - timedelta(days=400))

            self._run_daily(runner)

            floor = datetime.now(UTC) - timedelta(days=CATCHUP_LIMIT_DAYS)
            # A long silence must not turn one run into a full-archive crawl.
            self.assertAlmostEqual(collector.windows[-1].timestamp(), floor.timestamp(), delta=2)

    def test_a_healthy_source_keeps_the_plain_window(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            collector = WindowRecordingCollector()
            runner = RadarRunner(settings, collectors=[collector])

            self._run_daily(runner)
            self._run_daily(runner)

            expected = datetime.now(UTC) - timedelta(hours=48)
            self.assertAlmostEqual(collector.windows[-1].timestamp(), expected.timestamp(), delta=2)

    def test_daily_pipeline_writes_deduplicated_report(self):
        project_root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                db_path=root / "papers.db",
                output_dir=root / "outputs",
                config_path=project_root / "config" / "keywords.yml",
                slack_webhook_url=None,
                openalex_api_key=None,
                openai_api_key=None,
                openai_model="test-model",
                contact_email=None,
                user_agent="paper-radar-test",
            )
            runner = RadarRunner(settings, collectors=[FixtureCollector()])
            report = runner.collect_and_report(
                kind="daily",
                since_hours=48,
                top_n=10,
                summarize_n=3,
                dry_run=True,
                limit_per_query=5,
            )

            content = report.read_text(encoding="utf-8")
            self.assertIn("MARL Routing", content)
            self.assertIn("Relevant: 1", content)
            self.assertEqual(content.count("> 1."), 1)
            self.assertEqual(len(runner.store.papers_for_run(1)), 1)

            second_report = runner.collect_and_report(
                kind="daily",
                since_hours=48,
                top_n=10,
                summarize_n=3,
                dry_run=True,
                limit_per_query=5,
            )
            second_content = second_report.read_text(encoding="utf-8")
            self.assertIn("Relevant: 1", second_content)
            self.assertIn("New: 0", second_content)
            self.assertNotIn("MARL Routing", second_content)
            self.assertIn("No new relevant papers", second_content)

    def test_candidate_error_does_not_abort_report_delivery(self):
        project_root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                db_path=root / "papers.db",
                output_dir=root / "outputs",
                config_path=project_root / "config" / "keywords.yml",
                slack_webhook_url=None,
                openalex_api_key=None,
                openai_api_key=None,
                openai_model="test-model",
                contact_email=None,
                user_agent="paper-radar-test",
            )
            runner = RadarRunner(settings, collectors=[MixedCandidateCollector()])
            with self.assertLogs("radar.runner", level="ERROR") as logs:
                report = runner.collect_and_report(
                    kind="daily",
                    since_hours=48,
                    top_n=10,
                    summarize_n=0,
                    dry_run=True,
                    limit_per_query=5,
                )

            content = report.read_text(encoding="utf-8")
            self.assertIn("Good Author", str(runner.store.papers_for_run(1)))
            self.assertIn("Source errors: 8", content)
            self.assertEqual(len(logs.output), 8)
            with runner.store.connect() as connection:
                status = connection.execute(
                    "SELECT status FROM pipeline_runs WHERE id = 1"
                ).fetchone()["status"]
            self.assertEqual(status, "partial")

    def test_http_source_error_does_not_store_or_log_request_secrets(self):
        project_root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                db_path=root / "papers.db",
                output_dir=root / "outputs",
                config_path=project_root / "config" / "keywords.yml",
                slack_webhook_url=None,
                openalex_api_key=None,
                openai_api_key=None,
                openai_model="test-model",
                contact_email=None,
                user_agent="paper-radar-test",
            )
            runner = RadarRunner(settings, collectors=[HttpFailureCollector()])
            with self.assertLogs("radar.runner", level="ERROR") as logs:
                runner.collect_and_report(
                    kind="daily",
                    since_hours=48,
                    top_n=10,
                    summarize_n=0,
                    dry_run=True,
                )

            with runner.store.connect() as connection:
                error = connection.execute(
                    "SELECT error FROM pipeline_runs WHERE id = 1"
                ).fetchone()["error"]
            combined = "\n".join(logs.output) + "\n" + error
            self.assertNotIn("secret-value", combined)
            self.assertNotIn("apikey", combined)
            self.assertIn("HTTP 403 Forbidden", combined)

    def test_ieee_collector_is_disabled_without_explicit_enable_flag(self):
        project_root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                db_path=root / "papers.db",
                output_dir=root / "outputs",
                config_path=project_root / "config" / "keywords.yml",
                slack_webhook_url=None,
                openalex_api_key=None,
                openai_api_key=None,
                openai_model="test-model",
                contact_email=None,
                user_agent="paper-radar-test",
                ieee_xplore_api_key="pending-key",
            )
            runner = RadarRunner(settings)
            self.assertNotIn("ieee_xplore", [collector.name for collector in runner.collectors])

    def test_monthly_report_uses_gpt_analysis_when_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(self._settings(Path(directory)), openai_api_key="test-key")
            runner = RadarRunner(settings, collectors=[])
            row = {
                "id": 1,
                "title": "Physical AI for UAVs",
                "abstract": "An embodied controller.",
                "venue": "Test Venue",
                "published_at": "2026-08-20T00:00:00+00:00",
                "first_seen_at": "2026-08-21T00:00:00+00:00",
                "tags_json": '{"methods":["physical_ai"]}',
                "score": 70.0,
                "primary_url": "https://example.test/paper",
            }
            section = TrendSection(overview="분석", limitations=["표본 부족"])
            analysis = MonthlyTrendAnalysis("요약", section, section, section)

            with (
                patch.object(runner.store, "recent_papers", return_value=[row]),
                patch("radar.runner.OpenAITrendAnalyzer") as analyzer_type,
            ):
                analyzer_type.return_value.analyze.return_value = analysis
                path = runner.trend_report("monthly-trends", days=30, dry_run=True)

            analyzer_type.return_value.analyze.assert_called_once_with([row], 30)
            content = path.read_text(encoding="utf-8")
            self.assertIn("GPT Research Trend Analysis", content)
            self.assertIn("Physical AI 부문", content)

    def test_weekly_report_compares_current_rows_with_28_day_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(self._settings(Path(directory)), openai_api_key="test-key")
            runner = RadarRunner(settings, collectors=[])
            now = datetime.now(UTC)

            def row(paper_id: int, age_days: int) -> dict:
                return {
                    "id": paper_id,
                    "title": f"Paper {paper_id}",
                    "abstract": "Research abstract.",
                    "venue": "Test Venue",
                    "published_at": (now - timedelta(days=age_days)).isoformat(),
                    "first_seen_at": (now - timedelta(days=age_days)).isoformat(),
                    "tags_json": '{"domains":["sagin"]}',
                    "score": 70.0,
                    "primary_url": f"https://example.test/{paper_id}",
                }

            current = row(1, 2)
            baseline = row(2, 14)
            analysis = WeeklyTrendAnalysis(research_pulse="주간 변화")
            coverage = {"runs": 7, "source_errors": 0, "source_papers": {"arxiv": 1}}

            with (
                patch.object(
                    runner.store,
                    "recent_papers",
                    return_value=[current, baseline],
                ) as recent,
                patch.object(runner.store, "collection_health", return_value=coverage),
                patch("radar.runner.OpenAIWeeklyTrendAnalyzer") as analyzer_type,
            ):
                analyzer_type.return_value.analyze.return_value = analysis
                path = runner.trend_report("weekly-trends", days=7, dry_run=True)

            recent.assert_called_once_with(days=7 + WEEKLY_BASELINE_DAYS, limit=300)
            analyzer_type.return_value.analyze.assert_called_once_with(
                [current], [baseline], 7, coverage
            )
            self.assertIn("GPT Weekly Research Pulse", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
