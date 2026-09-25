# HA dashboard + theme — corrected (2026-09-25)

## What was wrong
1. **Theme didn't render** — the file used HA's **legacy flat schema**. HA 2026.9
   requires the **`modes.dark` / `modes.light`** schema. Rewritten correctly and
   both defaults set:
   `frontend_default_theme: kai`, `frontend_default_dark_theme: kai`.
2. **Dashboard was a flat list** — rebuilt as a **sections-based** layout with
   grouped "modules" (heading cards + Mushroom entity/media/chips cards).

## Current dashboard (verified)
- 4 views: **Overview · Tuya · eWeLink · Security & Media**
- **29 Mushroom cards** across sections
- **19 entity references, 0 missing** (checked against `/api/states`)
- dashboard `theme: kai` pinned

## Theme tokens (KAI design system)
dark: bg `#020617`, surface/card `#0F172A`, accent `#16A34A`,
text `#F8FAFC`, muted `#94A3B8`, border `#334155`.

## If it still looks unstyled in your browser
The server is correct; the remaining causes are client-side:
1. Hard refresh (Ctrl+Shift+R) — theme CSS is fetched per page load.
2. Profile → Appearance → **Theme = kai** (a per-user choice overrides the server default).
