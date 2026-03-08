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

import logging
import time
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from agent.tools.database import (
    get_review_count,
    get_sentiment_breakdown,
    get_theme_frequency,
    get_reviews,
    get_reviews_since,
)
from agent.tools.nlp_processor import get_theme_insights, VALID_THEMES

console = Console()
logger = logging.getLogger(__name__)

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


def notify_slack_dm(report_content: str, report_type: str) -> dict:
    """
    Send the full VoC report as a Slack DM to the configured user.
    
    Reads env vars:
      SLACK_BOT_TOKEN  — bot token starting with xoxb-
      SLACK_USER_ID    — recipient's Slack member ID (e.g. U0123456789)
    """
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    user_id = os.getenv("SLACK_USER_ID")
    
    if not slack_token or not user_id:
        return {"status": "skipped", "error": None}
    
    try:
        client = WebClient(token=slack_token)
        
        # Open DM
        response = client.conversations_open(users=user_id)
        channel_id = response["channel"]["id"]
        
        # Today's date for header
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Split report into chunks (3000 chars limit per block)
        chunks = []
        current_chunk = ""
        # Split by ## to keep sections together where possible
        sections = report_content.split("\n## ")
        for i, section in enumerate(sections):
            text = section if i == 0 else "## " + section
            if len(current_chunk) + len(text) > 2800:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = text
                else:
                    # Handle case where a single section is > 2800 chars
                    while len(text) > 2800:
                        chunks.append(text[:2800].strip())
                        text = text[2800:]
                    current_chunk = text
            else:
                current_chunk += ("\n\n" + text if current_chunk else text)
        
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        # Send each chunk
        for chunk in chunks:
            client.chat_postMessage(
                channel=channel_id,
                blocks=[
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"🦾 Vera VoC — {report_type.upper()} — {today}"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": chunk
                        }
                    }
                ]
            )
            time.sleep(0.5)
            
        # Final message
        filename = "global_action_report.md" if report_type == "global" else "weekly_delta_report.md"
        client.chat_postMessage(
            channel=channel_id,
            text=f"✅ Full report saved to reports/{filename}"
        )
        
        return {"status": "ok", "error": None}
        
    except SlackApiError as e:
        return {"status": "failed", "error": str(e)}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def generate_global_action_report(product_a_id, product_b_id):
    """Generate a comprehensive action-item report for all teams using Groq."""
    insights_a = get_theme_insights(product_a_id)
    insights_b = get_theme_insights(product_b_id)
    insights_all = get_theme_insights()
    
    # Fetch full reviews for deep analysis
    all_reviews = get_reviews(limit=1000)
    
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    
    # Collect raw data as JSON to inject into prompt
    db_data = json.dumps({
        "all_product_insights": insights_all,
        "product_a_insights": insights_a,
        "product_b_insights": insights_b,
        "raw_reviews": [
            {
                "review_text": r["review_text"],
                "rating": r["rating"],
                "theme": r["themes"],
                "sentiment": r["sentiment"],
                "review_date": r["review_date"]
            } for r in all_reviews
        ]
    }, indent=2)

    prompt = f"""
You are Vera, a Senior Consumer Insights Analyst. 
Write a deeply reasoned, evidence-backed Global Action Report based ONLY on the following JSON SQLite database export.
"You must cite the exact number of reviews behind every claim. Never make a claim that cannot be traced to a specific count in the data provided. If you don't have enough data, say so."

The report must include these markdown sections and follow this EXACT structure:

# VOC Global Action Report

## Executive Summary
(Brief overview of the competitive landscape and top priorities)

## 🔧 PRODUCT TEAM ACTIONS
For each theme with significant negative mentions:
- Theme name + exact count + % of total reviews + avg rating when mentioned
- 2-3 direct review quotes as evidence (verbatim, under 20 words each)
- Root cause hypothesis based on the pattern of complaints
- Specific, testable action item (not "improve X" but "investigate Y because Z% of 1-star reviews mention it alongside W")

## 📣 MARKETING TEAM ACTIONS
- Top 3 themes customers spontaneously praise (use as proof points)
- Exact language customers use when praising (for copy/messaging)
- Competitor comparisons mentioned in reviews (if any)
- Segments who love the product (verified buyers, specific use cases)

## 🎧 SUPPORT TEAM ACTIONS
- Top recurring issues that need troubleshooting guides, with frequency
- Exact error descriptions customers report verbatim
- Reviews that suggest unmet expectations (expectation vs reality gaps)

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

    # Trigger Slack DM notification
    slack_res = notify_slack_dm(report_content, "global")
    if slack_res["status"] == "ok":
        logger.info("Slack DM sent successfully")
    elif slack_res["status"] == "skipped":
        logger.info("Slack DM skipped — SLACK_BOT_TOKEN or SLACK_USER_ID not configured")
    else:
        logger.warning(f"Slack DM failed: {slack_res['error']}")

    return report


def generate_weekly_delta_report(product_a_id, product_b_id, since_date=None):
    """Generate a delta report for reviews added since a given date using Groq."""
    if not since_date:
        from datetime import timedelta
        since_date = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        last_week_start = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    else:
        # If since_date provided, assume last week is the 7 days prior to that
        start_dt = datetime.fromisoformat(since_date.replace("Z", "+00:00"))
        from datetime import timedelta
        last_week_start = (start_dt - timedelta(days=7)).isoformat()

    new_reviews = get_reviews_since(since_date)
    last_week_reviews = get_reviews_since(last_week_start, end_date=since_date)

    # Simplified theme frequency for comparison
    def get_freq(reviews):
        freq = {}
        for r in reviews:
            t = r.get("theme") or "Other"
            freq[t] = freq.get(t, 0) + 1
        return freq

    this_week_freq = get_freq(new_reviews)
    last_week_freq = get_freq(last_week_reviews)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    
    # Collect raw data as JSON to inject into prompt
    db_data = json.dumps({
        "this_week_counts": this_week_freq,
        "last_week_counts": last_week_freq,
        "new_reviews_detail": [
            {
                "review_text": r["review_text"],
                "rating": r["rating"],
                "theme": r["themes"],
                "sentiment": r["sentiment"],
                "review_date": r["review_date"]
            } for r in new_reviews
        ],
        "total_new_reviews": len(new_reviews)
    }, indent=2)

    prompt = f"""
