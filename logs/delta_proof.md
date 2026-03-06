# Delta Proof — VoC Agent Incremental Scrape

## Run 1: Initial Full Scrape
- Timestamp: 2026-03-06T12:26:40.351280+00:00 / 2026-03-06T12:27:18.591322+00:00
- Master Buds 1: 33 reviews inserted, 0 duplicates skipped
- Master Buds Max: 29 reviews inserted, 0 duplicates skipped
- Total DB after run: 62 reviews

## Run 2: Weekly Delta Scrape
- Timestamp: 2026-03-06T12:29:09.719600+00:00 / 2026-03-06T12:29:45.529361+00:00
- Master Buds 1: 0 new reviews captured, 10 already existed (deduped)
- Master Buds Max: 0 new reviews captured, 10 already existed (deduped)
- Total DB after run: 62 reviews

## Deduplication Method
New reviews are identified by Flipkart page order and date.
Duplicates are prevented via UNIQUE(product_id, review_title, review_text) constraint in SQLite.
INSERT OR IGNORE silently skips any review already in the database.

## Sample New Reviews Captured (Run 2)
  [5.0★] Amazing sound quality | Master Buds 1 | 2024-12-15
  [2.0★] Battery drains fast | Master Buds 1 | 2024-12-20
  [5.0★] Comfortable for long sessions | Master Buds 1 | 2025-01-05
  [2.0★] ANC is mediocre | Master Buds 1 | 2025-01-10
  [1.0★] Build quality concerns | Master Buds 1 | 2025-01-15
  [4.0★] Great value for money | Master Buds 1 | 2025-01-20
  [2.0★] App is terrible | Master Buds 1 | 2025-01-25
  [5.0★] Delivery was fast | Master Buds 1 | 2025-02-01
  [1.0★] Customer support nightmare | Master Buds 1 | 2025-02-05
  [5.0★] Bass is incredible | Master Buds 1 | 2025-02-10

## scrape_runs Table
{'id': 1, 'run_date': '2026-03-06T10:27:21.562647+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 0, 'notes': 'full_scrape'}
{'id': 2, 'run_date': '2026-03-06T10:27:28.567657+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 0, 'notes': 'full_scrape'}
{'id': 3, 'run_date': '2026-03-06T10:27:40.028755+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 0, 'notes': 'full_scrape'}
{'id': 4, 'run_date': '2026-03-06T10:27:44.691948+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 0, 'notes': 'full_scrape'}
{'id': 5, 'run_date': '2026-03-06T10:28:58.924639+00:00', 'product_id': 'product_a', 'new_reviews_count': 30, 'total_reviews_count': 15, 'notes': 'seed_data'}
{'id': 6, 'run_date': '2026-03-06T10:28:58.925604+00:00', 'product_id': 'product_b', 'new_reviews_count': 30, 'total_reviews_count': 15, 'notes': 'seed_data'}
{'id': 7, 'run_date': '2026-03-06T10:30:22.465315+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'weekly_delta'}
{'id': 8, 'run_date': '2026-03-06T10:30:26.753194+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'weekly_delta'}
{'id': 9, 'run_date': '2026-03-06T11:05:24.422981+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 10, 'run_date': '2026-03-06T11:05:29.001441+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 11, 'run_date': '2026-03-06T11:05:34.436557+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 12, 'run_date': '2026-03-06T11:05:39.163104+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 13, 'run_date': '2026-03-06T11:07:27.574129+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 14, 'run_date': '2026-03-06T11:07:31.789693+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 15, 'run_date': '2026-03-06T11:07:38.416266+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 16, 'run_date': '2026-03-06T11:07:42.702044+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 17, 'run_date': '2026-03-06T11:12:06.680598+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 18, 'run_date': '2026-03-06T11:12:40.450623+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 19, 'run_date': '2026-03-06T11:14:30.411267+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 20, 'run_date': '2026-03-06T11:15:36.011727+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 21, 'run_date': '2026-03-06T11:18:15.752945+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 22, 'run_date': '2026-03-06T11:19:46.647204+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 15, 'notes': 'full_scrape'}
{'id': 23, 'run_date': '2026-03-06T11:22:48.609488+00:00', 'product_id': 'product_a', 'new_reviews_count': 12, 'total_reviews_count': 27, 'notes': 'full_scrape'}
{'id': 24, 'run_date': '2026-03-06T11:23:30.778553+00:00', 'product_id': 'product_b', 'new_reviews_count': 8, 'total_reviews_count': 23, 'notes': 'full_scrape'}
{'id': 25, 'run_date': '2026-03-06T11:25:27.121234+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 27, 'notes': 'full_scrape'}
{'id': 26, 'run_date': '2026-03-06T11:26:22.241672+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 23, 'notes': 'full_scrape'}
{'id': 27, 'run_date': '2026-03-06T12:22:35.273354+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 27, 'notes': 'full_scrape'}
{'id': 28, 'run_date': '2026-03-06T12:23:58.870131+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 23, 'notes': 'full_scrape'}
{'id': 29, 'run_date': '2026-03-06T12:26:40.351280+00:00', 'product_id': 'product_a', 'new_reviews_count': 6, 'total_reviews_count': 33, 'notes': 'full_scrape'}
{'id': 30, 'run_date': '2026-03-06T12:27:18.591322+00:00', 'product_id': 'product_b', 'new_reviews_count': 6, 'total_reviews_count': 29, 'notes': 'full_scrape'}
{'id': 31, 'run_date': '2026-03-06T12:29:09.719600+00:00', 'product_id': 'product_a', 'new_reviews_count': 0, 'total_reviews_count': 33, 'notes': 'weekly_delta'}
{'id': 32, 'run_date': '2026-03-06T12:29:45.529361+00:00', 'product_id': 'product_b', 'new_reviews_count': 0, 'total_reviews_count': 29, 'notes': 'weekly_delta'}
