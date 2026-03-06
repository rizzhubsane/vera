import os
import requests
from dotenv import load_dotenv
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

load_dotenv()
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")
URL = "https://www.amazon.in/product-reviews/B0DK979YJ7/"

api_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={quote_plus(URL)}&render=true"

print(f"Fetching: {URL}")
response = requests.get(api_url)
print(f"Status Code: {response.status_code}")

html = response.text
soup = BeautifulSoup(html, "html.parser")
reviews = soup.select('div[data-hook="review"]')
print(f"Found {len(reviews)} review elements via data-hook='review'.")

# Let's save the HTML to inspect if it's 0
if len(reviews) == 0:
    with open("data/scraper_debug.html", "w") as f:
        f.write(html)
    print("Saved HTML to data/scraper_debug.html")
