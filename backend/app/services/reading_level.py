"""Age bands and what they change about a story.

Age is only useful if it changes the OUTPUT; otherwise the profile form is
decoration. A band sets paragraph count, words per paragraph, a sentence-length
ceiling, and — most importantly for a children's product — how much jeopardy is
allowed on the page.

Bands, not integers, are what cross every boundary: an exact age is more
personal data than the system ever consumes, and a coarse band is what actually
steers a model. Nothing here touches the database, settings, or I/O, so it is
trivially testable and reusable by both providers.
"""

from __future__ import annotations

from dataclasses import dataclass

# Closed vocabulary. Codes are permanent and never repurposed: if the numbers
# behind a band are ever retuned, that ships as a NEW code so stories generated
# under the old definition keep meaning what they meant.
TODDLER = "toddler"
PRESCHOOL = "preschool"
EARLY = "early_reader"
MIDDLE = "middle_grade"
PRETEEN = "preteen"
UNSPECIFIED = ""  # the parent did not say; behaves exactly as before this feature

AGE_BANDS = (TODDLER, PRESCHOOL, EARLY, MIDDLE, PRETEEN)
BAND_LABELS = {
    TODDLER: "2-3 years",
    PRESCHOOL: "4-5 years",
    EARLY: "6-7 years",
    MIDDLE: "8-10 years",
    PRETEEN: "11-12 years",
}

# The child-safety floor applies to EVERY band including the oldest. Bands may
# only tighten it, never loosen it — a test asserts this text appears verbatim
# in the instruction for every band, so a well-meaning tune of `preteen` cannot
# quietly erode what protects a four-year-old.
SAFETY_FLOOR = (
    "strictly child-appropriate: no violence, no cruelty, no romance, no adult themes, "
    "and nothing frightening left unresolved"
)


@dataclass(frozen=True)
class ReadingLevel:
    band: str
    paragraphs: int
    words_low: int
    words_high: int
    max_sentence_words: int | None
    jeopardy: str
    audience: str


_LEVELS: dict[str, ReadingLevel] = {
    TODDLER: ReadingLevel(
        band=TODDLER,
        paragraphs=3,
        words_low=20,
        words_high=35,
        max_sentence_words=7,
        jeopardy="No jeopardy at all. Nothing is lost, dark, or scary, and no one is ever alone.",
        audience="a 2-3 year old",
    ),
    PRESCHOOL: ReadingLevel(
        band=PRESCHOOL,
        paragraphs=3,
        words_low=25,
        words_high=45,
        max_sentence_words=9,
        jeopardy="No real jeopardy. Any worry is small and resolved in the same paragraph.",
        audience="a 4-5 year old",
    ),
    EARLY: ReadingLevel(
        band=EARLY,
        paragraphs=4,
        words_low=45,
        words_high=70,
        max_sentence_words=13,
        jeopardy="Mild worry is allowed, named plainly and resolved within the same scene.",
        audience="a 6-7 year old",
    ),
    MIDDLE: ReadingLevel(
        band=MIDDLE,
        paragraphs=5,
        words_low=70,
        words_high=100,
        max_sentence_words=18,
        jeopardy=(
            "A real obstacle may last across two scenes and a character may be briefly "
            "frightened, but no one is ever in physical danger."
        ),
        audience="an 8-10 year old",
    ),
    PRETEEN: ReadingLevel(
        band=PRETEEN,
        paragraphs=5,
        words_low=90,
        words_high=115,
        max_sentence_words=22,
        jeopardy=(
            "A genuine setback and a wrong choice with consequences are allowed. No physical "
            "danger, and the ending is still warm."
        ),
        audience="an 11-12 year old",
    ),
}

# What the app did before bands existed. Selecting it must reproduce the old
# behaviour exactly, so it is defined as "today's numbers", not as a new tuning.
_DEFAULT = ReadingLevel(
    band=UNSPECIFIED,
    paragraphs=5,
    words_low=60,
    words_high=110,
    max_sentence_words=None,
    jeopardy="",
    audience="kids aged 6-12",
)


def is_valid_band(band: str) -> bool:
    return band == UNSPECIFIED or band in _LEVELS


def level_for_band(band: str) -> ReadingLevel:
    """Unknown or empty resolves to the pre-feature default — never to a
    stricter or looser band by accident."""
    return _LEVELS.get(band, _DEFAULT)


def _rank(band: str) -> int:
    return AGE_BANDS.index(band) if band in AGE_BANDS else -1


def resolve_band(bands: list[str]) -> str:
    """One story, several siblings: the YOUNGEST sets the reading level.

    A book is read to all of them at once, so vocabulary, sentence length, and
    the jeopardy ceiling must suit the youngest listener. Per-child role
    complexity is handled separately in the story instruction, which is what
    keeps the eleven-year-old from being condescended to.

    Children with no band are excluded rather than treated as youngest — an
    unknown age must not silently drag the whole story down to toddler.
    """
    known = [b for b in bands if b in AGE_BANDS]
    if not known:
        return UNSPECIFIED
    return min(known, key=_rank)
