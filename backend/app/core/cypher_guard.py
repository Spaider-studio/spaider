"""
What raw Cypher is allowed to look at.

The read transaction stops writes. This stops reads of data the endpoint has no
business returning, above all API key hashes and principals, which live in the
same database.

The check is an allow-list over a tokenised query: only knowledge labels may be
named, every node pattern must carry one, and anything that can reach arbitrary
nodes or run nested Cypher is refused. It is deliberately stricter than it needs
to be; rejecting a valid query is cheap.
"""
from __future__ import annotations

import re

ALLOWED_LABELS = frozenset({"SpaiderNode", "Episode", "NodeVersion"})

_FIRST_KEYWORDS = {"MATCH", "OPTIONAL", "WITH", "UNWIND", "RETURN"}
_FORBIDDEN_WORDS = re.compile(
    r"\b(CALL|LOAD|USE|SHOW|START|STOP|GRANT|DENY|REVOKE|TERMINATE|FOREACH|CREATE|MERGE|DELETE|DETACH"
    r"|SET|REMOVE|DROP|ALTER|RENAME|apoc|gds|db|dbms|startNode|endNode|nodes|elementId|keys)\b",
    re.IGNORECASE,
)
_NODE_PATTERN_START = re.compile(r"(?<![\w.`])\(")
_LABEL = re.compile(r":\s*([A-Za-z_]\w*(?:\s*[|&]\s*[A-Za-z_]\w*)*)")


class CypherRejected(ValueError):
    """The query is outside what raw Cypher may do."""


def _strip_strings(cypher: str) -> str:
    """Blank out string literals, refusing anything that could hide tokens."""
    out: list[str] = []
    quote = None
    i = 0
    while i < len(cypher):
        char = cypher[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = None
                out.append('""')
        elif char in "'\"":
            quote = char
        elif char == "\\":
            raise CypherRejected("Escapes are only allowed inside strings.")
        elif cypher.startswith(("//", "/*"), i):
            raise CypherRejected("Comments are not allowed.")
        else:
            out.append(char)
        i += 1
    if quote:
        raise CypherRejected("Unterminated string.")
    return "".join(out)


def _matching_paren(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise CypherRejected("Unbalanced parentheses.")


def _is_node_pattern(text: str, start: int, end: int) -> bool:
    """True for ``(n:L)``, ``(n)``, ``()``; false for ``(a OR b)`` and arithmetic."""
    inner = text[start + 1 : end].strip()
    return bool(re.fullmatch(r"(?:[A-Za-z_]\w*)?\s*(?::[^(){}]*)?\s*(?:\{.*\})?", inner, re.DOTALL))


def check_cypher(cypher: str) -> None:
    """Raise ``CypherRejected`` unless the query stays within knowledge labels."""
    text = _strip_strings(cypher)
    if "`" in text:
        raise CypherRejected("Quoted identifiers are not allowed.")
    words = text.split()
    if not words or words[0].upper() not in _FIRST_KEYWORDS:
        raise CypherRejected("Query must start with MATCH, OPTIONAL MATCH, WITH, UNWIND or RETURN.")
    forbidden = _FORBIDDEN_WORDS.search(text)
    if forbidden:
        raise CypherRejected(f"'{forbidden.group()}' is not allowed.")
    if ";" in text.rstrip().rstrip(";"):
        raise CypherRejected("Only one statement is allowed.")

    for match in _NODE_PATTERN_START.finditer(text):
        start = match.start()
        end = _matching_paren(text, start)
        if not _is_node_pattern(text, start, end):
            continue
        inner = re.sub(r"\{.*\}", "", text[start + 1 : end], flags=re.DOTALL)
        label = _LABEL.search(inner)
        if label is None:
            raise CypherRejected("Every node pattern needs a label.")
        if any(c in inner for c in "!%"):
            raise CypherRejected("Label expressions are not allowed.")
        for name in re.split(r"[|&]", label.group(1)):
            if name.strip() not in ALLOWED_LABELS:
                raise CypherRejected(f"Label '{name.strip()}' is not queryable.")
