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


def scrape_flipkart_reviews(product_url, product_id, product_name, max_pages=25):
    """Scrape Flipkart product reviews using their internal JSON API.

    All requests are routed through ScraperAPI (without rendering)
    to avoid IP blocks. Uses ThreadPoolExecutor to concurrently
    fetch pages across all 4 sort orders.

    Args:
        product_url: The Flipkart product URL (to extract pid).
        product_id: Identifier like 'product_a'.
        product_name: Human-readable product name.
        max_pages: Maximum number of pages to scrape per sort order (default 25).

    Returns:
        A list of review dicts with standard keys.
    """
    console.print(f"[bold cyan]Scraping Flipkart reviews for {product_name}...[/]")

    # 1. Extract pid
    pid = parse_qs(urlparse(product_url).query).get('pid', [None])[0]
    if not pid:
        match = re.search(r'/p/([^?]+)', product_url)
        if match:
            pid = match.group(1)

    if not pid:
        console.print(f"  [red]Could not extract pid from URL: {product_url}[/]")
        return []

    # 4. Shared state for concurrency
    seen_ids = set()
    all_reviews = []
    SORT_ORDERS = ["MOST_HELPFUL", "RECENT", "POSITIVE_FIRST", "NEGATIVE_FIRST"]

    def fetch_sort_order(sort_order):
        local_reviews = []
        consecutive_high_dup_pages = 0
        
        for page in range(1, max_pages + 1):
            try:
                # Built exactly matching the DevTools network tab
                api_target = f"https://www.flipkart.com/api/3/page/dynamic/product-reviews?pid={pid}&sortOrder={sort_order}&page={page}&count=10"
                proxy_url = _scraperapi_json_url(api_target)

                console.print(f"  [dim]Page {page}/{max_pages} sort={sort_order}...[/]")
                
                # Fetch JSON without custom headers; let ScraperAPI handle it
                response = requests.get(proxy_url, timeout=60)
                response.raise_for_status()
                data = response.json()

                # Find reviews array deep in FlipKart's JSON structure
                reviews_list = []
                try:
                    # Recursive search function to find lists of review objects
                    def find_reviews(obj):
                        if isinstance(obj, dict):
                            if "author" in obj and "rating" in obj and ("text" in obj or "reviewer" in obj):
                                return [obj]
                            # Or look for 'reviewId'
                            if "reviewId" in obj:
                                return [obj]
                            # Standard places
                            if "data" in obj:
                                res = find_reviews(obj["data"])
                                if res: return res
                            if "RESPONSE" in obj:
                                res = find_reviews(obj["RESPONSE"])
                                if res: return res
                            # Search all values
                            for k, v in obj.items():
                                if k == "reviews" and isinstance(v, list):
                                    return v
                                if isinstance(v, (dict, list)):
                                    res = find_reviews(v)
                                    if res: return res
                        elif isinstance(obj, list):
                            found = []
                            for item in obj:
                                if isinstance(item, dict):
                                    # If it looks like a review, grab it
                                    if "author" in item and "rating" in item:
                                        found.append(item)
                                    elif "reviewId" in item:
                                        found.append(item)
                                    else:
                                        res = find_reviews(item)
                                        if res: found.extend(res)
                            if found: return found
                        return []
                    
                    reviews_list = find_reviews(data)
                except Exception as e:
                    logger.debug(f"JSON extraction error: {e}")

                if not reviews_list:
                    console.print(f"  [dim]No more JSON reviews on page {page} for {sort_order}[/]")
                    break

                page_new_reviews = []
                for review in reviews_list:
                    # 5. Extract fields safely
                    rating = review.get("rating") or review.get("value")
                    if isinstance(rating, dict): rating = rating.get("value", rating)
                    
                    title = review.get("title") or review.get("summary")
                    if isinstance(title, dict): title = title.get("text", title)
                        
                    text = review.get("text") or review.get("review") or review.get("reviewText")
                    reviewer = review.get("author") or review.get("reviewerName")
                    
                    try:
                        parsed_rating = float(rating) if rating is not None else 0.0
                    except:
                        parsed_rating = _extract_rating(str(rating)) or 0.0

                    # ID generation/extraction
                    rev_id = review.get("id") or review.get("reviewId")
                    if not rev_id:
                        hash_str = f"{pid}_{title}_{text}".encode('utf-8')
                        rev_id = hashlib.md5(hash_str).hexdigest()

                    # Deduplication using global seen_ids
                    if rev_id in seen_ids:
                        continue

                    # Date processing
                    raw_date = review.get("created") or review.get("reviewAge", "")
                    if str(raw_date).isdigit() and len(str(raw_date)) > 8:
                        dt = datetime.fromtimestamp(int(str(raw_date)[:10]))
                        parsed_date = dt.strftime("%Y-%m-%d")
                    elif "ago" in str(raw_date).lower():
                        parsed_date = _parse_flipkart_date(str(raw_date))
                    elif re.match(r'\d{4}-\d{2}-\d{2}', str(raw_date)):
                        parsed_date = str(raw_date)[:10]
                    else:
                        parsed_date = _parse_flipkart_date(str(raw_date))

                    title_str = str(title).strip() if title else ""
                    text_str = str(text).strip() if text else ""

                    if text_str and parsed_rating:
                        seen_ids.add(rev_id)
                        page_new_reviews.append({
                            "product_id": product_id,
                            "product_name": product_name,
                            "review_title": title_str[:100],
                            "review_text": text_str[:500],
                            "rating": parsed_rating,
                            "review_date": parsed_date,
                            "source": "flipkart",
                        })

                # Check for high duplication rate
                dups = len(reviews_list) - len(page_new_reviews)
                console.print(f"  [dim]Retrieved {len(reviews_list)} | {dups} duplicates[/]")
                
                dup_ratio = dups / max(len(reviews_list), 1)
                if dup_ratio > 0.70:
                    consecutive_high_dup_pages += 1
                    if consecutive_high_dup_pages >= 3:
                        console.print(f"  [yellow]Duplication too high for {sort_order}, stopping.[/]")
                        break
                else:
                    consecutive_high_dup_pages = 0

                local_reviews.extend(page_new_reviews)

                # Politeness
                time.sleep(random.uniform(1.5, 3.0))

            except Exception as e:
                logger.error("Failed to scrape Flipkart API page %d (sort=%s): %s", page, sort_order, e)
                console.print(f"  [red]Error on page {page} ({sort_order}): {e}[/]")
                break
                
        return local_reviews

    # Execute all sort orders concurrently
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_sort_order, sort): sort for sort in SORT_ORDERS}
        for future in as_completed(futures):
            try:
                res = future.result()
                all_reviews.extend(res)
            except Exception as e:
                console.print(f"  [red]Thread error: {e}[/]")

    # 7. Fallback behavior
    if not all_reviews:
        console.print("[yellow]API returned 0 reviews. Trying Playwright fallback...[/]")
        all_reviews = scrape_with_playwright_fallback(product_url, product_id, product_name, platform="flipkart")

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
    final_urls = _generate_sort_variant_urls(urls)

    # Part E — log the plan
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
    if not all_reviews and urls:
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
