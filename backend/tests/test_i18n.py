"""Drift gates for the interface catalogue.

The catalogue is not the hard part; keeping it true is. Without these, the next
feature adds English strings that bypass the mechanism entirely, CI stays green,
and the Nepali interface rots back into English one view at a time.

All pure Python over the frontend files plus an AST walk of the backend — no JS
test runner, no new dependency.

Honesty about strength, because a gate that is trusted more than it deserves is
worse than no gate: the HTML coverage, key-resolution, placeholder-parity and
raised-code gates are exact. `test_app_js_has_no_bare_english_sentences` is a
HEURISTIC over string literals — it catches the common shapes and can be evaded
by a helper it does not know. It is backed up by the review checklist in
docs/UI_UX_PATTERNS.md, not relied on alone.
"""

import ast
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
BACKEND_APP = Path(__file__).resolve().parents[1] / "app"

INDEX = FRONTEND / "index.html"
I18N_JS = FRONTEND / "i18n.js"
APP_JS = FRONTEND / "app.js"
NE_JS = FRONTEND / "i18n" / "ne.js"

# Legal prose is deliberately English-only pending counsel. Adding a page here
# is how you opt out of translation, and it has to be a visible decision.
LEGAL_PAGES = {"privacy.html", "terms.html"}


def localised_pages() -> list[Path]:
    """Every shipped page, discovered — not a hardcoded list.

    A hardcoded list meant a brand-new page (an FAQ, an about page — the most
    likely next frontend change) was not merely unchecked but unmentionable: it
    could ship with no keys, no switcher and no notice, all gates green.
    """
    return sorted(p for p in FRONTEND.glob("*.html"))


# --------------------------------------------------------------------------
# Catalogue loading

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.S)


def load_ne() -> dict:
    """Parse ne.js the way its docstring promises it can be parsed.

    This is itself a gate: the file must be a comment block, one assignment, and
    strict JSON — nothing else. Checking only the text AFTER the marker would
    let anything at all sit above it, which matters because this file is the one
    a translator edits and is therefore the one most likely to be reviewed as
    "just words" rather than as same-origin executable JavaScript.
    """
    raw = NE_JS.read_text(encoding="utf-8")
    marker = "window.KS_I18N_NE ="
    assert marker in raw, "ne.js must assign window.KS_I18N_NE so the catalogue stays parseable"
    prefix, _, body = raw.partition(marker)
    leftover = _COMMENT_BLOCK.sub("", prefix).strip()
    assert not leftover, (
        "ne.js must contain nothing but comments before the assignment. "
        f"Found executable content: {leftover[:200]!r}"
    )
    return json.loads(body.strip().rstrip(";").strip())


@pytest.fixture(scope="module")
def ne() -> dict:
    return load_ne()


def flatten(value) -> list[str]:
    """A catalogue value is a string or a {one, other} plural object.

    Every gate that inspects values must go through this. Filtering on
    `isinstance(v, str)` silently skipped the plural entries, which is how a
    Devanagari numeral could sit in the usage badge unnoticed.
    """
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, str)]
    return [value] if isinstance(value, str) else []


# --------------------------------------------------------------------------
# HTML parsing


