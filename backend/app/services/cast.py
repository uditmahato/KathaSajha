"""The cast of a story: who stars in it, frozen at the moment it was made.

A story stores a SNAPSHOT, never a reference to profile rows. Renaming a child
or deleting a profile must not rewrite the books already on the shelf — and a
foreign key would either forbid deletion or silently alter history. The
snapshot is also what lets `Story.hero_name` keep its exact original meaning
for every row that predates this feature.

Also home to coverage measurement: with two or three siblings the model tends
to give one child the adventure and leave the others as scenery, which a parent
notices immediately. `coverage_gaps` measures that from the finished text
rather than trusting the model's own account of itself.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass

from .reading_level import UNSPECIFIED, is_valid_band

logger = logging.getLogger(__name__)

CHILD = "child"
COMPANION = "companion"

# A 3-5 paragraph story is 180-550 words. Each child needs an introduction, one
# decisive act, and presence in at least two scenes — roughly 40-60 words of
# floor before any plot exists. Three is already tight; four is a roll call.
MAX_CHILDREN_PER_STORY = 3
MAX_COMPANIONS_PER_STORY = 2


@dataclass(frozen=True)
class CastMember:
    role: str  # child | companion
    name: str
    age_band: str = UNSPECIFIED  # children only; companions never carry one
    kind: str = ""  # companions only: animal | bird | toy | other
    description: str = ""  # companions only, short and parent-supplied


def to_json(cast: list[CastMember]) -> str:
    return json.dumps([asdict(m) for m in cast], ensure_ascii=False)


def from_json(raw: str) -> list[CastMember]:
    """Tolerant on purpose: a malformed snapshot must degrade to 'no cast'
    rather than break a story page the family can otherwise still read."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("Unreadable cast snapshot; rendering the story without one")
        return []
    out: list[CastMember] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        band = item.get("age_band", UNSPECIFIED) or UNSPECIFIED
        out.append(
            CastMember(
                role=COMPANION if item.get("role") == COMPANION else CHILD,
                name=str(item["name"]),
                age_band=band if is_valid_band(band) else UNSPECIFIED,
                kind=str(item.get("kind", "")),
                description=str(item.get("description", "")),
            )
        )
    return out


def children(cast: list[CastMember]) -> list[CastMember]:
    return [m for m in cast if m.role == CHILD]


def companions(cast: list[CastMember]) -> list[CastMember]:
    return [m for m in cast if m.role == COMPANION]


def hero_name_for(cast: list[CastMember], typed: str) -> str:
    """`Story.hero_name` stays the single authoritative name for the PDF cover,
    social previews, and every existing render path. The first child wins; a
    companion-only story falls back to the typed name."""
    kids = children(cast)
    if kids:
        return kids[0].name
    return typed


def _mentions(text: str, name: str) -> int:
    """Count name occurrences, case-insensitively.

    Devanagari has no word boundaries that \\b understands, so non-ASCII names
    fall back to a substring count rather than reporting zero for every Nepali
    story. Casefolded on both sides so "aarav" counts as Aarav.
    """
    if not name:
        return 0
    if name.isascii():
        return len(re.findall(rf"\b{re.escape(name)}\b", text, flags=re.IGNORECASE))
    return text.casefold().count(name.casefold())


def _attributed(paragraph: str, names: list[str]) -> dict[str, int]:
    """Mentions per name, with longer names claiming their text first.

    Without this, "Ana" is credited for every appearance of "Anaya" and a
    genuinely sidelined Ana looks well covered.
    """
    counts: dict[str, int] = {}
    remaining = paragraph
    for name in sorted(names, key=len, reverse=True):
        counts[name] = _mentions(remaining, name)
        if counts[name] and not name.isascii():
            remaining = remaining.replace(name, " ")
        elif counts[name]:
            remaining = re.sub(rf"\b{re.escape(name)}\b", " ", remaining, flags=re.IGNORECASE)
    return counts


def coverage_gaps(paragraphs: list[str], cast: list[CastMember]) -> list[str]:
    """Which named children the finished story sidelined.

    Measured against the TEXT, never against a model's self-report: a model that
    sidelines a child will happily claim it did not. Three signals:

      * present in fewer than two paragraphs — a walk-on part
      * present only in the opening — introduced and then forgotten
      * fewer than a third of the mentions of the most-mentioned child — the
        classic "one kid gets the adventure" shape, which a plain presence
        check passes while a parent's read fails

    Returns names, not a verdict. The caller decides whether to log, retry, or
    ignore; today the pipeline logs, so the signal exists before anything is
    spent acting on it.
    """
    kids = children(cast)
    if len(kids) < 2 or not paragraphs:
        return []  # a single hero cannot be sidelined by anyone

    names = [k.name for k in kids]
    per_paragraph = [_attributed(p, names) for p in paragraphs]
    counts = {n: sum(pp[n] for pp in per_paragraph) for n in names}
    scenes = {n: sum(1 for pp in per_paragraph if pp[n] > 0) for n in names}
    busiest = max(counts.values()) if counts else 0
    if busiest == 0:
        return []  # nobody was named at all: a different failure, not sidelining

    gaps = []
    for kid in kids:
        name = kid.name
        only_opening = scenes[name] == 1 and per_paragraph[0][name] > 0
        if scenes[name] < 2 or only_opening or counts[name] * 3 < busiest:
            gaps.append(name)
    return gaps
