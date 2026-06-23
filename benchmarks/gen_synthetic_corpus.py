"""Generate a synthetic *private* company corpus for token-economics scaling tests.

Everything is fictional and deterministic (fixed seed):
  * a bare LLM cannot know it  -> vanilla mode scores ~0%,
  * every QA answer is a UNIQUE value (budget $ or badge id) -> deterministic
    substring scoring, no LLM judge,
  * crucially, every fact is SEMANTICALLY DISTINCT (each project has a unique
    problem-domain + client; each employee a unique specialty), and questions
    anchor on that distinctive content -- so retrieval can pinpoint the exact
    fact instead of drowning among near-identical rows. This makes it a fair
    test of SpAIder's semantic retrieval (vs an exact-record-lookup stress test).

Outputs:
  benchmarks/corpus/nexora.yaml / .txt        full corpus
  benchmarks/tasks/nexora/*.yaml              substring-oracle QA tasks

Run: benchmarks/.venv/bin/python -m benchmarks.gen_synthetic_corpus
"""
from __future__ import annotations

import argparse
import pathlib
import random

import yaml

SEED = 20260615
N_EMPLOYEES = 240
N_PROJECTS = 340
DATE = "2026-05-01"
ROOT = pathlib.Path(__file__).resolve().parent.parent

FIRST = ["Elara", "Marcus", "Priya", "Tomas", "Nadia", "Soren", "Imani", "Dario",
         "Yuki", "Leonie", "Rafael", "Ada", "Kwame", "Ingrid", "Hassan", "Mei",
         "Oskar", "Valeria", "Niamh", "Bjorn", "Lucia", "Anton", "Freya", "Cyrus",
         "Talia", "Mateo", "Sasha", "Greta", "Idris", "Rosa", "Lev", "Anja"]
LAST = ["Quinn", "Vale", "Okonkwo", "Berg", "Halloran", "Nakamura", "Dvorak",
        "Solano", "Fischer", "Adeyemi", "Castellanos", "Rivkin", "Ferreira",
        "Lindqvist", "Marchetti", "Osei", "Petrov", "Abara", "Holloway",
        "Sandoval", "Kettler", "Voss", "Renner", "Dalgaard", "Ibarra", "Strand"]
CITIES = ["Reykjavik", "Porto", "Tallinn", "Valparaiso", "Hobart", "Ljubljana",
          "Kigali", "Da Nang", "Trondheim", "Asuncion"]
CLIENTS = ["Halberd Logistics", "Meridian Bank", "Sundial Health", "Ironwood Retail",
           "Cascade Energy", "Beacon Media", "Tessera Insurance", "Polaris Foods",
           "Vantage Telecom", "Keystone Aerospace"]

# Distinct project problem-domains (each project = unique problem x client).
PROBLEMS = [
    "real-time fraud scoring", "cold-chain temperature tracking", "customer churn prediction",
    "dynamic demand forecasting", "warehouse route optimization", "contract clause extraction",
    "invoice anomaly detection", "network intrusion detection", "predictive equipment maintenance",
    "automated KYC verification", "supply-chain risk modeling", "product recommendation ranking",
    "call-center sentiment analysis", "credit-default risk scoring", "energy-grid load balancing",
    "medical-claims adjudication", "ad-spend attribution", "inventory shrinkage detection",
    "loan-application triage", "sensor-drift calibration", "shipment ETA prediction",
    "price-elasticity modeling", "document redaction", "patient-readmission prediction",
    "clickstream funnel analysis", "vendor-compliance auditing", "telemetry outlier detection",
    "menu-pricing optimization", "workforce-shift scheduling", "returns-fraud detection",
    "lead-scoring automation", "satellite-imagery change detection", "chatbot intent routing",
    "warranty-claim classification", "carbon-footprint accounting", "cyber-threat triage",
    "real-estate valuation", "crop-yield forecasting", "transaction reconciliation",
    "subscription dunning optimization", "fleet fuel-efficiency modeling", "content moderation",
    "supplier lead-time prediction", "insurance-fraud ring detection", "store-traffic forecasting",
    "payment-routing optimization", "support-ticket auto-triage", "wind-turbine yaw control",
    "pharmacy stock replenishment", "ride-demand surge pricing",
]
TECH = ["a streaming Kafka pipeline", "a graph-neural-network model", "a gradient-boosted ensemble",
        "an on-device edge model", "a retrieval-augmented LLM", "a Bayesian state-space model",
        "a reinforcement-learning policy", "a vision transformer", "a federated-learning setup",
        "a real-time feature store"]