class KeyCollector(HTMLParser):
    """Every translation key referenced by the markup, and the English text
    each one annotates."""

    VOID = {"br", "img", "input", "meta", "link", "hr", "area", "base", "col", "source", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.keys: set[str] = set()
        self.attr_keys: set[str] = set()
        self.slot_parents: dict[str, list[str]] = {}
        self._stack: list[tuple[str, str | None]] = []
        self.english: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        key = a.get("data-i18n")
        if key:
            self.keys.add(key)
            if a.get("data-i18n-slots"):
                self.slot_parents[key] = [s.strip() for s in a["data-i18n-slots"].split(",")]
        if a.get("data-i18n-attr"):
            for pair in a["data-i18n-attr"].split(";"):
                bits = pair.split(":")
                if len(bits) == 2:
                    self.attr_keys.add(bits[1].strip())
        if tag not in self.VOID:
            self._stack.append((tag, key))

    def handle_startendtag(self, tag, attrs):
        # <br /> must not pop a frame it never pushed.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        # Unwind to the matching open tag rather than popping blindly: HTML5
        # makes </li>, </p>, </option> optional, and a blind pop desynced the
        # stack for the whole rest of the document.
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        text = data.strip()
        if not text or not self._stack:
            return
        key = self._stack[-1][1]
        if key:
            self.english[key] = (self.english.get(key, "") + " " + text).strip()


def collect(path: Path) -> KeyCollector:
    parser = KeyCollector()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


# --------------------------------------------------------------------------
# 1. The catalogue is well-formed and complete for every key the markup uses.


def test_ne_catalogue_is_strict_json_with_no_empty_values(ne):
    assert ne["_meta"]["locale"] == "ne"
    empty = [k for k, v in ne.items() if k != "_meta" and (not flatten(v) or any(not s for s in flatten(v)))]
    assert not empty, f"Nepali entries present but empty: {empty}"


def test_every_markup_key_has_a_nepali_translation(ne):
    missing = []
    for page in localised_pages():
        parsed = collect(page)
        for key in sorted(parsed.keys | parsed.attr_keys):
            if key not in ne:
                missing.append(f"{page.name}:{key}")
    assert not missing, (
        "Markup references keys with no Nepali translation. A partial catalogue "
        f"means a half-English interface: {missing}"
    )


# All three JS quote styles. Matching only single quotes made the gate look
# exact while a double-quoted key sailed past it — and the failure is silent in
# the worst way, because English still reads correctly and only Nepali is blank.
_T_CALL = re.compile(r"""\bt\(\s*['"`]([a-z0-9_.]*[a-z0-9])['"`]""")


def test_every_runtime_key_has_a_nepali_translation(ne):
    """Keys app.js passes to t() must resolve, or a screen goes blank in Nepali."""
    used = set(_T_CALL.findall(APP_JS.read_text(encoding="utf-8")))
    # Built from a prefix at runtime, so the literal never appears.
    used |= {
        f"stage.{s}" for s in ("queued", "writing_story", "illustrating", "finalizing", "done", "failed")
    }
    used |= {f"status.{s}" for s in ("pending", "generating", "complete", "failed")}
    missing = sorted(k for k in used if k not in ne)
    assert not missing, f"app.js calls t() for keys with no Nepali translation: {missing}"


def test_every_english_runtime_key_has_a_nepali_translation(ne):
    """The inverse, so an EN entry cannot be added without a translation
    regardless of how — or whether — it is referenced yet."""
    missing = sorted(k for k in _en_runtime_entries() if k not in ne)
    assert not missing, f"i18n.js EN entries with no Nepali translation: {missing}"


def test_catalogue_has_no_keys_nobody_uses(ne):
    """A key nobody reads is a translation nobody will maintain."""
    referenced: set[str] = set()
    for page in localised_pages():
        parsed = collect(page)
        referenced |= parsed.keys | parsed.attr_keys
    js = APP_JS.read_text(encoding="utf-8") + I18N_JS.read_text(encoding="utf-8")
    orphans = []
    for key in ne:
        if key == "_meta" or key in referenced:
            continue
        stem = key.rsplit(".", 1)[0]
        # Server codes and prefix-built keys are referenced by their stem.
        if key in js or f"'{stem}." in js or f"'{key}'" in js:
            continue
        if key.startswith("srv."):
            continue
        orphans.append(key)
    assert not orphans, f"Nepali entries nothing references: {orphans}"


# Values that are legitimately Latin, each justified once. The allowlist is the
# point: without it, a key left holding its English text during translation
# looks complete to every gate and to every reviewer scanning for missing keys.
LATIN_VALUE_ALLOWLIST = {
    "create.count",  # "{count} / {max}" — digits and a slash
    "plan.price.per_month",  # " / month" is rendered beside a Latin price
    "footer.line",  # carries the brand name and a © year
    "auth.tab.login",  # short forms that are borrowed verbatim in Nepali UI
}


def test_nepali_values_are_actually_nepali(ne):
    """A ne.js value that is just the English text passes every completeness
    check while the interface silently rots back to English."""
    devanagari = re.compile(r"[ऀ-ॿ]")
    latin_only = []
    for key, value in ne.items():
        if key == "_meta" or key in LATIN_VALUE_ALLOWLIST:
            continue
        for text in flatten(value):
            if not devanagari.search(text):
                latin_only.append(f"{key}={text!r}")
    assert not latin_only, (
        "Nepali entries with no Devanagari at all — untranslated, or Latin on "
        f"purpose and needing an entry in LATIN_VALUE_ALLOWLIST: {latin_only}"
    )


# --------------------------------------------------------------------------
# 2. Coverage: a new English string cannot ship without a key.

# Text nodes that are deliberately not translated, each with a reason.
COVERAGE_ALLOWLIST = {
    # Brand name. Never translated in any locale — it is the product's name.
    # Two nodes because the Devanagari half sits in its own <span>.
    "KathaSajha",
    "कथा साझा",
    # The character counter's initial value, replaced by t('create.count') on
    # the first keystroke. Latin digits in both locales, so it reads correctly
    # either way and does not need a key of its own.
    "0 / 500",
    # Structural separators between footer links, not language.
    "·",
    # Already Nepali in both locales: a sample title on the hero, and the
    # feature card whose whole point is that the product speaks both.
    '"साहसी हिमाली केटी र चङ्गा"',
    "नेपाली + English",
    # Step numerals and the language <select> whose options name themselves.
    "1",
    "2",
    "3",
    "English",
    "नेपाली (Nepali)",
    "नेपाली",
    # Decorative emoji.
    "🎨",
    "🇳🇵",
    "🦸",
    "📄",
    "🔗",
    "🛡️",
    "🦁",
    "🏔️",
    "🐉",
}

# Attribute values a reader never sees as language.
ATTR_COVERAGE_ALLOWLIST = {
    "you@example.com",  # an email shape, identical in every locale
    "Udit",  # example names
    "Aarav",
}

# Attributes the applier can translate, per i18n.js ATTR_ALLOWLIST.
TRANSLATABLE_ATTRS = ("placeholder", "title", "aria-label", "alt")


class BareTextFinder(HTMLParser):
    """Text and attribute values a reader sees that no key covers."""

    SKIP_TAGS = {"script", "style", "title"}
    VOID = KeyCollector.VOID

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.bare: list[str] = []
        self.bare_attrs: list[str] = []
        self._stack: list[tuple[str, bool, bool]] = []

    @property
    def _keyed(self) -> bool:
        return any(keyed for _, keyed, _ in self._stack)

    @property
    def _skipping(self) -> bool:
        return any(skip for _, _, skip in self._stack)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        # A screen-reader user on the Nepali interface loses most from an
        # unkeyed aria-label: it is the only text they get.
        keyed_attrs = a.get("data-i18n-attr", "")
        for attr in TRANSLATABLE_ATTRS:
            value = a.get(attr)
            if not value or not value.strip():
                continue
            if value.strip() in ATTR_COVERAGE_ALLOWLIST:
                continue
            if f"{attr}:" in keyed_attrs:
                continue
            self.bare_attrs.append(f"<{tag} {attr}={value!r}>")
        if tag not in self.VOID:
            self._stack.append((tag, bool(a.get("data-i18n")), tag in self.SKIP_TAGS))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text or self._keyed or self._skipping:
            return
        if text in COVERAGE_ALLOWLIST:
            return
        self.bare.append(text)


def test_index_html_has_no_untranslatable_text():
    """The gate that makes the whole thing survive the next feature.

    Add a sentence to index.html without a data-i18n key and this fails, which
    is the only reliable moment to notice — nobody reviews a diff asking "did
    this ship English into the Nepali build".
    """
    finder = BareTextFinder()
    finder.feed(INDEX.read_text(encoding="utf-8"))
    assert not finder.bare, (
        "Text in index.html carries no data-i18n key and would stay English in "
        f"every locale. Add a key, or add it to COVERAGE_ALLOWLIST with a reason: {finder.bare}"
    )


def test_index_html_has_no_untranslatable_attributes():
    finder = BareTextFinder()
    finder.feed(INDEX.read_text(encoding="utf-8"))
    assert not finder.bare_attrs, (
        "User-visible attributes with no data-i18n-attr key. An unkeyed "
        f"aria-label is the only text a screen-reader user gets: {finder.bare_attrs}"
    )


def test_the_html_parser_stack_stays_balanced():
    """The coverage gates are only as good as this.

    One element left unclosed used to strand a keyed frame on the stack, after
    which every remaining text node in the document counted as covered and the
    gate reported green over an entirely English page.
    """
    for page in localised_pages():
        parser = KeyCollector()
        parser.feed(page.read_text(encoding="utf-8"))
        assert not parser._stack, f"{page.name} has unclosed tags: {parser._stack[:5]}"


# A heuristic, and labelled as one. Catches the common shapes; a helper it does
# not know about can still slip through, which is why docs/UI_UX_PATTERNS.md
# carries the same rule for human review.
_ASSIGNED_LITERAL = re.compile(
    r"""(?:\.textContent|\.innerText|\.alt|\.title|\.placeholder|\.label)\s*=\s*['"`]([^'"`]{2,})['"`]"""
)
_PASSED_LITERAL = re.compile(r"""(?:toast|setError)\(\s*['"`]([^'"`]{2,})['"`]""")
_SENTENCE = re.compile(r"[A-Za-z]+\s+[A-Za-z]+")

APP_JS_LITERAL_ALLOWLIST = {
    "KathaSajha: /assets/i18n.js did not load; running in degraded English mode.",
}


def test_app_js_has_no_bare_english_sentences():
    """The scan the module docstring promises.

    Every string this file builds at runtime is exactly the class of text that
    belongs in the EN table. Without this, a new one ships untranslated with
    every other gate green — which is the specific rot this suite exists to stop.
    """
    src = APP_JS.read_text(encoding="utf-8")
    offenders = []
    for pattern in (_ASSIGNED_LITERAL, _PASSED_LITERAL):
        for literal in pattern.findall(src):
            if literal in APP_JS_LITERAL_ALLOWLIST:
                continue
            if _SENTENCE.search(literal):
                offenders.append(literal)
    assert not offenders, (
        "English sentences built in app.js instead of going through t(). Add a "
        f"key to the EN table in i18n.js and a Nepali value in ne.js: {offenders}"
    )


# --------------------------------------------------------------------------
# 3. Placeholders must survive translation.

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _en_runtime_entries() -> dict[str, str]:
    """The English runtime catalogue, read line-wise out of i18n.js.

    Deliberately textual: parsing JS properly is not worth a dependency, and
    every entry in that object is one line, which is itself worth enforcing.
    """
    src = I18N_JS.read_text(encoding="utf-8")
    start = src.index("var EN = {")
    end = src.index("\n    };", start)
    entries = {}
    for line in src[start:end].splitlines():
        m = re.match(r"\s*'([a-z0-9_.]+)':\s*(.+?),?\s*$", line)
        if m:
            entries[m.group(1)] = m.group(2)
    return entries


def test_placeholders_match_between_english_and_nepali(ne):
    """A dropped {count} renders a sentence with a hole in it; an invented one
    renders the literal braces to a parent."""
    problems = []

    for key, raw in _en_runtime_entries().items():
        if key not in ne:
            continue
        want = set(PLACEHOLDER.findall(raw))
        got: set[str] = set()
        for text in flatten(ne[key]):
            got |= set(PLACEHOLDER.findall(text))
        if want != got:
            problems.append(f"{key}: English has {sorted(want)}, Nepali has {sorted(got)}")

    for page in localised_pages():
        parsed = collect(page)
        for key, slots in parsed.slot_parents.items():
            if key not in ne:
                continue
            got = set(PLACEHOLDER.findall(ne[key]))
            missing = set(slots) - got
            assert not missing, (
                f"{key} in Nepali is missing slot(s) {sorted(missing)}. The slot "
                "mechanism re-threads real child elements through the translated "
                "sentence; without the placeholder they are dropped."
            )

    assert not problems, problems


def test_server_error_placeholders_match_the_params_the_server_sends(ne):
    """The highest-stakes interpolations in the product live here.

    srv.* keys have no English entry by design, so nothing compared them against
    the params the server actually passes — leaving the quota wall free to lose
    its number, and the prompt-length error free to render a literal {maximum}.
    """
    problems = []
    for code, params in _raised_code_params().items():
        key = f"srv.{code}"
        if key not in ne:
            continue
        used: set[str] = set()
        for text in flatten(ne[key]):
            used |= set(PLACEHOLDER.findall(text))
        unknown = used - params
        if unknown:
            problems.append(f"{key} interpolates {sorted(unknown)}, but the server sends {sorted(params)}")
    assert not problems, problems


def test_consent_sentence_keeps_both_legal_links(ne):
    """The sharpest case. A translation that lost these would ship an
    account-creation flow with no route to the terms it claims consent to."""
    consent = ne["auth.consent"]
    assert "{terms}" in consent and "{privacy}" in consent
    # Both documents are English-only pending counsel; the sentence a Nepali
    # parent consents to has to say so.
    assert "अङ्ग्रेजी" in consent, "The Nepali consent sentence must state the documents are in English"


# --------------------------------------------------------------------------
# 4. Server error codes.

# Raise sites deliberately left in English, each because a parent cannot reach
# it. Opting out is a visible line here rather than a silent omission.
UNCODED_RAISE_ALLOWLIST = {
    # Billing is dormant: plans.py derives purchasability from Stripe config, so
    # none of these render until billing ships, and its copy comes with it.
    "billing.py",
    "plans.py",
}


def _iter_calls(name: str):
    for path in BACKEND_APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if called == name:
                yield path, node


def _constant_or_fail(path: Path, node: ast.Call, kw: ast.keyword) -> str:
    if isinstance(kw.value, ast.Constant):
        return kw.value.value
    # Resolve a module-level constant by name, else fail loudly. Skipping it
    # silently was an escape: hoisting the code to a constant hid it entirely.
    if isinstance(kw.value, ast.Name):
        from app import errors as errors_module

        value = getattr(errors_module, kw.value.id, None)
        if isinstance(value, str):
            return value
    raise AssertionError(
        f"{path.name}:{node.lineno}: code= must be a literal or an app.errors constant "
        "so the drift gate can see it"
    )


def _raised_code_params() -> dict[str, set[str]]:
    """Every code raised, mapped to the param names the server passes with it."""
    found: dict[str, set[str]] = {}
    for name in ("CodedHTTPException", "GenerationError"):
        for path, node in _iter_calls(name):
            kwargs = {kw.arg: kw for kw in node.keywords}
            if "code" not in kwargs:
                continue
            code = _constant_or_fail(path, node, kwargs["code"])
            params: set[str] = set()
            pnode = kwargs.get("params")
            if pnode is not None and isinstance(pnode.value, ast.Dict):
                params = {k.value for k in pnode.value.keys if isinstance(k, ast.Constant)}
            found.setdefault(code, set()).update(params)
    return found


def test_every_raised_error_code_is_translated(ne):
    """Fallback masking is the quiet failure here: an untranslated code still
    renders (in English) and nothing complains at runtime, so the product looks
    finished while it speaks English at exactly the wrong moments."""
    raised = _raised_code_params()
    assert raised, "No coded errors found — the AST walk is not finding them"
    missing = sorted(c for c in raised if f"srv.{c}" not in ne)
    assert not missing, f"Error codes raised with no Nepali translation: {missing}"


def test_new_error_sites_must_carry_a_code():
    """A plain HTTPException is invisible to every other gate: no code means
    nothing to miss, so the string is permanently English and CI never says so."""
    offenders = []
    for path, node in _iter_calls("HTTPException"):
        if path.name in UNCODED_RAISE_ALLOWLIST:
            continue
        offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "Raise CodedHTTPException instead, so the message can be translated — or "
        f"add the module to UNCODED_RAISE_ALLOWLIST with a reason: {offenders}"
    )


