"""
database.py — Database operations for the VOC Agent.

All functions in this module are designed to be registered as tools
for an LLM agent. They provide full CRUD + analytics access to the
SQLite reviews database at database/reviews.db.
"""

import os
import sqlite3
from datetime import datetime, timezone
from collections import Counter

# Resolve the DB path relative to the project root
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "database", "reviews.db")
DB_PATH = os.path.normpath(DB_PATH)


def initialize_database():
    """Create the SQLite database and tables if they don't already exist.

    Tables created:
        - reviews: Stores individual product reviews with sentiment/theme metadata.
        - scrape_runs: Logs each scraping session with counts and notes.

    A UNIQUE constraint on (product_id, review_title, review_text) prevents
    duplicate reviews from being inserted.
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                review_title TEXT,
                review_text TEXT NOT NULL,
                rating REAL,
                review_date TEXT,
                source TEXT,
                sentiment TEXT,
                themes TEXT,
                scraped_at TEXT,
                UNIQUE(product_id, review_title, review_text)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT,
                product_id TEXT,
                new_reviews_count INTEGER,
                total_reviews_count INTEGER,
                notes TEXT
            )
        """)

        conn.commit()

    print("Database initialized successfully.")


def insert_review(product_id, product_name, review_title, review_text, rating, review_date, source):
    """Insert a single review into the database.

    Uses INSERT OR IGNORE so duplicate reviews (same product_id + review_title
    + review_text) are silently skipped.

    Args:
        product_id: Identifier like 'product_a' or 'product_b'.
        product_name: Human-readable product name.
        review_title: Title/headline of the review.
        review_text: Full body text of the review.
        rating: Numeric rating from 1.0 to 5.0.
        review_date: Date of the review in ISO format (YYYY-MM-DD).
        source: Platform source, e.g. 'amazon' or 'flipkart'.

    Returns:
        True if the review was inserted, False if it was a duplicate.
    """
    scraped_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO reviews
                (product_id, product_name, review_title, review_text, rating, review_date, source, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, product_name, review_title, review_text, rating, review_date, source, scraped_at),
        )
        conn.commit()
        return cursor.rowcount > 0


def bulk_insert_reviews(reviews_list):
    """Insert multiple reviews from a list of dictionaries.

    Each dict must contain keys matching the insert_review() parameters:
    product_id, product_name, review_title, review_text, rating,
    review_date, source.

    Args:
        reviews_list: A list of dicts, each representing one review.

    Returns:
        A dict with 'inserted' and 'duplicates' counts.
    """
    inserted = 0
    duplicates = 0

    for review in reviews_list:
        was_inserted = insert_review(
            product_id=review["product_id"],
            product_name=review["product_name"],
            review_title=review.get("review_title"),
            review_text=review["review_text"],
            rating=review.get("rating"),
            review_date=review.get("review_date"),
            source=review.get("source"),
        )
        if was_inserted:
            inserted += 1
        else:
            duplicates += 1

    return {"inserted": inserted, "duplicates": duplicates}


def get_reviews(product_id=None, sentiment=None, theme=None, limit=None):
    """Query reviews with optional filters.

    All parameters are optional. When omitted, no filtering is applied
    for that dimension.

    Args:
        product_id: Filter by product identifier.
        sentiment: Filter by sentiment label (Positive/Negative/Neutral).
        theme: Filter by a theme tag (checks comma-separated themes column).
        limit: Maximum number of reviews to return.

    Returns:
        A list of dicts, each representing a review row.
    """
    query = "SELECT * FROM reviews WHERE 1=1"
    params = []

    if product_id:
        query += " AND product_id = ?"
        params.append(product_id)
    if sentiment:
        query += " AND sentiment = ?"
        params.append(sentiment)
    if theme:
        query += " AND (',' || themes || ',') LIKE ?"
        params.append(f"%,{theme},%")
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def get_review_count(product_id=None):
    """Return the total count of reviews, optionally filtered by product.

    Args:
        product_id: If provided, count only reviews for this product.

    Returns:
        An integer count of matching reviews.
    """
    query = "SELECT COUNT(*) FROM reviews"
    params = []

    if product_id:
        query += " WHERE product_id = ?"
        params.append(product_id)

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()[0]


