import os
from dotenv import load_dotenv

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.tools.scraper import scrape_flipkart_reviews

load_dotenv()

urls_a = [
    os.getenv("PRODUCT_A_URL_1", ""),
]

if urls_a and urls_a[0]:
    print(f"Testing URL: {urls_a[0]}")
    reviews = scrape_flipkart_reviews(
        product_url=urls_a[0],
        product_id="product_a",
        product_name="Product A Test",
        max_pages=2
    )
    print(f"Got {len(reviews)} reviews")
    if reviews:
        print(f"First review title: {reviews[0]['review_title']}")
else:
    print("No PRODUCT_A_URL found in .env")
