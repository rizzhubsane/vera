import csv
import os
import sys

# Add project root to path so imports resolve correctly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tools.database import bulk_insert_reviews

BULK_FILE  = os.path.join(os.path.dirname(__file__), "flipkart (2).csv")
PRODUCT_ID = "noise_buds_vs102"
PRODUCT_NAME = "Noise Buds VS102"
PLATFORM   = "flipkart"

def main():
    if not os.path.exists(BULK_FILE):
        print(f"[Import] ERROR: File not found: {BULK_FILE}")
        print("[Import] Please copy flipkart (2).csv into the data/ folder.")
        sys.exit(1)

    with open(BULK_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        raw_rows = list(reader)

    print(f"[Import] Loaded {len(raw_rows)} raw rows from CSV.")

    reviews = []
    skipped = 0

    # Skip header-like rows (first 5 rows are usually junk in these scrapes)
    for row in raw_rows[5:]:
        if not row:
            continue
            
        # The CSV is highly unstructured. We'll find the longest string in the row 
        # and assume that's the review body.
        longest_str = ""
        for cell in row:
            cel_str = str(cell).strip()
            if len(cel_str) > len(longest_str):
                longest_str = cel_str

        body = longest_str
        if len(body) < 10:  # Skip if it's too short to be a real review
            skipped += 1
            continue

        # Try to extract rating if possible (usually in col 0)
        rating_str = str(row[0]).strip()
        rating = None
        try:
            rating = float(rating_str)
        except ValueError:
            pass

        # --- KEYS MATCH EXACTLY WITH database.py SCHEMA ---
        reviews.append({
            "product_id": PRODUCT_ID,
            "product_name": PRODUCT_NAME,
            "review_title": "",  # null/empty since not consistently provided
            "review_text": body,
            "rating": rating,
            "review_date": "unknown",  # source has messy dates; placeholder prevents delta logic issues
            "source": PLATFORM,
        })

    print(f"[Import] Prepared {len(reviews)} reviews. Skipped {skipped} (too short).")

    if not reviews:
        print("[Import] Nothing to import. Exiting.")
        return

    # Call bulk_insert_reviews and handle whatever it returns
    result = bulk_insert_reviews(reviews)

    # Note: database.py bulk_insert_reviews() returns a dict like:
    # {"inserted": inserted, "duplicates": duplicates}
    if isinstance(result, dict) and "inserted" in result and "duplicates" in result:
        print(f"[Import] Done. Inserted: {result['inserted']} | Duplicates skipped: {result['duplicates']}")
    else:
        print(f"[Import] Done. Result: {result}")

if __name__ == "__main__":
    main()
