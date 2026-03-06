"""
scraper.py — Web scraping tools for the VOC Agent.

Provides agent-callable functions to scrape product reviews from
Amazon and Flipkart. Uses a two-tier strategy:
  1. Primary: ScraperAPI (proxy) + BeautifulSoup (parsing)
  2. Fallback: Playwright headless Chromium for JS-rendered pages

All scraped reviews are returned as lists of dicts ready for
bulk_insert_reviews() in database.py.
"""

import os
import re
import time
import random
import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from agent.tools.database import (
    bulk_insert_reviews,
    get_review_count,
    log_scrape_run,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")
PRODUCT_A_URL = os.getenv("PRODUCT_A_URL", "")
PRODUCT_B_URL = os.getenv("PRODUCT_B_URL", "")
PRODUCT_A_NAME = os.getenv("PRODUCT_A_NAME", "Product A")
PRODUCT_B_NAME = os.getenv("PRODUCT_B_NAME", "Product B")

console = Console()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scraperapi_url(target_url: str) -> str:
    """Build a ScraperAPI proxy URL for the given target URL."""
    return (
        f"http://api.scraperapi.com"
        f"?api_key={SCRAPER_API_KEY}"
        f"&url={quote_plus(target_url)}"
        f"&render=true"
    )


def _parse_amazon_date(date_text: str) -> str:
    """Parse an Amazon date string like 'Reviewed in India on 9 March 2024'
    into ISO format YYYY-MM-DD.

    Returns the original string if parsing fails.
    """
    try:
        # Strip the 'Reviewed in <country> on ' prefix
        match = re.search(r"on\s+(.+)$", date_text.strip())
        if match:
            date_part = match.group(1).strip()
            for fmt in ("%d %B %Y", "%B %d, %Y", "%d %B, %Y"):
                try:
                    return datetime.strptime(date_part, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
        return date_text.strip()
    except Exception:
        return date_text.strip()


def _parse_flipkart_date(date_text: str) -> str:
    """Parse a Flipkart date string like '9 Mar, 2024' into YYYY-MM-DD.

    Returns the original string if parsing fails.
    """
    try:
        cleaned = date_text.strip().rstrip(",").strip()
        for fmt in ("%d %b, %Y", "%d %B, %Y", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return cleaned
    except Exception:
        return date_text.strip()


def _extract_rating(text: str) -> float | None:
    """Extract a numeric rating from text like '4.0 out of 5 stars' or '4'."""
    try:
        match = re.search(r"(\d+\.?\d*)", text.strip())
        if match:
            return float(match.group(1))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 1. Amazon Scraper (ScraperAPI + BeautifulSoup)
# ---------------------------------------------------------------------------


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_page(url: str) -> str:
    """Fetch a page through ScraperAPI with retries and exponential backoff.

    Args:
        url: The ScraperAPI-wrapped URL to fetch.

    Returns:
        The HTML content of the page.

    Raises:
        requests.HTTPError: On non-2xx responses (triggers retry).
    """
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def scrape_amazon_reviews(product_url, product_id, product_name, max_pages=10):
    """Scrape Amazon product reviews via ScraperAPI + BeautifulSoup.

    Paginates through review pages by appending &pageNumber=N to the URL.
    Stops when no more reviews are found or a "Next page" button is absent.

    Args:
        product_url: The Amazon product reviews URL.
        product_id: Identifier like 'product_a'.
        product_name: Human-readable product name.
        max_pages: Maximum number of pages to scrape (default 10).

    Returns:
        A list of review dicts with keys: product_id, product_name,
        review_title, review_text, rating, review_date, source.
    """
    all_reviews = []

    console.print(f"[bold cyan]Scraping Amazon reviews for {product_name}...[/]")

    for page in range(1, max_pages + 1):
        try:
            page_url = f"{product_url}&pageNumber={page}"
            api_url = _scraperapi_url(page_url)

            console.print(f"  [dim]Page {page}/{max_pages}...[/]")
            html = _fetch_page(api_url)
            soup = BeautifulSoup(html, "html.parser")

            review_divs = soup.select('div[data-hook="review"]')
            if not review_divs:
                console.print(f"  [yellow]No reviews found on page {page}. Stopping.[/]")
                break

            for div in review_divs:
                try:
                    # Title
                    title_el = div.select_one('span[data-hook="review-title"]')
                    title = title_el.get_text(strip=True) if title_el else ""

                    # Body
                    body_el = div.select_one('span[data-hook="review-body"]')
                    body = body_el.get_text(strip=True) if body_el else ""

                    # Rating
                    rating_el = div.select_one(
                        'i[data-hook="review-star-rating"], '
                        'i[data-hook="cmps-review-star-rating"]'
                    )
                    rating = _extract_rating(rating_el.get_text()) if rating_el else None

                    # Date
                    date_el = div.select_one('span[data-hook="review-date"]')
                    review_date = _parse_amazon_date(date_el.get_text()) if date_el else ""

                    if body:  # Only add reviews that have text
                        all_reviews.append({
                            "product_id": product_id,
                            "product_name": product_name,
                            "review_title": title,
                            "review_text": body,
                            "rating": rating,
                            "review_date": review_date,
                            "source": "amazon",
                        })
                except Exception as e:
                    logger.warning("Failed to parse an Amazon review element: %s", e)
                    continue

            # Check for "Next page" button
            next_btn = soup.select_one('li.a-last a')
            if not next_btn:
                console.print(f"  [yellow]No next page button. Stopping after page {page}.[/]")
                break

            # Random delay between pages
            delay = random.uniform(2, 4)
            time.sleep(delay)

        except Exception as e:
            logger.error("Failed to scrape Amazon page %d: %s", page, e)
            console.print(f"  [red]Error on page {page}: {e}[/]")
            break

    console.print(f"[green]Scraped {len(all_reviews)} Amazon reviews for {product_name}.[/]")
    return all_reviews


# ---------------------------------------------------------------------------
# 2. Flipkart Scraper (ScraperAPI + BeautifulSoup)
# ---------------------------------------------------------------------------


def scrape_flipkart_reviews(product_url, product_id, product_name, max_pages=10):
    """Scrape Flipkart product reviews via ScraperAPI + BeautifulSoup.

    Uses multiple fallback CSS selectors since Flipkart frequently
    changes its class names.

    Args:
        product_url: The Flipkart product reviews URL.
        product_id: Identifier like 'product_a'.
        product_name: Human-readable product name.
        max_pages: Maximum number of pages to scrape (default 10).

    Returns:
        A list of review dicts with keys: product_id, product_name,
        review_title, review_text, rating, review_date, source.
    """
    all_reviews = []

    console.print(f"[bold cyan]Scraping Flipkart reviews for {product_name}...[/]")

    for page in range(1, max_pages + 1):
        try:
            page_url = f"{product_url}&page={page}"
            api_url = _scraperapi_url(page_url)

            console.print(f"  [dim]Page {page}/{max_pages}...[/]")
            html = _fetch_page(api_url)
            soup = BeautifulSoup(html, "html.parser")

            # Flipkart's new React Native web DOM uses heavily obfuscated classes like css-175oi2r
            # The most reliable way to find reviews is to look for the "1" to "5" star rating divs.
            rating_els = soup.find_all("div", string=re.compile(r"^[1-5]$"))
            
            if not rating_els:
                console.print(f"  [yellow]No reviews found on page {page}. Stopping.[/]")
                break

            processed_containers = set()

            for rating_el in rating_els:
                try:
                    # Climb up 3-5 levels to find the whole review container
                    container = rating_el.parent
                    for _ in range(5):
                        if container and len(list(container.stripped_strings)) >= 5:
                            break
                        if container:
                            container = container.parent
                    
                    if not container or id(container) in processed_containers:
                        continue
                        
                    processed_containers.add(id(container))
                    texts = list(container.stripped_strings)
                    
                    # Pattern usually: [Rating, Title, Date, Body, ..., Name, 'Verified Buyer']
                    rating = _extract_rating(texts[0])
                    title = texts[1]
                    
                    # Sometimes "more" is in the middle
                    body_parts = []
                    review_date = ""
                    for t in texts[2:]:
                        if "ago" in t or re.search(r"\d{4}", t):
                            review_date = _parse_flipkart_date(t)
                        elif t not in ("more", "Verified Buyer") and len(t) > 3 and not re.search(r"^\d+$", t):
                            body_parts.append(t)
                            
                    body = " ".join(body_parts[:2]) # usually the first long texts are the body
                    
                    if body and rating:
                        all_reviews.append({
                            "product_id": product_id,
                            "product_name": product_name,
                            "review_title": title,
                            "review_text": body[:500],
                            "rating": rating,
                            "review_date": review_date,
                            "source": "flipkart",
                        })
                except Exception as e:
                    logger.warning("Failed to parse a Flipkart review element: %s", e)
                    continue

            # Check for next page link
            next_btn = soup.select_one('a._1LKTO3[href]') or soup.select_one('nav a:last-child')
            if not next_btn:
                console.print(f"  [yellow]No next page button. Stopping after page {page}.[/]")
                break

            delay = random.uniform(2, 4)
            time.sleep(delay)

        except Exception as e:
            logger.error("Failed to scrape Flipkart page %d: %s", page, e)
            console.print(f"  [red]Error on page {page}: {e}[/]")
            break

    console.print(f"[green]Scraped {len(all_reviews)} Flipkart reviews for {product_name}.[/]")
    return all_reviews


# ---------------------------------------------------------------------------
# 3. Playwright Fallback
# ---------------------------------------------------------------------------


def scrape_with_playwright_fallback(product_url, product_id, product_name, platform="amazon"):
    """Fallback scraper using Playwright headless Chromium.

    Used when ScraperAPI fails — handles JavaScript-rendered pages
    by launching a real browser instance.

    Args:
        product_url: The product reviews URL.
        product_id: Identifier like 'product_a'.
        product_name: Human-readable product name.
        platform: Either 'amazon' or 'flipkart' (determines parse logic).

    Returns:
        A list of review dicts with the standard schema.
    """

    async def _scrape():
        reviews = []
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                console.print(f"[bold magenta]Playwright fallback: loading {product_url}...[/]")
                await page.goto(product_url, wait_until="networkidle", timeout=30000)

                # Wait for review elements to appear
                if platform == "amazon":
                    await page.wait_for_selector(
                        'div[data-hook="review"]', timeout=15000
                    )
                    review_els = await page.query_selector_all('div[data-hook="review"]')

                    for el in review_els:
                        try:
                            title_el = await el.query_selector('span[data-hook="review-title"]')
                            body_el = await el.query_selector('span[data-hook="review-body"]')
                            rating_el = await el.query_selector(
                                'i[data-hook="review-star-rating"], '
                                'i[data-hook="cmps-review-star-rating"]'
                            )
                            date_el = await el.query_selector('span[data-hook="review-date"]')

                            title = await title_el.inner_text() if title_el else ""
                            body = await body_el.inner_text() if body_el else ""
                            rating_text = await rating_el.inner_text() if rating_el else ""
                            date_text = await date_el.inner_text() if date_el else ""

                            if body:
                                reviews.append({
                                    "product_id": product_id,
                                    "product_name": product_name,
                                    "review_title": title.strip(),
                                    "review_text": body.strip(),
                                    "rating": _extract_rating(rating_text),
                                    "review_date": _parse_amazon_date(date_text),
                                    "source": "amazon",
                                })
                        except Exception as e:
                            logger.warning("Playwright: failed to parse Amazon review: %s", e)

                elif platform == "flipkart":
                    await page.wait_for_selector(
                        "div._1AtVbE, div.col.EPCmJX", timeout=15000
                    )
                    review_els = await page.query_selector_all("div._1AtVbE, div.col.EPCmJX")

                    for el in review_els:
                        try:
                            title_el = await el.query_selector("p._2-N8zT")
                            body_el = await el.query_selector("div.t-ZTKy, div._6K-7Co")
                            rating_el = await el.query_selector("div._3LWZlK")
                            date_el = await el.query_selector("p._2sc7ZR")

                            title = await title_el.inner_text() if title_el else ""
                            body = await body_el.inner_text() if body_el else ""
                            rating_text = await rating_el.inner_text() if rating_el else ""
                            date_text = await date_el.inner_text() if date_el else ""

                            if body:
                                reviews.append({
                                    "product_id": product_id,
                                    "product_name": product_name,
                                    "review_title": title.strip(),
                                    "review_text": body.strip(),
                                    "rating": _extract_rating(rating_text),
                                    "review_date": _parse_flipkart_date(date_text),
                                    "source": "flipkart",
                                })
                        except Exception as e:
                            logger.warning("Playwright: failed to parse Flipkart review: %s", e)

                await browser.close()

        except Exception as e:
            logger.error("Playwright fallback failed: %s", e)
            console.print(f"[red]Playwright fallback error: {e}[/]")

        return reviews

    return asyncio.run(_scrape())


# ---------------------------------------------------------------------------
# 4. Full Scrape Orchestrator
# ---------------------------------------------------------------------------


def run_full_scrape(
    product_a_url,
    product_a_id,
    product_a_name,
    product_b_url,
    product_b_id,
    product_b_name,
    platform="amazon",
):
    """Run a complete scraping session for two products.

    Scrapes reviews from the specified platform, inserts them into the
    database via bulk_insert_reviews(), and logs each scrape run.

    Args:
        product_a_url: URL for product A reviews.
        product_a_id: Identifier for product A (e.g. 'product_a').
        product_a_name: Display name for product A.
        product_b_url: URL for product B reviews.
        product_b_id: Identifier for product B (e.g. 'product_b').
        product_b_name: Display name for product B.
        platform: 'amazon' or 'flipkart'.

    Returns:
        Summary dict: {"product_a": {"inserted": N, "duplicates": M},
                        "product_b": {"inserted": N, "duplicates": M}}
    """
    scrape_fn = scrape_amazon_reviews if platform == "amazon" else scrape_flipkart_reviews

    summary = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # --- Product A ---
        task_a = progress.add_task(f"Scraping {product_a_name}...", total=None)
        reviews_a = scrape_fn(product_a_url, product_a_id, product_a_name)
        if not reviews_a:
            console.print(f"  [yellow]ScraperAPI failed. Trying Playwright fallback...[/]")
            reviews_a = scrape_with_playwright_fallback(product_a_url, product_a_id, product_a_name, platform)
        result_a = bulk_insert_reviews(reviews_a)
        total_a = get_review_count(product_a_id)
        log_scrape_run(product_a_id, result_a["inserted"], total_a, notes="full_scrape")
        summary["product_a"] = result_a
        progress.update(task_a, description=f"[green]✓ {product_a_name} done")

        # --- Product B ---
        task_b = progress.add_task(f"Scraping {product_b_name}...", total=None)
        reviews_b = scrape_fn(product_b_url, product_b_id, product_b_name)
        if not reviews_b:
            console.print(f"  [yellow]ScraperAPI failed. Trying Playwright fallback...[/]")
            reviews_b = scrape_with_playwright_fallback(product_b_url, product_b_id, product_b_name, platform)
        result_b = bulk_insert_reviews(reviews_b)
        total_b = get_review_count(product_b_id)
        log_scrape_run(product_b_id, result_b["inserted"], total_b, notes="full_scrape")
        summary["product_b"] = result_b
        progress.update(task_b, description=f"[green]✓ {product_b_name} done")

    console.print("\n[bold green]Full scrape complete![/]")
    console.print(f"  Product A: {result_a['inserted']} inserted, {result_a['duplicates']} duplicates")
    console.print(f"  Product B: {result_b['inserted']} inserted, {result_b['duplicates']} duplicates")

    return summary


# ---------------------------------------------------------------------------
# 5. Weekly Delta Scrape
# ---------------------------------------------------------------------------


def run_weekly_delta_scrape(
    product_a_url,
    product_a_id,
    product_a_name,
    product_b_url,
    product_b_id,
    product_b_name,
    platform="amazon",
):
    """Run a weekly delta scrape for two products.

    Same as run_full_scrape but tags logs with 'weekly_delta' and
    appends a summary line to logs/delta_proof.log.

    Args:
        product_a_url: URL for product A reviews.
        product_a_id: Identifier for product A.
        product_a_name: Display name for product A.
        product_b_url: URL for product B reviews.
        product_b_id: Identifier for product B.
        product_b_name: Display name for product B.
        platform: 'amazon' or 'flipkart'.

    Returns:
        Summary dict: {"product_a": {"inserted": N, "duplicates": M},
                        "product_b": {"inserted": N, "duplicates": M}}
    """
    scrape_fn = scrape_amazon_reviews if platform == "amazon" else scrape_flipkart_reviews

    summary = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # --- Product A ---
        task_a = progress.add_task(f"Delta scrape: {product_a_name}...", total=None)
        reviews_a = scrape_fn(product_a_url, product_a_id, product_a_name)
        result_a = bulk_insert_reviews(reviews_a)
        total_a = get_review_count(product_a_id)
        log_scrape_run(product_a_id, result_a["inserted"], total_a, notes="weekly_delta")
        summary["product_a"] = result_a
        progress.update(task_a, description=f"[green]✓ {product_a_name} delta done")

        # --- Product B ---
        task_b = progress.add_task(f"Delta scrape: {product_b_name}...", total=None)
        reviews_b = scrape_fn(product_b_url, product_b_id, product_b_name)
        result_b = bulk_insert_reviews(reviews_b)
        total_b = get_review_count(product_b_id)
        log_scrape_run(product_b_id, result_b["inserted"], total_b, notes="weekly_delta")
        summary["product_b"] = result_b
        progress.update(task_b, description=f"[green]✓ {product_b_name} delta done")

    # Write delta proof log
    total_db = get_review_count()
    timestamp = datetime.now(timezone.utc).isoformat()
    log_line = (
        f"[{timestamp}] Weekly run complete. "
        f"Product A: +{result_a['inserted']} new reviews. "
        f"Product B: +{result_b['inserted']} new reviews. "
        f"Total DB: {total_db} reviews.\n"
    )

    log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "delta_proof.log")

    with open(log_path, "a") as f:
        f.write(log_line)

    console.print(f"\n[bold green]Weekly delta scrape complete![/]")
    console.print(f"  Product A: +{result_a['inserted']} new")
    console.print(f"  Product B: +{result_b['inserted']} new")
    console.print(f"  Total in DB: {total_db}")
    console.print(f"  Delta log written to: {log_path}")

    return summary
