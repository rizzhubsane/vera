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
    # Fetch data for Product A and B
    reviews_a = get_reviews(product_id=product_a_id, limit=500)
    reviews_b = get_reviews(product_id=product_b_id, limit=500)
    all_reviews = reviews_a + reviews_b
    
    total_count = len(all_reviews)
    count_a = len(reviews_a)
    count_b = len(reviews_b)
    
    # Sentiment data
    sentiment_data = json.dumps(get_sentiment_breakdown(), indent=2)
    
    # Theme data with avg rating
    theme_freq = get_theme_frequency()
    theme_stats = {}
    for theme in VALID_THEMES:
        theme_reviews = [r for r in all_reviews if r.get("themes") and theme in r["themes"]]
        if theme_reviews:
            avg_rating = sum(r["rating"] for r in theme_reviews) / len(theme_reviews)
            theme_stats[theme] = {
                "count": len(theme_reviews),
                "avg_rating": round(avg_rating, 2)
            }
    theme_data = json.dumps(theme_stats, indent=2)
    
    # Most recent 50 reviews for sampling
    sorted_reviews = sorted(all_reviews, key=lambda x: x.get("review_date") or "", reverse=True)
    sample_raw = sorted_reviews[:50]
    sample_reviews = json.dumps([
        {
            "product_id": r["product_id"],
            "review_text": r["review_text"],
            "rating": r["rating"],
            "themes": r["themes"].split(",") if isinstance(r["themes"], str) else r["themes"],
            "sentiment": r["sentiment"],
            "review_date": r["review_date"]
        } for r in sample_raw
    ], indent=2)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    
    system_prompt = """
You are Vera, an elite Voice of Customer analyst. You have 
been given structured review data from a SQLite database. 
Your job is to generate a comprehensive action item report 
segmented by department.

STRICT RULES:
1. Every single claim must cite exact numbers: 
   "X of Y reviews mention..." or "Z% of negative reviews..."
2. Include 2-3 verbatim review quotes per theme as evidence. 
   Keep quotes under 20 words.
3. Never write vague recommendations like "improve X". 
   Every action item must follow this format:
   ACTION: [Specific thing to do]
   BECAUSE: [Exact data that justifies it]
   METRIC: [How to measure success]
4. If data is insufficient to make a claim, say exactly 
   what data is missing.
5. Compare products directly wherever the data supports it.
"""

    user_prompt = f"""
Generate a full action item report from this review data.

TOTAL REVIEWS: {total_count}
PRODUCT A ({product_a_id}): {count_a} reviews
PRODUCT B ({product_b_id}): {count_b} reviews

SENTIMENT BREAKDOWN:
{sentiment_data}

THEME FREQUENCY (with avg rating per theme):
{theme_data}

SAMPLE REVIEWS (most recent 50, full text):
{sample_reviews}

Structure your report EXACTLY as follows:

## 📦 PRODUCT TEAM
For each theme with >10% negative mentions:
- Theme name | Negative count | % of total | Avg rating
- 2 supporting verbatim quotes
- Root cause hypothesis
- Specific action item (ACTION / BECAUSE / METRIC format)

## 📣 MARKETING TEAM  
- Top 3 themes customers spontaneously praise (use as proof points)
- Exact language customers use when praising (for ad copy)
- Which product has stronger word-of-mouth signals and why
- 3 specific messaging recommendations with data backing

## 🛠️ SUPPORT TEAM
- Top 5 recurring issues with exact frequency counts
- Verbatim error descriptions customers report
- Expectation vs reality gaps identified from reviews
- Recommended troubleshooting guide topics ranked by urgency

## 📊 COMPETITIVE SUMMARY
- Where Product A beats Product B (with counts)
- Where Product B beats Product A (with counts)  
- Overall sentiment score comparison
"""

    client = Groq()
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
        max_tokens=4000
    )
    
    report_content = response.choices[0].message.content
    report = f"{report_content}\n\n---\n*Report generated on {timestamp}*\n*Full report saved to reports/global_action_report.md*"

    filepath = os.path.join(REPORTS_DIR, "global_action_report.md")
    with open(filepath, "w") as f:
        f.write(report)

    # Trigger Slack DM notification
    notify_slack_dm(report_content, "global")

    return report


