from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from radar.config import Settings
from radar.dashboard.data import DashboardData, parse_query_id
from radar.dashboard.server import build_server
from radar.models import PaperCandidate
from radar.scoring import explain_score, is_survey, score_paper
from radar.storage import PaperStore

CONFIG = """
methods:
  marl:
    weight: 14
    terms: [multi-agent reinforcement learning, MARL]
domains:
  ntn_satellite:
    weight: 18
    terms: [LEO satellite]
tasks:
  routing:
    weight: 6
    terms: [routing]
queries:
  daily:
    - 'LEO satellite routing'
    - 'unused query'
scoring:
  minimum_relevant: 20
  title_multiplier: 1.35
  cross_axis_bonus: 12
  code_bonus: 8
  survey_bonus: 6
  max_score: 100
"""


def make_settings(directory: Path) -> Settings:
    config_path = directory / "keywords.yml"
    config_path.write_text(CONFIG, encoding="utf-8")
    (directory / "outputs" / "daily").mkdir(parents=True)
    (directory / "outputs" / "daily" / "2026-08-11.md").write_text(
        "> 1. [A paper](https://example.org/a)\n\nScore: **72.0**\n", encoding="utf-8"
    )
    return Settings(
        db_path=directory / "papers.db",
        output_dir=directory / "outputs",
        config_path=config_path,
        slack_webhook_url=None,
        openalex_api_key=None,
        openai_api_key=None,
        openai_model="test-model",
        contact_email=None,
        user_agent="paper-radar-test",
    )


def seed(settings: Settings, config_text: str = CONFIG) -> int:
    import yaml

    config = yaml.safe_load(config_text)
    store = PaperStore(settings.db_path)
    store.initialize()
    run_id = store.start_run("daily")
    candidate = PaperCandidate(
        source="arxiv",
        source_id="2608.00001v1",
        title="MARL Routing for LEO Satellite Networks",
        url="https://arxiv.org/abs/2608.00001",
        abstract="We study routing with multi-agent reinforcement learning.",
        authors=["A. Researcher", "B. Researcher"],
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        arxiv_id="2608.00001v1",
        code_url="https://github.com/example/repo",
    )
    candidate.query_ids.append("daily:0:LEO satellite routing")
    paper_id, _ = store.upsert_scored(score_paper(candidate, config), run_id)
    store.finish_run(run_id, "success", {"collected": 1, "relevant": 1, "new": 1})
    return paper_id


class ScoringExplanationTest(unittest.TestCase):
    def test_breakdown_matches_the_stored_score(self):
        config = __import__("yaml").safe_load(CONFIG)
        candidate = PaperCandidate(
            source="arxiv",
            source_id="x",
            title="MARL Routing for LEO Satellite Networks",
            url="https://example.org",
            abstract="Routing with multi-agent reinforcement learning.",
            code_url="https://github.com/example/repo",
        )
        scored = score_paper(candidate, config)
        breakdown = explain_score(candidate.title, candidate.abstract, config, has_code=True)

        self.assertEqual(scored.score, breakdown.score)
        self.assertEqual(scored.reasons, breakdown.reasons)
        self.assertEqual(scored.tags, breakdown.tags)
        # 14*1.35 + 18*1.35 + 6*1.35 + 12 cross-axis + 8 code
        self.assertAlmostEqual(breakdown.score, 71.3, places=1)
        self.assertTrue(breakdown.relevant)
        self.assertFalse(breakdown.capped)

    def test_title_hits_are_reported_separately(self):
        config = __import__("yaml").safe_load(CONFIG)
        breakdown = explain_score("A study of networks", "We use MARL for routing.", config)
        marl = next(match for match in breakdown.matches if match.tag == "marl")
        self.assertEqual(marl.title_terms, [])
        self.assertEqual(marl.multiplier, 1.0)

    def test_code_bonus_only_applies_when_a_repository_exists(self):
        config = __import__("yaml").safe_load(CONFIG)
        without = explain_score("MARL routing", "LEO satellite", config, has_code=False)
        with_code = explain_score("MARL routing", "LEO satellite", config, has_code=True)
        self.assertAlmostEqual(with_code.score - without.score, 8.0, places=1)


