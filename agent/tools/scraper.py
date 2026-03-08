"""
scraper.py — Web scraping tools for the VOC Agent.

Provides agent-callable functions to scrape product reviews from
Amazon and Flipkart. Uses a two-tier strategy:
  1. Primary: ScraperAPI (proxy) + BeautifulSoup (parsing)
  2. Fallback: Playwright headless Chromium for JS-rendered pages

Supports multiple base URLs per product (loaded from numbered env vars)
and automatic sort-variant expansion, maximizing unique review coverage.

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
from urllib.parse import quote_plus, urlparse, parse_qs, urlencode, urlunparse
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

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
PRODUCT_A_NAME = os.getenv("PRODUCT_A_NAME", "Product A")
PRODUCT_B_NAME = os.getenv("PRODUCT_B_NAME", "Product B")

console = Console()
logger = logging.getLogger(__name__)

# The 4 sort variants that are programmatically appended to every base URL.
SORT_VARIANTS = ["MOST_HELPFUL", "POSITIVE_FIRST", "NEGATIVE_FIRST", "RECENT"]

# ---------------------------------------------------------------------------
# URL Loading & Variant Generation (Parts A, B, C, D)
# ---------------------------------------------------------------------------


def load_product_urls(prefix: str) -> list[str]:
    """Load product URLs from numbered env vars.

    Reads {prefix}_URL_1 through {prefix}_URL_10 and stops at the first
    empty slot. Falls back to the legacy single-var {prefix}_URL if no
    numbered vars are found, so existing .env files keep working.

    Args:
        prefix: Environment variable prefix, e.g. "PRODUCT_A" or "PRODUCT_B".

    Returns:
        A list of non-empty URL strings (may be empty if nothing is set).
    """
    urls = []
    for i in range(1, 11):
        url = os.getenv(f"{prefix}_URL_{i}", "").strip()
        if not url:
            break
        urls.append(url)

    # Backward compatibility: fall back to the single PRODUCT_A_URL / PRODUCT_B_URL
    if not urls:
        legacy_url = os.getenv(f"{prefix}_URL", "").strip()
        if legacy_url:
            urls.append(legacy_url)

    return urls


def _normalize_url_for_dedup(url: str) -> str:
    """Strip sort, page, and pageNumber query params for dedup comparison.

    Two URLs that differ only in sort/page params are considered duplicates
    because the scraper will paginate and sort-vary them independently.

    Args:
        url: A full URL string.

    Returns:
        The URL with sort/page/pageNumber params removed, lowercased.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    # Remove params we control programmatically
    for key in ("sort", "page", "pageNumber"):
        params.pop(key, None)
    cleaned_query = urlencode(params, doseq=True)
    cleaned = urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),
        parsed.path,
        parsed.params,
        cleaned_query,
        "",  # drop fragment
    ))
    return cleaned


def _generate_sort_variant_urls(base_urls: list[str]) -> list[str]:
    """Generate sort-variant URLs from a list of base URLs.

    For each base URL, produces len(SORT_VARIANTS) variants by appending
    or replacing the &sort= query parameter. The final list is deduplicated
    (after normalizing away sort/page params) to avoid redundant HTTP calls.

    Args:
        base_urls: List of base URLs to expand.

    Returns:
        A deduplicated list of URLs with sort variants applied.
    """
    variant_urls = []
    for base_url in base_urls:
        for sort_key in SORT_VARIANTS:
            parsed = urlparse(base_url)
            params = parse_qs(parsed.query, keep_blank_values=True)
            params["sort"] = [sort_key]
            new_query = urlencode(params, doseq=True)
            variant_url = urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                "",
            ))
            variant_urls.append(variant_url)

    # Deduplicate: keep the first occurrence for each normalized form
    seen = set()
    unique_urls = []
    for url in variant_urls:
        norm = _normalize_url_for_dedup(url)
        if norm not in seen:
            seen.add(norm)
            unique_urls.append(url)

    return unique_urls


