"""
voc_agent.py — The VOC Agent brain (Vera).

Uses Groq's tool-use (function calling) API to autonomously decide
which tools to invoke, calls them, gets results, and synthesizes
a grounded answer for the user.

Usage:
    python -m agent.voc_agent              # Interactive chat (default)
    python -m agent.voc_agent chat         # Interactive chat
    python -m agent.voc_agent scrape       # Full scrape + NLP
    python -m agent.voc_agent report       # Generate reports
    python -m agent.voc_agent weekly       # Weekly delta pipeline
    python -m agent.voc_agent nlp          # Process unclassified reviews
"""

import os
import json
import sys
from datetime import datetime

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from groq import Groq
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from agent.tools.database import (
    initialize_database,
    get_review_count,
    get_reviews,
    get_last_scrape_date,
    get_sentiment_breakdown,
    get_theme_frequency,
)
from agent.tools.scraper import run_full_scrape, run_weekly_delta_scrape
from agent.tools.nlp_processor import (
    process_all_reviews,
    get_theme_insights,
    compare_products_on_theme,
)
from agent.tools.reporter import (
    generate_global_action_report,
    generate_weekly_delta_report,
    print_report_to_console,
    get_reports_list,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
AGENT_MODEL = "llama-3.3-70b-versatile"
console = Console()

PRODUCT_A_ID = os.getenv("PRODUCT_A_ID", "product_a")
PRODUCT_A_NAME = os.getenv("PRODUCT_A_NAME", "Product A")
PRODUCT_B_ID = os.getenv("PRODUCT_B_ID", "product_b")
PRODUCT_B_NAME = os.getenv("PRODUCT_B_NAME", "Product B")
PLATFORM = os.getenv("SCRAPE_PLATFORM", "amazon")

# ---------------------------------------------------------------------------
# STEP 1 — Tool Definitions (Groq / OpenAI format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "scrape_reviews",
            "description": "Scrapes product reviews from Amazon/Flipkart and stores them in the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_type": {
                        "type": "string",
                        "enum": ["full", "weekly_delta"],
                        "description": "Type of scrape run to perform.",
                    },
                    "max_pages": {
                        "type": "integer",
                        "default": 10,
                        "description": "Maximum pages to scrape per product.",
                    },
                },
                "required": ["run_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_nlp",
            "description": "Classifies sentiment and themes for all unprocessed reviews.",
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": "Query the reviews database with optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "product_a or product_b"},
                    "sentiment": {
                        "type": "string",
                        "enum": ["Positive", "Negative", "Neutral"],
                    },
                    "theme": {"type": "string", "description": "Theme tag to filter by."},
                    "keyword": {"type": "string", "description": "Keyword to search in review text."},
                    "limit": {"type": "integer", "description": "Max reviews to return."},
                }
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_statistics",
            "description": "Get full sentiment and theme statistics for one or both products.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "product_a, product_b, or omit for both.",
                    },
                }
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_products",
            "description": "Compare both products head-to-head on a specific theme.",
            "parameters": {
                "type": "object",
                "properties": {
                    "theme": {"type": "string", "description": "Theme to compare on."},
                },
                "required": ["theme"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "Generate a structured action item report for all teams.",
            "parameters": {
                "type": "object",
                "properties": {
                    "report_type": {
                        "type": "string",
                        "enum": ["global", "weekly_delta"],
                        "description": "Type of report to generate.",
                    },
                    "since_date": {
                        "type": "string",
                        "description": "ISO date for delta reports.",
                    },
                },
                "required": ["report_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_review_stats",
            "description": "Get review counts per product.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "product_a or product_b."},
                }
            },
        },
    },
]

# ---------------------------------------------------------------------------
# STEP 2 — Tool Execution Router
# ---------------------------------------------------------------------------


