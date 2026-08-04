# Lessons learned

Bugs and mistakes that cost real time. Each has a rule so it cannot recur.

## Never index parallel results by append order
The prototype appended to `images_base64` only when an image succeeded, but the frontend matched
images to paragraphs by array index. One safety block shifted every later illustration onto the wrong
paragraph.
**Rule**: when results must align to inputs, write them to an explicit position, never by append order.
Enforced by `UNIQUE(story_id, position)` and per-position updates.

## Pad by index, not by concatenation
The first realignment of image prompts did `(prompts + paragraphs)[:n]`, which padded a missing prompt
at index 3 with `paragraphs[0]`. Silently wrong art on the last pages.
**Rule**: pad element-wise (`prompts[i] if i < len(prompts) else paragraphs[i]`), and test the ragged case.

## A guard that only knows one bad value is not a guard
The production `SECRET_KEY` check rejected the pydantic default but not the different default that
docker-compose injected, so production could boot with a secret published in the repo.
**Rule**: validate secrets by property (known-bad set plus minimum entropy), never by equality to one string.

## Progress counters must be written under the same lock that increments them
Incrementing under a lock but writing to the DB outside it let a slower task overwrite a higher count,
so the progress bar went backwards.
**Rule**: the read-modify-write of shared progress state happens entirely inside the critical section.

## Check-then-insert is not a quota
Counting stories and then inserting in a separate step let concurrent requests all pass the check.
**Rule**: serialize per-user creation with a row lock before counting.

## Internal exception text is not a user message
Storage and provider errors were persisted verbatim into `image_error`, which was then served on public
share pages, exposing bucket names and endpoints.
**Rule**: users get a generic sentence; details go to the log. Public schemas omit internal fields entirely.

## A reaper that only runs on one endpoint does not reap
Stale-job failover lived only in `GET /api/jobs/{id}`, but a browser refresh loses the job id, so nothing
ever called it again and the story stayed "generating" forever and could not be deleted.
**Rule**: recovery logic must be reachable from every path a user can actually take.

## The dev password in `.env.example` will be copied literally
`POSTGRES_PASSWORD=pick-a-db-password` was copied into `.env`, but the database volume already held a
different password, so the API could not authenticate and the stack looked broken.
**Rule**: example env files ship values that actually work locally, and document that changing a DB
password requires recreating the volume.

## A patch that reads the name it replaces calls itself
`monkeypatch.setattr(asyncio, "sleep", lambda *a, **k: asyncio.sleep(0))` reads as "forward to the real
function". It does not. The body resolves `asyncio.sleep` when it is called, and by then that name is the
lambda, so it recurses until the stack limit.
**Rule**: bind the original to a local before patching (`real_sleep = asyncio.sleep`) and call that. Scan
for a patched name appearing inside its own replacement before running the suite.

## Measure the process tree, not the process you launched
A memory watchdog wrapped around `pytest` reported a flat 1 MB and never fired. The venv `python.exe` is a
launcher stub that spawns a child doing the real work; the stub stayed at 1 MB while its child climbed past
8 GB unseen. The guard looked like protection and was providing none.
**Rule**: sum memory across all descendants via `ParentProcessId`, and validate any resource guard against a
deliberate offender before trusting a quiet reading. A cap that never fires looks exactly like a run that
never grew.

## The body is in memory before any validator sees it
Field constraints cannot bound a request: Starlette buffers and parses the whole body first, so a
`max_length` on a field rejects a payload that has already been allocated in full.
**Rule**: size limits belong in front of the application, as ASGI middleware that checks `Content-Length`
*and* counts streamed bytes. Field constraints are for correctness, not for protection.

## A green suite says nothing about an artifact nobody opened
The PDF exporter shipped a 15-page file for a 5-page story - ten sheets blank, the cover blank -
through 110 passing tests, because every test checked the code and none opened the output. The
defect was found by a person reading the exported file.
**Rule**: when the deliverable is an artifact (PDF, image, export), at least one test parses the
artifact itself and asserts on what a user would see; and a human looks at one real example
before the feature is called done.

