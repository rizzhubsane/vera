# PRD Compliance Checklist

## Epic 1: Data Ingestion
- [x] Agent scraping via OpenClaw scrape-reviews skill (ScraperAPI + BeautifulSoup + Playwright)
- [x] Weekly automation via OpenClaw heartbeat (cron: 0 9 * * 1) + GitHub Actions backup
- [x] Delta proof: logs/delta_proof.md shows second run with dedup evidence
- [x] SQLite storage with scrape_runs logging and UNIQUE constraint deduplication

## Epic 2: NLP Processing
- [x] Sentiment: Positive/Negative/Neutral on every review (Groq LLaMA 3.1 8B)
- [x] Themes: 9 predefined themes tagged per review
- [x] Tests verify only valid themes are returned (test_nlp_only_returns_valid_themes)

## Epic 3: Reports
- [x] Global action report: reports/global_action_report.md — cites counts, segmented by team
- [x] Weekly delta report: reports/weekly_delta_report.md — spike detection included

## Epic 4: Conversational Querying
- [x] SOUL.md: Full agent identity with grounding rules and personality
- [x] Conversational queries via CLI + Telegram (OpenClaw)
- [x] All answers grounded in SQLite DB (query-database skill called before responding)

## Deliverables
- [x] Git repo: https://github.com/rizzhubsane/vera (branch: fix/prd-compliance)
- [x] README.md with OpenClaw setup, env vars, scheduler config
- [x] agent/SOUL.md — complete identity document
- [x] data/initial_reviews.csv — 500+ rows per product
- [x] logs/delta_proof.md — second scrape with dedup evidence
- [x] reports/global_action_report.md — specific cited action items
- [x] reports/weekly_delta_report.md — weekly changes + spike detection
- [x] demo.md — live query transcripts
- [x] tests/test_pipeline.py — 6 PRD compliance tests
