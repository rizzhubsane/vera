const PROJECT_PATH = process.env.PROJECT_PATH || require('path').resolve(__dirname, '..');

module.exports = {
    name: "process-nlp",
    description: "Runs sentiment analysis (Positive/Negative/Neutral) and theme tagging on all unprocessed reviews in the database. Returns total count processed.",
    parameters: {},
    async run(_, { bash }) {
        const result = await bash(`cd ${PROJECT_PATH} && python -c "
import sys
sys.path.insert(0, '${PROJECT_PATH}')
from agent.tools.nlp_processor import process_all_reviews
total = process_all_reviews()
print(f'NLP complete. Processed {total} reviews.')
"`);
        return result.stdout || result.stderr;
    }
};
