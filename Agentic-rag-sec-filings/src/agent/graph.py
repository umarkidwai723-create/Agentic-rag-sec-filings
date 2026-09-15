import os
import sys
import json
from typing import TypedDict, Optional

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
import config

from dotenv import load_dotenv
load_dotenv(override=True)

from langgraph.graph import StateGraph, END

from src.agent.router import classify_query, NARRATIVE, NUMERIC, CURRENT, CROSS_DOC
from src.tools.vector_search import vector_search
from src.tools.sql_tool import sql_tool
from src.tools.web_search import web_search_tool


# ── Agent State ───────────────────────────────────────────
class AgentState(TypedDict):
    query:           str
    route:           Optional[str]
    company:         Optional[str]
    section:         Optional[str]
    reasoning:       Optional[str]
    narrative_results: Optional[list]
    numeric_results:   Optional[dict]
    web_results:       Optional[dict]
    final_answer:    Optional[str]
    validated:       Optional[bool]
    validation_note: Optional[str]


# ── Node 1: Router ────────────────────────────────────────
def router_node(state: AgentState) -> AgentState:
    print(f"\n[ROUTER] Classifying query...")
    classification = classify_query(state["query"])
    print(f"[ROUTER] → route={classification['route']} | "
          f"company={classification['company']} | "
          f"section={classification['section']}")
    print(f"[ROUTER] → {classification['reasoning']}")
    return {
        **state,
        "route":     classification["route"],
        "company":   classification["company"],
        "section":   classification["section"],
        "reasoning": classification["reasoning"],
    }


# ── Node 2: Narrative retrieval ───────────────────────────
def narrative_node(state: AgentState) -> AgentState:
    print(f"\n[VECTOR SEARCH] Searching narrative sections...")
    results = vector_search(
        query=state["query"],
        company=state.get("company"),
        section=state.get("section"),
    )
    print(f"[VECTOR SEARCH] → {len(results)} chunks retrieved")
    return {**state, "narrative_results": results}


# ── Node 3: Numeric retrieval ─────────────────────────────
def numeric_node(state: AgentState) -> AgentState:
    print(f"\n[SQL TOOL] Running numeric query...")
    result = sql_tool(state["query"])
    print(f"[SQL TOOL] → SQL: {result['sql']}")
    print(f"[SQL TOOL] → {len(result['results'])} rows returned")
    return {**state, "numeric_results": result}


# ── Node 4: Web search ────────────────────────────────────
def web_node(state: AgentState) -> AgentState:
    print(f"\n[WEB SEARCH] Searching SEC EDGAR...")
    result = web_search_tool(state["query"])
    print(f"[WEB SEARCH] → {len(result['results'])} results found")
    return {**state, "web_results": result}


# ── Node 5: Synthesis ─────────────────────────────────────
def synthesis_node(state: AgentState) -> AgentState:
    print(f"\n[SYNTHESIS] Generating answer...")
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Build context from whichever tools ran
    context_parts = []

    if state.get("narrative_results"):
        chunks_text = "\n\n".join([
            f"[{r['company'].upper()} / {r['section']} / score={r['score']}]\n{r['text']}"
            for r in state["narrative_results"]
        ])
        context_parts.append(f"=== NARRATIVE SECTIONS (from 10-K filings) ===\n{chunks_text}")

    if state.get("numeric_results") and not state["numeric_results"].get("error"):
        rows = state["numeric_results"]["results"]
        sql  = state["numeric_results"]["sql"]
        rows_text = json.dumps(rows, indent=2)
        context_parts.append(f"=== FINANCIAL DATA (from XBRL/SQL) ===\nSQL: {sql}\nResults:\n{rows_text}")

    if state.get("web_results") and state["web_results"].get("summary"):
        context_parts.append(f"=== WEB / SEC EDGAR SEARCH ===\n{state['web_results']['summary']}")

    context = "\n\n".join(context_parts)

    prompt = f"""You are a financial analyst assistant. Answer the user's question using ONLY 
the provided context from SEC 10-K filings and financial data.

Rules:
- Be specific and cite which company/section/year the information comes from
- For numeric answers, always state the exact figure and its unit (USD, %)
- If comparing companies, present data in a clear structured way
- If the context doesn't contain enough information, say so explicitly
- Do not make up figures or facts not present in the context

Context:
{context}

Question: {state['query']}

Answer:"""

    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    answer = response.choices[0].message.content.strip()
    print(f"[SYNTHESIS] → Answer generated ({len(answer)} chars)")
    return {**state, "final_answer": answer}


