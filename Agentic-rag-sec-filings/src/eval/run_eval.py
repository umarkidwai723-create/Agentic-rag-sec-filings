import os
import sys
import json
import time

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
import config

from dotenv import load_dotenv
load_dotenv(override=True)

from src.eval.questions import EVAL_QUESTIONS
from src.agent.graph import run_query


def run_evaluation():
    print("=== EVALUATION RUN ===\n")
    print(f"Total questions: {len(EVAL_QUESTIONS)}\n")

    results = []
    category_stats = {
        "narrative":  {"total": 0, "route_correct": 0, "answer_correct": 0},
        "numeric":    {"total": 0, "route_correct": 0, "answer_correct": 0},
        "cross_doc":  {"total": 0, "route_correct": 0, "answer_correct": 0},
    }

    for i, q in enumerate(EVAL_QUESTIONS):
        print(f"[{i+1}/{len(EVAL_QUESTIONS)}] {q['id']}: {q['query'][:70]}...")

        try:
            result = run_query(q["query"])

            actual_route   = result.get("route", "unknown")
            route_correct  = actual_route == q["expected_route"]
            final_answer   = result.get("final_answer", "")
            validated      = result.get("validated", None)
            validation_note = result.get("validation_note", "")

            # Print routing result
            status = "✓" if route_correct else "✗"
            print(f"  {status} Route: expected={q['expected_route']} | actual={actual_route}")
            print(f"  Validated: {validated} | {validation_note}")

            # Store for manual grading
            record = {
                "id":               q["id"],
                "category":         q["category"],
                "query":            q["query"],
                "expected_route":   q["expected_route"],
                "actual_route":     actual_route,
                "route_correct":    route_correct,
                "final_answer":     final_answer,
                "validated":        validated,
                "validation_note":  validation_note,
                "answer_correct":   None,  # filled in manually after review
                "notes":            q["notes"],
            }
            results.append(record)

            # Update category stats (route only — answer graded manually)
            cat = q["category"]
            category_stats[cat]["total"] += 1
            if route_correct:
                category_stats[cat]["route_correct"] += 1

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "id":             q["id"],
                "category":       q["category"],
                "query":          q["query"],
                "expected_route": q["expected_route"],
                "actual_route":   "ERROR",
                "route_correct":  False,
                "final_answer":   f"ERROR: {e}",
                "validated":      False,
                "answer_correct": False,
                "notes":          q["notes"],
            })
            category_stats[q["category"]]["total"] += 1

        # Small delay to avoid rate limiting
        time.sleep(1)
        print()

    # Save full results to JSON
    os.makedirs("data/processed", exist_ok=True)
    with open("data/processed/eval_results.json", "w") as f:
        json.dump(results, f, indent=2)

    # Print routing summary table
    print("\n" + "="*60)
    print("ROUTING ACCURACY SUMMARY")
    print("="*60)
    print(f"{'Category':<15} {'Total':>6} {'Route Correct':>14} {'Accuracy':>10}")
    print("-"*50)

    total_q = total_correct = 0
    for cat, stats in category_stats.items():
        acc = stats["route_correct"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"{cat:<15} {stats['total']:>6} {stats['route_correct']:>14} {acc:>9.0f}%")
        total_q       += stats["total"]
        total_correct += stats["route_correct"]

    overall = total_correct / total_q * 100 if total_q > 0 else 0
    print("-"*50)
    print(f"{'OVERALL':<15} {total_q:>6} {total_correct:>14} {overall:>9.0f}%")

    print(f"\nFull results saved to: data/processed/eval_results.json")
    print("Note: 'answer_correct' field is null — review eval_results.json and fill in manually.")

    return results


if __name__ == "__main__":
    run_evaluation()