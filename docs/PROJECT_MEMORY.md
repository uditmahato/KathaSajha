# KathaSajha Project Memory

The durable memory of this project. Read this first before making any change.
Every iteration updates the relevant files here.

| Document | What it holds |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System shape, components, data flow, why it is built this way |
| [DECISIONS.md](DECISIONS.md) | Architecture Decision Records (ADRs). Append-only |
| [SCHEMA.md](SCHEMA.md) | Database model, indexes, migration policy |
| [API.md](API.md) | HTTP contract, status codes, error shapes |
| [UI_UX_PATTERNS.md](UI_UX_PATTERNS.md) | Design tokens, component patterns, interaction and accessibility rules |
| [ROADMAP.md](ROADMAP.md) | Product roadmap by horizon, tied to the business model |
| [TECH_DEBT.md](TECH_DEBT.md) | Known debt, bugs, risks. Ranked, with owner-facing impact |
| [LESSONS.md](LESSONS.md) | Lessons learned, including bugs that must never recur |
| [CHANGELOG.md](../CHANGELOG.md) | What shipped, per iteration |

## Product in one paragraph

KathaSajha (कथा साझा) turns a one-line idea into an illustrated children's story, in English or
Nepali, starring the child as the hero. Parents read it on any device, share a private link with
family, or download a print-ready PDF. The business is a subscription (with a free tier) plus
print-on-demand physical books, aimed first at Nepali and South Asian diaspora parents, who are
underserved by English-first generic story apps and who pay to keep language and culture alive
for their children.

## Non-negotiable principles

1. **The child is the customer's whole world.** Safety, warmth, and trust outrank cleverness.
2. **Never depend on a live API key to develop or test.** The mock provider must always run the
   full pipeline offline and free.
3. **Failure is soft where it can be.** A missing illustration degrades the page; it never loses
   the story.
4. **Cost per story is a product constraint**, not an afterthought. Every generation is real money.
5. **Nothing user-visible ships without loading, empty, and error states.**
6. **Nepali is a first-class language**, not a translation afterthought.
