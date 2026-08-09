import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from radar.config import Settings
from radar.models import PaperCandidate
from radar.runner import RadarRunner


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


class RunnerTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
