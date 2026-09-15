import os
import sys
import json
import re

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
import config

from dotenv import load_dotenv
load_dotenv(override=True)


def extract_numbers_from_text(text: str) -> list[float]:
    """Extract all numeric values from a text string, excluding years."""
    patterns = [
        r'\$?([\d,]+(?:\.\d+)?)\s*billion',
        r'\$?([\d,]+(?:\.\d+)?)\s*million',
        r'\$?([\d,]{4,})',
    ]
    numbers = []
    for pattern in patterns:
        multiplier = 1e9 if "billion" in pattern else (1e6 if "million" in pattern else 1)
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                raw = float(match.group(1).replace(",", ""))
                # Filter out years (1900-2100)
                if 1900 <= raw <= 2100:
                    continue
                val = raw * multiplier
                numbers.append(val)
            except ValueError:
                continue
    return numbers


def get_ground_truth(company: str, metric: str, fiscal_year: int) -> float | None:
    """Fetch the authoritative value directly from SQL Server."""
    import pyodbc
    conn = pyodbc.connect(config.SQL_CONNECTION_STRING)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT value FROM financials f1
        WHERE f1.company = ?
          AND f1.metric = ?
          AND f1.fiscal_year = ?
          AND f1.period_end = (
              SELECT MAX(f2.period_end)
              FROM financials f2
              WHERE f2.company = f1.company
                AND f2.metric = f1.metric
                AND f2.fiscal_year = f1.fiscal_year
          )
          AND f1.filed = (
              SELECT MAX(f3.filed)
              FROM financials f3
              WHERE f3.company = f1.company
                AND f3.metric = f1.metric
                AND f3.fiscal_year = f1.fiscal_year
                AND f3.period_end = f1.period_end
          )
    """, (company.lower(), metric, fiscal_year))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def validate_answer(answer: str, company: str, metric: str,
                    fiscal_year: int, tolerance: float = 0.02) -> dict:
    """
   

    tolerance: allowed fractional difference (default 2%)
    Returns { passed, ground_truth, extracted_numbers, note }
    """
    ground_truth = get_ground_truth(company, metric, fiscal_year)
    if ground_truth is None:
        return {
            "passed":            None,
            "ground_truth":      None,
            "extracted_numbers": [],
            "note":              f"No ground truth found for {company}/{metric}/{fiscal_year}",
        }

    extracted = extract_numbers_from_text(answer)
    if not extracted:
        return {
            "passed":            False,
            "ground_truth":      ground_truth,
            "extracted_numbers": [],
            "note":              "No numbers found in answer to validate",
        }

    # Check if any extracted number is within tolerance of ground truth
    passed = any(
        abs(n - ground_truth) / max(abs(ground_truth), 1) <= tolerance
        for n in extracted
    )

    closest = min(extracted, key=lambda n: abs(n - ground_truth))

    return {
        "passed":            passed,
        "ground_truth":      ground_truth,
        "extracted_numbers": extracted,
        "closest_match":     closest,
        "difference_pct":    round(abs(closest - ground_truth) / max(abs(ground_truth), 1) * 100, 2),
        "note":              "PASS — answer consistent with SQL data" if passed
                             else f"FAIL — answer states ~{closest:,.0f} but SQL says {ground_truth:,.0f}",
    }


def stress_test_validator():
    """
    Inject deliberately wrong answers and confirm the validator catches them.
    This proves the validator actually works.
    """
    print("=== Validator Stress Test ===\n")
    print("Testing with CORRECT answers first...\n")

    # Test 1: correct revenue — should PASS
    correct_answer = "Tesla's revenue in 2024 was $97,690,000,000 USD."
    result = validate_answer(correct_answer, "tesla",
                             "RevenueFromContractWithCustomerExcludingAssessedTax", 2024)
    print(f"Test 1 (correct answer — should PASS):")
    print(f"  Ground truth: ${result['ground_truth']:,.0f}")
    print(f"  Extracted:    {result['extracted_numbers']}")
    print(f"  → {result['note']}\n")

    # Test 2: inflated revenue — should FAIL
    wrong_answer = "Tesla's revenue in 2024 was $150 billion."
    result = validate_answer(wrong_answer, "tesla",
                             "RevenueFromContractWithCustomerExcludingAssessedTax", 2024)
    print(f"Test 2 (inflated answer — should FAIL):")
    print(f"  Ground truth: ${result['ground_truth']:,.0f}")
    print(f"  Extracted:    {result['extracted_numbers']}")
    print(f"  → {result['note']}\n")

    # Test 3: wrong order of magnitude — should FAIL
    wrong_answer2 = "Tesla's revenue in 2024 was $97.69 million."
    result = validate_answer(wrong_answer2, "tesla",
                             "RevenueFromContractWithCustomerExcludingAssessedTax", 2024)
    print(f"Test 3 (wrong magnitude — should FAIL):")
    print(f"  Ground truth: ${result['ground_truth']:,.0f}")
    print(f"  Extracted:    {result['extracted_numbers']}")
    print(f"  → {result['note']}\n")

    # Test 4: correct R&D — should PASS
    correct_rd = "Tesla spent $4,540,000,000 on R&D in 2024."
    result = validate_answer(correct_rd, "tesla",
                             "ResearchAndDevelopmentExpense", 2024)
    print(f"Test 4 (correct R&D — should PASS):")
    print(f"  Ground truth: ${result['ground_truth']:,.0f}")
    print(f"  Extracted:    {result['extracted_numbers']}")
    print(f"  → {result['note']}\n")

    # Test 5: wrong R&D — should FAIL
    wrong_rd = "Tesla spent $10 billion on R&D in 2024."
    result = validate_answer(wrong_rd, "tesla",
                             "ResearchAndDevelopmentExpense", 2024)
    print(f"Test 5 (wrong R&D — should FAIL):")
    print(f"  Ground truth: ${result['ground_truth']:,.0f}")
    print(f"  Extracted:    {result['extracted_numbers']}")
    print(f"  → {result['note']}")


if __name__ == "__main__":
    stress_test_validator()