def test_generation_errors_with_advice_carry_a_code():
    """A GenerationError's user_message is frozen into a database row, and the
    client prefers the code over that prose. A specific message shipped under
    the generic code is advice the reader in another language never receives."""
    offenders = []
    for path, node in _iter_calls("GenerationError"):
        kwargs = {kw.arg for kw in node.keywords}
        if "user_message" in kwargs and "code" not in kwargs:
            offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"GenerationError sets a custom user_message but no code: {offenders}"


def test_persisted_generation_codes_are_translated(ne):
    """These are frozen into rows and rendered months later."""
    from app import errors

    for code in (
        errors.GENERATION_FAILED,
        errors.GENERATION_INTERRUPTED,
        errors.GENERATION_STALLED,
        errors.GENERATION_BLOCKED,
    ):
        assert f"srv.{code}" in ne, f"Persisted error code {code} has no Nepali translation"


def test_coded_exception_rejects_non_scalar_params():
    """params are interpolated into a sentence a parent reads. Anything but a
    scalar is an invitation to hand an exception or an internal id to a user."""
    from app.errors import CodedHTTPException

    with pytest.raises(TypeError):
        CodedHTTPException(400, code="x.y", detail="d", params={"leak": ValueError("boom")})


# --------------------------------------------------------------------------
# 5. Structural invariants the design depends on.

