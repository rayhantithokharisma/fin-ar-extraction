#!/usr/bin/env python3
"""
Wren AI Conversational GenBI Chat Agent
======================================
Autonomous text-to-SQL analytics agent powered by Wren Engine's semantic layer
and OpenAI GPT-5.6-Luna (primary) / GPT-5.4-Mini (fallback).

Connects natively to idxfin Medallion Lakehouse (data/idxfin.duckdb) via
lock-free read-only semantics.

Features rich terminal rendering for Markdown tables, colored syntax, and tool traces.

Usage:
  # One-shot query:
  python wren/chat_agent.py -q "Berapa laba bersih dan pendapatan ESSA tahun 2024 dan 2025?"

  # Interactive REPL:
  python wren/chat_agent.py
"""

import argparse
import os
import sys
import warnings
from pathlib import Path
from dotenv import load_dotenv

# Suppress deprecation warnings from underlying libraries for clean terminal display
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Load root .env first, then wren/.env.wren as fallback
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent / ".env.wren")

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme

# Setup Rich console with custom financial palette
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "danger": "bold red",
    "success": "bold green",
    "tool": "bold bright_yellow",
    "sql": "bright_cyan",
})
console = Console(theme=custom_theme)

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent
from wren_langchain import WrenToolkit

# Indonesian Capital Market (IDX) & PSAK Grounding Prompt
PSAK_SYSTEM_GROUNDING = """
You are an expert Chief Financial Analyst and Lead Data Engineer for the Indonesian Stock Exchange (IDX / BEI).
You have access to the Wren Engine semantic layer over the idxfin Medallion DuckDB lakehouse.

Domain & Accounting Rules (PSAK / IFRS):
1. Tickers (issuer_id): Standard 4-letter IDX stock symbols (e.g., ESSA, BBCA, NCKL, ADRO, MAPI, ARCI, BREN).
2. PSAK 101 Sharia Accounting: For Islamic commercial banks or conglomerates with sharia business units (e.g., BBCA / BCA Syariah), 'temporary_syirkah_funds' (Dana Syirkah Temporer) is presented between liabilities and equity. The balance sheet identity is:
   Total Assets = Total Liabilities + Temporary Syirkah Funds + Total Equity.
3. Currency Isolation & FX Conversions: NEVER aggregate nominal figures across currencies (IDR vs USD) without conversion. Always check the 'currency' field before reporting or summing numbers.
   - For foreign exchange conversions (USD to IDR or vice-versa), use the 'fx_rate' model (`pair = 'USD/IDR'`).
   - For balance sheet point-in-time metrics, use the period-end closing rate `rate_close` (e.g. at `2024-12-31` or `2025-12-31`).
   - For period items (Revenue, Net Profit, Operating Cash Flow) or estimating commodity price realizations across a full year, use `yearly_avg_rate` (or monthly average).
   - For issuers reporting in USD (e.g. ESSA, ADRO, BREN, ARCI, ABMM), you can compute IDR-equivalent metrics or convert per-unit physical commodity prices (e.g. USD/MT to IDR/MT) using `fx_rate`.
4. Commercial Banking Liquidity & Quality: For banks (BBCA, BBSI), the balance sheet is presented in order of liquidity; current_assets and current_liabilities are NULL.
   - Core accounting metrics in 'financial_summary' are 'bank_loans', 'bank_deposits', and 'loan_to_deposit_ratio_pct' (LDR).
   - Regulatory credit quality metrics (NPL Gross, NPL Net, Loan at Risk / LAR, CAR, NIM) originate from Annual Report disclosures or 'operational_kpi'. If not found in the models, clearly state which model was checked and what metrics are available.
5. Commodity Benchmarks & Proxies: Monthly commodity prices and physical spot benchmarks are in 'commodity_trend_monthly':
   - Gold: 'GC=F' (USD/troy oz)
   - Crude Oil: 'CL=F' (WTI) and 'BZ=F' (Brent) in USD/bbl
   - Physical Ammonia Spot Benchmark: 'NH3=F' (USD/ton) — actual global physical spot price for anhydrous ammonia (e.g. ~$563/ton in 2024, ~$730/ton in 2025). When asked for ammonia market/benchmark prices, query 'NH3=F'.
   - Ammonia Feedstock Benchmark: 'NG=F' (Henry Hub Natural Gas in USD/MMBtu — accounts for 70-85% of cash production cost).
   - Ammonia Producer Proxies: 'ESSA.JK' (Domestic ammonia producer, IDR/share), 'CF' (Global ammonia producer proxy, USD/share), 'UAN' (Ammonia fertilizer proxy, USD/share).
6. Bilingual Disclosure Terminology: Line items (label_raw) in Indonesian IDX filings are in Bahasa Indonesia. When querying commodities or operations, always query both Indonesian and English terms:
   - Ammonia = 'Amoniak' or 'Amonia' (e.g. `LOWER(label_raw) LIKE '%amoniak%' OR LOWER(label_raw) LIKE '%ammonia%'`)
   - LPG = 'Elpiji' or 'LPG'
   - Condensate = 'Kondensat'
   - Nickel = 'Nikel'
   - Coal = 'Batubara'
   - Crude Palm Oil = 'Kelapa Sawit' or 'CPO'
   - Production volume = 'Produksi' or 'Volume Produksi'
   - Sales volume = 'Penjualan' or 'Volume Penjualan'
7. Output Formatting: Always format financial numbers with thousands separators and explicit currency (e.g., IDR 55,066,895 or USD 55,066,895), physical volumes with their unit (e.g. 748,274 metrik ton), format ratios as percentages with 2 decimals (e.g. 18.67%), and display tabular comparisons using clean Markdown tables.
"""

