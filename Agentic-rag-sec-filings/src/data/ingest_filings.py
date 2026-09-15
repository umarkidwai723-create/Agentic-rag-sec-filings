import os
import sys
import re
import requests
import warnings

from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

load_dotenv(override=True)

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
import config


# ── Chunking ──────────────────────────────────────────────

def chunk_text(
    text: str,
    chunk_size: int = config.CHUNK_SIZE,
    overlap: int = config.CHUNK_OVERLAP
) -> list[str]:
    """Split text into overlapping word-based chunks."""
    words = text.split()
    chunks = []

    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap

    return chunks


# ── SEC EDGAR: get latest 10-K filing index ───────────────

def get_latest_10k_index(cik: str) -> str | None:
    """Return the index URL for the most recent 10-K filing."""

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"

    resp = requests.get(url, headers=config.SEC_HEADERS)

    if resp.status_code != 200:
        print(f"  ERROR fetching submissions for CIK {cik}")
        return None

    data = resp.json()

    filings = data.get("filings", {}).get("recent", {})

    forms = filings.get("form", [])
    accessions = filings.get("accessionNumber", [])
    primary_docs = filings.get("primaryDocument", [])

    for i, form in enumerate(forms):

        if form == "10-K":

            accession = accessions[i].replace("-", "")
            doc = primary_docs[i]

            index_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{accession}/{doc}"
            )

            return index_url

    return None


# ── Extract narrative sections from 10-K HTML ─────────────

def extract_sections(html: str) -> dict[str, str]:
    """
    Pull Risk Factors and MD&A text from 10-K HTML.

    Returns:
        {
            "risk_factors": "...",
            "mdna": "..."
        }
    """

    soup = BeautifulSoup(html, "lxml")

    # Remove tables because numerical data is handled through XBRL
    for table in soup.find_all("table"):
        table.decompose()

    full_text = soup.get_text(separator=" ")
    full_text = re.sub(r"\s+", " ", full_text).strip()

    sections = {}

    # Risk Factors
    rf_match = re.search(
        r"(ITEM\s*1A[\.\s]*RISK FACTORS)(.*?)(ITEM\s*1B|ITEM\s*2)",
        full_text,
        re.IGNORECASE | re.DOTALL
    )

    sections["risk_factors"] = (
        rf_match.group(2).strip()
        if rf_match
        else ""
    )

    # MD&A
    mdna_match = re.search(
        r"(ITEM\s*7[\.\s]*MANAGEMENT.S DISCUSSION)(.*?)(ITEM\s*7A|ITEM\s*8)",
        full_text,
        re.IGNORECASE | re.DOTALL
    )

    sections["mdna"] = (
        mdna_match.group(2).strip()
        if mdna_match
        else ""
    )

    return sections


# ── Build ChromaDB vector store ───────────────────────────

def build_vector_store(documents: list[dict]):
    """
    documents:
        list of {
            text,
            company,
            section,
            source_url
        }

    Embeds and stores documents in ChromaDB using
    OpenAI embeddings.
    """

    import chromadb
    from chromadb.utils import embedding_functions

    os.makedirs(config.CHROMA_DIR, exist_ok=True)

    ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=os.getenv("OPENAI_API_KEY"),
        model_name="text-embedding-3-small"
    )

    client = chromadb.PersistentClient(
        path=config.CHROMA_DIR
    )

    # Recreate collection so repeated runs produce a clean index
    try:
        client.delete_collection("filings")
    except Exception:
        pass

    collection = client.create_collection(
        "filings",
        embedding_function=ef
    )

    ids = []
    texts = []
    metadatas = []

    for i, doc in enumerate(documents):

        chunks = chunk_text(doc["text"])

        for j, chunk in enumerate(chunks):

            ids.append(
                f"{doc['company']}_{doc['section']}_{i}_{j}"
            )

            texts.append(chunk)

            metadatas.append({
                "company": doc["company"],
                "section": doc["section"],
                "source_url": doc["source_url"],
            })

    # Add documents in batches
    batch_size = 100

    for start in range(0, len(ids), batch_size):

        collection.add(
            ids=ids[start:start + batch_size],
            documents=texts[start:start + batch_size],
            metadatas=metadatas[start:start + batch_size],
        )

        print(
            f"  Embedded batch {start // batch_size + 1} "
            f"({min(start + batch_size, len(ids))}/{len(ids)} chunks)"
        )

    return collection


# ── Main ──────────────────────────────────────────────────

def run():

    print("=== Filing Ingestion ===\n")

    all_documents = []

    for company, cik in config.COMPANIES.items():

        print(f"[{company.upper()}]")

        url = get_latest_10k_index(cik)

        if not url:
            print(f"  Could not find 10-K for {company}\n")
            continue

        print(f"  Filing URL: {url}")

        resp = requests.get(
            url,
            headers=config.SEC_HEADERS
        )

        if resp.status_code != 200:
            print(
                f"  ERROR fetching filing: "
                f"{resp.status_code}\n"
            )
            continue

        sections = extract_sections(resp.text)

        for section_name, text in sections.items():

            if not text:
                print(
                    f"  WARNING: {section_name} section empty "
                    f"— regex may need tuning"
                )
                continue

            word_count = len(text.split())

            print(
                f"  {section_name}: "
                f"{word_count} words extracted"
            )

            all_documents.append({
                "text": text,
                "company": company,
                "section": section_name,
                "source_url": url,
            })

        print()

    if not all_documents:

        print(
            "No documents extracted — "
            "check SEC headers and filing URLs above."
        )

        return

    print(
        f"Building vector store from "
        f"{len(all_documents)} sections..."
    )

    build_vector_store(all_documents)

    print(
        f"\nDone. Vector store saved to: "
        f"{config.CHROMA_DIR}"
    )


if __name__ == "__main__":
    run()