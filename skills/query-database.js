const PROJECT_PATH = "/Users/rishabhsain/Desktop/voc";

module.exports = {
    name: "query-database",
    description: "Query the SQLite reviews database with flexible filters. Always call this before answering questions about reviews to ensure grounded, data-backed responses.",
    parameters: {
        product_id: { type: "string", description: "product_a or product_b (omit for both)" },
        sentiment: { type: "string", enum: ["Positive", "Negative", "Neutral"], description: "Filter by sentiment" },
        theme: { type: "string", description: "Filter by theme e.g. 'ANC', 'Battery Life', 'Comfort & Fit'" },
        keyword: { type: "string", description: "Keyword to search in review text" },
        limit: { type: "number", description: "Max results to return", default: 20 }
    },
    async run({ product_id, sentiment, theme, keyword, limit = 20 }, { bash }) {
        const args = JSON.stringify({ product_id, sentiment, theme, keyword, limit });
        const result = await bash(`cd ${PROJECT_PATH} && python -c "
import sys, json
sys.path.insert(0, '${PROJECT_PATH}')
from agent.tools.database import get_reviews
args = json.loads('''${args}''')
filters = {k: v for k, v in args.items() if v is not None}
reviews = get_reviews(**filters)
print(json.dumps(reviews, indent=2))
"`);
        return result.stdout || result.stderr;
    }
};