def execute_tool(tool_name, tool_input):
    """Route a tool call to the correct function and return the result.

    All results are serialized to JSON strings. Any exception is caught
    and returned as an error string rather than crashing.

    Args:
        tool_name: The name of the tool to execute.
        tool_input: A dict of arguments for the tool.

    Returns:
        A JSON string with the tool's result, or an error message.
    """
    try:
        if tool_name == "scrape_reviews":
            run_type = tool_input.get("run_type", "full")
            max_pages = tool_input.get("max_pages", 25)
            fn = run_weekly_delta_scrape if run_type == "weekly_delta" else run_full_scrape
            result = fn(
                PRODUCT_A_ID, PRODUCT_A_NAME,
                PRODUCT_B_ID, PRODUCT_B_NAME,
                PLATFORM,
                max_pages,
            )
            return json.dumps(result)

        elif tool_name == "process_nlp":
            return str(process_all_reviews())

        elif tool_name == "query_database":
            # Pass all filters directly to the updated get_reviews
            results = get_reviews(**tool_input)
            return json.dumps(results[:30], default=str)

        elif tool_name == "get_statistics":
            return json.dumps(get_theme_insights(tool_input.get("product_id")), default=str)

        elif tool_name == "compare_products":
            return json.dumps(
                compare_products_on_theme(tool_input["theme"], PRODUCT_A_ID, PRODUCT_B_ID),
                default=str,
            )

        elif tool_name == "generate_report":
            if tool_input.get("report_type") == "weekly_delta":
                report = generate_weekly_delta_report(
                    PRODUCT_A_ID, PRODUCT_B_ID, tool_input.get("since_date")
                )
            else:
                report = generate_global_action_report(PRODUCT_A_ID, PRODUCT_B_ID)
            return report[:1000] + "\n[Full report saved to reports/ folder]"

        elif tool_name == "get_review_stats":
            return json.dumps({"count": get_review_count(tool_input.get("product_id"))})

        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as e:
        return f"ERROR: {str(e)}"


# ---------------------------------------------------------------------------
# STEP 3 — Agent Reasoning Loop
# ---------------------------------------------------------------------------


def _load_system_prompt():
    """Load the system prompt from SOUL.md and append live DB state."""
    soul_path = os.path.join(os.path.dirname(__file__), "SOUL.md")
    system_prompt = ""

    try:
        with open(soul_path, "r") as f:
            content = f.read()
        # Extract from "## System Prompt" heading through end of file
        marker = "## System Prompt (used in API calls)"
        idx = content.find(marker)
        if idx != -1:
            # Get the text after the heading line
            lines_after = content[idx:].split("\n", 1)
            system_prompt = lines_after[1].strip() if len(lines_after) > 1 else ""
        else:
            system_prompt = content
    except FileNotFoundError:
        system_prompt = (
            "You are Vera, a Voice of Customer Intelligence Agent. "
            "Answer questions grounded in the review database."
        )

    # Append live DB state
    system_prompt += (
        f"\n\nDB State: {PRODUCT_A_NAME}={get_review_count(PRODUCT_A_ID)} reviews"
        f" | {PRODUCT_B_NAME}={get_review_count(PRODUCT_B_ID)} reviews"
        f" | Last scrape: {get_last_scrape_date()}"
        f" | Today: {datetime.utcnow().strftime('%Y-%m-%d')}"
    )

    return system_prompt


def run_agent(user_message, conversation_history=None, max_turns=10):
    """Run the agent reasoning loop for a single user message.

    Uses Groq's tool-use API: the LLM autonomously decides which tools
    to call, receives results, and synthesizes a final grounded answer.

    Args:
        user_message: The user's natural language input.
        conversation_history: Optional list of prior messages for
            multi-turn context.
        max_turns: Maximum number of LLM ↔ tool round-trips (default 10).

    Returns:
        A tuple (final_text, messages) where final_text is the agent's
        response and messages is the full conversation history.
    """
    system_prompt = _load_system_prompt()

    if conversation_history is None:
        conversation_history = []

    messages = conversation_history + [{"role": "user", "content": user_message}]

    for turn in range(max_turns):
        try:
            response = client.chat.completions.create(
                model=AGENT_MODEL,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                tools=TOOLS,
                tool_choice="auto",
                max_tokens=2048,
            )
        except Exception as e:
            error_msg = str(e)
            if "tool_use_failed" in error_msg:
                console.print(f"[yellow]LLM syntax error, retrying tool call...[/]")
                messages.append({
                    "role": "user", 
                    "content": "Your previous tool call failed due to a JSON parsing error. Please try calling the tool again, ensuring you use perfect formatting."
                })
                continue
                
            error_str = f"LLM API error: {error_msg}"
            console.print(f"[red]{error_str}[/]")
            return error_str, messages

        choice = response.choices[0]
        msg = choice.message

        # Build assistant message dict for history
        assistant_msg = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_msg)

        # Check if the model wants to call tools
        if choice.finish_reason == "tool_calls" and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                tool_input = json.loads(tc.function.arguments)
                console.print(f"[dim]🔧 Vera calling: {tool_name}({json.dumps(tool_input)})[/dim]")
                result = execute_tool(tool_name, tool_input)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        elif choice.finish_reason == "stop":
            final_text = msg.content or ""
            return final_text, messages

    return "Agent reached max reasoning steps.", messages


# ---------------------------------------------------------------------------
# STEP 4 — Interactive Chat
# ---------------------------------------------------------------------------


