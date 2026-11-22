"""Counts the errors and warnings the rig's services logged, grouped by kind.

After a clean run of the browser tests nothing unexpected should be in the logs.
Every kind listed here has a reason. Anything else is reported and fails the run,
because a service that logs a new kind of error is usually broken in a way no
test asserted on.

    backend/.venv/bin/python backend/tests/rig/logs.py [--since 30m]

Do not run it after the chaos tests: stopping services on purpose fills the logs
with errors that are expected then.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys

CONTAINERS = ["spaider-backend-api", "s-backend-worker-1", "spaider-kong", "spaider-frontend"]

# (pattern, why it is expected). Patterns match the normalised message.
ALLOWED = [
    (
        r"Vector index 'spaider_embedding' was missing on an empty database",
        "a new database creates its index on the first start",
    ),
    (
        r'the "user" directive makes sense only if the master process runs with super-user',
        "Kong runs as an unprivileged user in its image",
    ),
    (
        r"an upstream response is buffered to a temporary file .*graph/multiverse",
        "the multiverse graph is a large JSON body; nginx spools it to disk, which is harmless",
    ),
]

# Only with --expect-restarts: the live feed journey restarts the API on purpose, so
# Kong cannot reach it for a moment and an open stream is cut.
RESTART_EXPECTED = [
    (
        r"connect\(\) failed \(\d+: Connection refused\) while connecting to upstream",
        "the API was restarted on purpose",
    ),
    (r"upstream prematurely closed connection while reading upstream", "the API was restarted on purpose"),
]


def logs(container: str, since: str) -> list[str]:
    result = subprocess.run(
        ["docker", "exec", "spaider-rig", "docker", "logs", "--since", since, container],
        capture_output=True,
        text=True,
        check=False,
    )
    return (result.stdout + result.stderr).splitlines()


def normalise(message: str) -> str:
    message = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f-]{23,}", "#", message)
    message = re.sub(r"\b\d+(\.\d+)?\b", "#", message)
    return message[:160]


def classify(line: str) -> tuple[str, str] | None:
    """(level, message) for a line that is an error or warning, else None."""
    try:
        record = json.loads(line)
        level, message = str(record.get("level", "")).upper(), str(record.get("message", ""))
    except ValueError:
        if re.search(r"\[(error|crit|emerg|warn)\]", line):
            return ("WARNING" if "[warn]" in line else "ERROR", line)
        return None
    if level in ("ERROR", "CRITICAL", "WARNING"):
        return level, message
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="24h")
    parser.add_argument(
        "--expect-restarts",
        action="store_true",
        help="also allow the errors an intentional API restart causes (the live feed journey does one)",
    )
    args = parser.parse_args()
    allowed = ALLOWED + (RESTART_EXPECTED if args.expect_restarts else [])

    unexpected: collections.Counter = collections.Counter()
    expected: collections.Counter = collections.Counter()
    for container in CONTAINERS:
        for line in logs(container, args.since):
            found = classify(line)
            if found is None:
                continue
            level, message = found
            kind = (container, level, normalise(message))
            reason = next((why for pattern, why in allowed if re.search(pattern, message)), None)
            (expected if reason else unexpected)[kind] += 1

    print(f"expected, with a reason: {sum(expected.values())} lines in {len(expected)} kinds")
    for (container, level, message), count in expected.most_common():
        print(f"  {count:5d} {container} {level[:4]} {message[:100]}")
    print(f"UNEXPECTED: {sum(unexpected.values())} lines in {len(unexpected)} kinds")
    for (container, level, message), count in unexpected.most_common(25):
        print(f"  {count:5d} {container} {level[:4]} {message}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