def generate_weekly_delta_report(product_a_id, product_b_id, since_date=None):
    """Generate a delta report for reviews added since a given date using Groq."""
    if not since_date:
        from datetime import timedelta
        since_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        last_week_start = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    else:
        # If since_date provided, assume last week is the 7 days prior to that
        start_dt = datetime.strptime(since_date[:10], "%Y-%m-%d")
        from datetime import timedelta
        last_week_start = (start_dt - timedelta(days=7)).strftime("%Y-%m-%d")

    new_reviews = get_reviews_since(since_date)
    last_week_reviews = get_reviews_since(last_week_start, end_date=since_date)

    def get_theme_breakdown(reviews):
        breakdown = {}
        for r in reviews:
            themes = r.get("themes")
            if not themes: continue
            theme_list = themes.split(",") if isinstance(themes, str) else themes
            for t in theme_list:
                t = t.strip()
                breakdown[t] = breakdown.get(t, 0) + 1
        return breakdown

    this_week_theme_breakdown = json.dumps(get_theme_breakdown(new_reviews), indent=2)
    last_week_theme_breakdown = json.dumps(get_theme_breakdown(last_week_reviews), indent=2)
    
    new_reviews_sample = json.dumps([
        {
            "product_id": r["product_id"],
            "review_text": r["review_text"],
            "rating": r["rating"],
            "themes": r["themes"].split(",") if isinstance(r["themes"], str) else r["themes"],
            "sentiment": r["sentiment"],
            "review_date": r["review_date"]
        } for r in new_reviews[:50]
    ], indent=2)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    
    system_prompt = """
You are Vera, an elite Voice of Customer analyst. You have 
been given structured review data from a SQLite database. 
Your job is to generate a comprehensive action item report 
segmented by department.

STRICT RULES:
1. Every single claim must cite exact numbers: 
   "X of Y reviews mention..." or "Z% of negative reviews..."
2. Include 2-3 verbatim review quotes per theme as evidence. 
   Keep quotes under 20 words.
3. Never write vague recommendations like "improve X". 
   Every action item must follow this format:
   ACTION: [Specific thing to do]
   BECAUSE: [Exact data that justifies it]
   METRIC: [How to measure success]
4. If data is insufficient to make a claim, say exactly 
   what data is missing.
5. Compare products directly wherever the data supports it.
"""

    user_prompt = f"""
Generate a weekly delta report comparing this week vs last week.

THIS WEEK ({since_date} to today):
New reviews: {len(new_reviews)}
{this_week_theme_breakdown}

LAST WEEK (baseline):
{last_week_theme_breakdown}

SAMPLE OF NEW REVIEWS THIS WEEK (full text):
{new_reviews_sample}

Structure your report EXACTLY as follows:

## 🚨 SPIKES & ALERTS
For each theme with >15% change week-over-week:
- Theme | This week count | Last week count | % change
- Direction: ▲ UP or ▼ DOWN
- Urgency: CRITICAL (>30% change) | WATCH (15-30%) | STABLE
- 2 supporting quotes from this week's reviews
- Recommended immediate action

## 📈 POSITIVE TRENDS
Themes gaining positive momentum this week

## 📉 NEGATIVE TRENDS  
Themes with worsening sentiment this week

## 💡 WEEKLY RECOMMENDATION
One highest-priority action item for the team this week,
with full data justification.
"""

    client = Groq()
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
        max_tokens=4000
    )
    
    report_content = response.choices[0].message.content
    report = f"{report_content}\n\n---\n*Report generated on {timestamp}*\n*Full report saved to reports/weekly_delta_report.md*"

    filepath = os.path.join(REPORTS_DIR, "weekly_delta_report.md")
    with open(filepath, "w") as f:
        f.write(report)

    # Trigger Slack DM notification
    notify_slack_dm(report_content, "weekly_delta")

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
