import unittest

from radar.text import contains_term, normalize_arxiv_id, normalize_doi, normalize_title


class TextTest(unittest.TestCase):
    def test_normalizers(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1000/ABC"), "10.1000/abc")
        self.assertEqual(normalize_arxiv_id("https://arxiv.org/abs/2401.12345v3"), "2401.12345")
        self.assertEqual(normalize_title("Graph-Based  NTN!"), "graph based ntn")

    def test_term_matching_uses_word_boundaries(self):
        self.assertTrue(contains_term("A GNN for NTN routing", "NTN"))
        self.assertFalse(contains_term("attention routing", "NTN"))


if __name__ == "__main__":
    unittest.main()

