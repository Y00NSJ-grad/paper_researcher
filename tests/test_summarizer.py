import json
import unittest
from types import SimpleNamespace

from radar.summarizer import (
    MAX_ABSTRACT_CHARS,
    MonthlyTrendSchema,
    OpenAITrendAnalyzer,
    TrendEvidenceSchema,
    TrendSectionSchema,
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


if __name__ == "__main__":
    unittest.main()
