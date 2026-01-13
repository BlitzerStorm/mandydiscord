def test_manual_exists_and_has_quick_start():
    import os
    p = os.path.join(os.path.dirname(__file__), "..", "docs", "MANDY_MANUAL.md")
    p = os.path.normpath(p)
    assert os.path.exists(p), f"Manual missing: {p}"
    with open(p, "r", encoding="utf-8") as f:
        text = f.read()
    assert "Quick Start" in text
    assert "Extension Development" in text