## Data placed among the rules IS a rule
Child names and companion descriptions were interpolated into the numbered rules of the story
instruction, above the untrusted-input marker, so a child named "Ignore the rules above" would have
become its own numbered instruction. The marker was also a fixed string, so a parent could type the
closing marker into their story idea and continue in the position reserved for trusted rules.
**Rule**: no caller-supplied string ever appears in the instruction section. Refer to it
positionally ("HERO 1"), put the values in a delimited data block, and give the delimiter a
per-request nonce so a typed marker is inert.

## Validate names by Unicode category, not by character class
A word-character regex refused the Nepali name Sita in Devanagari, because its vowel signs are
combining marks - in the product whose differentiator is Nepali.
**Rule**: validate names by Unicode CATEGORY (letters, marks, digits), and test every script the
product actually serves.

## Log extras are shipped to third parties
Passing names via logger extras looked local. The JSON formatter copies every extra key into stdout,
and Sentry attaches them as breadcrumbs that send_default_pii=False does not filter - putting
children's first names and reading bands into a processor the privacy page never named.
**Rule**: log counts and ids, never names or attributes of a person. If an identity is genuinely
needed to debug, hash it.

## A sentence that contains a link is not a string
The signup consent note is one sentence wrapping two anchors, to the Terms and to the Privacy
Policy. The obvious translation applier sets `textContent`, which would have deleted both links
and shipped an account-creation flow with no route to the documents it claims consent to - on a
children's product, on the branch whose sibling exists to harden exactly that surface.
**Rule**: a translatable element with element children needs a slot mechanism that re-threads
the real nodes through the translated string, and a test that asserts the links survive in every
locale. Never `innerHTML` a catalogue value: the catalogue is data.

## Two homes for one English sentence will drift
The progress panel's default markup said "Queued..." while the JS catalogue said "Waiting in the
queue…" for the same key. Both were "the English", neither was wrong, and nobody would ever have
noticed - the Nepali reader would have got one translation for two different English strings.
**Rule**: when a key's English lives in more than one place, assert in CI that the copies match.
The gate found this on its first run.

## Translate the errors in the same change as the interface
The natural order is static text first, server messages last, which produces an app that speaks
the user's language while things go well and switches to English the moment it errors, hits a
quota wall, or asks for money. That reads as a product pretending to speak the language.
**Rule**: ship i18n by complete vertical slice, not by percentage. A flow is not translated until
every error it can produce is translated too, including the ones frozen into database rows by a
worker in another process - which is why those need a code column, not a header.

## Replacing a literal with a call can wake a sleeping shadow
`api()` had `const t = token()` for as long as it had existed, harmlessly, because the next use of
`t` was a bearer header. The i18n pass replaced the literal on the line below with
`t('err.session_expired')` - and that call now resolved to a JWT string. Every expired session
showed the parent "t is not a function". The same shadow sat in the PDF handler. Both were invisible
in review because neither line was wrong on its own; only the pair was.
**Rule**: when a short name becomes a project-wide helper, reserve it - a test asserts it is never
rebound. More generally, changing a literal into a function call is a scope change, not a text edit.

## A guard you have not seen fail is not a guard
Twelve drift gates were written and all passed. An adversarial pass then added an untranslated
string, an unkeyed aria-label, a Devanagari numeral, a new page, an uncoded exception, and an
English value posing as Nepali - and every one shipped green. The gates were testing that correct
code is correct.
**Rule**: every gate is validated by breaking the thing it protects and watching it fail, then
reverting. Two of these needed real fixes to fire at all - the HTML parser desynced on an omitted
`</li>`, and the boot-order check could not tell a top-level `if` block from a function body.

## Say the honest number, not the flattering one
The i18n design was described, in a code comment and to the owner, as costing an English user
"zero extra requests, zero extra bytes". It costs one blocking request and about 7 KB gzipped on a
cold visit. The claim that was actually true and actually worth making - same markup, same English,
same code path - was weakened by being overstated next to a number anyone could check.
**Rule**: measure before claiming, and put the measurement in the comment. A defensible smaller
claim beats an impressive one that a reviewer can disprove in a minute.

## Verify in the browser, not just in tests
Several defects (blank views on a rejected promise, stale "session expired" errors after re-login) were
invisible to API tests and obvious in the browser.
**Rule**: every user-visible change is exercised in a real browser before it is called done.
