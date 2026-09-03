import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from radar.models import PaperCandidate, ScoredPaper
from radar.storage import PaperStore


class StorageTest(unittest.TestCase):
    def test_arxiv_versions_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStore(Path(directory) / "papers.db")
            store.initialize()
            run_id = store.start_run("test")
            base = {
                "source": "arxiv",
                "title": "MARL for LEO Satellite Routing",
                "abstract": "A routing method.",
                "authors": ["A. Researcher"],
                "published_at": datetime(2026, 8, 1, tzinfo=UTC),
                "url": "https://arxiv.org/abs/2608.00001",
            }
            first = PaperCandidate(source_id="2608.00001v1", arxiv_id="2608.00001v1", **base)
            second = PaperCandidate(source_id="2608.00001v2", arxiv_id="2608.00001v2", **base)
            first.query_ids = ["query-one"]
            second.query_ids = ["query-two"]
            scored_one = ScoredPaper(first, 70, {"domains": ["ntn"]}, ["fixture"])
            scored_two = ScoredPaper(second, 75, {"domains": ["ntn"]}, ["fixture"])

            paper_id_one, created_one = store.upsert_scored(scored_one, run_id)
            paper_id_two, created_two = store.upsert_scored(scored_two, run_id)

            self.assertTrue(created_one)
            self.assertFalse(created_two)
            self.assertEqual(paper_id_one, paper_id_two)
            rows = store.papers_for_run(run_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["score"], 75)
            self.assertIn("query-one", rows[0]["query_ids_json"])
            self.assertIn("query-two", rows[0]["query_ids_json"])

    def test_collection_health_reports_runs_errors_and_source_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStore(Path(directory) / "papers.db")
            store.initialize()
            run_id = store.start_run("daily")
            candidate = PaperCandidate(
                source="arxiv",
                source_id="2609.00001",
                arxiv_id="2609.00001",
                title="A Recent Paper",
                abstract="An abstract.",
                authors=["A. Researcher"],
                published_at=datetime.now(UTC),
                url="https://arxiv.org/abs/2609.00001",
            )
            store.upsert_scored(
                ScoredPaper(candidate, 50, {"domains": ["sagin"]}, ["fixture"]),
                run_id,
            )
            store.finish_run(
                run_id,
                "partial",
                {"source_errors": 2},
                "arxiv batch failed: timeout\nopenalex query failed ('x'): HTTP 429",
            )

            health = store.collection_health(7)

            self.assertEqual(health["runs"], 1)
            self.assertEqual(health["source_errors"], 2)
            self.assertEqual(health["source_failures"], {"arxiv": 1, "openalex": 1})
            self.assertEqual(health["source_papers"], {"arxiv": 1})


if __name__ == "__main__":
    unittest.main()
