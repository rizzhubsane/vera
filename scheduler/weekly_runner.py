"""
weekly_runner.py — Scheduled weekly VoC agent pipeline.

Runs the full weekly cycle: scrape → NLP → report via APScheduler.

Usage:
    python scheduler/weekly_runner.py          # Start cron scheduler (Monday 09:00 UTC)
    python scheduler/weekly_runner.py --now    # Run immediately (test mode)
"""

import sys
import os
from datetime import datetime

from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from rich.console import Console

# Ensure the project root is on sys.path so agent imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

load_dotenv()
console = Console()


def send_telegram_notification(message):
    import requests
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if token and chat_id:
        try:
            requests.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'},
                timeout=10
            )
            print('Telegram notification sent.')
        except Exception as e:
            print(f'Telegram notification failed: {e}')


def weekly_job():
    """Execute the full weekly VoC pipeline via the agent."""
    from agent.voc_agent import run_agent

    console.print(f"\n[bold yellow]=== WEEKLY VoC RUN STARTED: {datetime.utcnow()} ===[/bold yellow]")
    result, _ = run_agent(
        "Run a weekly delta scrape for both products. "
        "Then process NLP on all unprocessed reviews. "
        "Then generate the weekly delta report. "
        "Summarize what changed this week compared to before."
    )
    console.print(result)
    console.print(f"[bold yellow]=== WEEKLY VoC RUN COMPLETE: {datetime.utcnow()} ===[/bold yellow]\n")
    send_telegram_notification(
        f"✅ *Vera Weekly VoC Run Complete*\n"
        f"📅 {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"📊 Reports saved to reports/\n"
        f"See reports/weekly_delta_report.md for this week's action items."
    )


if __name__ == "__main__":
    if "--now" in sys.argv:
        console.print("Running weekly job immediately (test mode)...")
        weekly_job()
    else:
        scheduler = BlockingScheduler()
        scheduler.add_job(weekly_job, CronTrigger(day_of_week="mon", hour=9, minute=0))
        console.print("Scheduler started. Weekly job runs every Monday at 09:00 UTC.")
        try:
            scheduler.start()
        except KeyboardInterrupt:
            console.print("Scheduler stopped.")