BACKSLASH = chr(92)


_BLOCK_KEYWORDS = {"if", "for", "while", "switch", "catch", "else", "try", "do", "finally"}


def _opens_a_function(src: str, brace_index: int) -> bool:
    """Does the `{` at brace_index open a function body rather than a block?

    Depth alone cannot answer the question this file actually needs. `if (x) {
    show(v); }` at the top level of the IIFE runs during initial evaluation, but
    its brace makes the call look nested — which is exactly how a boot branch
    slipped past the first version of this gate.
    """
    j = brace_index - 1
    while j >= 0 and src[j].isspace():
        j -= 1
    if j >= 1 and src[j - 1 : j + 1] == "=>":
        return True
    if j < 0 or src[j] != ")":
        return False  # `try {`, `else {`, a bare block
    # Walk back to the matching '(' and look at the token before it.
    depth = 0
    while j >= 0:
        if src[j] == ")":
            depth += 1
        elif src[j] == "(":
            depth -= 1
            if depth == 0:
                break
        j -= 1
    k = j - 1
    while k >= 0 and src[k].isspace():
        k -= 1
    end = k + 1
    while k >= 0 and (src[k].isalnum() or src[k] in "_$"):
        k -= 1
    return src[k + 1 : end] not in _BLOCK_KEYWORDS


