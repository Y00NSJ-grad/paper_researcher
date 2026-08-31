import unittest

from radar.models import MonthlyTrendAnalysis, TrendEvidence, TrendSection
from radar.reports import render_digest, render_trend_report, trend_counts


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

    def test_monthly_report_renders_three_grounded_analysis_sections(self):
        row = {
            "id": 7,
            "title": "Physical AI for UAV Networks",
            "primary_url": "https://example.test/paper-7",
            "score": 72.0,
            "tags_json": '{"domains":["aerial_edge"],"methods":["physical_ai"]}',
            "published_at": "2026-08-20T00:00:00+00:00",
        }
        section = TrendSection(
            overview="관찰된 흐름입니다.",
            key_trends=["VLA와 UAV의 결합"],
            evidence=[TrendEvidence("한 편의 직접 근거", ["P7", "P999"])],
            research_opportunities=["실환경 검증"],
            limitations=["표본이 작음"],
        )
        analysis = MonthlyTrendAnalysis(
            executive_summary="이번 달의 핵심 요약입니다.",
            physical_ai=section,
            quantum_ai=section,
            domains=section,
        )

        report = render_trend_report("monthly-trends", [row], 30, analysis)

        self.assertIn("Physical AI 부문", report)
        self.assertIn("Quantum AI 부문", report)
        self.assertIn("도메인 부문", report)
        self.assertIn("[Physical AI for UAV Networks](https://example.test/paper-7)", report)
        self.assertNotIn("P999", report)


if __name__ == "__main__":
    unittest.main()
