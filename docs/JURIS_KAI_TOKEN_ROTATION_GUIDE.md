# Telegram Bot Token Rotation Guide (Compromised Tokens)

**Date:** 2026-09-21
**Reason:** Real Telegram bot tokens were committed to this repository and
pushed to GitHub (`runner-kai-2.0-20260918`). GitHub secret scanning flagged
them; a foreign webhook (`tele.goldenherd.com`) was subsequently set on two of
the bots, flooding the pollers with HTTP 409 conflicts.
**Status:** Working tree redacted. **History still contains the tokens.**
**Owner action required: rotate all three tokens in BotFather.**

> Redacting a file does **not** un-leak a secret. The tokens remain in git
> history and in every clone. The only real remediation is **rotation**:
> once rotated, the exposed values are worthless.

---

## 1. Which tokens were exposed

| Bot | Username | Token variable | Exposed in (working tree) | Git commits |
|-----|----------|----------------|---------------------------|-------------|
| KaiEnzo | `@KaiEnzo_bot` | `KAI_TELEGRAM_BOT_TOKEN` | `docs/superpowers/specs/2026-09-09-kai-telegram-gateway.md:635`, `core/telegram_bridge.py:63` (docstring example) | `20b363b`, `5f83549`, `c394837`, `262f7f4` |
| Juris Kai | `@Juriskai_bot` | `JURIS_KAI_BOT_TOKEN` | `docs/JURIS_KAI_TOKEN_ROTATION_GUIDE.md:6` | `c394837`, `262f7f4` |
| Deerude/notify | `@DeerudeClaude_Bot` | `TELEGRAM_BOT_TOKEN` (Claude Code plugin) | `reports/telegram-bots-audit-2026-08-30.md:116` | `c394837`, `262f7f4` |

**Not exposed (do not rotate):** `BETSPORTZ_BOT_TOKEN` (`@Betsportz_bot`) —
never committed, and its poller showed zero conflicts.

The fake token in `tests/test_telegram_bridge.py` is intentionally allowlisted
by `scripts/check_no_secrets.py` and is **not** a leak.

---

## 2. Rotate in BotFather (OWNER — cannot be automated)

Repeat for each of the three bots:

1. Open Telegram and message **@BotFather**.
2. Send `/mybots`.
3. Select the bot (`@KaiEnzo_bot`, `@Juriskai_bot`, `@DeerudeClaude_Bot`).
4. **API Token** → **Revoke current token** → confirm.
5. Copy the new token (format `1234567890:ABCdef...`). The old token dies
   immediately.

---

## 3. Update the environment files

Use a **local** editor / `sed`; never paste the token into a shell history or
a chat. Confirm each file is `chmod 600` afterwards.

| Bot | Variable | File |
|-----|----------|------|
| KaiEnzo | `KAI_TELEGRAM_BOT_TOKEN` | `/opt/ai-orchestrator/.env` |
| Juris Kai | `JURIS_KAI_BOT_TOKEN` | `/opt/ai-orchestrator/.env` |
| Deerude/notify | `TELEGRAM_BOT_TOKEN` | wherever the Claude Code Telegram plugin loads it (`~/.claude/channels/telegram/.env`) |
| (reference) | `BETSPORTZ_BOT_TOKEN` | `/etc/kai/kai_betting.env` — not exposed |

On LXC 111 (via Proxmox B):

```bash
# Backup first, then edit (token never echoed):
pct exec 111 -- cp /opt/ai-orchestrator/.env /opt/ai-orchestrator/.env.bak-rotate-$(date +%s)
pct exec 111 -- nano /opt/ai-orchestrator/.env
# set KAI_TELEGRAM_BOT_TOKEN=<new> and JURIS_KAI_BOT_TOKEN=<new>

pct exec 111 -- chmod 600 /opt/ai-orchestrator/.env
```

Also update any other host that stores these tokens (the master
`/project/ai-orchestrator/.env`, `/etc/kai/*.env`) — grep for the **variable
name**, never paste the value:

