const PROJECT_PATH = process.env.PROJECT_PATH || require('path').resolve(__dirname, '..');

module.exports = {
    name: "generate-report",
    description: "Generates a structured Markdown action item report segmented by team (Product, Marketing, Support). Global report = all historical data. Weekly delta = this week's changes + spike detection.",
    parameters: {
        report_type: {
            type: "string",
            enum: ["global", "weekly_delta"],
            description: "global = full history, weekly_delta = last 7 days with spike detection"
        },
        since_date: {
            type: "string",
            description: "For weekly_delta only: start date as YYYY-MM-DD (defaults to 7 days ago)"
        }
    },
    async run({ report_type = "global", since_date }, { bash }) {
        const since = since_date || "";
        const result = await bash(`cd ${PROJECT_PATH} && python -c "
import sys, os, json
sys.path.insert(0, '${PROJECT_PATH}')
from dotenv import load_dotenv
load_dotenv()
from agent.tools.reporter import generate_global_action_report, generate_weekly_delta_report
a = os.getenv('PRODUCT_A_ID', 'product_a')
b = os.getenv('PRODUCT_B_ID', 'product_b')
if '${report_type}' == 'weekly_delta':
    r = generate_weekly_delta_report(a, b, '${since}' if '${since}' else None)
else:
    r = generate_global_action_report(a, b)
print(r[:2500])
print('\\n[Full report saved to reports/ folder]')
"`);
        return result.stdout || result.stderr;
    }
};