def brace_depths(src: str) -> tuple[list[int], list[bool], list[bool]]:
    """Brace depth per character, ignoring strings and comments.

    Used to tell a top-level statement from one nested inside a function, which
    is what distinguishes "runs at boot" from "runs when a button is clicked".
    """
    out: list[int] = []
    code: list[bool] = []
    in_fn: list[bool] = []
    fn_stack: list[bool] = []
    depth = 0
    i = 0
    n = len(src)
    instr: str | None = None
    while i < n:
        ch = src[i]
        if instr:
            if ch == BACKSLASH:
                out.extend([depth, depth])
                code.extend([False, False])
                in_fn.extend([any(fn_stack[1:])] * 2)
                i += 2
                continue
            if ch == instr:
                instr = None
            out.append(depth)
            code.append(False)
            in_fn.append(any(fn_stack[1:]))
            i += 1
            continue
        if ch in "\"'`":
            instr = ch
            out.append(depth)
            code.append(False)
            in_fn.append(any(fn_stack[1:]))
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.extend([depth] * (j - i))
            code.extend([False] * (j - i))
            in_fn.extend([any(fn_stack[1:])] * (j - i))
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i)
            j = n if j < 0 else j + 2
            out.extend([depth] * (j - i))
            code.extend([False] * (j - i))
            in_fn.extend([any(fn_stack[1:])] * (j - i))
            i = j
            continue
        if ch == "{":
            depth += 1
            fn_stack.append(_opens_a_function(src, i))
        elif ch == "}":
            depth -= 1
            if fn_stack:
                fn_stack.pop()
        out.append(depth)
        code.append(True)
        # True when some enclosing brace is a function body, i.e. this position
        # runs only when something calls it — not during initial evaluation.
        in_fn.append(any(fn_stack[1:]))
        i += 1
    return out, code, in_fn


