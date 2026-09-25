# Home Assistant — dashboards & templates (2026-09-25)

## Installed
- **HACS 2.0.5** — `custom_components/hacs` (official release zip, includes the
  frontend bundle). Config entry `state: loaded`.
  Install note: `git clone` of HACS does NOT work (missing `hacs_frontend`
  module); use the **release zip**. The GitHub device-flow was completed with a
  **persistent systemd poller** (`scripts/gh_device_poller.py`) because HA's
  in-memory flow loses state on restart; the resulting token was seeded into
  `core.config_entries` (must use ISO-string `created_at`/`modified_at` and a
  `disabled_by` key — floats/missing keys crash HA 2026.9).
- **Mushroom v5.2.3** — `www/mushroom.js` (666 KB), registered as a Lovelace
  resource (`lovelace_resources`) and served at `/local/mushroom.js` (HTTP 200).
- **KAI Command Deck dashboard** — `lovelace.kai`, registered in
  `lovelace_dashboards` at `/kai`. Views: Overview, Power/UPS, Tuya, eWeLink,
  Cameras & Media.
- **KAI dark theme** — `themes/kai.yaml`, aligned with the Command Center tokens.

## Verified (HA API)
- version 2026.9.3; components loaded: **tuya, sonoff, hacs**
- entities: 61
- config entries: tuya/sonoff/androidtv_remote/hacs all `loaded`
- `/local/mushroom.js` → 200
- zero HA startup errors

## Note
`/api/lovelace/dashboards` is not exposed on this HA build (websocket-only);
dashboard registration was verified in `.storage/lovelace_dashboards`.

## Mushroom dashboard rebuild (2026-09-25)
The KAI Command Deck uses real **Mushroom cards** (`custom:mushroom-*`):
title cards, chips, entity cards, and a media-player card. 5 views
(Overview / Power·UPS / Tuya / eWeLink / Cameras·Media), **13 entity
references, 0 missing** (validated against `/api/states`).

Verified: `/local/mushroom.js` → 200 (666 KB bundle); HA 200; zero startup errors.
Note: the HA frontend cannot be screenshot from this harness because HA 2026.9
requires the interactive OAuth login (no raw-token localStorage); verification was
done via the HA API + storage, which is authoritative.
