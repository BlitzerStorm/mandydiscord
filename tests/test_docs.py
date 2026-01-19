import os
import unittest


class DocsTests(unittest.TestCase):
    def test_manual_exists_and_has_quick_start(self):
        p = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "docs", "MANDY_MANUAL.md"))
        self.assertTrue(os.path.exists(p), f"Manual missing: {p}")
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Quick Start", text)
        self.assertIn("Extension Development", text)


if __name__ == "__main__":
    unittest.main()
