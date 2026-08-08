import unittest

from radar.models import PaperCandidate
from radar.scoring import score_paper

CONFIG = {
    "methods": {
        "marl": {"weight": 14, "terms": ["multi-agent reinforcement learning", "MARL"]}
    },
    "domains": {"sagin": {"weight": 18, "terms": ["SAGIN"]}},
    "tasks": {"routing": {"weight": 6, "terms": ["routing"]}},
    "scoring": {
        "title_multiplier": 1.35,
        "cross_axis_bonus": 12,
        "max_score": 100,
    },
}


class ScoringTest(unittest.TestCase):
    def test_cross_axis_paper_scores_highly(self):
        paper = PaperCandidate(
            source="fixture",
            source_id="1",
            title="MARL Routing for SAGIN",
            abstract="Multi-agent reinforcement learning for routing.",
            url="https://example.test/paper",
        )
        scored = score_paper(paper, CONFIG)
        self.assertGreater(scored.score, 50)
        self.assertEqual(scored.tags["domains"], ["sagin"])
        self.assertEqual(scored.tags["methods"], ["marl"])
        self.assertIn("routing", scored.tags["tasks"])

    def test_unrelated_paper_scores_zero(self):
        paper = PaperCandidate(
            source="fixture",
            source_id="2",
            title="Marine biology observations",
            url="https://example.test/other",
        )
        self.assertEqual(score_paper(paper, CONFIG).score, 0)


if __name__ == "__main__":
    unittest.main()

