# HA theme — ROOT CAUSE (found & fixed 2026-09-25)

## The actual bug
HA 2026.9 has **no server-side "set theme" command**. Theme selection resolves
client-side:
  - `frontend/get_themes` returns the theme map + `default_theme`.
  - The frontend applies `default_theme` **per mode** (light/dark).
  - `frontend/set_theme`/`set_user_theme` no longer exist (only get_themes).

My theme's **`modes.light` block was light-coloured** (`#F8FAFC`), and the
browser/OS reported **light mode** (`darkMode: false`), so HA correctly applied
the *light* variant -> a white dashboard. The theme was never "not loading"; it
was loading the **light** variant.

## Fix
Set the KAI tokens in **both** `modes.dark` and `modes.light` to the KAI dark
palette (bg `#020617`, card `#0F172A`, accent `#16A34A`, text `#F8FAFC`). Now the
KAI look applies regardless of the client's light/dark preference.

## Verified live (Playwright, real login addoaryee)
- `get_themes` -> themes `['kai']`, default_theme `kai`, default_dark_theme `kai`
- resolved `--primary-color` `#16A34A` (KAI green)
- resolved `--primary-background-color` `#020617` (KAI dark)  <-- was `#F8FAFC`
- screenshot shows the dark dashboard with Mushroom cards + live energy values.

## Dashboard
KAI Command Deck (`/kai`), 4 views (Overview / Switches / eWeLink / Media &
Health), 21 entity refs verified present, Mushroom cards, theme pinned `kai`.
