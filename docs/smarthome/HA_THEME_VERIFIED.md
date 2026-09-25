# HA theme — server-side verification (complete)

## Server state (all verified)
- `themes/kai.yaml` — **`modes.dark` + `modes.light`** schema (HA 2026.9 requires
  this; the legacy flat schema is silently ignored). dark has 20 tokens.
- `.storage/frontend_theme` → `frontend_default_theme: kai`,
  `frontend_default_dark_theme: kai`.
- `frontend.set_theme name=kai mode=dark` → HTTP **200**.
- `frontend.reload_themes` → HTTP **200**.
- No per-user theme override exists (auth users: victor addo owner; no
  `frontend.user_data` entry) → the user inherits the server default.
- HA `/` → 200; no theme/Uncaught errors in the log.

## Therefore, if the browser still looks unstyled
It is 100% client-side. In order:
1. **Hard refresh** (Ctrl+Shift+R / Cmd+Shift+R).
2. **Profile → Appearance → Theme**: choose **`kai`** (or "Backend-selected").
   Any explicit per-user choice overrides the server default.
3. Confirm the tab isn't a stale cached session — open a private window.

There is nothing further to fix on the server.
