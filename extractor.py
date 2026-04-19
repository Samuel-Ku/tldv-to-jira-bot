"""
extractor.py — Keyword-based action item extraction from transcription segments.

Loads keywords from keywords.json, scans whisper segments for matches,
returns grouped fragments with context and timestamps.
"""

import json
import re
from pathlib import Path
from typing import TypedDict

KEYWORDS_PATH = Path(__file__).parent / "keywords.json"


class Segment(TypedDict):
    start: float
    end: float
    text: str


class Match(TypedDict):
    start: float
    end: float
    text: str
    keywords: list[str]


# ── Keyword CRUD ──────────────────────────────────────────────


def load_keywords() -> list[str]:
    """Load keywords from keywords.json."""
    if not KEYWORDS_PATH.exists():
        return []
    with open(KEYWORDS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("keywords", [])


def save_keywords(keywords: list[str]) -> None:
    """Save keywords to keywords.json."""
    with open(KEYWORDS_PATH, "w", encoding="utf-8") as f:
        json.dump({"keywords": keywords}, f, ensure_ascii=False, indent=2)


def add_keyword(phrase: str) -> bool:
    """Add a keyword (case-insensitive dedup). Returns True if added."""
    keywords = load_keywords()
    lower_existing = [k.lower() for k in keywords]
    if phrase.strip().lower() in lower_existing:
        return False
    keywords.append(phrase.strip())
    save_keywords(keywords)
    return True


def remove_keyword(phrase: str) -> bool:
    """Remove a keyword (case-insensitive match). Returns True if removed."""
    keywords = load_keywords()
    lower_phrase = phrase.strip().lower()
    new_keywords = [k for k in keywords if k.lower() != lower_phrase]
    if len(new_keywords) == len(keywords):
        return False
    save_keywords(new_keywords)
    return True


# ── Timestamp formatting ──────────────────────────────────────


def format_time(seconds: float) -> str:
    """Convert seconds to MM:SS or HH:MM:SS."""
    seconds = max(0, int(seconds))
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ── Matching engine ───────────────────────────────────────────


def find_matches(
    segments: list[Segment],
    keywords: list[str] | None = None,
    context: int = 2,
) -> list[Match]:
    """
    Scan segments for keyword matches.

    Returns grouped blocks with ±context segments merged into
    contiguous chunks. Each match includes timestamps and the
    keywords that triggered it.
    """
    if keywords is None:
        keywords = load_keywords()

    if not keywords or not segments:
        return []

    # Build regex pattern — match any keyword (case-insensitive)
    escaped = [re.escape(k) for k in keywords]
    pattern = re.compile("|".join(escaped), re.IGNORECASE)

    # Find which segment indices have hits
    hits: dict[int, list[str]] = {}
    for i, seg in enumerate(segments):
        found = pattern.findall(seg["text"])
        if found:
            hits[i] = [kw.lower() for kw in found]

    if not hits:
        return []

    # Expand hits by ±context and merge overlapping ranges
    ranges: list[tuple[int, int, list[str]]] = []
    for idx, kws in sorted(hits.items()):
        lo = max(0, idx - context)
        hi = min(len(segments) - 1, idx + context)
        if ranges and lo <= ranges[-1][1] + 1:
            # Merge with previous range
            prev_lo, prev_hi, prev_kws = ranges[-1]
            merged_kws = list(set(prev_kws + kws))
            ranges[-1] = (prev_lo, max(prev_hi, hi), merged_kws)
        else:
            ranges.append((lo, hi, list(kws)))

    # Build output
    matches: list[Match] = []
    for lo, hi, kws in ranges:
        text_parts = [segments[i]["text"] for i in range(lo, hi + 1)]
        matches.append(
            Match(
                start=segments[lo]["start"],
                end=segments[hi]["end"],
                text=" ".join(text_parts),
                keywords=sorted(set(kws)),
            )
        )

    return matches
