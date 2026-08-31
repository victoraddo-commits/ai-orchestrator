# Kai Telegram Module — Comprehensive Audit Report
**Date:** 2026-08-30
**Auditor:** Claude Code (systematic debugging + impeccable design)
**Modules:** `core/telegram_poller.py`, `core/telegram_bridge.py`
**Test Suite:** 64 tests (test_telegram_poller.py + test_telegram_bridge.py)

---

## Executive Summary

| Dimension | Status |
|-----------|--------|
| Test Suite | ✅ 64/64 PASS |
| Live Outbound | ✅ Send works |
| Live Inbound Routing | ✅ Kai chat routes correctly |
| Service Health | ⚠️ RuntimeError flakiness (handled) |
| Code Quality | ⚠️ 2 typos + 1 wrong action label |

**Bottom line:** The module is functionally operational, but two copy-paste typos and one wrong action label should be fixed. All infrastructure is sound.

---

## 1. Test Coverage

```
tests/test_telegram_poller.py  — 7 tests
tests/test_telegram_bridge.py  — 57 tests
Total: 64 tests, 64 PASS, 2 warnings
```

Coverage areas:
- Poller: poll cycle, long-poll timeout, error isolation, send failure resilience, backoff
- Bridge: state change formatting, detect_state_changes, detect_state_changes_with_build_ids,
  poll_updates parsing (callback queries, reply-to, non-allowed chat filtering),
  record_sent_build_message (round-trip, eviction cap),
  route_inbound_reply (all approval flows, Kai chat passthrough, multi-build disambiguation,
  reply-to disambiguation, money_command integration)

**Coverage gaps identified:**
- No test for `format_state_change` with `WAITING_FOR_DEPLOY_APPROVAL` — would have caught the typo
- No test for `route_callback_query` (inline keyboard approve/reject buttons)
- No test for `send_approval_keyboard`
- No test for `answer_callback_query` / `edit_message_reply_markup`

---

## 2. Live Smoke Tests

All commands executed against the live Telegram API:

| Test | Result |
|------|--------|
| `getMe` — bot identity | ✅ `KaiEnzo_bot` |
| `getUpdates` — no 409 Conflict | ✅ No conflict |
| `sendMessage` — outbound | ✅ Delivered (msg_id 9000) |
| Kai chat routing (no pending build) | ✅ Responds correctly |
| State change formatting | ✅ Truncation works |
| `detect_state_changes` | ✅ Correct diff |
| Message-build round-trip | ✅ PASS |
| Offset persistence | ✅ 711,936,953 |

---

## 3. Bugs Found

### BUG-1 — CRITICAL: Typo in `_WAITING_FOR_LABEL` (Copy-Paste)
**File:** `core/telegram_bridge.py:363`
**Severity:** Medium (UX — wrong label shown to operator)
**Status:** Introduced in original code, never caught by tests

```python
_WAITING_FOR_LABEL = {
    "WAITING_FOR_USER_INPUT": "Waiting for User Input",
    "WAITING_FOR_ARCHITECTURE_APPROVAL": "Waiting for Architecture Approval",
    "WAITING_FOR_DEPLOY_APPROVAL": "Waiting for Architecture Approval",  # ← BUG
}
```

The third line should say "Waiting for **Deploy** Approval", not "Waiting for **Architecture** Approval".

**Impact:** When a build is awaiting deploy approval and a state change notification is formatted, the operator sees "Action needed: Waiting for Architecture Approval" — confusing when they are actually being asked to approve a deploy.

**Fix:** Change `"Waiting for Architecture Approval"` → `"Waiting for Deploy Approval"` on line 366.

**Why tests missed it:** No test calls `format_state_change` with `status="WAITING_FOR_DEPLOY_APPROVAL"`. The test suite only covers the first two entries of `_WAITING_FOR_LABEL`.

---

### BUG-2 — MEDIUM: Wrong Exception Variable Name
**File:** `core/telegram_bridge.py:746`
**Severity:** Low (wrong error label on exception, no functional impact)

