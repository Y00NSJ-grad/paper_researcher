import unittest

from radar.models import (
    MonthlyTrendAnalysis,
    TrendEvidence,
    TrendSection,
    WeeklyInsight,
    WeeklyPaperPick,
    WeeklyTrendAnalysis,
)
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

    def test_weekly_report_renders_actions_evidence_confidence_and_coverage(self):
        row = {
            "id": 8,
            "title": "VLA for Satellite Robotics",
            "primary_url": "https://example.test/paper-8",
            "score": 81.0,
            "tags_json": '{"domains":["ntn_satellite"],"methods":["physical_ai"]}',
        }
        insight = WeeklyInsight(
            title="새 결합",
            insight="VLA와 위성 로봇의 결합이 관찰됨",
            confidence="Weak",
            paper_ids=["P8", "P404"],
        )
        analysis = WeeklyTrendAnalysis(
            research_pulse="이번 주에는 초기 신호가 관찰됐습니다.",
            emerging_signals=[insight],
            cross_domain_convergence=[insight],
            papers_worth_reading=[WeeklyPaperPick("P8", "새 아이디어", "연구와 직접 연결")],
            research_opportunities=[insight],
            watchlist=[insight],
            data_coverage="단일 논문 기반입니다.",
        )

        report = render_trend_report(
            "weekly-trends",
            [row],
            7,
            analysis,
            evidence_rows=[row],
            collection_coverage={
                "runs": 7,
                "source_errors": 2,
                "source_papers": {"arxiv": 3},
            },
        )

        self.assertIn("GPT Weekly Research Pulse", report)
        self.assertIn("Emerging Signals", report)
        self.assertIn("Cross-domain Convergence", report)
        self.assertIn("Papers Worth Reading", report)
        self.assertIn("Research Opportunities", report)
        self.assertIn("Watchlist for Next Week", report)
        self.assertIn("`Weak`", report)
        self.assertIn("[VLA for Satellite Robotics](https://example.test/paper-8)", report)
        self.assertNotIn("P404", report)
        self.assertIn("Source errors: 2", report)
        self.assertIn("arxiv 3", report)


if __name__ == "__main__":
    unittest.main()
