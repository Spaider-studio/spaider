"""Deterministic (model-independent) handling of explicit corrections and of
messages that are questions rather than statements.

The LLM supersession judge of SpAIder needs extracted edges and a capable
model; small local models often produce neither. This layer does not replace
the judge, it guarantees a floor that holds for every model:

* ``correction_strength``: the user text carries a correction marker (German
  or English; strong markers such as "Korrektur"/"nicht mehr" or weak ones
  such as "jetzt"/"now").
* ``find_corrected_facts_detailed``: older, still current FACT nodes that the
  correction rewrites. The correction is split into a *topic* (the words
  before the marker or the copula: "Der Leiter von Atlas") and a *value*
  (the words after it: "Priya Nair"). An old statement is replaced when it
  shares the topic, does not already contain the value, says something
  different and overlaps with the correction in most of its content words
  (strong marker: >= 50 %, weak marker: >= 75 %). Multi-sentence facts are
  matched per sentence; the untouched sentences are returned so the caller
  can keep them current.
* ``is_question_only``: text that only asks (every sentence ends with "?")
  and does not ask the assistant to remember something is not stored as a
  fact at all.

No entity names or project names are special-cased anywhere in here.
"""

from __future__ import annotations

import re
from typing import Iterable

from .store import _STOPWORDS, _WORD_RE

_STRONG = re.compile(
    r"\b(korrektur|korrigier\w*|berichtig\w*|richtigstell\w*|nicht mehr|nicht länger|nicht laenger|stattdessen|"
    r"statt dessen|falsch|stimmt nicht|stimmte nicht|war falsch|ist falsch|doch nicht|"
    r"correction|corrected|no longer|not anymore|instead|that was wrong|is wrong|was wrong|rather than)\b",
    re.IGNORECASE,
)
_WEAK = re.compile(
    r"\b(jetzt|nun|inzwischen|mittlerweile|ab sofort|ab jetzt|neuerdings|seit kurzem|aktuell|geändert|geaendert|"
    r"gewechselt|aktualisier\w*|update|now|currently|these days|actually|changed|switched)\b",
    re.IGNORECASE,
)
_COPULA = re.compile(
    r"\b(ist|sind|war|waren|heißt|heisst|heißen|lautet|lauten|beträgt|betraegt|betragen|wird|werden|kostet|"
    r"is|are|was|were|costs|equals|becomes)\b",
    re.IGNORECASE,
)
_REMEMBER = re.compile(
    r"\b(merk\w*|merke|erinner\w*|notier\w*|speicher\w*|behalt\w*|remember|note|save|keep in mind)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _strip_markers(text: str) -> str:
    return _WEAK.sub(" ", _STRONG.sub(" ", text or ""))


def _words(text: str) -> set[str]:
    """Content words (>= 4 chars, no stopwords) without correction markers."""
    return {w.lower() for w in _WORD_RE.findall(_strip_markers(text)) if len(w) >= 4 and w.lower() not in _STOPWORDS}


def _tokens(text: str) -> set[str]:
    """Content words plus numbers: a changed number is information too."""
    return {
        w.lower()
        for w in _WORD_RE.findall(_strip_markers(text))
        if (w.isdigit() or len(w) >= 4) and w.lower() not in _STOPWORDS
    }


def correction_strength(text: str) -> str | None:
    """'strong', 'weak' or None."""
    t = (text or "").strip()
    if not t or is_question_only(t):
        return None
    if _STRONG.search(t):
        return "strong"
    if _WEAK.search(t):
        return "weak"
    return None


def is_explicit_correction(text: str) -> bool:
    return correction_strength(text) is not None


def is_question_only(text: str) -> bool:
    """Every sentence is a question and nothing asks the assistant to remember."""
    t = (text or "").strip()
    if not t or not t.endswith("?"):
        return False
    if _REMEMBER.search(t):
        return False
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(t) if s.strip()]
    return bool(sentences) and all(s.endswith("?") for s in sentences)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split((text or "").strip()) if s.strip()]


def split_topic_value(text: str) -> tuple[set[str], set[str]]:
    """Topic words (before the first weak marker, else before the copula) and
    value tokens (after it). Without a split point everything is topic."""
    t = _STRONG.sub(" ", text or "")
    m = _WEAK.search(t) or _COPULA.search(t)
    if not m:
        return _words(t), set()
    return _words(t[: m.start()]), _tokens(t[m.end() :])


def find_corrected_facts_detailed(new_text: str, candidates: Iterable[dict]) -> list[tuple[str, str]]:
    """Older FACT rows (dicts with ``id`` and ``description``) rewritten by ``new_text``.

    Returns ``(id, remaining_text)`` pairs; ``remaining_text`` holds the
    sentences of a multi-sentence fact that the correction does not touch
    (empty when the whole text is replaced).
    """
    strength = correction_strength(new_text)
    if strength is None:
        return []
    threshold = 0.5 if strength == "strong" else 0.75
    new_words = _words(new_text)
    new_tokens = _tokens(new_text)
    topic, value = split_topic_value(new_text)
    if len(new_words) < 2:
        return []

    def matches(text: str) -> bool:
        old_words = _words(text)
        if len(old_words) < 2:
            return False
        shared = old_words & new_words
        if len(shared) < 2:
            return False
        if topic and len(topic & old_words) * 2 < len(topic):
            return False  # different subject
        old_tokens = _tokens(text)
        if value and value <= old_tokens:
            return False  # the old statement already says what the correction says
        if not (new_tokens - old_tokens):
            return False  # nothing new was said (same statement repeated)
        return len(shared) / len(old_words) >= threshold

    out: list[tuple[str, str]] = []
    for row in candidates:
        old_text = row.get("description") or ""
        if is_question_only(old_text):
            continue
        if matches(old_text):
            out.append((row["id"], ""))
            continue
        sentences = _sentences(old_text)
        if len(sentences) < 2:
            continue
        hit = [s for s in sentences if matches(s)]
        if not hit:
            continue
        remaining = " ".join(s for s in sentences if s not in hit)
        out.append((row["id"], remaining))
    return out


def find_corrected_facts(new_text: str, candidates: Iterable[dict]) -> list[str]:
    """Ids of older FACT rows replaced (fully or in one of their sentences) by ``new_text``."""
    return [fid for fid, _ in find_corrected_facts_detailed(new_text, candidates)]
