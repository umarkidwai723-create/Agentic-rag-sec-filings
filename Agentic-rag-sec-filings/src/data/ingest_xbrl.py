import requests
import pyodbc
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
import config

from dotenv import load_dotenv
load_dotenv(override=True)


def get_connection():
    """Return a live pyodbc connection to SQL Server."""
    return pyodbc.connect(config.SQL_CONNECTION_STRING)


def create_table():
    """Create the financials table in SQL Server if it doesn't exist."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        IF NOT EXISTS (
            SELECT * FROM sysobjects 
            WHERE name='financials' AND xtype='U'
        )
        CREATE TABLE financials (
            id          INT IDENTITY(1,1) PRIMARY KEY,
            company     NVARCHAR(50)  NOT NULL,
            cik         NVARCHAR(20)  NOT NULL,
            metric      NVARCHAR(200) NOT NULL,
            fiscal_year INT,
            period_end  NVARCHAR(20),
            value       FLOAT,
            unit        NVARCHAR(20),
            filed       NVARCHAR(20),
            CONSTRAINT uq_financials 
                UNIQUE (company, metric, fiscal_year, period_end)
        )
    """)
    conn.commit()
    print("  Table 'financials' ready in SQL Server.")
    conn.close()


def fetch_xbrl_data(company_name: str, cik: str) -> dict:
    """Fetch raw XBRL company facts from SEC API."""
    url = f"{config.XBRL_API_BASE}/CIK{cik}.json"
    print(f"  Fetching {company_name} ({cik}) from SEC...")
    response = requests.get(url, headers=config.SEC_HEADERS)
    if response.status_code != 200:
        raise Exception(f"SEC API error {response.status_code} for {company_name}")
    return response.json()


def extract_annual_metrics(company_name: str, cik: str, facts: dict) -> list[dict]:
    """Pull annual 10-K values for each metric we care about."""
    rows = []
    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    for metric in config.XBRL_METRICS:
        if metric not in us_gaap:
            continue
        entries = us_gaap[metric].get("units", {}).get("USD", [])
        for entry in entries:
            if entry.get("form") != "10-K":
                continue
            rows.append({
                "company":     company_name,
                "cik":         cik,
                "metric":      metric,
                "fiscal_year": entry.get("fy"),
                "period_end":  entry.get("end"),
                "value":       entry.get("val"),
                "unit":        "USD",
                "filed":       entry.get("filed"),
            })
    return rows


def insert_rows(conn: pyodbc.Connection, rows: list[dict]):
    """Insert rows into SQL Server, skipping duplicates."""
    cursor = conn.cursor()
    inserted = skipped = 0

    for row in rows:
        try:
            cursor.execute("""
                IF NOT EXISTS (
                    SELECT 1 FROM financials
                    WHERE company=? AND metric=? 
                      AND fiscal_year=? AND period_end=?
                )
                INSERT INTO financials
                    (company, cik, metric, fiscal_year, period_end, value, unit, filed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            # WHERE params
            row["company"], row["metric"], row["fiscal_year"], row["period_end"],
            # INSERT params
            row["company"], row["cik"], row["metric"], row["fiscal_year"],
            row["period_end"], row["value"], row["unit"], row["filed"]
            )
            if cursor.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except pyodbc.Error as e:
            print(f"    DB error: {e}")

    conn.commit()
    return inserted, skipped


def run():
    print("=== XBRL Ingestion → SQL Server ===\n")
    create_table()
    conn = get_connection()

    for company_name, cik in config.COMPANIES.items():
        print(f"\n[{company_name.upper()}]")
        try:
            facts = fetch_xbrl_data(company_name, cik)
            rows  = extract_annual_metrics(company_name, cik, facts)
            ins, skip = insert_rows(conn, rows)
            print(f"  Extracted {len(rows)} entries → inserted {ins}, skipped {skip} duplicates")
        except Exception as e:
            print(f"  ERROR: {e}")

    # Summary
    cursor = conn.cursor()
    print("\n=== Database Summary ===")
    cursor.execute("""
        SELECT company, 
               COUNT(*) as rows, 
               MIN(fiscal_year) as earliest, 
               MAX(fiscal_year) as latest
        FROM financials
        GROUP BY company
        ORDER BY company
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]:10s} | {row[1]:4d} rows | FY {row[2]}–{row[3]}")

    conn.close()
    print(f"\nDone. Data loaded into SQL Server: {config.SQL_DATABASE}")


if __name__ == "__main__":
    run()