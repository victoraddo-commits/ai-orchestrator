# Kai Service Naming + Directory — Design

Date: 2026-09-17
Status: Approved (design), pending spec review
Repo: ai-orchestrator (CT111 `/opt/ai-orchestrator`)

## 1. Problem

Every Kai app/module is addressed by raw IP:port (`192.168.1.111:8099`,
`192.168.1.118:8095`, …). There is no single place that lists what exists,
where it runs, or how to reach it. Knowledge is scattered across configs,
`.env` files, cloudflared ingress maps, and tribal memory. New services are
added without registration, so the inventory drifts.

## 2. Goals

- Every app/module is reachable by a **stable private name**, not an IP.
- One **directory** lists *all* apps/modules with name, host, port, health.
- Names + directory are **tailnet-only** (Tailscale). No public exposure.
- **Nothing is missed**: a conformance check proves the directory covers every
  discovered service.

### Non-goals

- No public/internet domains (explicitly out of scope).
- No secrets stored in the directory (names, hosts, ports, health only).
- No change to how services authenticate (Duo/SSO/tokens stay as-is).

## 3. Architecture

Two layers.

### Layer 1 — Naming (Tailscale Services)

- Tailnet policy gains a `services:` block defining one service per app
  (`svc:money`, `svc:bet`, `svc:directives`, `svc:command`, `svc:legal`, …),
  each `tcp:443`.
- `tailscale serve --bg --service=svc:<name> http://<target>` on a **proxy
  node** advertises and routes. MagicDNS yields `https://<name>.tail82a9ca.ts.net/`.
- Proxy nodes: **PVE-B (proxmox-b)** fronts PVE-B-hosted services; **PVE-A
  (proxmox-a)** fronts PVE-A-hosted services. Each reaches its own LAN.
- TLS is terminated by `tailscale serve` using the ts.net certificate — trusted
  on every tailnet device, zero client config.

**Fallback** (if Services cannot be enabled): Serve path-routing on a single
node, `https://proxmox-b.tail82a9ca.ts.net/<name>/`. The directory is
scheme-agnostic — only the `internal_url`/`tailnet_name` fields change.

### Layer 2 — Directory service (new standalone app)

- **Host**: new LXC `kai-directory` on PVE-B (2 vCPU / 1 GB RAM / 8 GB disk),
  joined to the tailnet as its own node.
- **Stack**: Python 3 + FastAPI + SQLite; systemd unit `kai-directory.service`;
  TDD; no external LLM.

## 4. Components

| Component | Responsibility | Interface |
|---|---|---|
| `discovery` | Enumerate services from all sources, normalise into records | pure functions + source adapters |
| `store` | Persist records (SQLite), upsert/reconcile, keep manual overrides | `RecordStore` |
| `namer` | Assign/validate `tailnet_name`; generate policy + serve config | `assign_names()` |
| `health` | Periodic health-check of each `health_url` | `check_all()` |
| `api` | REST surface | FastAPI `/services`, `/health`, `/conformance`, `/export` |
| `index` | Generated browseable HTML index | `GET /` |
| `conformance` | Prove every discovered service is registered | `GET /conformance` |

### Data model (one row per app/module)

```
id            TEXT PK   # slug, e.g. "money-center"
name          TEXT      # canonical name, e.g. "money"
display_name  TEXT
category      TEXT      # finances|legal|betting|infra|ai|docs|...
host          TEXT      # "ct108" / "pve-b" / "ct100-a"
container     TEXT      # "108" or "docker:money-center"
ip            TEXT
port          INTEGER
bind          TEXT      # 0.0.0.0 | 127.0.0.1 | *
target_url    TEXT      # http://192.168.1.118:8095
tailnet_name  TEXT      # "money.tail82a9ca.ts.net"
internal_url  TEXT      # https://money.tail82a9ca.ts.net/
proxy_node    TEXT      # "proxmox-b"
health_url    TEXT
health_status TEXT      # up|down|unknown
owner         TEXT
tags          TEXT      # csv
source        TEXT      # registry|docker|ss|cloudflared|manual
updated_at    TEXT
```

## 5. Discovery sources (per AGENTS rule: all apps/modules must be present)

1. `memory/kai_services.json` + `core/service_registry.py` (55 records).
2. `ss -ltnp` on every container (0.0.0.0 listeners = candidate services).
3. `docker ps` on Docker hosts (CT108, CT113, CT100-A, CT105, CT107).
4. cloudflared ingress configs (existing named URLs).
5. CC panel list (`command_center.html`) — modules that are UI-only.
6. Manual entries (anything not auto-discoverable).

**Conformance rule**: the set of discovered services (after normalisation and
an explicit allow/deny list for noise like sshd/postfix) must be a subset of
the directory. Any discovered service without a directory row is reported by
`GET /conformance` as a failure.

## 6. Naming rules

- name = short lowercase slug; `tailnet_name = <name>.tail82a9ca.ts.net`.
- Collisions are resolved with a `-2` suffix and flagged for manual review.
- A name is only advertised once its `target_url` passes health.

## 7. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/services` | list (filter: category, status, q) |
| GET | `/services/{id}` | one record |
| POST/PUT/DELETE | `/services/{id}` | manual register/update/remove |
| POST | `/discover` | re-run discovery + reconcile |
| POST | `/health/refresh` | re-check all health URLs |
| GET | `/health` | service self-health |
| GET | `/conformance` | coverage report (pass/fail + missing list) |
| GET | `/export?format=json\|yaml` | dump the catalog |
| GET | `/policy` | generated tailnet `services:` policy block |
| GET | `/` | generated HTML index |

## 8. UI

- **Index page** at the directory's own tailnet name: searchable, category
  filter, status badges, copy-URL, click-through. Server-rendered (no build).
- **Command Center → `directory` panel**: fetches `/services` via a server-side
  proxy in `cc_extra_routes.py` (token never reaches the browser), same data.
  Wired per the Kai design-system rule (nav entry, `panel-directory`,
  `PANEL_TITLES`, dispatcher).

## 9. Error handling

- Discovery source failure → keep last-known records, mark `source_stale`,
  never wipe the catalog.
- Health failure → `health_status=down`, keep the name (names are stable).
- Conformance failure → surfaced in CC + Telegram alert; build does not silently pass.
- No secrets in the store; `/export` excludes nothing sensitive because none is stored.

## 10. Testing

- **Unit**: discovery parsers (ss/docker/cloudflared/registry fixtures), name
  assignment + collision, conformance diff, health classification.
- **Integration**: API CRUD + discover + conformance against a seeded SQLite.
- **Verification**: directory count == discovered count; every `tailnet_name`
  resolves and returns 200 from a second tailnet node.

## 11. Rollout

1. Create `kai-directory` LXC on PVE-B; install app; run discovery → ~60 records.
2. Generate the tailnet `services:` policy block; operator pastes it in admin console.
3. `tailscale serve --service=...` each healthy name on the correct proxy node.
4. Wire CC `directory` panel + index; run `/conformance` until green.

## 12. Prerequisites (operator)

- One-time **Tailscale Serve** enable toggle.
- Paste the generated **`services:` policy block** into the tailnet admin console.
- Approval to create the `kai-directory` LXC on PVE-B.

## 13. Open questions

- Exact service set to expose as named services vs. directory-only (e.g. do we
  name PVE host APIs on :8006, or only app services?). Default: app services
  only; infra APIs listed in the directory but not named.
- Whether to also enumerate modules of the two orchestrator codebases
  (PVE-A `/project/ai-orchestrator` vs PVE-B `/opt/ai-orchestrator`) separately.
  Default: yes, tagged by host.
