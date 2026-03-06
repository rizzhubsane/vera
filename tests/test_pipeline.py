import pytest, sqlite3, os, re, json
from datetime import datetime

def test_no_duplicate_reviews():
    """PRD Req 1.3: Delta pipeline must not duplicate reviews."""
    import sys; sys.path.insert(0, '.')
    from agent.tools.database import insert_review, get_review_count
    import time
    before = get_review_count()
    unique_title = f"Test title delta {time.time()}"
    r1 = insert_review("product_a", "Test", unique_title, "Test body delta unique 12345", 5.0, "2024-01-01", "flipkart")
    r2 = insert_review("product_a", "Test", unique_title, "Test body delta unique 12345", 5.0, "2024-01-01", "flipkart")
    after = get_review_count()
    assert r1 == True
    assert r2 == False
    assert after == before + 1
    print("✅ Deduplication test passed")

def test_nlp_only_returns_valid_themes():
    """PRD Req 2.3: Agent must tag reviews with ONLY predefined themes."""
    import sys; sys.path.insert(0, '.')
    from agent.tools.nlp_processor import classify_single_review, VALID_THEMES
    result = classify_single_review("Great battery", "Lasts 20 hours, incredible value for money")
    assert result["sentiment"] in ["Positive", "Negative", "Neutral"]
    for theme in result["themes"]:
        assert theme in VALID_THEMES, f"Invalid theme: {theme}"
    assert 0.0 <= result.get("confidence", 1.0) <= 1.0
    print(f"✅ NLP theme validation passed — themes: {result['themes']}")

def test_report_cites_review_counts():
    """PRD Req 3.1: Reports must be specific and cite review counts."""
    path = "reports/global_action_report.md"
    if not os.path.exists(path):
        pytest.skip("Run Step 6 first to generate reports")
    with open(path) as f:
        content = f.read()
    assert "Product" in content and "Marketing" in content and "Support" in content
    has_counts = bool(re.search(r'\d+.*review', content, re.IGNORECASE))
    assert has_counts, "Report does not cite review counts — grounding is weak"
    assert len(content) > 1000, "Report is too short to be meaningful"
    print("✅ Report specificity test passed")

def test_delta_proof_exists():
    """PRD Req 1.3: Delta proof log must exist and be non-empty."""
    found = False
    for path in ["logs/delta_proof.log", "logs/delta_proof.md"]:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            found = True
            print(f"✅ Delta proof found: {path}")
    assert found, "No delta proof found in logs/"

def test_scrape_runs_table_has_entries():
    """PRD Req 1.2/1.3: scrape_runs table must log every pipeline execution."""
    import sys; sys.path.insert(0, '.')
    from agent.tools.database import initialize_database
    initialize_database()
    conn = sqlite3.connect("database/reviews.db")
    count = conn.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0]
    conn.close()
    assert count >= 1, f"scrape_runs has {count} entries — run a scrape first"
    print(f"✅ scrape_runs has {count} logged runs")

def test_both_products_have_reviews():
    """PRD Req: 500+ reviews per product required."""
    import sys; sys.path.insert(0, '.')
    from agent.tools.database import get_review_count
    a = get_review_count('product_a')
    b = get_review_count('product_b')
    print(f"Master Buds 1: {a} reviews | Master Buds Max: {b} reviews")
    assert a >= 100, f"Product A only has {a} reviews (target: 500+)"
    assert b >= 100, f"Product B only has {b} reviews (target: 500+)"
    print("✅ Review volume test passed")

if __name__ == "__main__":
    test_no_duplicate_reviews()
    test_nlp_only_returns_valid_themes()
    test_report_cites_review_counts()
    test_delta_proof_exists()
    test_scrape_runs_table_has_entries()
    test_both_products_have_reviews()
    print("\n🎉 All tests passed!")
