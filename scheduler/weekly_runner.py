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
