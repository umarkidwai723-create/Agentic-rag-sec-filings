# Agentic RAG for SEC Financial Filings

An agent that answers questions about SEC 10-K filings by picking the
right tool for the job instead of throwing every question at a vector
store. Ask it something about strategy or risk and it searches text.
Ask it for a number and it queries a real database instead of guessing
from a paragraph that merely sounds relevant.

## Why this exists

Most RAG demos vector-search everything. That's fine for "explain the
risk factors," but for "what was the revenue" it's a liability — the
retriever will happily hand back a chunk that *mentions* a number
without being *the* number, and the LLM will state it with total
confidence. For financial data that's not a UX problem, it's a
correctness problem. So instead of one retrieval path, this project
routes each question to whichever source can actually answer it
correctly, and then double-checks the numeric ones before they go out
the door.

## How a question moves through the system

1. **Classify.** A small LLM call tags the incoming question as
   narrative, numeric, current-events, or cross-document.
2. **Fetch.** The tagged question is handed to one or more tools —
   never all of them, only the ones relevant to that tag.
3. **Write the answer.** Whatever came back from step 2 gets turned
   into a grounded, cited response.
4. **Check the numbers.** If the answer contains figures, they're
   pulled back out and matched against the database directly. Anything
   that doesn't match gets flagged before the user ever sees it.

```
                 ┌───────────────┐
   question ───► │ classify (LLM)│
                 └──────┬────────┘
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   risk/strategy     hard numbers     "latest filing?"
        │                │                │
        ▼                ▼                ▼
   ChromaDB search   SQL Server       SEC EDGAR API
        │                │                │
        └────────────────┼────────────────┘
                          ▼
                  write grounded answer
                          ▼
                cross-check figures vs DB
                          ▼
                     final answer
```

## What's under the hood

| Piece | Choice | Why it's there |
|---|---|---|
| Orchestration | LangGraph `StateGraph` | Gives conditional branching between tools instead of one linear chain |
| Classification / writing | GPT-4o-mini | Cheap enough to call twice per question (route + synthesize) |
| Embeddings | `text-embedding-3-small` | Narrative chunk similarity |
| Vector store | ChromaDB | 154 chunks across Risk Factors + MD&A |
| Structured data | Microsoft SQL Server via `pyodbc` | Exact figures, no rounding-by-vibes |
| Filing data source | SEC XBRL API | Machine-readable financial facts |
| Live lookups | SEC EDGAR full-text + submissions API | Filing dates, accession numbers, recency |
| HTML parsing | BeautifulSoup + lxml | Pulls narrative sections out of raw 10-K HTML |
| Config | `python-dotenv` | Keeps API keys out of source |

## The five stops in the pipeline

**Router** — one LLM call, structured JSON output, four possible
labels (`narrative`, `numeric`, `current`, `cross_doc`).

**Vector search** — embeds the question, pulls the top 5 closest
chunks from ChromaDB, filterable by company and filing section.

**SQL tool** — turns the question into T-SQL, runs it against SQL
Server, and dedupes rows using `MAX(period_end)` / `MAX(filed)`
subqueries so you get the latest figure, not every historical
restatement of it. Two hardening pieces live here — see below.

**Web search** — hits SEC EDGAR directly for filing metadata when the
question is really "what's the newest filing," not "what does it say."

**Synthesis + validator** — one call writes the answer with citations
to company/section/year; a second step regexes out any dollar figures
in that answer and checks them against SQL Server ground truth before
release.

## Two things I had to go back and fix

These weren't part of the original design — they came out of actually
stress-testing the SQL path and running the agent against companies
with gaps in their XBRL data.