# Distinct employee specialties (each employee = unique specialty x sub-domain).
SPECIALTY = [
    "adversarial-robustness testing", "data-governance policy", "GPU-cluster operations",
    "model-drift monitoring", "privacy-preserving machine learning", "real-time streaming infrastructure",
    "experiment-platform design", "vector-index tuning", "LLM red-teaming", "data-pipeline reliability",
    "FinOps cost optimization", "knowledge-graph modeling", "annotation-quality assurance",
    "edge-deployment optimization", "synthetic-data generation", "fairness auditing",
    "observability tooling", "schema-evolution management", "incident response", "feature-store architecture",
    "prompt-injection defense", "retrieval evaluation", "labeling-workflow design", "canary-release tooling",
]
SUBDOMAIN = ["for fraud systems", "for recommendation systems", "for forecasting systems",
             "for computer-vision systems", "for the data platform", "for LLM services",
             "for the risk org", "for the logistics suite", "for healthcare workloads",
             "for the payments stack"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the Nexora synthetic private corpus + QA tasks")
    ap.add_argument("--employees", type=int, default=N_EMPLOYEES, help="number of STAFF facts")
    ap.add_argument("--projects", type=int, default=N_PROJECTS, help="number of PROJECT facts")
    ap.add_argument("--budget-q", type=int, default=11, help="number of project-budget questions")
    ap.add_argument("--badge-q", type=int, default=10, help="number of employee-badge questions")
    ap.add_argument("--suffix", default="", help="output variant suffix, e.g. '_mid' → corpus/nexora_mid.yaml, tasks/nexora_mid/, category nexora_mid")
    ap.add_argument("--oracle", default="substring", choices=["substring", "composite"],
                    help="substring = free pass/fail (default, legacy nexora); composite = EM/F1/GEval continuous metrics")
    args = ap.parse_args()

    n_emp, n_proj = args.employees, args.projects
    corpus_name = f"nexora{args.suffix}"
    rng = random.Random(SEED)

    # unique full names
    names, used = [], set()
    while len(names) < n_emp:
        n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if n not in used:
            used.add(n); names.append(n)

    # unique specialties (specialty x subdomain)
    spec_pairs = [(s, d) for s in SPECIALTY for d in SUBDOMAIN]
    rng.shuffle(spec_pairs)
    spec_pairs = spec_pairs[:n_emp]

    facts, employees, badges = [], [], set()
    for i, name in enumerate(names):
        while True:
            badge = f"NX-{rng.randint(1000, 9999)}"
            if badge not in badges:
                badges.add(badge); break
        spec, sub = spec_pairs[i]
        city = rng.choice(CITIES)
        emp = dict(name=name, badge=badge, spec=spec, sub=sub, city=city,
                   anchor=f"{spec} {sub}")
        employees.append(emp)
        facts.append(dict(date=DATE, type="STAFF", source=f"hr:{badge}",
            text=(f"{name} is Nexora Systems' lead for {spec} {sub}, working out of the "
                  f"{city} office under badge {badge}. They are the point of contact for any "
                  f"work touching {spec}.")))

    # unique projects (problem x client)
    prob_client = [(p, c) for p in PROBLEMS for c in CLIENTS]
    rng.shuffle(prob_client)
    prob_client = prob_client[:n_proj]
    projects, budgets = [], set()
    for problem, client in prob_client:
        owner = rng.choice(employees)
        while True:
            budget = rng.randint(110, 990) * 10000
            if budget not in budgets:
                budgets.add(budget); break
        tech = rng.choice(TECH)
        proj = dict(problem=problem, client=client, owner=owner["name"],
                    budget=budget, tech=tech, anchor=f"{problem} project for {client}")
        projects.append(proj)
        facts.append(dict(date=DATE, type="PROJECT", source=f"proj:{problem}:{client}",
            text=(f"Nexora Systems' {problem} project for {client} is built on {tech}, "
                  f"is led by {owner['name']}, and was greenlit with a budget of ${budget:,}. "
                  f"It is the only Nexora engagement delivering {problem} to {client}.")))

    rng.shuffle(facts)

    # ---- QA tasks: anchor on the UNIQUE semantic descriptor, answer is a unique value ----
    qa = []
    for p in rng.sample(projects, min(args.budget_q, len(projects))):
        qa.append((f"What budget was Nexora's {p['problem']} project for {p['client']} greenlit with?",
                   f"{p['budget']:,}"))
    for e in rng.sample(employees, min(args.badge_q, len(employees))):
        qa.append((f"What is the badge ID of Nexora's lead for {e['spec']} {e['sub']}?",
                   e['badge']))

    (ROOT / f"benchmarks/corpus/{corpus_name}.yaml").write_text(
        yaml.safe_dump({"company": "Nexora Systems", "facts": facts}, sort_keys=False), encoding="utf-8")
    flat = "\n\n".join(f"[{f['type']}] {f['text']}" for f in facts)
    (ROOT / f"benchmarks/corpus/{corpus_name}.txt").write_text(flat, encoding="utf-8")

    tdir = ROOT / f"benchmarks/tasks/{corpus_name}"
    tdir.mkdir(parents=True, exist_ok=True)
    for old in tdir.glob("*.yaml"):
        old.unlink()
    for i, (q, ans) in enumerate(qa, 1):
        tid = f"nx{args.suffix}_{i:02d}"
        task = {
            "id": tid, "category": corpus_name, "title": q, "prompt": q,
            "format_hint": "Answer with just the value, no explanation.",
            "expected_substring": ans, "max_tokens": 256, "requires_mcp": False,
        }
        if args.oracle == "composite":
            # Continuous EM/F1/GEval (the unique value makes EM exact); flows
            # through the same scoring as the AcmeAI/HotpotQA arms.
            task["oracle"] = {"kind": "composite"}
            task["expected_output"] = ans
        (tdir / f"{tid}.yaml").write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")

    print(f"{corpus_name}: {len(facts)} facts (~{len(flat)//4} tokens), {len(qa)} QA tasks")


if __name__ == "__main__":
    main()
