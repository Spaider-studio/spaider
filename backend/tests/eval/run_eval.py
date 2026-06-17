"""
Automated Evaluation Framework for SemanticCompressor.

Usage:
    python -m tests.eval.run_eval
    python -m tests.eval.run_eval --ids tc_001 tc_002
    python -m tests.eval.run_eval --limit 10
"""
import json
import argparse
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.compressor import SemanticCompressor  # noqa: E402


CASES_PATH = Path(__file__).parent / "test_cases.json"


def score_case(
    result_nodes: list[str],
    result_relations: list[str],
    expected_nodes: list[str],
    expected_relations: list[str],
) -> dict:
    """Compute precision/recall for nodes and relations."""
    result_nodes_lower = {n.lower() for n in result_nodes}
    expected_nodes_lower = {n.lower() for n in expected_nodes}

    result_relations_lower = {r.lower() for r in result_relations}
    expected_relations_lower = {r.lower() for r in expected_relations}

    node_hits = len(result_nodes_lower & expected_nodes_lower)
    rel_hits = len(result_relations_lower & expected_relations_lower)

    node_precision = node_hits / len(result_nodes_lower) if result_nodes_lower else 0.0
    node_recall = node_hits / len(expected_nodes_lower) if expected_nodes_lower else 1.0

    rel_precision = rel_hits / len(result_relations_lower) if result_relations_lower else 0.0
    rel_recall = rel_hits / len(expected_relations_lower) if expected_relations_lower else 1.0

    def f1(p, r):
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    return {
        "node_precision": round(node_precision, 3),
        "node_recall": round(node_recall, 3),
        "node_f1": round(f1(node_precision, node_recall), 3),
        "relation_precision": round(rel_precision, 3),
        "relation_recall": round(rel_recall, 3),
        "relation_f1": round(f1(rel_precision, rel_recall), 3),
    }


def run_eval(case_ids: list[str] | None = None, limit: int | None = None):
    cases = json.loads(CASES_PATH.read_text())

    if case_ids:
        cases = [c for c in cases if c["id"] in case_ids]
    if limit:
        cases = cases[:limit]

    compressor = SemanticCompressor()

    results = []
    passed = 0
    total = len(cases)

    print(f"\nRunning evaluation on {total} test case(s)...\n")
    print(f"{'ID':<12} {'Node F1':>8} {'Rel F1':>8} {'Status':>8}")
    print("-" * 42)

    for case in cases:
        try:
            payload = compressor.extract(case["text"])
            result_nodes = [n.label for n in payload.nodes]
            result_relations = [e.relation for e in payload.edges]

            scores = score_case(
                result_nodes,
                result_relations,
                case["expected_nodes"],
                case["expected_relations"],
            )

            ok = scores["node_f1"] >= 0.5 and scores["relation_f1"] >= 0.5
            if ok:
                passed += 1

            status = "PASS" if ok else "FAIL"
            print(
                f"{case['id']:<12} {scores['node_f1']:>8.3f} {scores['relation_f1']:>8.3f} {status:>8}"
            )

            results.append({"id": case["id"], "scores": scores, "status": status})

        except Exception as e:
            print(f"{case['id']:<12} {'ERROR':>8} {'ERROR':>8} {'FAIL':>8}  ({e})")
            results.append({"id": case["id"], "error": str(e), "status": "ERROR"})

    print("-" * 42)
    print(f"\nPassed: {passed}/{total} ({100 * passed / total:.1f}%)")

    # Aggregate
    scored = [r for r in results if "scores" in r]
    if scored:
        avg_node_f1 = sum(r["scores"]["node_f1"] for r in scored) / len(scored)
        avg_rel_f1 = sum(r["scores"]["relation_f1"] for r in scored) / len(scored)
        print(f"Avg Node F1:     {avg_node_f1:.3f}")
        print(f"Avg Relation F1: {avg_rel_f1:.3f}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Run Spaider extraction eval")
    parser.add_argument("--ids", nargs="+", help="Specific test case IDs to run")
    parser.add_argument("--limit", type=int, help="Limit number of cases")
    args = parser.parse_args()

    results = run_eval(case_ids=args.ids, limit=args.limit)

    # Exit with error code if too many failures
    failures = sum(1 for r in results if r["status"] != "PASS")
    sys.exit(1 if failures > len(results) // 2 else 0)


if __name__ == "__main__":
    main()