```bash
grep -rn "KAI_TELEGRAM_BOT_TOKEN\|JURIS_KAI_BOT_TOKEN" /opt /etc/kai /project 2>/dev/null | sed 's/=.*/=<redacted>/'
```

---

## 4. Clear any webhook and restart the pollers

The pollers now call `deleteWebhook` at startup and self-heal on a webhook
409, but clear it explicitly once:

```bash
pct exec 111 -- systemctl restart ai-orchestrator-telegram juris-kai kai-betting-telegram

# Verify no webhook (prints the URL, not the token):
pct exec 111 -- /opt/ai-orchestrator/.venv/bin/python - <<'PY'
import os, json, urllib.request
for var in ("KAI_TELEGRAM_BOT_TOKEN", "JURIS_KAI_BOT_TOKEN", "BETSPORTZ_BOT_TOKEN"):
    tok = os.environ.get(var, "")
    if not tok:
        print(var, "not set"); continue
    with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getWebhookInfo") as r:
        print(var, json.load(r)["result"]["url"])
PY
```

Env vars are loaded from the unit's `EnvironmentFile`, so set them in the
shell or add `-E` as appropriate before running the snippet.

Expected: `url` is `""` for all three.

---

## 5. Verify the new tokens work

```bash
# Should return {"ok":true,...,"username":"<bot>"} — token never printed:
pct exec 111 -- journalctl -u juris-kai --since '2 minutes ago' | sed -n '1,5p'
pct exec 111 -- journalctl -u ai-orchestrator-telegram --since '2 minutes ago' | sed -n '1,5p'
```

Then confirm **no** webhook-conflict lines (see step 6).

---

## 6. Confirm the error flood stopped

```bash
pct exec 111 -- sh -c 'journalctl -u ai-orchestrator-telegram --since "10 min ago" | grep -ci "webhook is active\|Conflict"'
pct exec 111 -- sh -c 'journalctl -u juris-kai --since "10 min ago" | grep -ci "webhook is active\|409"'
```

Expected: `0` for both.

---

## 7. (Optional, destructive) Purge the tokens from git history

**Do not run this without owner sign-off and a coordinated force-push across
all clones.** Rotation already neutralizes the leaked values; history rewrite
is defense-in-depth only.

```bash
# Requires git-filter-repo. Work on a fresh mirror clone, never the runner.
git clone --mirror https://github.com/victoraddo-commits/ai-orchestrator.git
cd ai-orchestrator.git
git filter-repo --replace-text <(printf '%s\n' 'regex:\d{8,10}:[A-Za-z0-9_-]{33,}==>TELEGRAM_BOT_TOKEN_REDACTED')
# inspect, then force-push and notify every collaborator to re-clone
```

This was **not executed**. No history was rewritten in this change.

---

## 8. Prevent recurrence

- `scripts/check_no_secrets.py` scans git-tracked files for Telegram tokens
  and provider keys; findings are always masked.
- Wired into: `tests/test_no_secrets.py` (pytest / deploy gate),
  `.pre-commit-config.yaml` (run `pre-commit install` once), and
  `.github/workflows/secret-scan.yml` (CI).
- `.gitignore` now excludes `.env*`, `*.token`, `*.secret`, `*_secrets.json`,
  `*_credentials.json`, etc.

---

## Post-Rotation Checklist

- [ ] KaiEnzo token revoked + replaced in `/opt/ai-orchestrator/.env`
- [ ] Juris Kai token revoked + replaced in `/opt/ai-orchestrator/.env`
- [ ] Deerude/notify token revoked + replaced in its plugin env
- [ ] Any secondary host/`/etc/kai/*.env` checked for the old values
- [ ] `getWebhookInfo.url == ""` for all bots
- [ ] Poller conflict count over 10 min == 0
- [ ] `python scripts/check_no_secrets.py` passes
- [ ] (Optional) git history purged with owner sign-off