# ── Node 6: Validator ─────────────────────────────────────
def validator_node(state: AgentState) -> AgentState:
    """
    For numeric/cross_doc queries: re-derive the key figure from SQL
    and check it matches what the LLM stated in the answer.
    """
    print(f"\n[VALIDATOR] Checking answer...")

    # Only validate numeric answers
    if state.get("route") not in {NUMERIC, CROSS_DOC}:
        return {**state, "validated": True, "validation_note": "No numeric validation needed"}

    if not state.get("numeric_results") or state["numeric_results"].get("error"):
        return {**state, "validated": False, "validation_note": "No SQL results to validate against"}

    sql_rows   = state["numeric_results"]["results"]
    answer     = state.get("final_answer", "")

    if not sql_rows:
        return {**state, "validated": False, "validation_note": "SQL returned no rows"}

    # Extract numbers from SQL results
    sql_values = []
    for row in sql_rows:
        if "value" in row and row["value"] is not None:
            sql_values.append(row["value"])

    if not sql_values:
        return {**state, "validated": True, "validation_note": "No numeric values in SQL results to check"}

    # Ask GPT to check if the answer is consistent with SQL values
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    check_prompt = f"""You are a numeric fact-checker. 
    
SQL query returned these values (in USD): {sql_values}

The generated answer states:
{answer}

Does the answer correctly reference figures that are consistent with the SQL values?
Check for:
1. Are the numbers in the answer close to the SQL values (within rounding)?
2. Are the numbers in the correct order of magnitude (billions vs millions)?
3. Are there any figures in the answer that contradict the SQL data?

Respond ONLY with JSON:
{{"consistent": true/false, "note": "brief explanation"}}"""

    resp = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "user", "content": check_prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )

    try:
        check = json.loads(resp.choices[0].message.content)
        consistent = check.get("consistent", True)
        note = check.get("note", "")
        print(f"[VALIDATOR] → consistent={consistent} | {note}")
        return {
            **state,
            "validated":       consistent,
            "validation_note": note,
        }
    except Exception:
        return {**state, "validated": True, "validation_note": "Validator parse error — skipped"}


# ── Routing logic ─────────────────────────────────────────
def route_after_router(state: AgentState) -> str:
    route = state.get("route")
    if route == NARRATIVE:
        return "narrative"
    elif route == NUMERIC:
        return "numeric"
    elif route == CURRENT:
        return "web"
    elif route == CROSS_DOC:
        return "narrative"  # cross_doc starts with narrative, numeric runs after
    return "narrative"


def route_after_narrative(state: AgentState) -> str:
    """For cross_doc queries, also run numeric after narrative."""
    if state.get("route") == CROSS_DOC:
        return "numeric"
    return "synthesis"


# ── Build the graph ───────────────────────────────────────
def build_graph():
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("router",    router_node)
    graph.add_node("narrative", narrative_node)
    graph.add_node("numeric",   numeric_node)
    graph.add_node("web",       web_node)
    graph.add_node("synthesis", synthesis_node)
    graph.add_node("validator", validator_node)

    # Entry point
    graph.set_entry_point("router")

    # Router → tool nodes
    graph.add_conditional_edges("router", route_after_router, {
        "narrative": "narrative",
        "numeric":   "numeric",
        "web":       "web",
    })

    # Narrative → numeric (cross_doc) or synthesis
    graph.add_conditional_edges("narrative", route_after_narrative, {
        "numeric":   "numeric",
        "synthesis": "synthesis",
    })

    # Numeric and web → synthesis
    graph.add_edge("numeric",   "synthesis")
    graph.add_edge("web",       "synthesis")

    # Synthesis → validator → END
    graph.add_edge("synthesis", "validator")
    graph.add_edge("validator", END)

    return graph.compile()


# ── Public interface ──────────────────────────────────────
def run_query(query: str) -> dict:
    """Run a query through the full agent pipeline."""
    app = build_graph()
    initial_state: AgentState = {
        "query":             query,
        "route":             None,
        "company":           None,
        "section":           None,
        "reasoning":         None,
        "narrative_results": None,
        "numeric_results":   None,
        "web_results":       None,
        "final_answer":      None,
        "validated":         None,
        "validation_note":   None,
    }
    result = app.invoke(initial_state)
    return result


if __name__ == "__main__":
    test_queries = [
        "What are Tesla's main risk factors related to competition?",
        "What was Tesla's revenue in 2023 and 2024?",
        "Has Tesla filed a new 10-K since January 2025?",
    ]

    for query in test_queries:
        print("\n" + "="*60)
        print(f"QUERY: {query}")
        print("="*60)
        result = run_query(query)
        print(f"\n FINAL ANSWER:\n{result['final_answer']}")
        print(f"\n VALIDATED: {result['validated']} | {result['validation_note']}")