def test_i18n_applier_never_uses_innerhtml():
    """Story titles are model output and are treated as hostile everywhere else.
    A catalogue path that could inject markup would be the one place that
    assumption breaks."""
    for path in (I18N_JS, NE_JS):
        src = path.read_text(encoding="utf-8")
        for sink in (".innerHTML", ".outerHTML", "insertAdjacentHTML"):
            assert sink not in src, f"{path.name} must not write markup: found {sink}"


def test_no_i18n_markup_inside_the_social_meta_block():
    """`/shared/{slug}` replaces everything between these markers with the
    story's own preview tags. Anything of ours in there is deleted on that route
    only — the growth loop — while `/` looks perfect."""
    html = INDEX.read_text(encoding="utf-8")
    start = html.index("<!--SOCIAL_META_START-->")
    end = html.index("<!--SOCIAL_META_END-->")
    block = html[start:end]
    assert "data-i18n" not in block
    assert "i18n.js" not in block


def test_every_view_ships_hidden_so_the_hero_cannot_flash():
    """The whole no-flash argument rests on this. CSP forbids an inline
    bootstrap, so if a view ever paints before the catalogue lands, a Nepali
    visitor watches the landing page flip from English.

    Matches the class, not a snapshot of today's ids — a seventh view is exactly
    what a next feature adds and exactly what a fixed list cannot see.
    """
    html = INDEX.read_text(encoding="utf-8")
    views = re.findall(r'<section\b[^>]*\bclass="([^"]*\bview\b[^"]*)"', html)
    assert len(views) >= 6, f"Expected the app's views to be found, got {views}"
    unhidden = [c for c in views if "hidden" not in c.split()]
    assert not unhidden, f'Every <section class="view"> must also ship hidden: {unhidden}'


