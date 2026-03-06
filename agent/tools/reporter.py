"""
reporter.py — Report generation tools for the VOC Agent.

Generates structured action-item reports for Product, Marketing,
and Support teams based on the review database.

(Full implementation to follow — stubs for now.)
"""

import os
import json
from datetime import datetime, timezone

from rich.console import Console
from rich.markdown import Markdown

from agent.tools.database import (
    get_review_count,
    get_sentiment_breakdown,
    get_theme_frequency,
    get_reviews,
    get_reviews_since,
)
from agent.tools.nlp_processor import get_theme_insights, VALID_THEMES

console = Console()

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


def generate_global_action_report(product_a_id, product_b_id):
    """Generate a comprehensive action-item report for all teams.

    Analyzes sentiment and theme data across both products and produces
    a markdown report with recommendations for Product, Marketing,
    and Support teams.

    Args:
        product_a_id: Identifier for product A.
        product_b_id: Identifier for product B.

    Returns:
        The full markdown report as a string. Also saves to reports/ folder.
    """
    insights_a = get_theme_insights(product_a_id)
    insights_b = get_theme_insights(product_b_id)
    insights_all = get_theme_insights()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    report = f"""# VOC Global Action Report
**Generated:** {timestamp}

---

## Overall Summary
- **Total reviews in DB:** {insights_all['total_reviews']}
- **Product A ({product_a_id}):** {insights_a['total_reviews']} reviews
- **Product B ({product_b_id}):** {insights_b['total_reviews']} reviews

## Sentiment Overview

| Metric | Product A | Product B |
|--------|-----------|-----------|
| Positive | {insights_a['sentiment_breakdown']['Positive']} | {insights_b['sentiment_breakdown']['Positive']} |
| Negative | {insights_a['sentiment_breakdown']['Negative']} | {insights_b['sentiment_breakdown']['Negative']} |
| Neutral | {insights_a['sentiment_breakdown']['Neutral']} | {insights_b['sentiment_breakdown']['Neutral']} |

## Theme Frequency (All Products)

"""
    for theme, count in insights_all.get("theme_frequency", {}).items():
        report += f"- **{theme}**: {count} mentions\n"

    report += f"""
## 🔧 Product Team Actions
- **Top negative themes (Product A):** {', '.join(insights_a.get('top_negative_themes', ['N/A']))}
- **Top negative themes (Product B):** {', '.join(insights_b.get('top_negative_themes', ['N/A']))}
- Investigate reviews tagged with the above themes for hardware/feature issues.

## 📣 Marketing Team Actions
- **Top positive themes (Product A):** {', '.join(insights_a.get('top_positive_themes', ['N/A']))}
- **Top positive themes (Product B):** {', '.join(insights_b.get('top_positive_themes', ['N/A']))}
- Leverage these themes in messaging and campaigns.

## 🎧 Support Team Actions
- Review the most frequent negative themes for FAQ and troubleshooting updates.
- Prioritize themes with highest negative counts across both products.

---
*Report saved to reports/ folder.*
"""

    # Save to file
    filename = f"global_report_{timestamp}.md"
    filepath = os.path.join(REPORTS_DIR, filename)
    with open(filepath, "w") as f:
        f.write(report)

    return report


def generate_weekly_delta_report(product_a_id, product_b_id, since_date=None):
    """Generate a delta report for reviews added since a given date.

    Focuses on new reviews only, highlighting emerging themes and
    sentiment shifts.

    Args:
        product_a_id: Identifier for product A.
        product_b_id: Identifier for product B.
        since_date: ISO date string to filter from. Defaults to last 7 days.

    Returns:
        The markdown delta report as a string. Also saves to reports/ folder.
    """
    if not since_date:
        from datetime import timedelta
        since_date = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    new_a = get_reviews_since(since_date, product_a_id)
    new_b = get_reviews_since(since_date, product_b_id)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    report = f"""# VOC Weekly Delta Report
**Generated:** {timestamp}
**Since:** {since_date}

---

## New Reviews This Period
- **Product A ({product_a_id}):** {len(new_a)} new reviews
- **Product B ({product_b_id}):** {len(new_b)} new reviews
- **Total new:** {len(new_a) + len(new_b)}

## Sentiment of New Reviews

### Product A
"""
    # Count sentiments for new reviews
    for product_label, new_reviews in [("A", new_a), ("B", new_b)]:
        pos = sum(1 for r in new_reviews if r.get("sentiment") == "Positive")
        neg = sum(1 for r in new_reviews if r.get("sentiment") == "Negative")
        neu = sum(1 for r in new_reviews if r.get("sentiment") == "Neutral")
        report += f"- Positive: {pos} | Negative: {neg} | Neutral: {neu}\n"
        if product_label == "A":
            report += f"\n### Product B\n"

    report += f"""
## Emerging Themes
*Review new reviews for any emerging patterns not seen in previous periods.*

---
*Delta report saved to reports/ folder.*
"""

    filename = f"weekly_delta_{timestamp}.md"
    filepath = os.path.join(REPORTS_DIR, filename)
    with open(filepath, "w") as f:
        f.write(report)

    return report


def print_report_to_console(report_text):
    """Pretty-print a markdown report to the console using rich.

    Args:
        report_text: The markdown report string to display.
    """
    console.print(Markdown(report_text))


def get_reports_list():
    """List all generated report files in the reports/ directory.

    Returns:
        A list of dicts with 'filename' and 'path' for each report.
    """
    reports = []
    if os.path.exists(REPORTS_DIR):
        for f in sorted(os.listdir(REPORTS_DIR), reverse=True):
            if f.endswith(".md"):
                reports.append({
                    "filename": f,
                    "path": os.path.join(REPORTS_DIR, f),
                })
    return reports
