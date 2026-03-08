const PROJECT_PATH = process.env.PROJECT_PATH || require('path').resolve(__dirname, '..');

module.exports = {
  name: "scrape-reviews",
  description: "Scrapes public Flipkart reviews for Master Buds 1 and Master Buds Max and stores them in the SQLite database. Use run_type='full' for initial scrape, 'weekly_delta' for weekly incremental runs.",
  parameters: {
    run_type: {
      type: "string",
      enum: ["full", "weekly_delta"],
      description: "full = scrape all pages (initial), weekly_delta = only new reviews",
      default: "full"
    },
    max_pages: {
      type: "number",
      description: "Max review pages per product (20 reviews/page on Flipkart)",
      default: 30
    }
  },
  async run({ run_type = "full", max_pages = 30 }, { bash }) {
    const result = await bash(`cd ${PROJECT_PATH} && python -c "
import os, json, sys
sys.path.insert(0, '${PROJECT_PATH}')
from dotenv import load_dotenv
load_dotenv()
from agent.tools.scraper import run_full_scrape, run_weekly_delta_scrape
fn = run_weekly_delta_scrape if '${run_type}' == 'weekly_delta' else run_full_scrape
result = fn(
    os.getenv('PRODUCT_A_ID', 'product_a'),
    os.getenv('PRODUCT_A_NAME', 'Master Buds 1'),
    os.getenv('PRODUCT_B_ID', 'product_b'),
    os.getenv('PRODUCT_B_NAME', 'Master Buds Max'),
    os.getenv('SCRAPE_PLATFORM', 'flipkart'),
    int(${max_pages})
)
print(json.dumps(result))
"`);
    return result.stdout || result.stderr;
  }
};
