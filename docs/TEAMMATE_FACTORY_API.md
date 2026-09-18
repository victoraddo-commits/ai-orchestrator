# Teammate Factory API (KAI 2.0 Phase 1)

Runtime wiring for the Autonomous Teammate Factory. The library in
`core/teammate/` is now instantiated by
[`core/teammate/runtime.py`](../core/teammate/runtime.py), exposed over HTTP by
[`core/teammate/routes.py`](../core/teammate/routes.py) (mounted in
`core/api.py`), and driven by the operator/Telegram commands in
`core/team_commands.py`.

Object graph (all reused, no duplicates):

```
Registry ── Skills ── WorkerIntegrator ── Factory
                              │
                   ExecutionGuard (AgentGuard + vault scope)
                              │
                   ToolFabric (tool scope + sandbox)
                              │
    TieredDispatcher ─── TeammateRuntime.task_runner ── ai_router.delegate
                              │
                       gpu_arbiter (T0 permits)
```

Every model prompt goes through `core.ai.ai_router.delegate`; every skill
execution goes through `ExecutionGuard` (AgentGuard); every state change is
persisted via `core.memory`.

## Authentication

The API uses the existing bridge-token / session gate (`core/bridge_auth.py`).
Send the bridge token as a Bearer header:

```
Authorization: Bearer <token>
```

The token is the file `~/.ai-orchestrator/api_token` on the API host
(LXC 111 `/opt/ai-orchestrator`, service `ai-orchestrator-api`). A Command
Center session with the `delegate.use` capability also works:

```
X-Kai-Session: <session-token>
```

Missing / bad credentials → `401 Missing or invalid credentials`.

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/workforce/teammates` | `{"role":"coder","skills":[...],"model":"..."}` | `{"teammate":{... "status":"READY"}}` |
| GET | `/api/workforce/teammates` | `?status=&specialization=` | `{"teammates":[...]}` |
| GET | `/api/workforce/teammates/{id}` | — | `{"teammate":{...}}` |
| POST | `/api/workforce/teams` | `{"requirement":"Build a small feature"}` (or `{"mission":...}`) | `{"team":{...}}` |
| GET | `/api/workforce/teams` | — | `{"teams":[...]}` |
| GET | `/api/workforce/teams/{id}` | — | `{"team":{...}}` |
| POST | `/api/missions` | `{"goal":"...","execute":true,"background":false}` | `{"mission":{task graph}}` |
| GET | `/api/missions` | `?status=` | `{"missions":[...]}` |
| GET | `/api/missions/{id}` | — | `{"mission":{task graph + status + verification}}` |

Engineering goals (`build`/`implement`/`feature`/`code`/`fix`/`refactor`/…)
assemble the planner→coder→qa→reviewer team automatically. Teammates are
reused when a healthy, capable, available match already exists (directive §38).

## curl examples

```bash
TOKEN=$(cat ~/.ai-orchestrator/api_token)
BASE=https://127.0.0.1:8000

# §46 — create (or reuse) a researcher teammate
curl -sk -X POST "$BASE/api/workforce/teammates" \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"role":"researcher"}'

# list / detail
curl -sk "$BASE/api/workforce/teammates" -H "Authorization: Bearer $TOKEN"
curl -sk "$BASE/api/workforce/teammates/<id>" -H "Authorization: Bearer $TOKEN"

# §47 — form a team for a feature
curl -sk -X POST "$BASE/api/workforce/teams" \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"requirement":"Build a small software feature"}'

# run a mission (decompose → assign → execute → verify)
curl -sk -X POST "$BASE/api/missions" \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"goal":"Build a small software feature"}'

# inspect the mission task graph + verification
curl -sk "$BASE/api/missions/<mission_id>" -H "Authorization: Bearer $TOKEN"
```

## Operating from another host (OpenCode / LXC 113)

The API listens on `:8000` on LXC 111 and is firewalled from the LAN. From
OpenCode (LXC 113) reach it through the Proxmox host:

```bash
ssh -i /root/.ssh/pve2_deploy -N -L 18000:192.168.1.111:8000 root@192.168.1.110 &
export KAI_API_BASE=https://127.0.0.1:18000
export KAI_API_TOKEN=$(ssh -i /root/.ssh/pve2_deploy root@192.168.1.110 \
  'pct exec 111 -- cat /root/.ai-orchestrator/api_token')
scripts/kai-workforce.sh teammate-create researcher
```

Or run the wrapper on the API host itself.

## Operator / Telegram commands

`core/team_commands.py` now defaults to the live `WorkforceEngine`, so the
Telegram control-command path works without injection:

| Command | Effect |
|---|---|
| `/teams` | list teams |
| `/team <id>` | team members + status |
| `/create-team <role> [skill ...]` | create/reuse a teammate |
| `/mission <goal>` | form a team and run a mission (background) |
| `/assign <team_id> <goal>` | run a mission on an existing team |
| `/why-failed <mission_id>` | show failed tasks for a mission |

## Verification

- `tests/test_teammate_runtime.py` — 16 tests covering §46–§54 with a mocked
  model (real object graph, no network).
- `tests/test_teammate_e2e.py` — real model §46/§47, run with `KAI_E2E=1`.
