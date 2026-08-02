# UI and UX patterns

The product must feel like a warm printed storybook, not a developer tool. Parents are the users;
children are the audience. Calm, generous spacing, no jargon.

## Design tokens (`frontend/styles.css`)

| Token | Value | Use |
|---|---|---|
| `--parchment` | `#f5f0e6` | page background |
| `--sepia` | `#704214` | primary text, primary button fill, header/footer |
| `--sepia-soft` | `rgba(112,66,20,0.7)` | secondary text |
| `--oldgold` | `#d4af37` | accents, focus ring, progress fill, guest CTA |
| `--danger` | `#b3362b` | errors and destructive actions |
| `--card-bg` | `#ffffff` | cards |
| `--shadow-page` / `--shadow-book` | soft brown shadows | resting / raised elevation |

Type: Playfair Display for headings and titles, Merriweather for body. Serif throughout; it reads as
"book", which is the entire brand promise.

## View model

Single page, five views, exactly one visible at a time via `show(view)`:
`landingView` (logged out), `authView`, `createView` (create form + library), `storyView` (reader, own
or shared). `show()` also swaps the header nav between guest and user states.

## Rules that must hold for every screen

1. **Three states minimum**: loading, empty, error. An empty library says what to do next; a failed
   story says what happened in plain language and offers a way forward.
2. **Never surface internal errors.** Users see friendly sentences. Raw exceptions go to the log only.
3. **Text into the DOM with `textContent`**, never `innerHTML`. Model output is untrusted content.
4. **Every async action disables its trigger** and shows progress, so nothing can be double-fired.
5. **Progress must be honest**: the generation panel reports the real stage and the real
   illustration count from the job row, not a fake timer.
6. **Destructive actions confirm** and say exactly what will be lost.
7. **Mobile first ergonomics**: single column under 40rem, controls reachable one-handed, targets at
   least 44px tall.
8. **Nepali is first class**: Devanagari must render at a comfortable size and never be clipped.

## Copy voice

Warm, plain, and short. Speak to a parent, not an engineer. "Painting the illustrations" beats
"Processing image generation queue". Never use em dashes or en dashes in user-facing copy; use commas,
colons, or separate sentences.

## Accessibility baseline (WCAG 2.1 AA)

- Semantic landmarks (`header`, `main`, `footer`, `nav`) and correct heading order.
- Focus must move to the newly shown view on navigation, and focus styles must be visible.
- The generation progress region must be a live region so screen readers announce stage changes.
- Illustration alt text must describe the scene, not repeat "image".
- Respect `prefers-reduced-motion`.
- Nepali content must carry `lang="ne"`.