You are Vera, a Senior Consumer Insights Analyst. 
Write a Weekly Delta Report based ONLY on the following JSON SQLite database export of NEW reviews since {since_date}.
"You must cite the exact number of reviews behind every claim. Never make a claim that cannot be traced to a specific count in the data provided. If you don't have enough data, say so."

The report must include these markdown sections:

# VOC Weekly Delta Report

## Emerging Themes (New This Week)
Compare this week's theme counts to last week's theme counts.
1. List all themes.
2. Flag any theme with >15% change in frequency (up or down) as a SPIKE.
3. For each spike, pull 2 supporting review quotes from THIS week.
4. Assign an urgency level: 
   - CRITICAL (>30% spike)
   - WATCH (15-30%)
   - STABLE (<15% change)

## 🔧 PRODUCT TEAM ACTIONS
(Same detailed requirements as Global report but focused on this week's trends)

## 📣 MARKETING TEAM ACTIONS
(Same detailed requirements as Global report but focused on this week's trends)

## 🎧 SUPPORT TEAM ACTIONS
(Same detailed requirements as Global report but focused on this week's trends)

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

    # Trigger Slack DM notification
    slack_res = notify_slack_dm(report_content, "weekly_delta")
    if slack_res["status"] == "ok":
        logger.info("Slack DM sent successfully")
    elif slack_res["status"] == "skipped":
        logger.info("Slack DM skipped — SLACK_BOT_TOKEN or SLACK_USER_ID not configured")
    else:
        logger.warning(f"Slack DM failed: {slack_res['error']}")

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
