import os
import sys
import json

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
import config

from dotenv import load_dotenv
load_dotenv(override=True)

# ── Route categories ──────────────────────────────────────
NARRATIVE  = "narrative"   # → vector search
NUMERIC    = "numeric"     # → SQL tool
CURRENT    = "current"     # → web search
CROSS_DOC  = "cross_doc"   # → both narrative + numeric

VALID_ROUTES = {NARRATIVE, NUMERIC, CURRENT, CROSS_DOC}


def classify_query(query: str) -> dict:
    """
    Use GPT to classify a financial query into a route category.
    Returns { route, company, section, reasoning }
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    company_list = list(config.COMPANIES.keys())

    prompt = f"""You are a query router for a financial document intelligence system.
Classify the user's query into exactly one of these categories:

CATEGORIES:
- narrative  : Questions about qualitative content — risk factors, strategy, 
               management commentary, business description, ESG, competitive landscape.
               These should be answered by searching narrative text from 10-K filings.

- numeric    : Questions requiring specific financial figures, calculations, 
               comparisons, growth rates, ratios. These must be answered from 
               structured financial data (SQL), NOT from narrative text.

- current    : Questions about whether new filings exist, recent news, 
               anything that may have changed after the dataset was built (mid-2025).

- cross_doc  : Questions that require BOTH narrative text AND financial numbers,
               OR questions that compare what management said vs what the numbers show.
               Example: "Does Tesla's stated AI investment align with R&D spend?"

COMPANIES IN SCOPE: {company_list}
SECTIONS AVAILABLE: risk_factors, mdna (Management Discussion & Analysis)

Respond ONLY with a valid JSON object, no explanation, no markdown:
{{
  "route":     "<narrative|numeric|current|cross_doc>",
  "company":   "<tesla|ford|rivian|null>",
  "section":   "<risk_factors|mdna|null>",
  "reasoning": "<one sentence explaining why>"
}}

If multiple companies are mentioned, set company to null (query all).
If section is unclear, set to null (search both sections).

User query: {query}
JSON:"""

    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback if JSON parsing fails
        return {
            "route":     NARRATIVE,
            "company":   None,
            "section":   None,
            "reasoning": "JSON parse failed — defaulting to narrative search",
        }

    # Validate route
    if result.get("route") not in VALID_ROUTES:
        result["route"] = NARRATIVE

    # Normalize nulls
    result["company"] = result.get("company") if result.get("company") != "null" else None
    result["section"] = result.get("section") if result.get("section") != "null" else None

    return result


if __name__ == "__main__":
    test_queries = [
        "What are Tesla's main risk factors related to competition?",
        "What was Tesla's revenue growth from 2023 to 2024?",
        "Compare R&D spending between Tesla, Ford and Rivian in 2024",
        "Has Tesla filed a new 10-K since January 2025?",
        "Does Tesla's management discussion about AI investment align with their R&D spend figures?",
        "What does Ford's MD&A say about their EV transition strategy?",
        "What was Rivian's net loss in 2023?",
        "Find contradictions between Tesla's earnings call and annual report",
    ]

    print("=== Router Classification Tests ===\n")
    for q in test_queries:
        result = classify_query(q)
        print(f"Q: {q}")
        print(f"   → route={result['route']:10s} | company={str(result['company']):8s} | section={str(result['section'])}")
        print(f"   → {result['reasoning']}")
        print()