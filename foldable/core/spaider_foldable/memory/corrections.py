"""Deterministic (model-independent) handling of explicit corrections and of
messages that are questions rather than statements.

The LLM supersession judge of SpAIder needs extracted edges and a capable
model; small local models often produce neither. This layer does not replace
the judge, it guarantees a floor that holds for every model:

* ``is_explicit_correction``: the user text carries a correction marker
  (German or English; strong markers such as "Korrektur"/"nicht mehr" or
  weak ones such as "jetzt"/"now").
* ``find_corrected_facts``: older, still current FACT nodes that share most of
  their content words with the new statement (strong marker: >= 50 % of the
  old fact's content words, weak marker: >= 75 %; always >= 2 shared words
  and at least one new content word) are considered replaced.
* ``is_question_only``: text that only asks (every sentence ends with "?")
  and does not ask the assistant to remember something is not stored as a
  fact at all.

No entity names or project names are special-cased anywhere in here.
"""

from __future__ import annotations

import re
from typing import Iterable

from .store import _STOPWORDS, _WORD_RE, _content_words


def _novelty_tokens(text: str) -> set[str]:
    """Content words plus numbers: a changed number is new information too."""
    return {
        w.lower()
        for w in _WORD_RE.findall(text or "")
        if (w.isdigit() or len(w) >= 4) and w.lower() not in _STOPWORDS
    }

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
_REMEMBER = re.compile(
    r"\b(merk\w*|merke|erinner\w*|notier\w*|speicher\w*|behalt\w*|remember|note|save|keep in mind)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


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


def find_corrected_facts(new_text: str, candidates: Iterable[dict]) -> list[str]:
    """Ids of older FACT rows (dicts with ``id`` and ``description``) replaced by ``new_text``."""
    strength = correction_strength(new_text)
    if strength is None:
        return []
    threshold = 0.5 if strength == "strong" else 0.75
    new_words = _content_words(new_text)
    # the marker words themselves ("Korrektur", "jetzt", ...) are not new information
    novel_words = _novelty_tokens(_WEAK.sub(" ", _STRONG.sub(" ", new_text)))
    if len(new_words) < 2:
        return []
    out: list[str] = []
    for row in candidates:
        old_text = row.get("description") or ""
        if is_question_only(old_text):
            continue
        old_words = _content_words(old_text)
        if len(old_words) < 2:
            continue
        shared = old_words & new_words
        if len(shared) < 2:
            continue
        if not (novel_words - _novelty_tokens(old_text)):
            continue  # nothing new was said (same statement repeated)
        if len(shared) / len(old_words) >= threshold:
            out.append(row["id"])
    return out