def update_review_nlp(review_id, sentiment, themes):
    """Update the sentiment and themes fields for a given review.

    Called after NLP processing to annotate a review with its
    classified sentiment and extracted theme tags.

    Args:
        review_id: The integer ID of the review to update.
        sentiment: One of 'Positive', 'Negative', or 'Neutral'.
        themes: Comma-separated string of theme tags.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE reviews SET sentiment = ?, themes = ? WHERE id = ?",
            (sentiment, themes, review_id),
        )
        conn.commit()


def get_unprocessed_reviews(limit=100):
    """Return reviews that have not yet been processed by NLP.

    These are reviews where the sentiment column is NULL.

    Args:
        limit: Maximum number of unprocessed reviews to return (default 100).

    Returns:
        A list of dicts, each representing an unprocessed review row.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM reviews WHERE sentiment IS NULL LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_reviews_since(date_str, product_id=None):
    """Return all reviews scraped after a given date.

    Args:
        date_str: An ISO-format date/datetime string. Reviews with
                  scraped_at greater than this value are returned.
        product_id: If provided, filter to this product only.

    Returns:
        A list of dicts, each representing a review row.
    """
    query = "SELECT * FROM reviews WHERE scraped_at > ?"
    params = [date_str]

    if product_id:
        query += " AND product_id = ?"
        params.append(product_id)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def log_scrape_run(product_id, new_count, total_count, notes=""):
    """Log a scraping session to the scrape_runs table.

    Args:
        product_id: The product that was scraped.
        new_count: Number of newly inserted reviews in this run.
        total_count: Total review count for this product after the run.
        notes: Optional free-text notes about the run.
    """
    run_date = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO scrape_runs (run_date, product_id, new_reviews_count, total_reviews_count, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_date, product_id, new_count, total_count, notes),
        )
        conn.commit()


def get_theme_frequency(product_id=None):
    """Calculate how often each theme tag appears across reviews.

    Parses the comma-separated themes column and counts occurrences.

    Args:
        product_id: If provided, restrict analysis to this product.

    Returns:
        A dict of {theme: count} sorted by count descending.
    """
    query = "SELECT themes FROM reviews WHERE themes IS NOT NULL"
    params = []

    if product_id:
        query += " AND product_id = ?"
        params.append(product_id)

    counter = Counter()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)

        for (themes_str,) in cursor.fetchall():
            if themes_str:
                for theme in themes_str.split(","):
                    theme = theme.strip()
                    if theme:
                        counter[theme] += 1

    return dict(counter.most_common())


def get_sentiment_breakdown(product_id=None):
    """Return a count of reviews by sentiment label.

    Args:
        product_id: If provided, restrict breakdown to this product.

    Returns:
        A dict like {"Positive": N, "Negative": N, "Neutral": N}.
    """
    query = "SELECT sentiment, COUNT(*) FROM reviews WHERE sentiment IS NOT NULL"
    params = []

    if product_id:
        query += " AND product_id = ?"
        params.append(product_id)

    query += " GROUP BY sentiment"

    breakdown = {"Positive": 0, "Negative": 0, "Neutral": 0}

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)

        for sentiment, count in cursor.fetchall():
            if sentiment in breakdown:
                breakdown[sentiment] = count

    return breakdown


def search_reviews_by_keyword(keyword, product_id=None):
    """Full-text search across review_text and review_title.

    Uses SQL LIKE for substring matching (case-insensitive).

    Args:
        keyword: The search term to look for.
        product_id: If provided, restrict search to this product.

    Returns:
        A list of dicts for matching review rows.
    """
    query = "SELECT * FROM reviews WHERE (review_text LIKE ? OR review_title LIKE ?)"
    pattern = f"%{keyword}%"
    params = [pattern, pattern]

    if product_id:
        query += " AND product_id = ?"
        params.append(product_id)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def get_last_scrape_date():
    """Return the most recent scrape run date from the scrape_runs table.

    Returns:
        The ISO datetime string of the last scrape, or 'Never' if no
        scraping has been performed yet.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(run_date) FROM scrape_runs")
        result = cursor.fetchone()[0]
        return result if result else "Never"


def export_to_csv(filepath=None):
    """Export all reviews to a CSV file.

    Args:
        filepath: Output CSV path. Defaults to data/initial_reviews.csv.

    Returns:
        The absolute path of the exported CSV file.
    """
    import csv

    if filepath is None:
        filepath = os.path.join(os.path.dirname(DB_PATH), "..", "data", "initial_reviews.csv")
        filepath = os.path.normpath(filepath)

    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM reviews")
        rows = cursor.fetchall()

        if not rows:
            return "No reviews to export."

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))

    return filepath


# --- Auto-initialize the database on import ---
if __name__ == "__main__":
    initialize_database()
