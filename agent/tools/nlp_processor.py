"""
nlp_processor.py — NLP classification tools for the VOC Agent.

Uses Groq API with llama-3.1-8b-instant to classify product reviews
by sentiment and theme. Supports single, batch, and bulk processing
modes with rate-limit-friendly delays and retry logic.
"""

import os
import json
import time
import logging

from groq import Groq
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
from rich.progress import track

from agent.tools.database import (
    get_unprocessed_reviews,
    update_review_nlp,
    get_review_count,
    get_theme_frequency,
    get_sentiment_breakdown,
    get_reviews,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
NLP_MODEL = "llama-3.1-8b-instant"

VALID_THEMES = [
    "Sound Quality",
    "Battery Life",
    "Comfort & Fit",
    "ANC",
    "App Experience",
    "Price & Value",
    "Build Quality",
    "Delivery",
    "Customer Support",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Single Review Classification
# ---------------------------------------------------------------------------


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def classify_single_review(review_title, review_text):
    """Classify a single review's sentiment and themes using Groq LLM.

    Sends the review to llama-3.1-8b-instant and parses the JSON response.
    Retries up to 3 times with exponential backoff on failure.

    Args:
        review_title: The title/headline of the review.
        review_text: The full body text of the review.

    Returns:
        A dict with keys: sentiment (str), themes (list[str]), confidence (float).
        On parse failure, returns a safe default: Neutral / [] / 0.0.
    """
    try:
        response = client.chat.completions.create(
            model=NLP_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a product review classifier. "
                        "Respond with ONLY a valid JSON object. "
                        "No markdown, no explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Classify this review:\n"
                        f"Title: {review_title}\n"
                        f"Text: {review_text[:500]}\n\n"
                        f"Respond with ONLY:\n"
                        f'{{"sentiment": "Positive"|"Negative"|"Neutral", '
                        f'"themes": [1-3 strings from {VALID_THEMES}], '
                        f'"confidence": 0.0-1.0}}'
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=200,
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(raw)

        # Validate and sanitize
        if result.get("sentiment") not in ("Positive", "Negative", "Neutral"):
            result["sentiment"] = "Neutral"
        result["themes"] = [t for t in result.get("themes", []) if t in VALID_THEMES]
        result["confidence"] = float(result.get("confidence", 0.0))

        return result

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to parse LLM response: %s", e)
        return {"sentiment": "Neutral", "themes": [], "confidence": 0.0}


# ---------------------------------------------------------------------------
# 2. Batch Classification (up to 5 at a time)
# ---------------------------------------------------------------------------


def classify_batch(reviews_batch):
    """Classify a batch of up to 5 reviews in a single LLM call.

    Sends all reviews in one prompt requesting a JSON array of results.
    If the batch call fails, falls back to classify_single_review() for
    each item individually.

    Args:
        reviews_batch: A list of dicts with keys: id, review_title, review_text.

    Returns:
        A list of dicts, each with: id, sentiment, themes, confidence.
    """
    # Build the batch prompt
    reviews_text = ""
    for i, r in enumerate(reviews_batch):
        reviews_text += (
            f"\nReview {i + 1} (ID: {r['id']}):\n"
            f"Title: {r.get('review_title', '')}\n"
            f"Text: {r.get('review_text', '')[:500]}\n"
        )

    try:
        response = client.chat.completions.create(
            model=NLP_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a product review classifier. "
                        "Respond with ONLY a valid JSON array. "
                        "No markdown, no explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Classify these {len(reviews_batch)} reviews:\n"
                        f"{reviews_text}\n\n"
                        f"Respond with ONLY a JSON array, one object per review:\n"
                        f'[{{"id": <review_id>, "sentiment": "Positive"|"Negative"|"Neutral", '
                        f'"themes": [1-3 strings from {VALID_THEMES}], '
                        f'"confidence": 0.0-1.0}}, ...]'
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=800,
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        results = json.loads(raw)

        if not isinstance(results, list):
            raise ValueError("Expected a JSON array from batch response")

        # Validate each result
        validated = []
        for result in results:
            if result.get("sentiment") not in ("Positive", "Negative", "Neutral"):
                result["sentiment"] = "Neutral"
            result["themes"] = [t for t in result.get("themes", []) if t in VALID_THEMES]
            result["confidence"] = float(result.get("confidence", 0.0))
            validated.append(result)

        return validated

    except Exception as e:
        logger.warning("Batch classification failed (%s), falling back to single.", e)

        # Fallback: classify each review individually
        fallback_results = []
        for r in reviews_batch:
            single = classify_single_review(
                r.get("review_title", ""), r.get("review_text", "")
            )
            single["id"] = r["id"]
            fallback_results.append(single)

        return fallback_results


# ---------------------------------------------------------------------------
# 3. Process Unprocessed Reviews
# ---------------------------------------------------------------------------


def process_unprocessed_reviews(batch_size=50):
    """Fetch and classify unprocessed reviews in batches of 5.

    Retrieves up to batch_size reviews where sentiment IS NULL, groups
    them into mini-batches of 5, and calls classify_batch() for each.
    Updates the database after each classification. Sleeps 1 second
    between batches to respect Groq free-tier rate limits.

    Args:
        batch_size: Maximum number of reviews to process (default 50).

    Returns:
        A dict: {"processed": N, "failed": M}
    """
    reviews = get_unprocessed_reviews(limit=batch_size)

    if not reviews:
        logger.info("No unprocessed reviews found.")
        return {"processed": 0, "failed": 0}

    processed = 0
    failed = 0

    # Group into mini-batches of 5
    mini_batches = [reviews[i : i + 5] for i in range(0, len(reviews), 5)]

    for batch in track(mini_batches, description="Classifying reviews..."):
        try:
            results = classify_batch(batch)

            for result in results:
                try:
                    review_id = result.get("id")
                    sentiment = result.get("sentiment", "Neutral")
                    themes = ", ".join(result.get("themes", []))
                    update_review_nlp(review_id, sentiment, themes)
                    processed += 1
                except Exception as e:
                    logger.error("Failed to update review %s: %s", result.get("id"), e)
                    failed += 1

        except Exception as e:
            logger.error("Batch processing error: %s", e)
            failed += len(batch)

        # Rate-limit-friendly delay
        time.sleep(1)

    return {"processed": processed, "failed": failed}


# ---------------------------------------------------------------------------
# 4. Process All Reviews
# ---------------------------------------------------------------------------


def process_all_reviews():
    """Continuously process all unprocessed reviews until none remain.

    Calls process_unprocessed_reviews(50) in a loop, stopping when
    zero reviews are returned.

    Returns:
        The total number of reviews processed across all iterations.
    """
    total_processed = 0

    while True:
        result = process_unprocessed_reviews(batch_size=50)
        batch_count = result["processed"]

        if batch_count == 0:
            break

        total_processed += batch_count
        logger.info("Processed %d so far (total: %d)", batch_count, total_processed)

    print(f"Total reviews processed: {total_processed}")
    return total_processed


# ---------------------------------------------------------------------------
# 5. Theme Insights
# ---------------------------------------------------------------------------


def get_theme_insights(product_id=None):
    """Generate a comprehensive theme and sentiment insights report.

    Combines review counts, sentiment breakdown, theme frequency, and
    identifies the top positive and negative themes.

    Args:
        product_id: If provided, restrict insights to this product.
                    Otherwise, report across all products.

    Returns:
        A dict with: product_id, total_reviews, sentiment_breakdown,
        theme_frequency, top_negative_themes, top_positive_themes.
    """
    total = get_review_count(product_id)
    sentiment = get_sentiment_breakdown(product_id)
    themes = get_theme_frequency(product_id)

    # Compute per-theme sentiment to find top positive / negative themes
    positive_theme_counts = {}
    negative_theme_counts = {}

    for theme_name in VALID_THEMES:
        pos_reviews = get_reviews(product_id=product_id, sentiment="Positive", theme=theme_name)
        neg_reviews = get_reviews(product_id=product_id, sentiment="Negative", theme=theme_name)
        positive_theme_counts[theme_name] = len(pos_reviews)
        negative_theme_counts[theme_name] = len(neg_reviews)

    top_positive = sorted(
        positive_theme_counts.items(), key=lambda x: x[1], reverse=True
    )[:3]
    top_negative = sorted(
        negative_theme_counts.items(), key=lambda x: x[1], reverse=True
    )[:3]

    return {
        "product_id": product_id or "all",
        "total_reviews": total,
        "sentiment_breakdown": sentiment,
        "theme_frequency": themes,
        "top_positive_themes": [t[0] for t in top_positive if t[1] > 0],
        "top_negative_themes": [t[0] for t in top_negative if t[1] > 0],
    }


# ---------------------------------------------------------------------------
# 6. Product Comparison on a Theme
# ---------------------------------------------------------------------------


def compare_products_on_theme(theme, product_a_id, product_b_id):
    """Compare two products on a specific theme dimension.

    For each product, retrieves reviews tagged with the given theme,
    computes a sentiment score, and selects sample review texts.

    Sentiment score = (positive - negative) / total.  Returns 0 if no
    reviews exist for the theme.

    Args:
        theme: The theme tag to compare on (e.g. "Sound Quality").
        product_a_id: Identifier for product A.
        product_b_id: Identifier for product B.

    Returns:
        A dict with per-product stats, a winner determination, and
        the margin of victory.
    """

    def _product_stats(pid):
        all_reviews = get_reviews(product_id=pid, theme=theme)
        total = len(all_reviews)
        positive = [r for r in all_reviews if r.get("sentiment") == "Positive"]
        negative = [r for r in all_reviews if r.get("sentiment") == "Negative"]
        neutral = [r for r in all_reviews if r.get("sentiment") == "Neutral"]

        score = (len(positive) - len(negative)) / total if total > 0 else 0.0

        return {
            "id": pid,
            "total": total,
            "positive": len(positive),
            "negative": len(negative),
            "neutral": len(neutral),
            "sentiment_score": round(score, 4),
            "sample_positive": [r["review_text"][:200] for r in positive[:2]],
            "sample_negative": [r["review_text"][:200] for r in negative[:2]],
        }

    stats_a = _product_stats(product_a_id)
    stats_b = _product_stats(product_b_id)

    score_a = stats_a["sentiment_score"]
    score_b = stats_b["sentiment_score"]

    if score_a > score_b:
        winner = "product_a"
    elif score_b > score_a:
        winner = "product_b"
    else:
        winner = "tie"

    return {
        "theme": theme,
        "product_a": stats_a,
        "product_b": stats_b,
        "winner": winner,
        "margin": round(abs(score_a - score_b), 2),
    }
