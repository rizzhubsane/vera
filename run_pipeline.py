import os
import sys
from dotenv import load_dotenv

# Force load from .env even if it's already set in the environment
load_dotenv(override=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent.tools.scraper import run_full_scrape

def main():
    print("Starting full scrape pipeline...")
    
    product_a_id = os.getenv("PRODUCT_A_ID", "product_a")
    product_a_name = os.getenv("PRODUCT_A_NAME", "Product A")
    product_b_id = os.getenv("PRODUCT_B_ID", "product_b")
    product_b_name = os.getenv("PRODUCT_B_NAME", "Product B")
    platform = os.getenv("SCRAPE_PLATFORM", "flipkart")

    api_key = os.getenv("FIRECRAWL_API_KEY")
    # print what we think the key starts with for debugging
    if api_key:
        print(f"DEBUG: Found FIRECRAWL_API_KEY starting with '{api_key[:6]}...'")

    if not api_key or api_key == "fc-PASTE-YOUR-REAL-KEY-HERE":
        print("ERROR: FIRECRAWL_API_KEY is still the placeholder in .env!")
        print("Please add your real Firecrawl API key before running.")
        sys.exit(1)

    summary = run_full_scrape(
        product_a_id=product_a_id,
        product_a_name=product_a_name,
        product_b_id=product_b_id,
        product_b_name=product_b_name,
        platform=platform,
        max_pages=2  # Just 2 pages for testing
    )
    print("Full scrape pipeline completed!")
    print(summary)

if __name__ == "__main__":
    main()
