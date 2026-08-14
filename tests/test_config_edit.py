from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from radar.config import (
    config_token,
    replace_queries_block,
    validate_queries,
    write_queries,
)

SAMPLE = """\
methods:
  marl:
    weight: 14
    terms: [MARL, multi-agent reinforcement learning]

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
