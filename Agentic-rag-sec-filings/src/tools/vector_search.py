import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
import config

from dotenv import load_dotenv
load_dotenv(override=True)


def vector_search(query: str, company: str = None, section: str = None, top_k: int = config.TOP_K_CHUNKS) -> list[dict]:
    """
    Search ChromaDB for narrative chunks relevant to the query.
    Optionally filter by company and/or section (risk_factors or mdna).
    Returns list of { text, company, section, source_url, score }
    """
    import chromadb
    from openai import OpenAI

    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Embed the query
    response = openai_client.embeddings.create(
        input=[query],
        model="text-embedding-3-small"
    )
    query_embedding = response.data[0].embedding

    # Connect to ChromaDB
    chroma_client = chromadb.PersistentClient(path=config.CHROMA_DIR)
    collection    = chroma_client.get_collection("filings")

    # Build optional metadata filter
    where = {}
    if company and section:
        where = {"$and": [{"company": company}, {"section": section}]}
    elif company:
        where = {"company": company}
    elif section:
        where = {"section": section}

    # Query
    kwargs = dict(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"]
    )
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    # Format output
    output = []
    for i in range(len(results["documents"][0])):
        output.append({
            "text":       results["documents"][0][i],
            "company":    results["metadatas"][0][i]["company"],
            "section":    results["metadatas"][0][i]["section"],
            "source_url": results["metadatas"][0][i]["source_url"],
            "score":      round(1 - results["distances"][0][i], 4),  # cosine similarity
        })

    return output


if __name__ == "__main__":
    # Quick test
    results = vector_search("What are Tesla's main risk factors related to competition?", company="tesla")
    for r in results:
        print(f"[{r['company']} / {r['section']}] score={r['score']}")
        print(r["text"][:200])
        print()