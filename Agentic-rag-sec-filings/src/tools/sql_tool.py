import os
import sys
import pyodbc
import json

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
import config

from dotenv import load_dotenv
load_dotenv(override=True)


def get_connection() -> pyodbc.Connection:
    """Return a live pyodbc connection to SQL Server."""
    return pyodbc.connect(config.SQL_CONNECTION_STRING)


def run_sql_query(query: str) -> list[dict]:
    """
    Execute a SQL query against SQL Server.
    Returns list of row dicts.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return rows
    except pyodbc.Error as e:
        return [{"error": str(e)}]
    finally:
        conn.close()


def get_schema() -> str:
    """Return table schema description for the LLM."""
    return """
Database: SQL Server
Table: financials
Columns:
  - company     NVARCHAR : 'tesla', 'ford', 'rivian'
  - cik         NVARCHAR : SEC CIK number
  - metric      NVARCHAR : XBRL metric name
  - fiscal_year INT      : e.g. 2022, 2023, 2024
  - period_end  NVARCHAR : date string e.g. '2024-12-31'
  - value       FLOAT    : monetary value in USD (raw dollars, not thousands)
  - unit        NVARCHAR : 'USD'
  - filed       NVARCHAR : date the filing was submitted

Available metrics:
  Revenues, RevenueFromContractWithCustomerExcludingAssessedTax,
  NetIncomeLoss, ResearchAndDevelopmentExpense, GrossProfit,
  OperatingIncomeLoss, EarningsPerShareBasic

CRITICAL DEDUPLICATION RULE:
The table has multiple rows per company+metric+fiscal_year.
You MUST deduplicate by selecting the row with the longest period_end 
(most complete annual period) AND latest filed date.

Always use this EXACT pattern — never deviate from it:

  SELECT company, metric, fiscal_year, value, filed, period_end
  FROM financials f1
  WHERE f1.period_end = (
      SELECT MAX(f2.period_end)
      FROM financials f2
      WHERE f2.company  = f1.company
        AND f2.metric   = f1.metric
        AND f2.fiscal_year = f1.fiscal_year
  )
  AND f1.filed = (
      SELECT MAX(f3.filed)
      FROM financials f3
      WHERE f3.company   = f1.company
        AND f3.metric    = f1.metric
        AND f3.fiscal_year = f1.fiscal_year
        AND f3.period_end = f1.period_end
  )
  AND <your filters here>

IMPORTANT SQL SERVER RULES:
- Use TOP N not LIMIT N
- Use single quotes for strings
- Do NOT use tuple/row-value constructors like (a,b) IN (SELECT ...)
- Always include metric filter inside the subquery
- Return ONLY the SQL query, no markdown, no backticks
"""


def nl_to_sql(natural_language_query: str) -> str:
    """Use GPT to convert a natural language question to T-SQL."""
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    prompt = f"""You are a T-SQL expert working with Microsoft SQL Server.
Convert the user's financial question into a valid T-SQL query.

{get_schema()}

Rules:
- Only SELECT statements — never INSERT, UPDATE, DELETE, DROP
- Always lowercase company names: 'tesla', 'ford', 'rivian'
- For revenue use both 'Revenues' AND 'RevenueFromContractWithCustomerExcludingAssessedTax'
- Always deduplicate using the MAX(filed) subquery pattern shown above
- Use TOP N not LIMIT N
- Return ONLY the SQL query, no explanation, no markdown, no backticks

User question: {natural_language_query}
T-SQL:"""

    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    sql = response.choices[0].message.content.strip()
    sql = sql.replace("```sql", "").replace("```", "").strip()
    return sql


def deduplicate_rows(rows: list[dict]) -> list[dict]:
    """
    Safety-net dedup: keep only the latest filed row 
    per company+metric+fiscal_year.
    """
    seen = {}
    for row in rows:
        key = (
            row.get("company", ""),
            row.get("metric", ""),
            row.get("fiscal_year", ""),
        )
        if key == ("", "", ""):
            return rows  # can't dedup without these columns
        filed = row.get("filed", "")
        if key not in seen or filed > seen[key]["filed"]:
            seen[key] = row
    return list(seen.values()) if seen else rows

def filter_relevant_rows(rows: list[dict], query: str) -> list[dict]:
    """
    Post-filter: if query mentions a specific metric keyword,
    keep only rows matching that metric.
    """
    query_lower = query.lower()

    # Metric keyword mapping
    metric_keywords = {
        "revenue":      ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
        "net income":   ["NetIncomeLoss"],
        "net loss":     ["NetIncomeLoss"],
        "r&d":          ["ResearchAndDevelopmentExpense"],
        "research":     ["ResearchAndDevelopmentExpense"],
        "gross profit": ["GrossProfit"],
        "operating":    ["OperatingIncomeLoss"],
        "earnings per": ["EarningsPerShareBasic"],
        "eps":          ["EarningsPerShareBasic"],
    }

    target_metrics = []
    for keyword, metrics in metric_keywords.items():
        if keyword in query_lower:
            target_metrics.extend(metrics)

    if not target_metrics:
        return rows  # no specific metric detected — return all

    filtered = [r for r in rows if r.get("metric") in target_metrics]
    return filtered if filtered else rows  # fallback to all if filter too aggressive


def sql_tool(natural_language_query: str) -> dict:
    """
    Full pipeline: NL question → T-SQL → execute → deduplicate → return.
    Returns { sql, results, error }
    """
    sql     = nl_to_sql(natural_language_query)
    rows    = run_sql_query(sql)
    error   = rows[0].get("error") if rows and "error" in rows[0] else None

    if not error:
        rows = deduplicate_rows(rows)
        rows = filter_relevant_rows(rows, natural_language_query)

    return {
        "sql":     sql,
        "results": rows,
        "error":   error,
    }


if __name__ == "__main__":
    print("=== Test 1: Tesla revenue 2023 and 2024 ===")
    result = sql_tool("What was Tesla's revenue in 2023 and 2024?")
    print("SQL:", result["sql"])
    print("Results:", json.dumps(result["results"], indent=2, default=str))

    print("\n=== Test 2: R&D comparison 2024 ===")
    result = sql_tool("Compare R&D spending between Tesla, Ford and Rivian in 2024")
    print("SQL:", result["sql"])
    print("Results:", json.dumps(result["results"], indent=2, default=str))

    print("\n=== Test 3: Tesla net income 2022-2024 ===")
    result = sql_tool("Show Tesla net income from 2022 to 2024")
    print("SQL:", result["sql"])
    print("Results:", json.dumps(result["results"], indent=2, default=str))