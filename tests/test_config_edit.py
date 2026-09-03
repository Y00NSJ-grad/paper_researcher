from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from radar.config import (
    AXES,
    config_token,
    flow_style_tags,
    render_axis_block,
    replace_block,
    replace_queries_block,
    validate_axes,
    validate_queries,
    write_axes,
    write_queries,
)

SAMPLE = """\
methods:
  marl:
    weight: 14
    terms: [MARL, multi-agent reinforcement learning]

domains:
  ntn_satellite:
    weight: 18
    terms:
      - LEO satellite
      - non-terrestrial network

tasks:
  routing:
    weight: 6
    terms: [routing]

# A comment that must survive an edit.
queries:
  daily:
    - '"space-air-ground integrated network" reinforcement learning'
    - 'SAGIN graph neural network routing'
  weekly:
    - 'digital twin 6G reinforcement learning'

# Anchors widen the arXiv net.
extra_anchors:
  - vision-language-action

scoring:
  minimum_relevant: 20
  max_score: 100
"""


def write_sample(directory: Path, text: str = SAMPLE) -> Path:
    path = directory / "keywords.yml"
    path.write_text(text, encoding="utf-8")
    return path


class ValidateQueriesTest(unittest.TestCase):
    def test_trims_and_fills_missing_groups(self):
        cleaned = validate_queries({"daily": ["  a query  "]})
        self.assertEqual(cleaned, {"daily": ["a query"], "weekly": []})

    def test_rejects_bad_input(self):
        for queries, error in (
            ({"monthly": ["x"]}, ValueError),
            ({"daily": [""]}, ValueError),
            ({"daily": ["   "]}, ValueError),
            ({"daily": ["line\nbreak"]}, ValueError),
            ({"daily": ["a", "a"]}, ValueError),
            ({"daily": ['unbalanced "quote']}, ValueError),
            ({"daily": ["x" * 301]}, ValueError),
            ({"daily": [7]}, TypeError),
            ({"daily": "not a list"}, TypeError),
        ):
            with self.subTest(queries=queries), self.assertRaises(error):
                validate_queries(queries)


class ReplaceQueriesBlockTest(unittest.TestCase):
    def test_an_unchanged_rewrite_is_byte_identical(self):
        parsed = yaml.safe_load(SAMPLE)
        rewritten = replace_queries_block(SAMPLE, validate_queries(parsed["queries"]))
        self.assertEqual(rewritten, SAMPLE)

    def test_comments_and_other_sections_survive(self):
        queries = {"daily": ["only daily"], "weekly": []}
        rewritten = replace_queries_block(SAMPLE, validate_queries(queries))

        self.assertIn("# A comment that must survive an edit.", rewritten)
        self.assertIn("# Anchors widen the arXiv net.", rewritten)
        self.assertIn("terms: [MARL, multi-agent reinforcement learning]", rewritten)
        parsed = yaml.safe_load(rewritten)
        self.assertEqual(parsed["queries"], {"daily": ["only daily"], "weekly": []})
        self.assertEqual(parsed["extra_anchors"], ["vision-language-action"])
        self.assertEqual(parsed["scoring"]["minimum_relevant"], 20)

    def test_quotes_inside_a_query_round_trip(self):
        tricky = ["it's a \"quoted phrase\" query", "plain one"]
        rewritten = replace_queries_block(SAMPLE, validate_queries({"daily": tricky}))
        self.assertEqual(yaml.safe_load(rewritten)["queries"]["daily"], tricky)

    def test_an_empty_group_is_rendered_as_an_empty_list(self):
        rewritten = replace_queries_block(SAMPLE, validate_queries({"daily": [], "weekly": []}))
        self.assertEqual(yaml.safe_load(rewritten)["queries"], {"daily": [], "weekly": []})

    def test_a_config_without_a_queries_block_gains_one(self):
        text = "scoring:\n  max_score: 100\n"
        rewritten = replace_queries_block(text, validate_queries({"daily": ["new"]}))
        parsed = yaml.safe_load(rewritten)
        self.assertEqual(parsed["queries"]["daily"], ["new"])
        self.assertEqual(parsed["scoring"]["max_score"], 100)


