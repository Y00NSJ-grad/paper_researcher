import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import httpx

from radar.collectors.arxiv import ArxivCollector
from radar.collectors.huggingface import HuggingFaceCollector
from radar.collectors.ieee_xplore import (
    IEEE_JOURNALS,
    IeeeApiGuard,
    IeeeQuotaExceeded,
    IeeeXploreCollector,
)
from radar.collectors.openalex import OpenAlexCollector
from radar.collectors.openreview import OpenReviewCollector
from radar.collectors.semantic_scholar import SemanticScholarCollector

SINCE = datetime(2026, 8, 8, tzinfo=UTC)


def client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class CollectorTest(unittest.TestCase):
    def test_arxiv_retries_rate_limit_response(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(
                200,
                text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>',
            )

        collector = ArxivCollector(
            "test-agent",
            min_interval_seconds=0,
            max_attempts=3,
            backoff_base_seconds=0,
        )
        collector.client = client_with(handler)
        papers = collector.search("network learning", SINCE)
        self.assertEqual(papers, [])
        self.assertEqual(calls, 2)

    def test_arxiv_batches_queries_and_routes_results_locally(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            self.assertIn(" OR ", request.url.params["search_query"])
            return httpx.Response(
                200,
                text="""<feed xmlns="http://www.w3.org/2005/Atom">
                  <entry>
                    <id>https://arxiv.org/abs/2608.00001v1</id>
                    <title>Network Learning for Satellite Routing</title>
                    <summary>A network learning method.</summary>
                    <published>2026-08-09T00:00:00+00:00</published>
                    <updated>2026-08-09T00:00:00+00:00</updated>
                    <author><name>A. Author</name></author>
                  </entry>
                  <entry>
                    <id>https://arxiv.org/abs/2608.00002v1</id>
                    <title>UAV Trajectory Control</title>
                    <summary>Trajectory planning for an aerial robot.</summary>
                    <published>2026-08-09T00:00:00+00:00</published>
                    <updated>2026-08-09T00:00:00+00:00</updated>
                    <author><name>B. Author</name></author>
                  </entry>
                </feed>""",
            )

        collector = ArxivCollector("test-agent", min_interval_seconds=0)
        collector.client = client_with(handler)
        results = collector.search_many(
            ["network learning", '"UAV trajectory" control'],
            SINCE,
        )

        self.assertEqual(calls, 1)
        self.assertEqual([paper.arxiv_id for paper in results["network learning"]], ["2608.00001"])
        self.assertEqual(
            [paper.arxiv_id for paper in results['"UAV trajectory" control']],
            ["2608.00002"],
        )

    def test_semantic_scholar_maps_metadata_and_date_filter(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            self.assertEqual(request.url.params["sort"], "publicationDate:desc")
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "paperId": "s2-1",
                            "externalIds": {"DOI": "https://doi.org/10.1/test", "ArXiv": "2608.1"},
                            "title": "Network Learning",
                            "abstract": "Abstract",
                            "publicationDate": "2026-08-09",
                            "authors": [{"name": "A. Author"}],
                            "publicationVenue": {"name": "Venue"},
                            "openAccessPdf": {"url": "https://example.test/paper.pdf"},
                            "citationCount": 7,
                        }
                    ]
                },
            )

        collector = SemanticScholarCollector(
            "s2-key",
            "test-agent",
            min_interval_seconds=0,
            backoff_base_seconds=0,
        )
        collector.client = client_with(handler)
        papers = collector.search("network-learning", SINCE, 10)
        self.assertEqual(calls, 2)
        self.assertEqual(papers[0].source_id, "s2-1")
        self.assertEqual(papers[0].doi, "10.1/test")
        self.assertEqual(papers[0].arxiv_id, "2608.1")

    def test_openalex_retries_rate_limit_response(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"results": []})

        collector = OpenAlexCollector(
            None,
            None,
            "test-agent",
            min_interval_seconds=0,
            backoff_base_seconds=0,
        )
        collector.client = client_with(handler)
        self.assertEqual(collector.search("network learning", SINCE), [])
        self.assertEqual(calls, 2)

    def test_ieee_queries_all_tracked_journals(self):
        requested_venues = []

        def handler(request):
            requested_venues.append(request.url.params["publication_title"])
            number = str(len(requested_venues))
            return httpx.Response(
                200,
                json={
                    "articles": [
                        {
                            "article_number": number,
                            "title": f"Paper {number}",
                            "abstract": "Network learning",
                            "publication_title": request.url.params["publication_title"],
                            "publication_date": "9 August 2026",
                            "html_url": f"https://ieeexplore.ieee.org/document/{number}",
                            "authors": {"authors": [{"full_name": "I. Author"}]},
                        }
                    ]
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            collector = IeeeXploreCollector("ieee-key", "test-agent", Path(directory) / "quota.db")
            collector.client = client_with(handler)
            papers = collector.search("network learning", SINCE, 25)
        self.assertEqual(set(requested_venues), set(IEEE_JOURNALS.values()))
        self.assertEqual(len(papers), 5)
        journal_ids = {paper.external_ids["ieee_journal"] for paper in papers}
        self.assertEqual(journal_ids, set(IEEE_JOURNALS))

    def test_ieee_guard_enforces_spacing_and_persisted_daily_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "quota.db"
            clock_value = [datetime(2026, 8, 9, tzinfo=UTC).timestamp()]
            sleeps = []

            def clock():
                return clock_value[0]

            def sleeper(delay):
                sleeps.append(delay)
                clock_value[0] += delay

            guard = IeeeApiGuard(db_path, clock=clock, sleeper=sleeper)
            for expected_count in range(1, 201):
                self.assertEqual(guard.reserve_call(), expected_count)

            self.assertEqual(len(sleeps), 199)
            self.assertTrue(all(delay >= 0.109999 for delay in sleeps))
            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    "SELECT quota_day, call_count FROM api_rate_limits WHERE source = ?",
                    ("ieee_xplore",),
                ).fetchone()
            self.assertEqual(row, ("2026-08-09", 200))

            persisted_guard = IeeeApiGuard(db_path, clock=clock, sleeper=sleeper)
            with self.assertRaises(IeeeQuotaExceeded):
                persisted_guard.reserve_call()

            clock_value[0] += 24 * 60 * 60
            self.assertEqual(persisted_guard.reserve_call(), 1)

    def test_ieee_quota_failure_happens_before_http_request(self):
        class ExhaustedGuard:
            def reserve_call(self):
                raise IeeeQuotaExceeded("quota exhausted")

        http_calls = 0

        def handler(request):
            nonlocal http_calls
            http_calls += 1
            return httpx.Response(200, json={"articles": []})

        with tempfile.TemporaryDirectory() as directory:
            collector = IeeeXploreCollector("ieee-key", "test-agent", Path(directory) / "quota.db")
            collector.api_guard = ExhaustedGuard()
            collector.client = client_with(handler)
            with self.assertRaises(IeeeQuotaExceeded):
                collector.search("network learning", SINCE)

        self.assertEqual(http_calls, 0)

    def test_openreview_unwraps_api_v2_content(self):
        timestamp = int(datetime(2026, 8, 9, tzinfo=UTC).timestamp() * 1000)

        def handler(request):
            self.assertEqual(request.url.params["source"], "forum")
            return httpx.Response(
                200,
                json={
                    "notes": [
                        {
                            "id": "note-1",
                            "forum": "forum-1",
                            "cdate": timestamp,
                            "content": {
                                "title": {"value": "An Open Paper"},
                                "abstract": {"value": "Network learning"},
                                "authors": {
                                    "value": [{"fullname": "O. Author", "username": "~openreview1"}]
                                },
                                "venue": {"value": "ICLR 2027"},
                                "pdf": {"value": "/pdf/note-1.pdf"},
                            },
                        }
                    ]
                },
            )

        collector = OpenReviewCollector("test-agent")
        collector.client = client_with(handler)
        paper = collector.search("network learning", SINCE)[0]
        self.assertEqual(paper.source_id, "forum-1")
        self.assertEqual(paper.title, "An Open Paper")
        self.assertEqual(paper.authors, ["O. Author"])
        self.assertEqual(paper.pdf_url, "https://openreview.net/pdf/note-1.pdf")

    def test_huggingface_reuses_daily_feed_across_queries(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json=[
                    {
                        "publishedAt": datetime.now(UTC).isoformat(),
                        "paper": {
                            "id": "2608.00001",
                            "title": "Network Learning",
                            "summary": "Graph methods for network learning",
                            "authors": [{"name": "H. Author"}],
                            "githubRepo": "https://github.com/example/code",
                        },
                    }
                ],
            )

        collector = HuggingFaceCollector("test-agent")
        collector.client = client_with(handler)
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        first = collector.search("network learning", since)
        second = collector.search("graph methods", since)
        self.assertEqual(calls, 1)
        self.assertEqual(first[0].arxiv_id, "2608.00001")
        self.assertEqual(second[0].code_url, "https://github.com/example/code")

    def test_huggingface_treats_current_date_400_as_feed_not_ready(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(400, json={"error": "feed not ready"})

        collector = HuggingFaceCollector("test-agent", max_attempts=3, backoff_base_seconds=0)
        collector.client = client_with(handler)
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(collector.search("network learning", since), [])
        self.assertEqual(collector.search("graph methods", since), [])
        self.assertEqual(calls, 3)

    def test_huggingface_historical_date_400_remains_an_error(self):
        def handler(request):
            return httpx.Response(400, json={"error": "invalid historical date"})

        collector = HuggingFaceCollector("test-agent", max_attempts=1)
        collector.client = client_with(handler)
        with self.assertRaises(httpx.HTTPStatusError):
            collector.search("network learning", SINCE)


if __name__ == "__main__":
    unittest.main()
