import os
from dotenv import load_dotenv
load_dotenv(override=True)

# ── API Keys ──────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ── Model ─────────────────────────────────────────────────
LLM_MODEL = "gpt-4o-mini"  # cheap + fast; swap to gpt-4o for better routing accuracy

# ── Companies in scope ────────────────────────────────────
COMPANIES = {
    "tesla":  "0001318605",
    "ford":   "0000037996",
    "rivian": "0001874178",
}

# ── SEC XBRL API ──────────────────────────────────────────
SEC_HEADERS = {"User-Agent": "Yash-Mahajan mahajanyash42@gmail.com"}  # SEC requires this
XBRL_API_BASE = "https://data.sec.gov/api/xbrl/companyfacts"

# ── XBRL financial metrics to pull ────────────────────────
XBRL_METRICS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss",
    "ResearchAndDevelopmentExpense",
    "GrossProfit",
    "OperatingIncomeLoss",
    "EarningsPerShareBasic",
]

# ── Microsoft SQL Server connection ─────────────────────────────────────────────────
SQL_SERVER   = "Yash"
SQL_DATABASE = "agentic_rag_finance"
SQL_DRIVER   = "ODBC Driver 17 for SQL Server"

# Windows Authentication connection string (no username/password needed)
SQL_CONNECTION_STRING = (
    f"DRIVER={{{SQL_DRIVER}}};"
    f"SERVER={SQL_SERVER};"
    f"DATABASE={SQL_DATABASE};"
    f"Trusted_Connection=yes;"
)

# ── Paths ─────────────────────────────────────────────────
DATA_RAW_DIR       = "data/raw"
DATA_PROCESSED_DIR = "data/processed"
CHROMA_DIR         = "data/processed/chroma"

# ── Retrieval ─────────────────────────────────────────────
TOP_K_CHUNKS = 5
CHUNK_SIZE   = 700   # tokens
CHUNK_OVERLAP = 100