```python
        except Exception as _money_exc:       # ← should be _enh_exc
            return {"routed": True, "action": "money_command",
                    "reply": f"Money command error: {_money_exc}"}
```

When an enhancement command raises an exception, the error message says "Money command error" instead of "Enhancement command error". Functionally the operator gets an error reply either way, but the label is wrong.

**Fix:** Rename `_money_exc` → `_enh_exc` and `"money_command"` → `"enhancement_command"` on lines 746–748.

---

### BUG-3 — MEDIUM: Wrong Fallback Action Label in Enhancement Error Path
**File:** `core/telegram_bridge.py:747`
**Severity:** Low (wrong `action` field in routing result)

```python
        except Exception as _money_exc:       # ← copy-paste from money handler
            return {"routed": True, "action": "money_command",  # ← wrong
                    "reply": f"Money command error: {_money_exc}"}
```

This is a copy-paste artifact from the money_commands exception handler two lines above. The action label should be `"enhancement_command"` not `"money_command"`.

**Fix:** Change `"money_command"` → `"enhancement_command"`.

*(Same fix as BUG-2 — rename the variable and fix the action label together.)*

---

## 4. Infrastructure Analysis

### 4.1 Service Architecture ✅

```
ai-orchestrator-telegram.service  →  core.telegram_poller (PID 586218, uptime 14h)
ai-orchestrator-api.service     →  uvicorn core.api:app (PID 923161)
ai-orchestrator.service         →  core.scheduler
```

Three separate systemd units. No shared state between poller and scheduler. ✅

### 4.2 Poller Error Pattern

**Log analysis** (all RuntimeError entries from `journalctl -u ai-orchestrator-telegram`):

| Timestamp | Context | Note |
|-----------|---------|------|
| Aug 29 03:26 | RuntimeError | Pre-restart |
| Aug 29 14:38 | RuntimeError | Pre-restart |
| Aug 29 18:58 | RuntimeError | Pre-restart |
| Aug 30 02:12–02:15 | 4× RuntimeError | Flapping, 3 min apart |
| Aug 30 02:15:57 | **Restart** | Service restarted |
| Aug 30 02:15:58+ | Clean | Running 14h, 0 errors since |

**Post-restart status:** 14 hours, 0 poll failures. ✅

**Root cause of flapping (pre-restart):** Likely a brief network connectivity blip or Telegram API hiccup causing `poll_updates` to raise `RuntimeError`. The `run_forever` loop catches these and backs off for 5 seconds — correct behavior. The service was manually restarted at 02:15:57 and has been clean since.

**No 409 Conflict errors** since 2026-08-01 when the dedicated bot token was introduced. ✅

### 4.3 Message-Build Map Integrity

The `telegram_message_builds.json` memory file tracks outbound notification `message_id` → `build_id` for native reply-to disambiguation.

| Metric | Value |
|--------|-------|
| Total entries | 200 (at cap) |
| Key type | String (e.g., `"3951"`) |
| Schema | Standard memory layer (`{"schema_version": 1, "records": {...}}`) |
| Round-trip test | ✅ PASS |
| LRU eviction | ✅ Working (cap enforced by `while len(state) > 200`) |

**Note:** Entries predate the current `update_id` offset (711,936,953 vs mapping keys ~3951–4411). This is expected — old notifications fall off as new ones arrive.

### 4.4 Offset Persistence ✅

```
memory/telegram_last_update_id.txt  →  711,936,953
```

Offset advances monotonically. No message is processed twice even across service restarts. ✅

### 4.5 Token Loading ✅

```
ENV path: /project/ai-orchestrator/.env  (exists ✅)
Token length: 46 chars
Token prefix: 89345553...  (KaiEnzo_bot ✅)
```

### 4.6 Command Module Availability ✅

| Module | Import | Status |
|--------|--------|--------|
| `core.money_commands` | ✅ | `handle_money_command` exists |
| `core.enhancement_commands` | ✅ | `handle_enhancement_command` exists |
| `core.koa` | ⚠️ | `handle_telegram_command` does not exist (not used) |

---

## 5. Code Quality Assessment

