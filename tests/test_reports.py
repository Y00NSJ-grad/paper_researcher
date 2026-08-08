import unittest

from radar.reports import render_digest, trend_counts


class ReportsTest(unittest.TestCase):
    def test_digest_and_trend_counts(self):
        row = {
            "title": "SAGIN Routing",
            "primary_url": "https://example.test/paper",
            "score": 80.0,
            "tags_json": (
                '{"domains":["sagin"],"methods":["marl"],"tasks":["routing"]}'
            ),
            "summary_json": None,
            "abstract": "An abstract.",
            "venue": None,
            "pdf_url": None,
            "code_url": None,
        }
        digest = render_digest("daily", [row], {"collected": 1, "relevant": 1})
        self.assertIn("SAGIN Routing", digest)
        _, pairs = trend_counts([row, row])
        self.assertEqual(pairs["sagin × marl"], 2)
        self.assertEqual(pairs["sagin × routing"], 2)


if __name__ == "__main__":
    unittest.main()

