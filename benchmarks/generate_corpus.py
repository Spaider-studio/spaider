"""
Generate a 30-day synthetic corpus for the "Compounding Brain" demo.

Produces two artefacts under ``benchmarks/corpus/``:

  acmeai_30d.yaml  — structured list of dated facts; consumed by
                     ``benchmarks.seed --corpus`` to ingest into SpAIder.
  acmeai_30d.txt   — flat-text dump of the same facts; injected as the
                     system prompt in ``--mode vanilla-context``.

The corpus has two layers:

  * **Story arc** (hand-authored, ~30 facts): coherent narrative about
    AcmeAI's Q2 launch of project Atlas, with embedded risks and
    customer threads that the strategic question set asks about.
  * **Noise** (procedural, ~90 facts): realistic but unrelated PR
    merges, standups, code reviews. Forces synthesis — the answer is
    not the first thing retrieved; the model has to weigh signal
    against irrelevance.

Determinism: ``random.Random(seed=42)`` — re-running produces byte-identical
output. The generated files are checked into the repo so consumers don't
need Python to read them.

Run:
    benchmarks/.venv/bin/python -m benchmarks.generate_corpus
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Constants — the AcmeAI universe
# ---------------------------------------------------------------------------

CORPUS_DIR = Path(__file__).parent / "corpus"

# Anchor the 30-day window to a stable date so regenerations match.
START_DATE = date(2026, 3, 1)
DAYS = 30

ENGINEERS = ["Sara", "Marcus", "Priya", "Diego", "Jin", "Olivia"]
CTO = "Olivia"
CUSTOMERS = [
    "Acme Corp",       # general account, mostly happy
    "Globex",          # billing reconciliation issue
    "Initech",         # quiet, churn risk
    "Wayne Enterprises",
    "Stark Industries",  # demanding feature Y by Q2 — churn risk
]
PROJECTS = ["Atlas", "Beacon", "Cobalt"]
FILES = [
    "backend/app/auth_service.py",
    "backend/app/billing.py",
    "backend/app/api/atlas.py",
    "backend/app/services/parser.py",
    "frontend/src/pages/Dashboard.tsx",
    "frontend/src/components/AtlasView.tsx",
    "infra/k8s/atlas-deployment.yaml",
    "infra/terraform/billing.tf",
]


@dataclass
class Fact:
    date: str       # ISO yyyy-mm-dd
    type: str       # PR | CUSTOMER | DECISION | SPRINT | BLOCKER | HIRE | RETRO | REVIEW | OPS
    text: str       # natural-language fact
    source: str     # provenance hint


# ---------------------------------------------------------------------------
# Story arc — hand-authored. These are the facts the strategic questions
# ask about. ~30 entries woven across the 30 days.
# ---------------------------------------------------------------------------

STORY_FACTS: list[Fact] = [
    # === Week 1 — setup ===
    Fact("2026-03-02", "SPRINT",
         "Sprint 11 kickoff. Atlas Q2 launch target reaffirmed for June 28. "
         "Olivia (CTO) confirmed launch-readiness review will gate the release.",
         "notion:sprint-11-kickoff"),
    Fact("2026-03-02", "DECISION",
         "Decided to delay the Beacon analytics rollout to Q3. "
         "Reason: focus the team on Atlas Q2 launch. "
         "Decision owner: Olivia. Affects: Diego, Jin (were staffed on Beacon).",
         "notion:adr-2026-03-02"),
    Fact("2026-03-03", "CUSTOMER",
         "Stark Industries (Tony, VP Product) on a call with Olivia: explicitly "
         "stated they will churn if feature Y (per-tenant rate limiting) is not "
         "shipped before their Q2 board review on June 20. Renewal at risk: $480k ARR.",
         "salesforce:opp-stark-renewal"),
    Fact("2026-03-04", "DECISION",
         "Feature Y (per-tenant rate limiting) bumped to Sprint 13 priority 1. "
         "Owner: Marcus. Driven by: Stark Industries churn risk.",
         "notion:adr-2026-03-04"),
    Fact("2026-03-05", "BLOCKER",
         "Marcus reported AWS rate-limit ceiling on the billing reconciliation "
         "job is now hit daily. Globex's invoices are failing to reconcile in time. "
         "Marcus is blocked pending a quota raise from AWS support.",
         "slack:#engineering"),
    Fact("2026-03-06", "CUSTOMER",
         "Globex (Sam, finance) emailed: their March invoice was generated 6 days "
         "late again. This is the third consecutive month. They are escalating to "
         "their CFO. Account at risk.",
         "email:support@globex"),
    # === Week 2 — risks accumulate ===
    Fact("2026-03-09", "BLOCKER",
         "Sara found an intermittent token-validation race in auth_service.py. "
         "Cannot reproduce reliably; only happens under load >300 RPS. "
         "Could affect the Atlas launch if it manifests in prod.",
         "github:issue#287"),
    Fact("2026-03-10", "OPS",
         "Domain registrar reminder email forwarded to ops@acmeai: the "
         "acmeai.com domain expires on 2026-06-15 — 13 days before Atlas launch. "
         "No action recorded. Owner unclear.",
         "email:ops@acmeai"),
    Fact("2026-03-11", "DECISION",
         "Database migration window for Atlas: scheduled for 2026-06-25 "
         "(weekend before launch). Priya raised concern that this leaves zero "
         "buffer if migration fails. Olivia confirmed the schedule anyway, "
         "citing migration window availability with the DB vendor.",
         "notion:adr-2026-03-11"),
    Fact("2026-03-12", "CUSTOMER",
         "Acme Corp (Nina, eng manager) requested a sandbox environment for "
         "their team to test integration with Atlas pre-launch. Diego promised "
         "delivery by end of Sprint 12.",
         "intercom:thread-acme-sandbox"),
    Fact("2026-03-13", "REVIEW",
         "Code review on PR#312 (Marcus, feature Y skeleton): Sara raised concern "
         "about the rate-limiter using Redis sorted sets without a TTL — could "
         "lead to unbounded key growth. Marcus accepted; deferred fix to next PR.",
         "github:PR#312"),
    Fact("2026-03-13", "RETRO",
         "Sprint 11 retro: team flagged that the Stark feature Y dependency "
         "is now blocking Atlas critical path. Olivia accepted the risk; "
         "did not authorise additional headcount.",
         "notion:retro-sprint-11"),
    # === Week 3 — mid-month ===
    Fact("2026-03-16", "BLOCKER",
         "Marcus: AWS support raised quota but the reconciliation job is still "
         "missing the SLA. Root cause is now suspected to be a join in billing.py "
         "that was added in Q1 — it scans the full ledger table. Plan: rewrite "
         "as windowed query in Sprint 13.",
         "slack:#engineering"),
    Fact("2026-03-17", "CUSTOMER",
         "Initech (Brad, IT) sent an unrenewal notice. They cite no specific "
         "issue but the account has been quiet for 4 months. Diego flagged: "
         "no one from AcmeAI has reached out since November. ARR at risk: $90k.",
         "email:brad@initech"),
    Fact("2026-03-18", "PR",
         "Priya merged PR#319 into main: 'atlas: add migration scaffolding for "
         "v2 schema'. Reviewed by Jin. The schema introduces a non-null column "
         "with a default — should be a forward-only migration.",
         "github:PR#319"),
    Fact("2026-03-19", "DECISION",
         "Decided NOT to add OpenAI as a fallback LLM provider — Olivia again. "
         "Reason: cost model (per-token) doesn't fit the agent budget that "
         "Atlas's pricing assumes. Confirmed for the second time this quarter; "
         "this discussion is now closed.",
         "notion:adr-2026-03-19"),
    Fact("2026-03-20", "CUSTOMER",
         "Stark Industries (Tony) escalated again: 'we have not seen a feature Y "
         "preview yet; we are in 14-day notice territory unless you confirm a "
         "demo by April 1.' Forwarded to Marcus.",
         "email:tony@stark"),
    Fact("2026-03-20", "BLOCKER",
         "Sara: auth-service token race now reliably reproducible at 350 RPS "
         "with the load-test harness Diego wrote. Root cause located in "
         "session-cache invalidation. Fix in progress, ETA Sprint 13.",
         "github:issue#287"),
    # === Week 4 — final stretch ===
    Fact("2026-03-23", "PR",
         "Marcus merged PR#338 into main: 'feature Y skeleton — per-tenant rate "
         "limiting (alpha)'. Reviewed by Sara. Demo-ready behind a feature flag.",
         "github:PR#338"),
    Fact("2026-03-24", "CUSTOMER",
         "Marcus delivered a feature Y preview to Stark Industries. Tony's "
         "feedback: 'directionally good, but we need rate-limit overrides per "
         "API key, not per tenant'. Bumps scope.",
         "intercom:thread-stark-rate-limits"),
    Fact("2026-03-25", "DECISION",
         "Per-API-key rate limiting accepted as a Sprint 13 follow-up to "
         "feature Y. Owner: Marcus. Confirmed by Olivia.",
         "notion:adr-2026-03-25"),
    Fact("2026-03-26", "BLOCKER",
         "Priya: the schema migration plan now requires a 4-hour read-only "
         "window. Olivia asked whether this conflicts with Stark's Q2 board "
         "review on June 20. Confirmed: if migration window stays at June 25, "
         "Stark is unaffected, but the launch buffer is still zero.",
         "notion:risk-register"),
    Fact("2026-03-27", "OPS",
         "DNS / domain renewal still has no recorded owner. Diego raised it in "
         "the ops standup; action item assigned to ops@ but no human picked it up.",
         "slack:#ops"),
    Fact("2026-03-30", "RETRO",
         "Sprint 12 retro. Top open risks for Atlas Q2 launch named explicitly: "
         "(1) auth-service race (Sara, fix in progress); (2) DB migration "
         "buffer = 0 days (Priya, accepted); (3) acmeai.com domain renewal "
         "due 2026-06-15 — STILL NO OWNER; (4) Stark Industries feature Y "
         "scope creep to per-API-key.",
         "notion:retro-sprint-12"),
    Fact("2026-03-30", "HIRE",
         "Reaffirmed: no additional hiring in Q2. Headcount freeze pending "
         "Series B close (estimated Q3). All Atlas work has to come from "
         "existing team.",
         "notion:adr-2026-03-30"),
]


# ---------------------------------------------------------------------------
# Procedural noise — realistic but irrelevant. Forces synthesis.
# ---------------------------------------------------------------------------


def _noise_facts(rng: random.Random) -> list[Fact]:
    facts: list[Fact] = []

    # Routine PR merges
    pr_titles = [
        "refactor parser_service exception handling",
        "fix flaky pagination test",
        "frontend: dark-mode toggle in user settings",
        "docs: update README examples",
        "infra: bump Postgres minor version",
        "chore: clean up unused imports in atlas.py",
        "atlas: add request_id to log lines",
        "atlas: cache JWT decoder result per request",
        "billing: better error messages on retry exhaustion",
        "frontend: extract DateRangePicker into a hook",
        "tests: add property-based tests for parser",
        "ops: rotate Grafana API tokens",
        "chore: ruff autofix across backend",
        "atlas: drop dead /v0 health route",
        "frontend: rework breadcrumb component",
        "billing: idempotency keys on retries",
        "atlas: extract RateLimitConfig out of settings.py",
        "chore: bump Python to 3.11.15 in CI",
        "infra: add ECR image scanning step",
        "tests: cover the websocket-reconnect path",
    ]
    for i, title in enumerate(pr_titles):
        d = START_DATE + timedelta(days=rng.randint(0, DAYS - 1))
        author = rng.choice(ENGINEERS)
        reviewer = rng.choice([e for e in ENGINEERS if e != author])
        pr_num = 200 + i
        facts.append(Fact(
            d.isoformat(), "PR",
            f"{author} merged PR#{pr_num} into main: '{title}'. Reviewed by {reviewer}.",
            f"github:PR#{pr_num}",
        ))

    # Standup notes
    standup_lines = [
        "team agreed to skip Friday standup, ship a written status instead",
        "Diego will help Marcus on the rate-limiter once feature Y skeleton merges",
        "Jin is on PTO Monday-Wednesday; CI on-call shifted to Sara",
        "Sara mentioned the new ruff version is surfacing pre-existing warnings",
        "Priya proposed pairing on the schema migration; Marcus agreed for one afternoon",
        "Olivia reminded everyone to log launch-readiness blockers in the risk register",
        "Diego: the load-test harness is now reusable; documented in tools/loadgen/README.md",
        "Marcus thanked Sara for the unblock on the redis sorted-set TTL question",
        "Jin: the dashboard query is sometimes slow — assigned to herself for next sprint",
        "team voted to keep the on-call rotation at 1 week, not switch to 2",
    ]
    for line in standup_lines:
        d = START_DATE + timedelta(days=rng.randint(0, DAYS - 1))
        facts.append(Fact(
            d.isoformat(), "SPRINT", f"Standup note: {line}.", "slack:#standups",
        ))

    # Random code reviews
    review_lines = [
        "raised a comment about test coverage on the new parser branch",
        "approved with one minor naming nit, addressed before merge",
        "flagged a missing await on a redis call; author fixed",
        "asked for a smaller PR; author split into two follow-ups",
        "asked for a CHANGELOG entry; author added",
        "questioned whether the new retry logic should use jitter; rolled in",
        "approved without comments — small docs change",
        "noted that the new SQL view should have an index; opened follow-up",
        "asked for a feature flag wrap; author added one",
    ]
    for i, line in enumerate(review_lines):
        d = START_DATE + timedelta(days=rng.randint(0, DAYS - 1))
        author = rng.choice(ENGINEERS)
        reviewer = rng.choice([e for e in ENGINEERS if e != author])
        pr_num = 250 + i
        facts.append(Fact(
            d.isoformat(), "REVIEW",
            f"{reviewer} reviewed PR#{pr_num} (by {author}): {line}.",
            f"github:PR#{pr_num}",
        ))

    # Random ops events
    ops_lines = [
        "Postgres minor-version upgrade applied to staging without incident",
        "Grafana board for atlas latency added; pinned to engineering channel",
        "blue/green deploy pipeline now requires manual approval into prod",
        "S3 bucket lifecycle rule added: archive logs >90 days to Glacier",
        "Datadog forwarder updated to v2; metric drop verified zero gap",
        "Kafka topic retention bumped from 24h to 72h on staging",
        "production read-only banner tested in staging; behavior matches expectation",
        "vendor (auth0) sent a notice about a planned April maintenance window",
        "weekly DB backup verified; restore time = 22 minutes for 50 GB snapshot",
        "stage-only chaos run last night; killed one billing pod, recovered in 18s",
    ]
    for line in ops_lines:
        d = START_DATE + timedelta(days=rng.randint(0, DAYS - 1))
        facts.append(Fact(d.isoformat(), "OPS", f"{line}.", "slack:#ops"))

    # Random customer touches (low-stakes)
    customer_touches = [
        "Wayne Enterprises (Mei) asked about Postgres connection pooling; resolved on a 15-min call",
        "Acme Corp (Nina) confirmed sandbox access works as advertised",
        "Wayne Enterprises asked for a soc2-type-ii letter; sent",
        "Acme Corp asked whether SSO via Okta is on the roadmap; answered yes Q3",
        "Wayne Enterprises asked about data residency in EU; answered next year",
        "Acme Corp requested a renewal call early; scheduled for April 8",
        "Wayne Enterprises CFO joined a quarterly review; pleased with the integration",
    ]
    for line in customer_touches:
        d = START_DATE + timedelta(days=rng.randint(0, DAYS - 1))
        facts.append(Fact(d.isoformat(), "CUSTOMER", f"{line}.", "intercom:assorted"))

    # Hires (one routine, no Q2 hires per the freeze)
    facts.append(Fact(
        "2026-03-04", "HIRE",
        "Diego closed Q1 hiring for the Atlas team — no offers extended. "
        "Q2 freeze in effect.",
        "notion:hiring-q1-close",
    ))
    facts.append(Fact(
        "2026-03-25", "HIRE",
        "Recruiter (external, Lisa) followed up on a senior platform candidate. "
        "Olivia declined, citing the Q2 freeze.",
        "email:lisa@recruiter",
    ))

    return facts


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _ordered(facts: list[Fact]) -> list[Fact]:
    return sorted(facts, key=lambda f: (f.date, f.type, f.source))


def _write_yaml(facts: list[Fact], target: Path) -> None:
    data = {
        "company": "AcmeAI",
        "window": {"start": START_DATE.isoformat(), "days": DAYS},
        "facts": [asdict(f) for f in facts],
    }
    target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _write_text(facts: list[Fact], target: Path) -> None:
    """Flat dump consumed verbatim by --mode vanilla-context."""
    lines = [
        "AcmeAI — 30-day team activity log",
        "Window: 2026-03-01 → 2026-03-30",
        "",
        "Format: [YYYY-MM-DD] [TYPE] text  (source: ...)",
        "",
    ]
    for f in facts:
        lines.append(f"[{f.date}] [{f.type}] {f.text}  (source: {f.source})")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _summarise(facts: list[Fact]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for f in facts:
        counts[f.type] = counts.get(f.type, 0) + 1
    return {"total": len(facts), "by_type": counts}


def main() -> int:
    rng = random.Random(42)
    facts = _ordered(STORY_FACTS + _noise_facts(rng))
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    yaml_target = CORPUS_DIR / "acmeai_30d.yaml"
    text_target = CORPUS_DIR / "acmeai_30d.txt"
    _write_yaml(facts, yaml_target)
    _write_text(facts, text_target)
    print(f"wrote {yaml_target}  ({yaml_target.stat().st_size} bytes)")
    print(f"wrote {text_target}  ({text_target.stat().st_size} bytes)")
    summary = _summarise(facts)
    print(f"summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
