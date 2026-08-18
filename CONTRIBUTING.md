# Contributing to KathaSajha

Thanks for looking. This is a children's product handling family accounts and, soon, payments, so
a few of the rules below are stricter than they might be elsewhere. Most of them exist because
something went wrong once — [docs/LESSONS.md](docs/LESSONS.md) records what, and why the rule is
worded the way it is.

## Licence and what forking means here

KathaSajha is **AGPL-3.0**. Clone it, fork it, change it, self-host it — all fine.

The one obligation worth understanding before you invest time: if you run a **modified** version
as a network service that other people use, section 13 requires you to offer those users the
source of your modified version. Running an unmodified copy, or a modified copy privately, carries
no such duty. Contributions are accepted under the same licence.

## Getting it running

```bash
git clone https://github.com/uditmahato/KathaSajha.git && cd KathaSajha
```

```bash
cp .env.example .env && docker compose up --build
```

That is the whole setup. **No API key is needed** — with `GOOGLE_API_KEY` empty the app runs on a
mock provider that writes deterministic stories and draws local illustrations, so the entire
system (queue, progress, storage, PDF, share links) works end to end at zero cost.

Without Docker:

```bash
cd backend && python -m venv .venv && pip install -r requirements-dev.txt
```

```bash
uvicorn app.main:app --reload --port 8000
```

SQLite, inline jobs, mock provider. `.claude/launch.json` has three prepared configurations,
including one that pairs real Gemini stories with placeholder art.

## Running the tests

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

**On Windows, do not run `pytest` bare.** A bare run once reached 58 GB of committed memory and
took the development machine down. The cause was never reproduced and the item is still on the
watch list, so runs go through a memory-capped wrapper that measures the whole **process tree** —
the venv `python.exe` is a launcher stub whose child does the real work, so a guard watching only
the process it launched reports a flat 1 MB and never fires.

**Before any test run, scan for self-referential monkeypatches.** `monkeypatch.setattr(mod, "f",
lambda *a: mod.f(...))` reads as "forward to the original" and is infinite recursion: the body
resolves `mod.f` at call time, and by then that name is the lambda. Bind the original to a local
first.

## House rules

These are enforced by tests where they can be, and by review where they cannot.

**Never surface internal errors to users.** Provider and storage exceptions go to the log; the user
gets a written sentence. Public schemas omit internal fields entirely — bucket names and endpoints
once shipped onto public share pages this way.

**Log counts and ids, never names.** Log extras are copied verbatim into stdout and ride along as
Sentry breadcrumbs that `send_default_pii=False` does not filter. Children's first names must not
reach a third-party processor.

**No caller-supplied string may enter an instruction section.** Names and descriptions go in the
nonce-delimited data block and are referred to positionally (`HERO 1`). Data placed among the rules
*is* a rule.

**`textContent`, never `innerHTML`.** Story titles are model output and are treated as hostile
everywhere. The i18n applier is bound by the same rule, with a test that fails if `innerHTML`
appears in it.

**Every new user-facing string needs a translation key.** Static text is `data-i18n="key"` in the
markup; text built in JS goes through `t('key')`. Both need a Nepali value in
`frontend/i18n/ne.js`. Sixteen gates in `backend/tests/test_i18n.py` enforce most of this; the
`app.js` literal scan is a documented heuristic, so review still matters.

**Editing `models.py` requires a migration.** CI runs `alembic check` and fails on drift.

**Verify user-visible changes in a real browser.** Several defects here were invisible to API
tests and obvious on screen — a blank SPA caused by a Windows MIME-type quirk, a PDF with ten
blank pages, an expired session showing `t is not a function`.

**A mock provider proves the plumbing, not the product.** Before calling a generation feature
done, run it against the real model and read the output. The mock hid a defect where the model
rewrote a child's name into the story's script.

## Pull requests

- One coherent change per PR, with a message that says *why*, not just what. The commit log here
  is written to be read later.
- Run the suite and `ruff check app tests` plus `ruff format --check app tests` before pushing.
- New behaviour comes with a test. A guard nobody has watched fail is not a guard — break the
  thing it protects and confirm it fires.
- If you add a known gap, add it to [docs/TECH_DEBT.md](docs/TECH_DEBT.md). Honest debt docs are
  the reason this codebase is navigable.
- Architectural choices go in [docs/DECISIONS.md](docs/DECISIONS.md) as an ADR — including the
  option you rejected and why.

## Where the maps are

| Question | File |
|---|---|
| How does it fit together? | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Why is it built this way? | [docs/DECISIONS.md](docs/DECISIONS.md) |
| What is broken or missing? | [docs/TECH_DEBT.md](docs/TECH_DEBT.md) |
| What went wrong before? | [docs/LESSONS.md](docs/LESSONS.md) |
| What are the interface rules? | [docs/UI_UX_PATTERNS.md](docs/UI_UX_PATTERNS.md) |

## Reporting problems

Ordinary bugs: open an issue. **Security issues: do not open a public issue** — see
[SECURITY.md](SECURITY.md).
