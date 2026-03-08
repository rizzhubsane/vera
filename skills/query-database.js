const PROJECT_PATH = process.env.PROJECT_PATH || require('path').resolve(__dirname, '..');

module.exports = {
    name: "query-database",
    description: "Submit a natural language question to the SQLite reviews database. Vera will search the data, analyze matching reviews, and provide a grounded, evidence-backed answer.",
    parameters: {
        question: { type: "string", description: "The natural language question to ask Vera (e.g., 'What are the main complaints about battery life for Master Buds 1?')" }
    },
    async run({ question }, { bash }) {
        const result = await bash(`cd ${PROJECT_PATH} && python -c "
import sys, json
sys.path.insert(0, '${PROJECT_PATH}')
from agent.tools.query_engine import query_reviews
result = query_reviews('''${question}''')
print(result)
"`);
        return result.stdout || result.stderr;
    }
};