def start_interactive_chat():
    """Launch an interactive chat session with Vera in the terminal.

    Provides slash commands for common operations and a free-form
    conversation mode backed by the Groq tool-use loop.
    """
    # Welcome banner
    count_a = get_review_count(PRODUCT_A_ID)
    count_b = get_review_count(PRODUCT_B_ID)
    last_scrape = get_last_scrape_date()

    banner = (
        f"[bold cyan]👋 Hi, I'm Vera[/bold cyan] — your Voice of Customer Intelligence Analyst.\n\n"
        f"[bold]Model:[/bold] {AGENT_MODEL}\n"
        f"[bold]{PRODUCT_A_NAME}:[/bold] {count_a} reviews\n"
        f"[bold]{PRODUCT_B_NAME}:[/bold] {count_b} reviews\n"
        f"[bold]Last scrape:[/bold] {last_scrape}\n\n"
        f"[dim]Commands:[/dim]\n"
        f"  [cyan]/report global[/cyan]   — Generate full action report\n"
        f"  [cyan]/report weekly[/cyan]   — Generate weekly delta report\n"
        f"  [cyan]/scrape[/cyan]          — Run full scrape for both products\n"
        f"  [cyan]/nlp[/cyan]             — Process NLP on unclassified reviews\n"
        f"  [cyan]/stats[/cyan]           — Show theme insights for both products\n"
        f"  [cyan]/quit[/cyan]            — Exit\n"
    )
    console.print(Panel(banner, title="[bold green]Vera — VOC Agent[/bold green]", border_style="green"))

    conversation_history = []

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]You[/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # --- Slash commands ---
        if user_input.lower() == "/quit":
            console.print("[dim]Goodbye! 👋[/dim]")
            break

        elif user_input.lower() == "/report global":
            console.print("[dim]Generating global action report...[/dim]")
            report = generate_global_action_report(PRODUCT_A_ID, PRODUCT_B_ID)
            print_report_to_console(report)
            continue

        elif user_input.lower() == "/report weekly":
            console.print("[dim]Generating weekly delta report...[/dim]")
            report = generate_weekly_delta_report(PRODUCT_A_ID, PRODUCT_B_ID)
            print_report_to_console(report)
            continue

        elif user_input.lower() == "/scrape":
            response, conversation_history = run_agent(
                "Run a full scrape for both products.", conversation_history
            )
            console.print(Panel(Markdown(response), title="[bold green]Vera[/bold green]", border_style="green"))
            continue

        elif user_input.lower() == "/nlp":
            response, conversation_history = run_agent(
                "Process NLP on all unprocessed reviews.", conversation_history
            )
            console.print(Panel(Markdown(response), title="[bold green]Vera[/bold green]", border_style="green"))
            continue

        elif user_input.lower() == "/stats":
            console.print("[dim]Fetching insights...[/dim]")
            insights_a = get_theme_insights(PRODUCT_A_ID)
            insights_b = get_theme_insights(PRODUCT_B_ID)
            console.print(Panel(
                json.dumps({"product_a": insights_a, "product_b": insights_b}, indent=2, default=str),
                title="[bold yellow]Theme Insights[/bold yellow]",
                border_style="yellow",
            ))
            continue

        # --- Free-form conversation ---
        response, conversation_history = run_agent(user_input, conversation_history)
        console.print(Panel(Markdown(response), title="[bold green]Vera[/bold green]", border_style="green"))


# ---------------------------------------------------------------------------
# STEP 5 — Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    initialize_database()  # Ensure tables exist before any DB read
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "chat":
            start_interactive_chat()
        elif cmd == "scrape":
            resp, _ = run_agent("Run a full scrape for both products, then process NLP.")
            console.print(Panel(Markdown(resp), title="[bold green]Vera[/bold green]", border_style="green"))
        elif cmd == "report":
            resp, _ = run_agent("Generate the global action report and weekly delta report.")
            console.print(Panel(Markdown(resp), title="[bold green]Vera[/bold green]", border_style="green"))
        elif cmd == "weekly":
            resp, _ = run_agent("Run weekly delta scrape, process NLP, generate weekly report.")
            console.print(Panel(Markdown(resp), title="[bold green]Vera[/bold green]", border_style="green"))
        elif cmd == "nlp":
            resp, _ = run_agent("Process NLP on all unprocessed reviews.")
            console.print(Panel(Markdown(resp), title="[bold green]Vera[/bold green]", border_style="green"))
        else:
            console.print(f"[red]Unknown command: {cmd}[/red]")
            console.print("Usage: python -m agent.voc_agent [chat|scrape|report|weekly|nlp]")
    else:
        start_interactive_chat()
