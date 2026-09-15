import os
import sys
import requests
import json

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
import config

from dotenv import load_dotenv
load_dotenv(override=True)


def sec_full_text_search(query: str, num_results: int = 5) -> list[dict]:
    """Search SEC EDGAR full-text search for recent filings."""
    try:
        url = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "q": query,
            "forms": "10-K",
            "dateRange": "custom",
            "startdt": "2024-01-01",
        }
        resp = requests.get(url, headers=config.SEC_HEADERS, params=params, timeout=10)
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        results = []
        for hit in hits[:num_results]:
            src = hit.get("_source", {})
            name = src.get("display_names", ["Unknown"])[0] if src.get("display_names") else "Unknown"
            results.append({
                "title":   f"{name} — {src.get('form_type', '10-K')}",
                "url":     f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={src.get('entity_id','')}&type=10-K",
                "snippet": f"Period: {src.get('period_of_report', 'N/A')} | Filed: {src.get('file_date', 'N/A')} | {src.get('form_type','')}",
            })
        return results
    except Exception as e:
        return [{"error": f"SEC search failed: {e}"}]


def check_latest_filing(company: str) -> dict:
    """
    Check SEC EDGAR for the most recent 10-K filing for a company.
    Returns filing metadata.
    """
    cik = config.COMPANIES.get(company.lower())
    if not cik:
        return {"error": f"Unknown company: {company}. Known: {list(config.COMPANIES.keys())}"}

    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=config.SEC_HEADERS, timeout=10)
        data = resp.json()

        filings = data.get("filings", {}).get("recent", {})
        forms      = filings.get("form", [])
        dates      = filings.get("filingDate", [])
        accessions = filings.get("accessionNumber", [])
        periods    = filings.get("reportDate", [])

        for i, form in enumerate(forms):
            if form == "10-K":
                return {
                    "company":        company,
                    "cik":            cik,
                    "form":           form,
                    "filing_date":    dates[i],
                    "period":         periods[i],
                    "accession":      accessions[i],
                    "edgar_url":      f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K",
                }

        return {"error": f"No 10-K found for {company}"}
    except Exception as e:
        return {"error": f"EDGAR lookup failed: {e}"}


def web_search_tool(query: str) -> dict:
    """
    Main web search tool for the agent.
    Detects if query is about a specific company and uses targeted EDGAR lookup,
    otherwise falls back to full-text search.
    Returns { query, results, summary }
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Detect if query mentions a specific company
    results = []
    for company in config.COMPANIES:
        if company.lower() in query.lower():
            filing_info = check_latest_filing(company)
            if "error" not in filing_info:
                results.append({
                    "title":   f"{company.title()} latest 10-K",
                    "url":     filing_info["edgar_url"],
                    "snippet": f"Most recent 10-K filed on {filing_info['filing_date']} "
                               f"for period ending {filing_info['period']}. "
                               f"Accession: {filing_info['accession']}",
                })

    # Also run full-text search
    ft_results = sec_full_text_search(query)
    if ft_results and "error" not in ft_results[0]:
        results.extend(ft_results)

    if not results:
        return {
            "query":   query,
            "results": [],
            "summary": f"No SEC filings found for query: '{query}'. "
                       f"Check EDGAR directly: https://www.sec.gov/cgi-bin/browse-edgar",
        }

    results_text = "\n\n".join([
        f"Title: {r['title']}\nURL: {r['url']}\nSnippet: {r['snippet']}"
        for r in results
    ])

    prompt = f"""Based on these SEC EDGAR search results, answer the query concisely.
Always mention filing dates and periods where available.

Query: {query}

Results:
{results_text}

Answer:"""

    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    return {
        "query":   query,
        "results": results,
        "summary": response.choices[0].message.content.strip(),
    }


if __name__ == "__main__":
    print("=== Test 1: Latest Tesla filing ===")
    result = web_search_tool("When did Tesla last file a 10-K?")
    print("Summary:", result["summary"])
    print("Sources:", len(result["results"]))

    print("\n=== Test 2: Rivian filing check ===")
    result = web_search_tool("Has Rivian filed a 10-K recently?")
    print("Summary:", result["summary"])
    print("Sources:", len(result["results"]))

    print("\n=== Test 3: General SEC search ===")
    result = web_search_tool("EV company 10-K filings 2024")
    print("Summary:", result["summary"])
    print("Sources:", len(result["results"]))