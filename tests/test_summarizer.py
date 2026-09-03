import json
import unittest
from types import SimpleNamespace

from radar.summarizer import (
    MAX_ABSTRACT_CHARS,
    MonthlyTrendSchema,
    OpenAITrendAnalyzer,
    OpenAIWeeklyTrendAnalyzer,
    TrendEvidenceSchema,
    TrendSectionSchema,
    WeeklyInsightSchema,
    WeeklyPaperPickSchema,
    WeeklyTrendSchema,
    weekly_signal_metrics,
)


class _Responses:
    def __init__(self, parsed):
        self.parsed = parsed
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.parsed)


class TrendAnalyzerTest(unittest.TestCase):
    def test_analyzer_sends_bounded_grounded_records_and_maps_response(self):
        section = TrendSectionSchema(
            overview="근거가 제한적입니다.",
            key_trends=["관찰된 신호"],
            evidence=[TrendEvidenceSchema(claim="직접 근거", paper_ids=["P12"])],
            research_opportunities=["후속 검증"],
            limitations=["논문 수가 적음"],
        )
        responses = _Responses(
            MonthlyTrendSchema(
                executive_summary="월간 요약",
                physical_ai=section,
                quantum_ai=section,
                domains=section,
            )
        )
        analyzer = OpenAITrendAnalyzer.__new__(OpenAITrendAnalyzer)
        analyzer.client = SimpleNamespace(responses=responses)
        analyzer.model = "test-model"
        rows = [
            {
                "id": 12,
                "title": "A grounded paper",
                "abstract": "a" * (MAX_ABSTRACT_CHARS + 50),
                "venue": "Test Venue",
                "published_at": "2026-08-20",
                "first_seen_at": "2026-08-21",
                "tags_json": '{"methods":["physical_ai"]}',
                "score": 80.0,
            }
        ]

        result = analyzer.analyze(rows, 30)

        self.assertEqual(result.executive_summary, "월간 요약")
        self.assertEqual(result.physical_ai.evidence[0].paper_ids, ["P12"])
        user_content = responses.kwargs["input"][1]["content"]
        payload = json.loads(user_content.split("다음 JSON 데이터만 근거로 분석하세요:\n", 1)[1])
        self.assertEqual(payload[0]["id"], "P12")
        self.assertEqual(len(payload[0]["abstract"]), MAX_ABSTRACT_CHARS)
        self.assertIs(responses.kwargs["text_format"], MonthlyTrendSchema)

    def test_weekly_analyzer_compares_current_week_with_baseline(self):
        insight = WeeklyInsightSchema(
            title="새 결합",
            insight="이번 주 처음 관찰됨",
            confidence="Weak",
            paper_ids=["P21"],
        )
        responses = _Responses(
            WeeklyTrendSchema(
                research_pulse="이번 주 펄스",
                emerging_signals=[insight],
                cross_domain_convergence=[insight],
                papers_worth_reading=[
                    WeeklyPaperPickSchema(paper_id="P21", role="새 아이디어", why="직접 관련")
                ],
                research_opportunities=[insight],
                watchlist=[insight],
                data_coverage="표본이 작습니다.",
            )
        )
        analyzer = OpenAIWeeklyTrendAnalyzer.__new__(OpenAIWeeklyTrendAnalyzer)
        analyzer.client = SimpleNamespace(responses=responses)
        analyzer.model = "test-model"
        current = [
            {
                "id": 21,
                "title": "VLA for UAV Control",
                "abstract": "Physical AI control.",
                "tags_json": '{"domains":["aerial_edge"],"methods":["physical_ai"]}',
                "code_url": "https://github.test/code",
            }
        ]
        baseline = [
            {
                "id": 10,
                "title": "Earlier UAV Control",
                "abstract": "Conventional control.",
                "tags_json": '{"domains":["aerial_edge"],"methods":["marl"]}',
            }
        ]

        result = analyzer.analyze(current, baseline, 7, {"runs": 7, "source_errors": 1})

        self.assertEqual(result.emerging_signals[0].confidence, "Weak")
        content = responses.kwargs["input"][1]["content"]
        payload = json.loads(content.split("주간 분석을 작성하세요:\n", 1)[1])
        self.assertEqual(payload["metrics"]["current_unique_papers"], 1)
        physical = next(
            item for item in payload["metrics"]["tag_comparison"] if item["name"] == "physical_ai"
        )
        self.assertTrue(physical["newly_observed"])
        self.assertEqual(payload["current_papers"][0]["id"], "P21")
        self.assertEqual(payload["baseline_papers"][0]["id"], "P10")
        self.assertIs(responses.kwargs["text_format"], WeeklyTrendSchema)

    def test_weekly_metrics_normalize_the_28_day_baseline(self):
        current = [{"tags_json": '{"domains":["sagin"],"methods":["marl"]}'}] * 2
        baseline = [{"tags_json": '{"domains":["sagin"],"methods":["marl"]}'}] * 8

        metrics = weekly_signal_metrics(current, baseline)

        sagin = next(item for item in metrics["tag_comparison"] if item["name"] == "sagin")
        self.assertEqual(sagin["current_count"], 2)
        self.assertEqual(sagin["baseline_weekly_average"], 2.0)


if __name__ == "__main__":
    unittest.main()
