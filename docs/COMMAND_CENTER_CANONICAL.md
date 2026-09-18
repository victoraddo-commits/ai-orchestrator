# Canonical Command Center — Decision & Retirement of the React SPA

**Date:** 2026-09-18 · **Phase:** KAI 2.0 Phase 2 · **Owner:** Kai / operator

## Decision

There is **one canonical Command Center**:

| | Canonical | Retired |
|---|---|---|
| App | FastAPI Command Center (`core/kai/command_center.html`) | React SPA (`/project/src/kai-command-center`) |
| Host | LXC 111 `/opt/ai-orchestrator`, `ai-orchestrator-api.service` (:8000) | PVE-A CT100 `claude-code`, `kai-command-center.service` (vite) + nginx :80 |
| URL | `https://orchestrator.tail82a9ca.ts.net/command-center` | `https://command.tail82a9ca.ts.net` → **301 redirect** |
| Panels | 40 wired panels + runtime per-model pages | — |

The React SPA is **retired (option b)** with a **301 redirect (option a)** layered
on top, so no user is presented with a second Command Center:

1. Tailnet service `svc:command` proxies to the CT100 nginx (`http://192.168.99.11:80`).
2. CT100 nginx now answers `command.tail82a9ca.ts.net`, `command.kai`, and
   `command.deerude.com` with `301 → https://orchestrator.tail82a9ca.ts.net/command-center`.
3. `kai-command-center.service` (the SPA vite server) is **stopped and disabled**.
4. The SPA source **is preserved** at `/project/src/kai-command-center` (not deleted).

## Evidence

```
$ systemctl is-active kai-command-center.service   # CT100
inactive
$ systemctl is-enabled kai-command-center.service
disabled

$ curl -sk -o /dev/null -w '%{http_code} %{redirect_url}\n' \
    https://command.tail82a9ca.ts.net/command-center
301 https://orchestrator.tail82a9ca.ts.net/command-center

$ curl -skL https://command.tail82a9ca.ts.net/command-center | grep -c apiT
7                       # canonical FastAPI CC markers
$ curl -skL https://command.tail82a9ca.ts.net/command-center | grep -c 'assets/index-'
0                       # React SPA bundle no longer served

$ curl -sk -o /dev/null -w '%{http_code}\n' \
    https://orchestrator.tail82a9ca.ts.net/command-center
200
```

Config touched on PVE-A CT100 (`claude-code`):
- `/etc/nginx/sites-available/kai` — `location /` of the command server block
  replaced with a 301 (backup: `/etc/nginx/sites-enabled/kai.bak-phase2-*`,
  moved to `/root/` so nginx does not load it).
- `/etc/nginx/sites-available/kai-command-canonical` — new server block for
  `command.tail82a9ca.ts.net` returning the 301.

Tailnet: `tailscale serve --service=svc:command --https=443 http://192.168.99.11:80`
on `proxmox-a` (100.83.4.27). Config backup:
`/root/tailscale-serve-backup-*.json` on proxmox-a.

## Reverting

```sh
# On CT100
systemctl enable --now kai-command-center.service
cp /root/kai.bak-phase2-* /etc/nginx/sites-enabled/kai   # restore site
rm -f /etc/nginx/sites-enabled/kai-command-canonical
nginx -t && systemctl reload nginx
```

## Wiring rule

All new surfaces are built in the canonical FastAPI CC
(`core/kai/command_center.html`) and follow the pattern in
`docs/COMMAND_CENTER_COVERAGE.md` (sidebar `nav-item`, `panel-<x>`,
`PANEL_TITLES`, dispatcher, `load<X>()`). The React SPA is not a target.