class DashboardDataTest(unittest.TestCase):
    def test_overview_counts_and_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            seed(settings)
            data = DashboardData(settings).overview(days=7)

            self.assertEqual(data["counts"]["papers"], 1)
            self.assertEqual(data["counts"]["pipeline_runs"], 1)
            self.assertEqual(len(data["timeline"]), 7)
            self.assertEqual(sum(point["papers"] for point in data["timeline"]), 1)
            self.assertEqual(data["sources"][0]["source"], "arxiv")
            self.assertEqual(sum(bucket["papers"] for bucket in data["histogram"]), 1)

    def test_paper_detail_explains_the_stored_score(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            paper_id = seed(settings)
            detail = DashboardData(settings).paper(paper_id)

            self.assertTrue(detail["breakdown"]["matches_stored"])
            self.assertEqual(detail["breakdown"]["score"], detail["score"])
            tags = {match["tag"] for match in detail["breakdown"]["matches"]}
            self.assertEqual(tags, {"marl", "ntn_satellite", "routing"})
            self.assertIn("code available", [b["name"] for b in detail["breakdown"]["bonuses"]])
            self.assertEqual(detail["queries"][0]["text"], "LEO satellite routing")
            self.assertEqual(detail["versions"][0]["source"], "arxiv")

    def test_paper_detail_flags_a_changed_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            paper_id = seed(settings)
            settings.config_path.write_text(
                CONFIG.replace("weight: 18", "weight: 40"), encoding="utf-8"
            )
            detail = DashboardData(settings).paper(paper_id)

            self.assertFalse(detail["breakdown"]["matches_stored"])
            self.assertGreater(detail["breakdown"]["score"], detail["score"])

    def test_papers_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            seed(settings)
            data = DashboardData(settings)

            self.assertEqual(data.papers(search="MARL")["total"], 1)
            self.assertEqual(data.papers(search="quantum")["total"], 0)
            self.assertEqual(data.papers(source="arxiv")["total"], 1)
            self.assertEqual(data.papers(source="openalex")["total"], 0)
            self.assertEqual(data.papers(tag="routing")["total"], 1)
            self.assertEqual(data.papers(min_score=99)["total"], 0)
            self.assertEqual(data.papers()["items"][0]["sources"], ["arxiv"])

    def test_list_badges_mirror_the_score_bonuses(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            seed(settings)
            item = DashboardData(settings).papers()["items"][0]

            self.assertEqual(item["code_url"], "https://github.com/example/repo")
            self.assertFalse(item["is_survey"])
            self.assertEqual(item["published_at"][:10], "2026-08-01")

    def test_survey_badge_uses_the_scorer_predicate(self):
        self.assertTrue(is_survey("A Survey of LEO Routing"))
        self.assertTrue(is_survey("Deep Learning: A Review"))
        self.assertFalse(is_survey("Reviewing Boards for Satellites"))
        self.assertFalse(is_survey("Surveying the Ocean Floor"))

    def test_queries_report_configured_and_orphaned_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            seed(settings)
            items = {item["text"]: item for item in DashboardData(settings).queries()["items"]}

            self.assertEqual(items["LEO satellite routing"]["papers"], 1)
            self.assertEqual(items["LEO satellite routing"]["runs"], 1)
            self.assertTrue(items["LEO satellite routing"]["configured"])
            self.assertEqual(items["unused query"]["papers"], 0)

    def test_scoring_joins_config_weights_with_stored_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            seed(settings)
            scoring = DashboardData(settings).scoring()
            by_axis = {axis["axis"]: axis for axis in scoring["axes"]}

            self.assertEqual(by_axis["domains"]["tags"][0]["tag"], "ntn_satellite")
            self.assertEqual(by_axis["domains"]["tags"][0]["papers"], 1)
            self.assertEqual(scoring["params"]["minimum_relevant"], 20)
            self.assertEqual(scoring["orphan_tags"], [])

    def test_trends_build_a_domain_by_method_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            seed(settings)
            trends = DashboardData(settings).trends(days=30)

            self.assertEqual(trends["papers"], 1)
            self.assertEqual(trends["matrix"]["rows"], ["ntn_satellite"])
            self.assertEqual(
                sorted(column["label"] for column in trends["matrix"]["columns"]),
                ["marl", "routing"],
            )
            self.assertEqual(trends["matrix"]["max"], 1)
            self.assertEqual(len(trends["timeline"]["dates"]), 30)

    def test_reports_are_listed_and_read_within_the_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            seed(settings)
            data = DashboardData(settings)

            listing = data.reports()
            self.assertEqual(listing["items"][0]["kind"], "daily")
            self.assertIn("A paper", data.report("daily", "2026-08-11"))
            self.assertIsNone(data.report("daily", "missing"))
            self.assertIsNone(data.report("..", "papers"))

    def test_config_is_reloaded_when_the_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            seed(settings)
            data = DashboardData(settings)
            self.assertIn("marl", data.config["methods"])

            settings.config_path.write_text(
                CONFIG.replace("marl:", "marl_v2:"), encoding="utf-8"
            )
            self.assertIn("marl_v2", data.config["methods"])


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.settings = make_settings(Path(self._directory.name))
        self.paper_id = seed(self.settings)
        self.data = DashboardData(self.settings)

    def test_records_and_supersedes_a_verdict(self):
        self.data.record_feedback(self.paper_id, "maybe")
        state = self.data.record_feedback(self.paper_id, "keep")

        self.assertEqual(state["feedback"], "keep")
        self.assertEqual([entry["value"] for entry in state["feedback_history"]], ["keep", "maybe"])
        self.assertEqual(state["feedback_history"][0]["source"], "dashboard")

    def test_clearing_removes_the_history(self):
        self.data.record_feedback(self.paper_id, "reject")
        state = self.data.clear_feedback(self.paper_id)

        self.assertIsNone(state["feedback"])
        self.assertEqual(state["feedback_history"], [])

    def test_rejects_a_value_outside_the_schema_constraint(self):
        with self.assertRaises(ValueError):
            self.data.record_feedback(self.paper_id, "starred")

    def test_rejects_an_unknown_paper(self):
        with self.assertRaises(LookupError):
            self.data.record_feedback(9999, "keep")

    def test_paper_list_and_detail_expose_the_current_verdict(self):
        self.data.record_feedback(self.paper_id, "keep")

        self.assertEqual(self.data.papers()["items"][0]["feedback"], "keep")
        self.assertEqual(self.data.paper(self.paper_id)["feedback"], "keep")

    def test_papers_can_be_filtered_by_verdict(self):
        self.assertEqual(self.data.papers(feedback="none")["total"], 1)
        self.assertEqual(self.data.papers(feedback="keep")["total"], 0)

        self.data.record_feedback(self.paper_id, "keep")

        self.assertEqual(self.data.papers(feedback="keep")["total"], 1)
        self.assertEqual(self.data.papers(feedback="none")["total"], 0)
        self.assertEqual(self.data.papers(feedback="reject")["total"], 0)

    def test_filter_follows_the_newest_verdict_only(self):
        self.data.record_feedback(self.paper_id, "keep")
        self.data.record_feedback(self.paper_id, "reject")

        self.assertEqual(self.data.papers(feedback="reject")["total"], 1)
        self.assertEqual(self.data.papers(feedback="keep")["total"], 0)

    def test_overview_counts_each_paper_once(self):
        self.data.record_feedback(self.paper_id, "keep")
        self.data.record_feedback(self.paper_id, "maybe")
        overview = self.data.overview(days=7)
        counts = {entry["value"]: entry["papers"] for entry in overview["feedback"]}

        self.assertEqual(overview["judged_papers"], 1)
        self.assertEqual(counts["maybe"], 1)
        self.assertEqual(counts["keep"], 0)
        self.assertEqual(counts["none"], 0)

    def test_deleting_a_paper_removes_its_feedback(self):
        self.data.record_feedback(self.paper_id, "keep")
        with self.data.store.connect() as connection:
            connection.execute("DELETE FROM papers WHERE id = ?", (self.paper_id,))

        self.assertEqual(self.data.store.feedback_history(self.paper_id), [])


class QueryIdTest(unittest.TestCase):
    def test_parses_kind_index_and_text(self):
        parsed = parse_query_id('daily:3:"non-terrestrial network" handover')
        self.assertEqual(parsed["kind"], "daily")
        self.assertEqual(parsed["index"], "3")
        self.assertEqual(parsed["text"], '"non-terrestrial network" handover')

    def test_tolerates_a_malformed_identifier(self):
        self.assertEqual(parse_query_id("legacy-query")["text"], "legacy-query")


class DashboardServerTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.settings = make_settings(Path(self._directory.name))
        self.paper_id = seed(self.settings)
        self.httpd = build_server(self.settings, "127.0.0.1", 0)
        self.addCleanup(self.httpd.server_close)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def get(self, path: str, host: str | None = None):
        request = urllib.request.Request(self.base + path)
        if host:
            request.add_header("Host", host)
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()

    def test_serves_the_index_page(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Paper Radar", body)

    def test_api_endpoints_return_json(self):
        for path in ("/api/overview", "/api/papers", "/api/queries", "/api/scoring",
                     "/api/trends", "/api/reports", "/api/filters"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIsInstance(json.loads(body), dict, path)

    def test_paper_detail_endpoint(self):
        status, body = self.get(f"/api/papers/{self.paper_id}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["id"], self.paper_id)

    def test_missing_paper_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/papers/9999")
        self.assertEqual(caught.exception.code, 404)

    def test_simulator_endpoint_scores_free_text(self):
        status, body = self.get("/api/simulate?title=MARL%20routing&abstract=LEO%20satellite")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertGreater(payload["score"], 0)
        self.assertTrue(payload["relevant"])

    def test_non_loopback_host_header_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/overview", host="radar.example.com")
        self.assertEqual(caught.exception.code, 403)

    def test_static_paths_cannot_escape_the_static_directory(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/static/../../storage.py")
        self.assertEqual(caught.exception.code, 404)

    def post(self, path: str, body, headers: dict[str, str] | None = None):
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=payload, method="POST")
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_feedback_round_trip(self):
        status, body = self.post(f"/api/papers/{self.paper_id}/feedback", {"value": "keep"})
        self.assertEqual(status, 200)
        self.assertEqual(body["feedback"], "keep")

        _, detail = self.get(f"/api/papers/{self.paper_id}")
        self.assertEqual(json.loads(detail)["feedback"], "keep")

        _, cleared = self.post(f"/api/papers/{self.paper_id}/feedback", {"value": None})
        self.assertIsNone(cleared["feedback"])

    def test_feedback_rejects_an_invalid_value(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(f"/api/papers/{self.paper_id}/feedback", {"value": "starred"})
        self.assertEqual(caught.exception.code, 400)

    def test_feedback_rejects_an_unknown_paper(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/papers/9999/feedback", {"value": "keep"})
        self.assertEqual(caught.exception.code, 404)

    def test_cross_origin_writes_are_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(
                f"/api/papers/{self.paper_id}/feedback",
                {"value": "keep"},
                {"Origin": "https://evil.example.com"},
            )
        self.assertEqual(caught.exception.code, 403)

    def test_same_origin_writes_are_allowed(self):
        status, _ = self.post(
            f"/api/papers/{self.paper_id}/feedback",
            {"value": "keep"},
            {"Origin": f"http://127.0.0.1:{self.httpd.server_address[1]}"},
        )
        self.assertEqual(status, 200)

    def test_form_content_types_are_refused(self):
        """A cross-site form POST cannot set application/json, so reject the rest."""
        payload = b"value=keep"
        request = urllib.request.Request(
            f"{self.base}/api/papers/{self.paper_id}/feedback", data=payload, method="POST"
        )
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 403)

    def test_preflight_is_not_answered(self):
        request = urllib.request.Request(
            f"{self.base}/api/papers/{self.paper_id}/feedback", method="OPTIONS"
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertIn(caught.exception.code, (405, 501))

    def test_post_to_an_unknown_route_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/overview", {"value": "keep"})
        self.assertEqual(caught.exception.code, 404)

    def test_oversized_bodies_are_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(f"/api/papers/{self.paper_id}/feedback", {"value": "keep", "pad": "x" * 5000})
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