def create_agent(project_path: Path, model_override: str | None = None):
    toolkit = WrenToolkit.from_project(project_path)
    tools = toolkit.get_tools()

    primary_model = model_override or os.getenv("DEFAULT_MODEL", "gpt-5.6-luna")
    fallback_model = os.getenv("FALLBACK_MODEL", "gpt-5.4-mini")

    combined_prompt = f"{toolkit.system_prompt()}\n\n{PSAK_SYSTEM_GROUNDING}"

    for model_name in [primary_model, fallback_model, "gpt-4o", "gpt-4o-mini"]:
        try:
            # reasoning_effort="none" is required when binding tools to gpt-5.x in chat/completions
            llm = ChatOpenAI(
                model=model_name,
                temperature=0,
                reasoning_effort="none",
            )
            agent = create_react_agent(
                llm,
                tools=tools,
                prompt=combined_prompt,
            )
            return agent, model_name, toolkit
        except Exception as e:
            console.print(f"[warning]Warning:[/warning] Failed initializing model {model_name}: {e}. Trying fallback...")

    raise RuntimeError("Could not initialize any OpenAI LLM model.")


def print_execution_trace(messages):
    """Render intermediate tool calls using Rich syntax highlighting and panels."""
    has_trace = False
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            if not has_trace:
                console.print()
                console.rule("[bold cyan]Agent Execution Trace[/bold cyan]")
                has_trace = True

            for tc in msg.tool_calls:
                name = tc.get("name", "unknown_tool")
                args = tc.get("args", {})

                if name == "wren_query" and "sql" in args:
                    sql_text = args["sql"]
                    limit = args.get("limit", 100)
                    syntax = Syntax(sql_text, "sql", theme="monokai", word_wrap=True)
                    panel = Panel(
                        syntax,
                        title=f"[tool]Tool Call: {name}[/tool]",
                        subtitle=f"[dim]limit: {limit}[/dim]",
                        border_style="bright_yellow",
                        expand=False
                    )
                    console.print(panel)
                else:
                    args_text = str(args) if args else "[dim](no arguments)[/dim]"
                    panel = Panel(
                        args_text,
                        title=f"[tool]Tool Call: {name}[/tool]",
                        border_style="bright_yellow",
                        expand=False
                    )
                    console.print(panel)

    if has_trace:
        console.rule(style="dim cyan")
        console.print()


def run_query(agent, toolkit, question: str, explain: bool = False) -> str:
    messages = [HumanMessage(content=question)]
    response = agent.invoke({"messages": messages}, {"recursion_limit": 15})

    if explain:
        print_execution_trace(response["messages"])

    last_message = response["messages"][-1]
    return last_message.content


def repl(agent, toolkit, model_name: str, explain: bool = False):
    welcome_content = Text.from_markup(
        "[bold bright_cyan]Wren AI GenBI Conversational Analytics[/bold bright_cyan] [white](IDX Medallion Lakehouse)[/white]\n"
        f"[dim]Model:[/dim] [bold green]{model_name}[/bold green] | "
        f"[dim]Semantic Engine:[/dim] [bold yellow]Wren Engine (Rust / DataFusion)[/bold yellow]\n"
        f"[dim]Data Lakehouse:[/dim] [bold magenta]data/idxfin.duckdb (Lock-Free Read-Only)[/bold magenta]\n\n"
        "[italic dim]Type your financial questions in English or Bahasa Indonesia.\n"
        "Type 'exit', 'quit', or 'q' to terminate.[/italic dim]"
    )
    console.print(Panel(welcome_content, title="🏛️  IDX-Fin Conversational Agent", border_style="bright_blue", padding=(1, 2)))

    while True:
        try:
            prompt = console.input("\n[bold cyan]IDX-Fin (Wren AI)[/bold cyan] [bright_yellow]>[/bright_yellow] ").strip()
            if not prompt:
                continue
            if prompt.lower() in ("exit", "quit", "q"):
                console.print("\n[bold green]Sampai jumpa / Goodbye![/bold green]\n")
                break

            with console.status("[bold cyan]Analyzing question with Wren Engine...[/bold cyan]", spinner="dots"):
                answer = run_query(agent, toolkit, prompt, explain=explain)

            console.print()
            console.rule("[bold green]IDX-Fin Response[/bold green]", style="green")
            console.print(Markdown(answer))
            console.rule(style="dim")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Exiting session.[/dim]\n")
            break
        except Exception as e:
            console.print(f"\n[danger]Error executing query:[/danger] {e}\n")


def main():
    parser = argparse.ArgumentParser(description="Wren AI Conversational GenBI Chat Agent")
    parser.add_argument("-q", "--question", type=str, help="Single query to ask the agent")
    parser.add_argument("-m", "--model", type=str, help="Override OpenAI model")
    parser.add_argument("--explain", action="store_true", help="Print intermediate tool calls & SQL")
    args = parser.parse_args()

    project_path = Path(__file__).parent / "projects" / "idxfin"
    agent, active_model, toolkit = create_agent(project_path, model_override=args.model)

    if args.question:
        answer = run_query(agent, toolkit, args.question, explain=args.explain)
        console.print(Markdown(answer))
    else:
        repl(agent, toolkit, active_model, explain=args.explain)


if __name__ == "__main__":
    main()
