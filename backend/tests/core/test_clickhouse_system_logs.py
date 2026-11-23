"""ClickHouse deletes nothing from its own system logs unless told to. On a stack that ran for 13 days they
held 6.3 GB next to about 1 MB of audit rows. The file that gives them a lifetime has to stay valid, has
to be the one the compose file mounts, and has to be the copy the CLI ships."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "clickhouse" / "system-logs.xml"

# Logs ClickHouse 24.8 writes by default. A section for any other log would switch that log on.
ON_BY_DEFAULT = {
    "text_log",
    "trace_log",
    "metric_log",
    "asynchronous_metric_log",
    "query_log",
    "part_log",
    "processors_profile_log",
    "error_log",
}
BUSY = {"text_log", "trace_log", "metric_log", "asynchronous_metric_log"}


def _logs() -> dict[str, str]:
    root = ET.parse(CONFIG).getroot()
    assert root.tag == "clickhouse"
    return {child.tag: (child.findtext("ttl") or "").strip() for child in root}


def test_every_default_log_gets_a_lifetime_and_no_other_log_is_switched_on():
    logs = _logs()
    assert set(logs) == ON_BY_DEFAULT
    for name, ttl in logs.items():
        assert re.fullmatch(r"event_date \+ INTERVAL \d+ DAY DELETE", ttl), f"{name}: {ttl!r}"


def test_the_busy_logs_are_kept_at_most_a_week():
    for name, ttl in _logs().items():
        days = int(re.search(r"INTERVAL (\d+) DAY", ttl).group(1))
        assert days <= 30
        if name in BUSY:
            assert days <= 7, f"{name} keeps {days} days"


def test_compose_mounts_the_file_into_the_clickhouse_config_directory():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    block = compose.split("  clickhouse:\n", 1)[1].split("\n\n", 1)[0]
    assert "./clickhouse/system-logs.xml:/etc/clickhouse-server/config.d/system-logs.xml:ro" in block


def test_the_cli_ships_the_same_file():
    shipped = ROOT / "cli" / "src" / "spaider_cli" / "stack" / "clickhouse" / "system-logs.xml"
    assert shipped.read_bytes() == CONFIG.read_bytes()