def test_no_view_is_revealed_before_the_catalogue_is_ready():
    """Positional checks let a new boot branch slip in above the gate.

    The real invariant is structural: every show() must be nested inside a
    function or inside the ready callback, never at the top level of the IIFE
    where it would run before the catalogue lands.
    """
    src = APP_JS.read_text(encoding="utf-8")
    assert "i18n.ready.then(" in src, "app.js must gate its boot routing on the catalogue"
    depths, is_code, in_function = brace_depths(src)
    assert depths[-1] == 0, "brace scanner desynced; the gate below cannot be trusted"
    top_level = [
        src[: m.start()].count("\n") + 1
        # Real code only (not a mention in a comment), a CALL not the function
        # declaration, and not enclosed by any function body — so it runs during
        # initial evaluation. An `if (...) { show(v); }` at the top level counts,
        # which is precisely the branch the first version of this gate missed.
        for m in re.finditer(r"(?<!function )\bshow\(", src)
        if is_code[m.start()] and not in_function[m.start()]
    ]
    assert not top_level, (
        "show() runs at the top level of app.js, so it paints before the "
        f"catalogue resolves. Move it inside i18n.ready.then(). Lines: {top_level}"
    )


def test_the_translator_is_never_shadowed():
    """`t` is the translator for the whole of app.js.

    Two call sites did `const t = token()` — harmless while the next line was an
    English literal, and a TypeError the moment it became `t('err.session_expired')`.
    A parent whose session expired was shown "t is not a function". The name is
    now reserved: bind the JWT to something else.
    """
    src = APP_JS.read_text(encoding="utf-8")
    shadows = []
    for i, line in enumerate(src.splitlines(), start=1):
        if re.search(r"\b(?:const|let|var)\s+t\s*=", line) and "i18n.t(" not in line:
            shadows.append(f"{i}: {line.strip()}")
        if re.search(r"function\s*\w*\s*\([^)]*\bt\b[^)]*\)", line):
            shadows.append(f"{i}: {line.strip()}")
        if re.search(r"\(\s*t\s*(?:,|\)\s*=>)", line):
            shadows.append(f"{i}: {line.strip()}")
    assert not shadows, f"`t` is the translator and must not be rebound in app.js: {shadows}"


