# Autonomous Voice of Customer (VoC) Intelligence Agent

## Overview

**Vera** is an autonomous AI agent that scrapes, classifies, and analyzes public e-commerce product reviews to generate actionable intelligence for Product, Marketing, and Support teams. Built on Python with Groq API (LLaMA 3.3 70B for reasoning, LLaMA 3.1 8B for bulk NLP), SQLite for storage, and ScraperAPI for reliable web scraping. It delivers grounded, data-backed insights — never hallucinated — by enforcing strict anti-hallucination rules and always citing review counts behind every claim.

---

## Agent Framework: OpenClaw

Vera runs on [OpenClaw](https://openclaw.ai) — an open-source autonomous agent runtime.
OpenClaw provides:
- Persistent memory and identity (loaded from SOUL.md)
- Skills system (5 custom skills in skills/ folder)
- Telegram/Discord integration for conversational querying
- Built-in heartbeat/cron for weekly automation

### Install OpenClaw
```bash
npm i -g openclaw
openclaw onboard
```

### Register Vera's Skills
```bash
openclaw skills add ./skills/scrape-reviews.js
openclaw skills add ./skills/process-nlp.js
openclaw skills add ./skills/query-database.js
openclaw skills add ./skills/get-statistics.js
openclaw skills add ./skills/generate-report.js
```

### Connect Telegram
Add `TELEGRAM_BOT_TOKEN` to `.env` (get from @BotFather)
```bash
openclaw integrations add telegram --token $TELEGRAM_BOT_TOKEN
```

### Start Vera
```bash
openclaw start --config ./openclaw-config.json
```

---

## Architecture

```
User Query
    ↓
Vera Agent (llama-3.3-70b-versatile via Groq)
    ↓ decides which tools to call
Tool Dispatcher
    ├── scrape_reviews     → ScraperAPI + BeautifulSoup + Playwright
    ├── process_nlp        → llama-3.1-8b-instant (bulk classification)
    ├── query_database     → SQLite
    ├── get_statistics     → SQLite aggregation
    ├── compare_products   → Computed sentiment scores
    └── generate_report    → llama-3.3-70b-versatile (prose writing)
    ↓
Grounded answer returned to user
```

---

## Setup

### Prerequisites

- **Python 3.10+**
- **Groq API Key** — free at [console.groq.com](https://console.groq.com)
- **ScraperAPI Key** — free tier at [scraperapi.com](https://www.scraperapi.com)

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/voc-agent.git
cd voc-agent

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright Chromium (fallback scraper)
playwright install chromium

# Configure environment variables
cp .env.example .env
# Edit .env and fill in your API keys and product URLs
```

### Environment Variables

| Variable | Description | Where to Get |
|----------|-------------|--------------|
| `GROQ_API_KEY` | Groq API key for LLM calls | [console.groq.com](https://console.groq.com) |
| `SCRAPER_API_KEY` | ScraperAPI key for web scraping | [scraperapi.com](https://www.scraperapi.com) |
| `PRODUCT_A_URL` | Amazon/Flipkart review page URL for Product A | Copy from browser |
| `PRODUCT_B_URL` | Amazon/Flipkart review page URL for Product B | Copy from browser |
| `PRODUCT_A_ID` | Internal identifier for Product A (e.g. `product_a`) | You define |
| `PRODUCT_A_NAME` | Display name for Product A (e.g. `Master Buds 1`) | You define |
| `PRODUCT_B_ID` | Internal identifier for Product B (e.g. `product_b`) | You define |
| `PRODUCT_B_NAME` | Display name for Product B (e.g. `Master Buds Max`) | You define |
| `SCRAPE_PLATFORM` | Target platform: `amazon` or `flipkart` | You define |

---

## Usage

### Initial Scrape

Scrape reviews for both products and process NLP:

```bash
python agent/voc_agent.py scrape
```

### Interactive Chat

Start a conversation with Vera:

```bash
python agent/voc_agent.py chat
```

### Generate Reports

Generate global and weekly delta reports:

```bash
python agent/voc_agent.py report
```

### Run Weekly Job

Test the weekly pipeline immediately:

```bash
python scheduler/weekly_runner.py --now
```

### Sample Queries for Chat

```
> What does Product B do better than Product A on ANC and comfort?
> Top 3 complaints about Product A's battery life?
> Give the Marketing team 3 specific action items.
> Any spike in negative reviews about Build Quality this week?
```

---

## Automated Weekly Schedule

The project includes a GitHub Actions workflow (`.github/workflows/weekly_voc.yml`) that runs every **Monday at 09:00 UTC**.

The workflow:
1. Checks out the repo
2. Installs Python 3.11, pip dependencies, and Playwright Chromium
3. Runs the weekly VoC pipeline (`scheduler/weekly_runner.py --now`)
4. Uploads generated reports as GitHub Actions artifacts

### Setup

Add the following secrets in your GitHub repo under **Settings → Secrets and variables → Actions**:

- `GROQ_API_KEY`
- `SCRAPER_API_KEY`
- `PRODUCT_A_URL`
- `PRODUCT_B_URL`
- `PRODUCT_A_NAME`
- `PRODUCT_B_NAME`

You can also trigger the workflow manually via the **Actions** tab → **Run workflow**.

---

## Deliverables

- [x] **Web scraping pipeline** — ScraperAPI + BeautifulSoup + Playwright fallback
- [x] **Structured database** — SQLite with reviews and scrape_runs tables
- [x] **NLP classification** — Sentiment + theme tagging via LLaMA 3.1 8B
- [x] **Agentic tool-use** — Groq function calling with LLaMA 3.3 70B
- [x] **Interactive chat** — Rich terminal UI with slash commands
- [x] **Department reports** — Product, Marketing, and Support action items
- [x] **Weekly automation** — APScheduler + GitHub Actions cron
- [x] **Anti-hallucination** — Grounding rules enforced via SOUL.md system prompt

---

## Agent Identity

See [`agent/SOUL.md`](agent/SOUL.md) — contains Vera's personality, grounding rules, tracked themes, department mappings, and the system prompt used in every API call.

---

## Project Structure

```
voc-agent/
├── agent/
│   ├── __init__.py
│   ├── SOUL.md              # Agent identity & grounding rules
│   ├── voc_agent.py          # Agent brain (Groq tool-use loop)
│   └── tools/
│       ├── __init__.py
│       ├── database.py        # SQLite CRUD & analytics
│       ├── scraper.py         # Web scraping (ScraperAPI + Playwright)
│       ├── nlp_processor.py   # Sentiment & theme classification (Groq)
│       └── reporter.py        # Report generation
├── scheduler/
│   └── weekly_runner.py       # APScheduler weekly cron job
├── database/                  # SQLite DB (auto-created)
├── reports/                   # Generated reports
├── logs/                      # Delta proof logs
├── data/                      # Data files
├── tests/                     # Test suite
├── .env                       # API keys (git-ignored)
├── .gitignore
├── requirements.txt
└── .github/workflows/
    └── weekly_voc.yml         # GitHub Actions weekly automation
```
