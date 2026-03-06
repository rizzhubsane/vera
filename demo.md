# Vera VoC Agent — Demo Guide

Welcome to **Vera**, your autonomous Voice of Customer (VoC) Intelligence Analyst. This guide will walk you through the end-to-end pipeline, from raw web scraping to AI-generated executive reports.

## 1. Installation & Environment
First, ensure you have the dependencies installed and your `.env` file configured.

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add GROQ_API_KEY and SCRAPER_API_KEY
```

## 2. Initial Data Ingest (Full Scrape)
To populate the database with real reviews from Flipkart, run the initial full scrape. This bypasses bots via ScraperAPI and deduplicates incoming data.

```bash
# Run the scraping skill via OpenClaw
node skills/scrape-reviews.js run_type=full max_pages=30
```

## 3. NLP Processing
Once reviews are in the SQLite database, run them through the NLP pipeline. Vera uses **LLaMA 3.3 70B** to classify every review by sentiment (Positive/Negative/Neutral) and tag them with up to 3 specific themes.

```bash
# Process all unprocessed reviews
node skills/process-nlp.js
```

## 4. Generating Action Reports
Vera generates two types of reports designed for Product, Marketing, and Support teams. Every recommendation is grounded in real data and cites exact review counts.

```bash
# Generate the Global Action Report
node skills/generate-report.js report_type=global

# Generate the Weekly Delta Report (Changes since last week)
node skills/generate-report.js report_type=weekly_delta
```

Check the `reports/` folder for the markdown output.

## 5. Automated Weekly Runs
Vera can run autonomously every Monday at 9AM UTC using the integrated heartbeat system.

```bash
# Start the scheduler with Telegram notifications
python scheduler/weekly_runner.py
```

## 6. PRD Compliance Audit
To verify that the system is meeting all PRD requirements (deduplication, numeric grounding, theme constraints), run the test suite:

```bash
python tests/test_pipeline.py
```

---
*Vera: Turning customer noise into product signal.*
