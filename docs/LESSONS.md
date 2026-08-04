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

## Verify in the browser, not just in tests
Several defects (blank views on a rejected promise, stale "session expired" errors after re-login) were
invisible to API tests and obvious in the browser.
**Rule**: every user-visible change is exercised in a real browser before it is called done.
