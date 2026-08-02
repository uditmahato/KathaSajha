# Data model

All ids are 32-char hex UUIDs (`uuid4().hex`). All timestamps are timezone-aware UTC in Postgres;
SQLite returns naive datetimes, so code that compares them normalizes with `replace(tzinfo=utc)`.

## users
| Column | Type | Notes |
|---|---|---|
| id | str(32) PK | |
| email | str(255) unique, indexed | stored lowercased and stripped |
| password_hash | str(255) | bcrypt |
| display_name | str(100) | may be empty |
| plan | str(20) | `free` \| `plus`. Drives `PLAN_DAILY_LIMITS` |
| created_at | timestamptz | |

## stories
| Column | Type | Notes |
|---|---|---|
| id | str(32) PK | |
| user_id | FK users.id, indexed, ON DELETE CASCADE | |
| prompt | text | the user's raw idea, never mutated |
| hero_name | str(60) | optional personalization, passed to the provider separately |
| language | str(10) | `en` \| `ne` |
| title | str(300) | filled by generation |
| status | str(20), indexed | `pending` -> `generating` -> `complete` \| `failed` |
| error | text | user-facing message only, never raw exceptions |
| share_slug | str(32) unique nullable, indexed | 48 bits of entropy, null when unshared |
| provider | str(20) | which provider produced it, for debugging and cost attribution |
| created_at | timestamptz, indexed | drives the daily quota query and library ordering |

## story_pages
| Column | Type | Notes |
|---|---|---|
| id | str(32) PK | |
| story_id | FK stories.id, indexed, ON DELETE CASCADE | |
| position | int | 0-based. UNIQUE(story_id, position) so queue retries cannot duplicate pages |
| text | text | one paragraph, one scene |
| image_prompt | text | what was sent to the image model |
| image_url | str(500) | empty string means no image for this page |
| image_error | text | generic user-facing text only; never exposed on public share pages |

## generation_jobs
| Column | Type | Notes |
|---|---|---|
| id | str(32) PK | |
| story_id | FK stories.id unique, indexed, ON DELETE CASCADE | one job per story |
| status | str(20), indexed | `queued` -> `running` -> `complete` \| `failed` |
| stage | str(50) | `queued`, `writing_story`, `illustrating`, `done`, `failed`. Drives the progress UI copy |
| progress_current / progress_total | int | illustrations finished / total |
| error | text | user-facing |
| created_at / updated_at | timestamptz | `updated_at` is the heartbeat used for stale-job failover (15 min) |

## Query patterns that justify the indexes

- Library list: `WHERE user_id = ? ORDER BY created_at DESC LIMIT ?` (user_id + created_at)
- Daily quota: `COUNT(*) WHERE user_id = ? AND created_at >= ? AND status != 'failed'`
- Share lookup: `WHERE share_slug = ? AND status = 'complete'` (unique index on share_slug)
- Job poll: PK lookup joined to stories for ownership

## Migration policy

Today: `Base.metadata.create_all` under a Postgres advisory lock so the API replicas and the worker
cannot race on first boot. This is acceptable ONLY because there is no production data yet.

Before the first real user: introduce Alembic, baseline the current schema, and never edit a model
without a migration. See TECH_DEBT.
