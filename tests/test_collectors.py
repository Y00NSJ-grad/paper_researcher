import unittest
from datetime import UTC, datetime

import httpx

from radar.collectors.huggingface import HuggingFaceCollector
from radar.collectors.ieee_xplore import IEEE_JOURNALS, IeeeXploreCollector
from radar.collectors.openreview import OpenReviewCollector
from radar.collectors.semantic_scholar import SemanticScholarCollector

SINCE = datetime(2026, 8, 8, tzinfo=UTC)


def client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class CollectorTest(unittest.TestCase):
    def test_semantic_scholar_maps_metadata_and_date_filter(self):
        def handler(request):
            self.assertEqual(request.url.params["sort"], "publicationDate:desc")
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

        collector = SemanticScholarCollector("s2-key", "test-agent")
        collector.client = client_with(handler)
        papers = collector.search("network-learning", SINCE, 10)
        self.assertEqual(papers[0].source_id, "s2-1")
        self.assertEqual(papers[0].doi, "10.1/test")
        self.assertEqual(papers[0].arxiv_id, "2608.1")

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

        collector = IeeeXploreCollector("ieee-key", "test-agent")
        collector.client = client_with(handler)
        papers = collector.search("network learning", SINCE, 25)
        self.assertEqual(set(requested_venues), set(IEEE_JOURNALS.values()))
        self.assertEqual(len(papers), 5)
        journal_ids = {paper.external_ids["ieee_journal"] for paper in papers}
        self.assertEqual(journal_ids, set(IEEE_JOURNALS))

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
                                "authors": {"value": ["O. Author"]},
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


if __name__ == "__main__":
    unittest.main()