def test_i18n_script_loads_before_app_js():
    html = INDEX.read_text(encoding="utf-8")
    assert html.index("/assets/i18n.js") < html.index("/assets/app.js")


def test_every_page_ships_the_language_switcher():
    """A page without the switcher is a dead end for a reader who cannot read
    the language it is in."""
    for page in localised_pages():
        html = page.read_text(encoding="utf-8")
        assert "/assets/i18n.js" in html, f"{page.name} does not load the i18n runtime"
        assert 'id="localeSelect"' in html, f"{page.name} has no language switcher"


def test_legal_pages_are_not_translated_but_say_so(ne):
    """Machine-translating a privacy policy for a children's product is worse
    than leaving it English. Declaring it, in Nepali, is the honest middle."""
    for page in localised_pages():
        if page.name not in LEGAL_PAGES:
            continue
        html = page.read_text(encoding="utf-8")
        assert 'data-i18n-locale-only="ne"' in html, f"{page.name} must carry the Nepali notice"
        body = html[html.index("<main") :]
        # The policy text itself carries no keys — nobody can fill them in later.
        assert body.count("data-i18n=") <= 3, f"{page.name} legal prose must not be keyed for translation"
    assert "legal.english_only.body" in ne
    # And the footer links admit it before the reader clicks.
    assert "अङ्ग्रेजी" in ne["footer.privacy"] and "अङ्ग्रेजी" in ne["footer.terms"]


def test_numerals_stay_latin_in_the_nepali_catalogue(ne):
    """Decided once, on purpose: prices and quota counts cannot be Devanagari
    without more work, and mixing the two on one card is worse than either."""
    devanagari_digits = re.compile(r"[०-९]")
    offenders = [key for key, value in ne.items() if any(devanagari_digits.search(t) for t in flatten(value))]
    assert not offenders, f"Use Latin digits: {offenders}"


def test_nepali_spells_visarga_not_an_ascii_colon(ne):
    """निःशुल्क was written with U+003A nine times, including on the signup CTA.

    A colon is Latin punctuation, not a Devanagari letter: it is a mandatory
    line-break opportunity, it breaks word selection and in-page search, and a
    screen reader pronounces it. It is also the loudest possible tell that no
    native speaker read the file.
    """
    offenders = []
    for key, value in ne.items():
        for text in flatten(value):
            # A colon is fine after a word (introducing a list); inside one it is
            # a misspelt visarga.
            for m in re.finditer(r"[ऀ-ॿ]:[ऀ-ॿ]", text):
                offenders.append(f"{key}: ...{text[max(0, m.start() - 10) : m.end() + 10]}...")
    assert not offenders, "ASCII colon inside a Devanagari word — use U+0903 VISARGA (ः): " + str(offenders)


def test_shared_keys_have_the_same_english_in_markup_and_js(ne):
    """index.html and i18n.js both carry English for a handful of keys. Two
    sources for one sentence drift; this makes the drift a CI failure."""
    runtime = _en_runtime_entries()
    markup = collect(INDEX).english
    mismatches = []
    for key, html_text in markup.items():
        if key not in runtime:
            continue
        js_literal = runtime[key].strip().strip(",")
        if not (js_literal.startswith("'") or js_literal.startswith('"')):
            continue  # plural object; compared by placeholder test instead
        js_text = js_literal[1:-1].replace("\\'", "'")
        if js_text != html_text:
            mismatches.append(f"{key}: markup={html_text!r} js={js_text!r}")
    assert not mismatches, mismatches
