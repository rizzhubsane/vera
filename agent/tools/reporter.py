"""
reporter.py — Report generation tools for the VOC Agent.

Generates structured action-item reports for Product, Marketing,
and Support teams based on the review database.

(Full implementation to follow — stubs for now.)
"""

import os
import json
from datetime import datetime, timezone
from groq import Groq
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
    """Generate a comprehensive action-item report for all teams using Groq."""
    insights_a = get_theme_insights(product_a_id)
    insights_b = get_theme_insights(product_b_id)
    insights_all = get_theme_insights()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    
    # Collect raw data as JSON to inject into prompt
    db_data = json.dumps({
        "all_product_insights": insights_all,
        "product_a_insights": insights_a,
        "product_b_insights": insights_b
    }, indent=2)

    prompt = f"""
CRITICAL REQUIREMENTS FOR EVERY ACTION ITEM:
1. Must name the specific theme it relates to (e.g., ANC, Battery Life, Build Quality)
2. Must cite the exact review count: e.g., '67 of 243 reviews (28%) rate this Negative'
3. Must give one concrete recommendation, not vague advice
4. Bad example: 'Improve sound quality' 
5. Good example: 'Sound Quality [Product Team]: 89 of 312 Master Buds 1 reviews (29%) cite distortion at high volumes. Recommend hardware review of driver tuning — 34 reviews specifically mention crackling above 70% volume.'
Apply this format to every single action item across all three teams.

You are Vera, a Senior Consumer Insights Analyst. 
Write a Global Action Report based ONLY on the following JSON SQLite database export.
The report must include these markdown sections:
# VOC Global Action Report
## Executive Summary
## 🔧 Product Team Actions
## 📣 Marketing Team Actions
## 🎧 Support Team Actions

Here is the database export:
{db_data}
"""

    client = Groq()
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=4000
    )
    
    report_content = response.choices[0].message.content
    
    report = f"{report_content}\n\n---\n*Report generated on {timestamp}*\n*Full report saved to reports/global_action_report.md*"

    # Save to file (PRD explicitly requires this exact filename for the final run)
    filepath = os.path.join(REPORTS_DIR, "global_action_report.md")
    with open(filepath, "w") as f:
        f.write(report)

    return report


def generate_weekly_delta_report(product_a_id, product_b_id, since_date=None):
    """Generate a delta report for reviews added since a given date using Groq."""
    if not since_date:
        from datetime import timedelta
        since_date = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    new_a = get_reviews_since(since_date, product_a_id)
    new_b = get_reviews_since(since_date, product_b_id)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    
    # Collect raw data as JSON to inject into prompt
    db_data = json.dumps({
        "product_a_new_reviews": new_a,
        "product_b_new_reviews": new_b,
        "total_new_reviews": len(new_a) + len(new_b)
    }, indent=2)

    prompt = f"""
CRITICAL REQUIREMENTS FOR EVERY ACTION ITEM:
1. Must name the specific theme it relates to (e.g., ANC, Battery Life, Build Quality)
2. Must cite the exact review count: e.g., '67 of 243 reviews (28%) rate this Negative'
3. Must give one concrete recommendation, not vague advice
4. Bad example: 'Improve sound quality' 
5. Good example: 'Sound Quality [Product Team]: 89 of 312 Master Buds 1 reviews (29%) cite distortion at high volumes. Recommend hardware review of driver tuning — 34 reviews specifically mention crackling above 70% volume.'
Apply this format to every single action item across all three teams.

You are Vera, a Senior Consumer Insights Analyst. 
Write a Weekly Delta Report based ONLY on the following JSON SQLite database export of NEW reviews since {since_date}.
The report must include these markdown sections:
# VOC Weekly Delta Report
## Emerging Themes (New This Week)
## 🔧 Product Team Actions
## 📣 Marketing Team Actions
## 🎧 Support Team Actions

Here is the database export of new reviews:
{db_data}
"""

    client = Groq()
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=4000
    )
    
    report_content = response.choices[0].message.content
    
    report = f"{report_content}\n\n---\n*Report generated on {timestamp}*\n*Full report saved to reports/weekly_delta_report.md*"

    filename = "weekly_delta_report.md"
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