class AxisBlockTest(unittest.TestCase):
    def axes_of(self, text: str) -> dict:
        parsed = yaml.safe_load(text)
        return {
            axis: [
                {"tag": tag, "weight": body["weight"], "terms": body["terms"]}
                for tag, body in (parsed.get(axis) or {}).items()
            ]
            for axis in AXES
        }

    def rewrite(self, text: str, axes: dict) -> str:
        cleaned = validate_axes(axes)
        for axis, tags in cleaned.items():
            text = replace_block(
                text, axis, render_axis_block(axis, tags, flow_style_tags(text, axis))
            )
        return text

    def test_an_unchanged_rewrite_is_byte_identical(self):
        self.assertEqual(self.rewrite(SAMPLE, self.axes_of(SAMPLE)), SAMPLE)

    def test_inline_terms_keep_their_style(self):
        axes = self.axes_of(SAMPLE)
        axes["tasks"][0]["terms"].append("route optimization")
        rewritten = self.rewrite(SAMPLE, axes)

        self.assertIn("    terms: [routing, route optimization]", rewritten)
        # A block-style tag stays block style.
        self.assertIn("      - LEO satellite", rewritten)

    def test_a_term_needing_quotes_forces_block_style(self):
        axes = self.axes_of(SAMPLE)
        axes["tasks"][0]["terms"].append("sensing, communication, and computation")
        rewritten = self.rewrite(SAMPLE, axes)

        # A comma cannot survive inline, so this tag drops to block style.
        self.assertNotIn("terms: [routing", rewritten)
        self.assertEqual(
            yaml.safe_load(rewritten)["tasks"]["routing"]["terms"],
            ["routing", "sensing, communication, and computation"],
        )

    def test_terms_that_would_parse_as_other_types_are_quoted(self):
        axes = self.axes_of(SAMPLE)
        axes["methods"][0]["terms"] = ["5", "true", "no", "on", "MARL"]
        rewritten = self.rewrite(SAMPLE, axes)

        self.assertEqual(
            yaml.safe_load(rewritten)["methods"]["marl"]["terms"],
            ["5", "true", "no", "on", "MARL"],
        )

    def test_an_emptied_axis_renders_as_an_empty_mapping(self):
        rewritten = self.rewrite(SAMPLE, {**self.axes_of(SAMPLE), "methods": []})
        self.assertEqual(yaml.safe_load(rewritten)["methods"], {})

    def test_comments_and_queries_survive_a_tag_edit(self):
        axes = self.axes_of(SAMPLE)
        axes["domains"].append({"tag": "new_domain", "weight": 9, "terms": ["fresh"]})
        rewritten = self.rewrite(SAMPLE, axes)

        self.assertIn("# A comment that must survive an edit.", rewritten)
        self.assertIn("# Anchors widen the arXiv net.", rewritten)
        parsed = yaml.safe_load(rewritten)
        self.assertEqual(parsed["domains"]["new_domain"], {"weight": 9, "terms": ["fresh"]})
        self.assertEqual(len(parsed["queries"]["daily"]), 2)


class WriteAxesTest(unittest.TestCase):
    def test_only_changed_axes_are_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_sample(Path(directory))
            axes = {
                "methods": [{"tag": "marl", "weight": 14, "terms": ["MARL"]}],
                "domains": [{"tag": "ntn", "weight": 18, "terms": ["LEO satellite"]}],
                "tasks": [{"tag": "routing", "weight": 6, "terms": ["routing"]}],
            }
            write_axes(path, axes)
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))

            self.assertEqual(parsed["methods"]["marl"]["terms"], ["MARL"])
            self.assertEqual(parsed["domains"]["ntn"]["weight"], 18)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_a_rejected_edit_leaves_the_file_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_sample(Path(directory))
            before = path.read_text(encoding="utf-8")

            with self.assertRaises(ValueError):
                write_axes(path, {"methods": [{"tag": "x", "weight": 1, "terms": []}]})

            self.assertEqual(path.read_text(encoding="utf-8"), before)


class WriteQueriesTest(unittest.TestCase):
    def test_writes_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_sample(Path(directory))
            write_queries(path, {"daily": ["fresh query"], "weekly": []})

            self.assertEqual(
                yaml.safe_load(path.read_text(encoding="utf-8"))["queries"]["daily"],
                ["fresh query"],
            )
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_a_rejected_edit_leaves_the_file_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_sample(Path(directory))
            before = path.read_text(encoding="utf-8")

            with self.assertRaises(ValueError):
                write_queries(path, {"daily": ["duplicate", "duplicate"]})

            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_the_token_changes_only_when_the_file_does(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_sample(Path(directory))
            token = config_token(path)

            self.assertEqual(config_token(path), token)
            write_queries(path, {"daily": ["changed"], "weekly": []})
            self.assertNotEqual(config_token(path), token)

    def test_a_missing_file_has_no_token(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(config_token(Path(directory) / "absent.yml"), "")


if __name__ == "__main__":
    unittest.main()
