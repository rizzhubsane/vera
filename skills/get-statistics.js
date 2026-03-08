const PROJECT_PATH = process.env.PROJECT_PATH || require('path').resolve(__dirname, '..');

module.exports = {
    name: "get-statistics",
    description: "Returns full sentiment breakdown and theme frequency statistics from the database. Call this to ground any comparative or analytical answer with real review counts.",
    parameters: {
        product_id: { type: "string", description: "product_a, product_b, or omit for both" }
    },
    async run({ product_id }, { bash }) {
        const pid = product_id || "";
        const result = await bash(`cd ${PROJECT_PATH} && python -c "
import sys, json
sys.path.insert(0, '${PROJECT_PATH}')
from agent.tools.nlp_processor import get_theme_insights
stats = get_theme_insights('${pid}' if '${pid}' else None)
print(json.dumps(stats, indent=2))
"`);
        return result.stdout || result.stderr;
    }
};