def _print_scrape_plan(
    product_name: str,
    base_urls: list[str],
    final_urls: list[str],
    max_pages: int,
) -> None:
    """Print a summary of the scrape plan before execution (Part E).

    Example output:
        Scraping Product A using 6 URLs × 4 sort variants × 25 pages
        = 600 URL attempts (24 unique after dedup)
    """
    raw_count = len(base_urls) * len(SORT_VARIANTS)
    total_attempts = len(final_urls) * max_pages
    console.print(
        f"\n[bold cyan]Scraping {product_name} using "
        f"{len(base_urls)} URL{'s' if len(base_urls) != 1 else ''} × "
        f"{len(SORT_VARIANTS)} sort variants × "
        f"{max_pages} pages "
        f"= {total_attempts} URL attempts"
        f" ({len(final_urls)} unique URLs after dedup)[/]"
    )
    if raw_count != len(final_urls):
        console.print(
            f"  [dim]({raw_count - len(final_urls)} duplicate variant URLs removed)[/]"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scraperapi_url(target_url: str) -> str:
    """Build a ScraperAPI proxy URL for the given HTML target URL."""
    return (
        f"http://api.scraperapi.com"
        f"?api_key={SCRAPER_API_KEY}"
        f"&url={quote_plus(target_url)}"
        f"&render=true"
    )

def _scraperapi_json_url(target_url: str) -> str:
    """Build a ScraperAPI proxy URL for the given JSON API target URL.
    Overrides render=false because JS execution is not needed for JSON data.
    """
    return (
        f"http://api.scraperapi.com"
        f"?api_key={SCRAPER_API_KEY}"
        f"&url={quote_plus(target_url)}"
        f"&render=false"
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


def scrape_flipkart_reviews(
    product_url, product_id, product_name, max_pages=25
):
    """Scrape Flipkart product reviews using Playwright network interception.
    
    This handles the sync/async bridge to run the async scraper in a sync context.
    """
    return asyncio.run(
        _scrape_flipkart_async(
            product_url, product_id, product_name, max_pages
        )
    )


async def _scrape_flipkart_async(
    product_url, product_id, product_name, max_pages
):
    from playwright.async_api import async_playwright

    all_reviews = []
    seen_ids = set()
    intercepted_responses = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
        )
        page = await context.new_page()

        # --- Network interception ---
        async def handle_response(response):
            url = response.url
            if (
                "api" in url
                and ("review" in url.lower() or "ratings" in url.lower())
                and response.status == 200
            ):
                try:
                    content_type = response.headers.get("content-type", "")
                    if "json" in content_type:
                        body = await response.json()
                        intercepted_responses.append(body)
                        console.print(
                            f"  [dim]Intercepted API response from: "
                            f"{url[:80]}...[/]"
                        )
                except Exception:
                    pass

        page.on("response", handle_response)

        # --- Load the review page ---
        console.print(
            f"[bold cyan]Loading Flipkart reviews for "
            f"{product_name}...[/]"
        )
        await page.goto(
            product_url,
            wait_until="load",
            timeout=60000,
        )
        await page.wait_for_timeout(5000)

        # --- Paginate by clicking Next ---
        pages_scraped = 0

        while pages_scraped < max_pages:
            # Wait for review containers
            # Flipkart uses obfuscated classes that change. We'll use multiple fallback selectors.
            container_selectors = ["div.col.EPCmJX", "div.ZmyHeS", "div._27M-N_"]
            found_container = False
            for sel in container_selectors:
                try:
                    await page.wait_for_selector(sel, timeout=10000)
                    found_container = True
                    break
                except: continue
            
            if not found_container:
                console.print(f"  [yellow]No review containers found on page {pages_scraped+1}. Stopping.[/]")
                break

            # Extract from DOM
            review_containers = await page.query_selector_all("div.col.EPCmJX, div.ZmyHeS")
            page_reviews = []
            
            for el in review_containers:
                try:
                    # Selectors based on current Flipkart layout
                    rating_el = await el.query_selector("div._3LWZlK, div.X1_N6m")
                    title_el = await el.query_selector("p._2-N8zT, p.iN7S9y")
                    text_el = await el.query_selector("div.t-ZTKy, div.ZmyHeS div, div.row div div")
                    date_els = await el.query_selector_all("p._2sc7ZR, p.m-88wv")
                    
                    rating = await rating_el.inner_text() if rating_el else ""
                    title = await title_el.inner_text() if title_el else ""
                    text = await text_el.inner_text() if text_el else ""
                    
                    # Clean the "READ MORE" text often present in Flipkart reviews
                    text = text.replace("READ MORE", "").strip()
                    
                    # Date is usually the last child in the info row
                    date_text = await date_els[-1].inner_text() if date_els else ""

                    if text:
                        # ID for dedup
                        hash_str = f"{product_id}_{title}_{text[:100]}".encode()
                        rev_id = hashlib.md5(hash_str).hexdigest()
                        
                        if rev_id not in seen_ids:
                            seen_ids.add(rev_id)
                            page_reviews.append({
                                "product_id": product_id,
                                "product_name": product_name,
                                "review_title": title.strip()[:100],
                                "review_text": text.strip()[:500],
                                "rating": _extract_rating(rating) or 0.0,
                                "review_date": _parse_flipkart_date(date_text),
                                "source": "flipkart",
                            })
                except Exception as e:
                    logger.warning("Failed to parse DOM review: %s", e)

            all_reviews.extend(page_reviews)
            console.print(
                f"  [dim]Page {pages_scraped + 1}: "
                f"+{len(page_reviews)} reviews "
                f"(total: {len(all_reviews)})[/]"
            )
            pages_scraped += 1

            if pages_scraped >= max_pages:
                break

            # Find and click the Next button
            next_clicked = False
            next_selectors = [
                 "nav a:has-text('Next')",
                 "a[href*='page=']:has-text('Next')",
                 "//span[text()='Next']/parent::a",
                 "a._1LKTO3:last-child"
            ]
            for selector in next_selectors:
                try:
                    if selector.startswith("//"):
                        btn = await page.wait_for_selector(selector, timeout=5000)
                    else:
                        btn = await page.query_selector(selector)
                    
                    if btn:
                        # Scroll into view to ensure clickability
                        await btn.scroll_into_view_if_needed()
                        await btn.click()
                        await page.wait_for_timeout(3000)
                        await page.wait_for_load_state("load")
                        next_clicked = True
                        break
                except Exception:
                    continue

            if not next_clicked:
                console.print(
                    f"  [yellow]No Next button found. "
                    f"Stopping at page {pages_scraped}.[/]"
                )
                break

        await browser.close()

    console.print(
        f"[green]Scraped {len(all_reviews)} Flipkart reviews "
        f"for {product_name}.[/]"
    )
    return all_reviews


def _parse_intercepted_flipkart_responses(
    responses: list,
    seen_ids: set,
    product_id: str,
    product_name: str,
) -> list[dict]:
    """Parse intercepted JSON responses and extract review dicts."""
    new_reviews = []

    def find_reviews(obj):
        """Recursively find review objects in nested JSON."""
        if isinstance(obj, dict):
            if ("rating" in obj or "value" in obj) and (
                "text" in obj or "review" in obj or "reviewText" in obj
            ):
                return [obj]
            if "reviewId" in obj or "review_id" in obj:
                return [obj]
            for key in ("data", "RESPONSE", "reviews", "reviewData",
                        "reviewDetails", "paginatedData"):
                if key in obj:
                    result = find_reviews(obj[key])
                    if result:
                        return result
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    result = find_reviews(v)
                    if result:
                        return result
        elif isinstance(obj, list):
            found = []
            for item in obj:
                if isinstance(item, dict):
                    if ("rating" in item or "value" in item) and (
                        "text" in item
                        or "review" in item
                        or "reviewText" in item
                        or "reviewId" in item
                    ):
                        found.append(item)
                    else:
                        found.extend(find_reviews(item))
            return found
        return []

    for resp in responses:
        reviews_list = find_reviews(resp)
        for review in reviews_list:
            try:
                rating = (
                    review.get("rating")
                    or review.get("value")
                    or review.get("starRating")
                )
                if isinstance(rating, dict):
                    rating = rating.get("value") or rating.get("rating")

                title = (
                    review.get("title")
                    or review.get("summary")
                    or review.get("reviewTitle", "")
                )
                if isinstance(title, dict):
                    title = title.get("text", "")

                text = (
                    review.get("text")
                    or review.get("review")
                    or review.get("reviewText")
                    or review.get("description", "")
                )
                if isinstance(text, dict):
                    text = text.get("text", "")

                rev_id = (
                    review.get("id")
                    or review.get("reviewId")
                    or review.get("review_id")
                )
                if not rev_id:
                    hash_str = f"{product_id}_{title}_{text}".encode()
                    rev_id = hashlib.md5(hash_str).hexdigest()

                if rev_id in seen_ids:
                    continue

                raw_date = (
                    review.get("created")
                    or review.get("reviewAge")
                    or review.get("date", "")
                )
                if str(raw_date).isdigit() and len(str(raw_date)) > 8:
                    dt = datetime.fromtimestamp(int(str(raw_date)[:10]))
                    parsed_date = dt.strftime("%Y-%m-%d")
                elif "ago" in str(raw_date).lower():
                    parsed_date = _parse_flipkart_date(str(raw_date))
                elif re.match(r"\d{4}-\d{2}-\d{2}", str(raw_date)):
                    parsed_date = str(raw_date)[:10]
                else:
                    parsed_date = _parse_flipkart_date(str(raw_date))

                try:
                    parsed_rating = float(rating) if rating else 0.0
                except Exception:
                    parsed_rating = _extract_rating(str(rating)) or 0.0

                title_str = str(title).strip()[:100] if title else ""
                text_str = str(text).strip()[:500] if text else ""

                if text_str and parsed_rating:
                    seen_ids.add(rev_id)
                    new_reviews.append({
                        "product_id": product_id,
                        "product_name": product_name,
                        "review_title": title_str,
                        "review_text": text_str,
                        "rating": parsed_rating,
                        "review_date": parsed_date,
                        "source": "flipkart",
                    })
            except Exception as e:
                logger.warning("Failed to parse intercepted review: %s", e)
                continue

    return new_reviews


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
# 4. Multi-URL Single Product Scraper
# ---------------------------------------------------------------------------


def _scrape_single_product(
    urls: list[str],
    product_id: str,
    product_name: str,
    platform: str,
    max_pages: int = 25,
) -> list[dict]:
    """Scrape a single product across multiple URLs with sort variants.

    This is the core orchestrator that:
    1. Generates sort variants from the base URLs.
    2. Deduplicates the URL list.
    3. Logs the scrape plan.
    4. Iterates each URL through the platform-specific scraper.
    5. Falls back to Playwright if the total haul is zero.

    Args:
        urls: Base URLs loaded from env for this product.
        product_id: Identifier like 'product_a'.
        product_name: Human-readable product name.
        platform: 'amazon' or 'flipkart'.
        max_pages: Pages to scrape per URL (default 25).

    Returns:
        A flat list of review dicts (may contain in-memory duplicates;
        the database UNIQUE constraint handles final dedup on insert).
    """
    if not urls:
        console.print(f"[red]No URLs configured for {product_name}. Skipping.[/]")
        return []

    # Generate sort-variant URLs and deduplicate
    if platform == "flipkart":
        final_urls = urls  # no variant expansion needed
        console.print(
            f"\n[bold cyan]Scraping {product_name} via "
            f"Playwright network interception[/]"
        )
    else:
        final_urls = _generate_sort_variant_urls(urls)
        _print_scrape_plan(product_name, urls, final_urls, max_pages)

    scrape_fn = scrape_amazon_reviews if platform == "amazon" else scrape_flipkart_reviews
    all_reviews = []

    for idx, url in enumerate(final_urls, 1):
        console.print(
            f"\n[bold yellow]  [{idx}/{len(final_urls)}] Variant URL:[/] "
            f"[dim]{url[:120]}{'...' if len(url) > 120 else ''}[/]"
        )
        try:
            reviews = scrape_fn(url, product_id, product_name, max_pages=max_pages)
            all_reviews.extend(reviews)
            console.print(
                f"  [green]→ Got {len(reviews)} reviews "
                f"(running total: {len(all_reviews)})[/]"
            )
        except Exception as e:
            logger.error("Failed scraping URL %s: %s", url, e)
            console.print(f"  [red]Error: {e}[/]")

        # Small delay between variant URLs to be polite
        if idx < len(final_urls):
            time.sleep(random.uniform(1, 2))

    # Playwright fallback if we got nothing at all
    if not all_reviews and urls and platform == "amazon":
        console.print(f"[yellow]All ScraperAPI attempts failed for {product_name}. "
                       f"Trying Playwright fallback on first URL...[/]")
        all_reviews = scrape_with_playwright_fallback(
            urls[0], product_id, product_name, platform
        )

    console.print(
        f"\n[bold green]Total scraped for {product_name}: "
        f"{len(all_reviews)} reviews (pre-DB-dedup)[/]"
    )
    return all_reviews


# ---------------------------------------------------------------------------
# 5. Full Scrape Orchestrator
# ---------------------------------------------------------------------------


def run_full_scrape(
    product_a_id,
    product_a_name,
    product_b_id,
    product_b_name,
    platform="amazon",
    max_pages=25,
):
    """Run a complete scraping session for two products.

    Loads URLs from environment variables (multi-URL pattern), generates
    sort variants, and scrapes all combinations.

    Args:
        product_a_id: Identifier for product A (e.g. 'product_a').
        product_a_name: Display name for product A.
        product_b_id: Identifier for product B (e.g. 'product_b').
        product_b_name: Display name for product B.
        platform: 'amazon' or 'flipkart'.
        max_pages: Pages to scrape per variant URL (default 25).

    Returns:
        Summary dict: {"product_a": {"inserted": N, "duplicates": M},
                        "product_b": {"inserted": N, "duplicates": M}}
    """
    urls_a = load_product_urls("PRODUCT_A")
    urls_b = load_product_urls("PRODUCT_B")

    summary = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # --- Product A ---
        task_a = progress.add_task(f"Scraping {product_a_name}...", total=None)
        reviews_a = _scrape_single_product(
            urls_a, product_a_id, product_a_name, platform, max_pages
        )
        result_a = bulk_insert_reviews(reviews_a)
        total_a = get_review_count(product_a_id)
        log_scrape_run(product_a_id, result_a["inserted"], total_a, notes="full_scrape")
        summary["product_a"] = result_a
        progress.update(task_a, description=f"[green]✓ {product_a_name} done")

        # --- Product B ---
        task_b = progress.add_task(f"Scraping {product_b_name}...", total=None)
        reviews_b = _scrape_single_product(
            urls_b, product_b_id, product_b_name, platform, max_pages
        )
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
# 6. Weekly Delta Scrape
# ---------------------------------------------------------------------------


def run_weekly_delta_scrape(
    product_a_id,
    product_a_name,
    product_b_id,
    product_b_name,
    platform="amazon",
    max_pages=25,
):
    """Run a weekly delta scrape for two products.

    Same as run_full_scrape but tags logs with 'weekly_delta' and
    appends a summary line to logs/delta_proof.log.

    Args:
        product_a_id: Identifier for product A.
        product_a_name: Display name for product A.
        product_b_id: Identifier for product B.
        product_b_name: Display name for product B.
        platform: 'amazon' or 'flipkart'.
        max_pages: Pages to scrape per variant URL (default 25).

    Returns:
        Summary dict: {"product_a": {"inserted": N, "duplicates": M},
                        "product_b": {"inserted": N, "duplicates": M}}
    """
    urls_a = load_product_urls("PRODUCT_A")
    urls_b = load_product_urls("PRODUCT_B")

    summary = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # --- Product A ---
        task_a = progress.add_task(f"Delta scrape: {product_a_name}...", total=None)
        reviews_a = _scrape_single_product(
            urls_a, product_a_id, product_a_name, platform, max_pages
        )
        result_a = bulk_insert_reviews(reviews_a)
        total_a = get_review_count(product_a_id)
        log_scrape_run(product_a_id, result_a["inserted"], total_a, notes="weekly_delta")
        summary["product_a"] = result_a
        progress.update(task_a, description=f"[green]✓ {product_a_name} delta done")

        # --- Product B ---
        task_b = progress.add_task(f"Delta scrape: {product_b_name}...", total=None)
        reviews_b = _scrape_single_product(
            urls_b, product_b_id, product_b_name, platform, max_pages
        )
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