**The router could ask the database to do something destructive, and
nothing but a system prompt was stopping it.**
The SQL node generates T-SQL from natural language and runs it
directly. I'd told the model in the prompt to only ever write
`SELECT` statements, but "I told the model not to" isn't a safety
guarantee — a weird phrasing, an injected instruction, or a plain
model mistake could produce an `INSERT`/`UPDATE`/`DELETE`/`DROP` and
it would just... run. I added a hard filter in `sql_tool.py` that sits
between the LLM's generated query and the actual execution call: if
the string doesn't start with `SELECT`, or it contains any of
`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `EXEC`, or
`MERGE`, it's rejected before it ever reaches `pyodbc`. The model can
still misbehave; it just can't do anything about it anymore. That's
the point — treat LLM output as untrusted input the moment it touches
a real database, the same way you'd treat any other untrusted input.

**Some perfectly answerable questions were coming back empty because
of how XBRL tagging actually works.**
Ford, it turns out, doesn't file `GrossProfit` as its own XBRL tag —
even though `Revenue` and `CostOfRevenue` are both right there. So a
totally reasonable question like "what was Ford's gross profit" ran a
correct SQL query against a column that just didn't exist for that
company, and the system returned nothing. I added a
`derive_gross_profit()` fallback that only kicks in after a direct
lookup comes back empty: it pulls Revenue and Cost of Revenue for the
requested year and computes `Revenue − Cost of Revenue` itself,
tagging the result as *derived* so it's never confused with a number
the company actually reported. Same fix pattern would apply to any
other tag with this problem — it's a gap in what's fileable, not a
bug in the data model.

## Results

**Routing accuracy** — 19 hand-written questions, split across the
three query types the router can assign:

| Type | Questions | Routed correctly | Accuracy |
|---|---|---|---|
| Narrative | 7 | 7/7 | 100% |
| Numeric | 7 | 7/7 | 100% |
| Cross-document | 5 | 5/5 | 100% |
| **Overall** | **19** | **19/19** | **100%** |

**Validator stress test** — 5 answers with deliberately injected
errors were fed back into the validator to see if it would catch them:

| Injected error type | Caught? |
|---|---|
| Inflated figure (number bumped up) | ✅ |
| Wrong order of magnitude (millions vs. billions) | ✅ |
| Hallucinated value (not in source data at all) | ✅ |
| Right number, wrong fiscal year | ✅ |
| Right figure, wrong company | ✅ |

5/5 caught — nothing bad slipped through to a final answer.

**Sample run:**

```
Q: What was Ford's gross profit for fiscal year 2025?

Router  → numeric
SQL     → GrossProfit not filed directly by Ford → derive_gross_profit() triggered
Answer  → "Ford's derived gross profit for FY2025 was $X,XXX million
           (Revenue − Cost of Revenue), as GrossProfit is not filed
           as a standalone XBRL tag for Ford."
Validator → figure recomputed independently from SQL Server → match → passed
```

This is the exact case Fix 2 was built for — the direct-lookup path
alone would have returned nothing here.

## Companies covered

| Company | Ticker | CIK |
|---|---|---|
| Tesla | TSLA | 0001318605 |
| Ford | F | 0000037996 |
| Rivian | RIVN | 0001874178 |

FY2025 10-Ks (filed Jan–Feb 2026), plus full historical XBRL data,
loaded into SQL Server ahead of time.

## Running it yourself

Needs Python 3.11+, an OpenAI key, and a local SQL Server instance
(SSMS is the easiest way to manage it).

```bash
git clone https://github.com/umarkidwai723-create/agentic-rag-sec-filings.git
cd agentic-rag-sec-filings
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux
pip install -r requirements.txt
```

Add your key to `.env`:
```
OPENAI_API_KEY=your_openai_api_key.
```

Spin up the database in SSMS — right-click **Databases** → **New
Database** → name it `agentic_rag_finance` — then point `config.py`
at your server:
```python
SQL_SERVER = "localhost"
SQL_DATABASE = "agentic_rag_finance"
SQL_DRIVER = "ODBC Driver 17 for SQL Server"
```

Load the data:
```bash
python src/data/ingest_xbrl.py      # XBRL facts → SQL Server
python src/data/ingest_filings.py   # 10-K narrative → ChromaDB
```

Ask it something:
```bash
python -c "
from src.agent.graph import run_query
result = run_query('What are Tesla\'s main risk factors related to competition?')
print(result['final_answer'])
print('Validated:', result['validated'])
"
```

Run the evaluation suite or the validator stress test on their own:
```bash
python src/eval/run_eval.py
python src/agent/validator.py
```

## Layout

```
src/
├── agent/
│   ├── router.py       # classifies each query
│   ├── graph.py         # the LangGraph pipeline itself
│   └── validator.py     # checks numeric answers against SQL Server
├── tools/
│   ├── vector_search.py # ChromaDB lookups
│   ├── sql_tool.py       # NL→SQL, safety filter, derived-metric fallback
│   └── web_search.py     # SEC EDGAR live queries
├── data/
│   ├── ingest_xbrl.py     # loads XBRL facts into SQL Server
│   └── ingest_filings.py  # loads 10-K text into ChromaDB
└── eval/
    ├── questions.py       # the 19 test questions
    └── run_eval.py         # scores routing + accuracy

data/processed/
├── chroma/             # vector store files
└── eval_results.json   # last eval run's output

config.py
requirements.txt
```