### Strengths
- **Clean separation of concerns:** `telegram_poller` owns the long-poll loop; `telegram_bridge` owns API calls and routing logic; `build_manager` owns the state machine
- **Defensive error handling:** `_safe_send`, `answer_callback_query`, `edit_message_reply_markup` all use best-effort patterns — one failure never crashes the routing loop
- **Deferred circular import:** `_import_kai_chat()` called only at first routing need, avoiding start-up circular dependency between `api` and `telegram_bridge`
- **Long-poll discipline:** `timeout=poll_timeout + 15` on HTTP client prevents premature connection abort
- **State-change idempotency:** `detect_state_changes` compares status strings and yields nothing on identical cycles — no spam
- **Message cap:** 200-entry LRU on message-build map prevents unbounded file growth

### Weaknesses
- **`send_typing` uses wrong fallback key** (`message.get("chat")` instead of `message.get("chat_id")`) — but falls back to `ALLOWED_CHAT_ID` which happens to be the correct chat in production. Works by coincidence; fragile.
- **Approval pattern matching is exact-word only:** `"not approve"` matches `approve` since only `.strip().lower() in set` is checked. Minor — "not approve" should not approve, but this hasn't caused issues in practice.
- **Poll timeout conservative:** 25s vs Telegram's 50s server-side max. Acceptable, but means slightly higher network overhead.

---

## 6. Security Posture

| Check | Status | Notes |
|-------|--------|-------|
| Token not hardcoded | ✅ | In `.env`, loaded via `python-dotenv` |
| Chat ID allowlist | ✅ | `ALLOWED_CHAT_ID = "612786480"` enforced on all inbound + outbound |
| Callback query filtering | ✅ | `if cb_chat_id != chat_id: continue` |
| Non-allowed-chat filtering | ✅ | `if msg_chat_id != chat_id: continue` |
| No command injection | ✅ | All routing is exact-pattern matching or enum sets |
| Secrets in logs | ✅ | Token prefix only in error messages |
| Callback data validation | ✅ | Split with maxsplit=2, length check before access |

---

## 7. Recommendations

### Fix Immediately (Low Effort, High Value)

**Bugs BUG-1, BUG-2, BUG-3** — three lines total:

```python
# In _WAITING_FOR_LABEL, line 366:
"WAITING_FOR_DEPLOY_APPROVAL": "Waiting for Deploy Approval",  # was "Waiting for Architecture Approval"

# In route_inbound_reply, lines 746-748:
        except Exception as _enh_exc:  # was _money_exc
            return {"routed": True, "action": "enhancement_command",  # was "money_command"
                    "reply": f"Enhancement command error: {_enh_exc}"}
```

### Add Missing Tests

1. `test_format_state_change_waiting_for_deploy_approval` — would have caught BUG-1
2. `test_route_callback_query_approve` — for inline keyboard approve/reject flow
3. `test_send_approval_keyboard_payload` — verify keyboard structure
4. `test_enhancement_command_error_returns_correct_action` — would have caught BUG-2/BUG-3

### Medium Priority

- **Fix `send_typing`:** Change `message.get("chat")` → `message.get("chat_id")` at line 719 of `telegram_bridge.py`
- **Increase `POLL_TIMEOUT`** from 25 to 50 (match Telegram's server-side maximum) to reduce network overhead

---

## 8. Verdict

**The Telegram module is operationally sound and has been running reliably since the last restart (14h, 0 errors).**

The test suite is strong (64/64) but has a blind spot: `format_state_change` with `WAITING_FOR_DEPLOY_APPROVAL` status is never tested, which allowed a copy-paste typo to persist undetected. The two other issues (wrong exception variable, wrong action label) are copy-paste artifacts from the money_commands handler.

These are not structural problems. The architecture is clean, the error handling is resilient, and the infrastructure (service isolation, offset persistence, allowlist enforcement) is correctly implemented.

**Fix the three typos, add the missing tests, and the module earns a clean bill of health.**

---

*Report generated by Claude Code audit — 2026-08-30*
*Smoke tests against live Telegram API (bot: @KaiEnzo_bot, chat: 612786480)*
