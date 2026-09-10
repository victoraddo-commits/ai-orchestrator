# Juris KAI Bot Token Rotation Guide

**Date:** 2026-09-10  
**Reason:** External unauthorized poller detected (409 conflicts)  
**Bot:** @Juriskai_bot  
**Current Token:** `8968355425:AAFiYRMzc1zeFj3VgiA94jIJSd3PbYP4W68`

---

## CRITICAL: Why Rotation is Needed

**Security Breach Detected:**
- External device is polling @Juriskai_bot
- Continuous 409 conflicts prove unauthorized access
- Someone/something has the bot token
- **Must rotate immediately** to revoke unauthorized access

---

## Step-by-Step Rotation Procedure

### Step 1: Generate New Token via BotFather

1. Open Telegram
2. Message @BotFather
3. Send: `/mybots`
4. Select: **Juris Kai**
5. Select: **API Token**
6. Select: **Revoke current token**
7. Confirm: **Yes, I'm totally sure**
8. **Copy the new token** (format: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)

⚠️ **WARNING:** Old token stops working immediately after revoke!

---

### Step 2: Update All Authorized Locations

**On CT 111 (Proxmox B):**

```bash
# SSH to Proxmox B
ssh -J root@192.168.99.2 root@100.122.38.118

# Update system env
pct exec 111 -- bash -c 'sed -i "s/JURIS_KAI_BOT_TOKEN=.*/JURIS_KAI_BOT_TOKEN=<NEW_TOKEN>/" /etc/ai-orchestrator.env'

# Update project env (backup)
pct exec 111 -- bash -c 'sed -i "s/JURIS_KAI_BOT_TOKEN=.*/JURIS_KAI_BOT_TOKEN=<NEW_TOKEN>/" /opt/ai-orchestrator/.env'

# Restart service
pct exec 111 -- systemctl restart juris-kai

# Verify no 409 conflicts
pct exec 111 -- journalctl -u juris-kai -f
```

**Replace `<NEW_TOKEN>` with actual token from BotFather!**

---

### Step 3: Verify New Token Working

```bash
# Test new token
curl "https://api.telegram.org/bot<NEW_TOKEN>/getMe"

# Expected response:
# {"ok":true,"result":{"id":8968355425,"is_bot":true,"first_name":"Juris Kai","username":"Juriskai_bot"}}
```

---

### Step 4: Confirm 409 Conflicts Stopped

```bash
# Watch logs for 2 minutes
pct exec 111 -- journalctl -u juris-kai --since '2 minutes ago' -f

# Should see:
# - NO "409 Conflict" errors
# - Bot polling successfully
# - Responding to messages
```

✅ **Success:** No 409 conflicts = External poller blocked!

---

### Step 5: Update Vault (Optional but Recommended)

```bash
# If you want to store in vault for future use
pct exec 111 -- /opt/ai-orchestrator/.venv/bin/python3 << 'PYTHON'
import sys
sys.path.insert(0, '/opt/ai-orchestrator')
from core.ai.credential_vault import store_credential

store_credential(
    provider="juris_kai",
    api_key="<NEW_TOKEN>",
    api_base=""
)
print("✅ Token stored in vault")
PYTHON
```

---

## Security Best Practices Going Forward

### 1. **Token Storage**
- ✅ Store in env files with restricted permissions (`chmod 600`)
- ✅ Never commit tokens to git
- ✅ Use vault for production
- ❌ Don't share tokens between machines unnecessarily

### 2. **Access Control**
- ✅ Each bot on ONE machine only
- ✅ One systemd service per bot
- ✅ Monitor for 409 conflicts (= unauthorized access)
- ❌ Don't run same bot on multiple machines

### 3. **Monitoring**
- ✅ Set up alerts for 409 conflicts
- ✅ Regular token rotation (every 90 days)
- ✅ Audit which machines have which tokens
- ✅ Log all bot activity

### 4. **Bot Isolation** (Recommended Architecture)

```
Each Bot = Own Everything:
├─ Own Telegram bot (@unique_name)
├─ Own bot token (not shared)
├─ Own systemd service
├─ Own poller process
├─ Own environment variables
└─ NO shared resources with other bots
```

---

## Rollback Procedure (If Something Goes Wrong)

**Can't rollback!** BotFather doesn't allow un-revoking tokens.

**Instead:**
1. Generate another new token
2. Update all locations again
3. Verify working

**Prevention:** Test on dev bot first before rotating production bot

---

## Post-Rotation Checklist

- [ ] New token generated via BotFather
- [ ] Old token revoked
- [ ] Updated in `/etc/ai-orchestrator.env`
- [ ] Updated in `/opt/ai-orchestrator/.env`
- [ ] Service restarted (`systemctl restart juris-kai`)
- [ ] Verified no 409 conflicts
- [ ] Bot responding to messages
- [ ] Tested with actual legal query
- [ ] Vault updated (if using)
- [ ] Documentation updated with new token location

---

## Troubleshooting

### Issue: "401 Unauthorized" after rotation
**Cause:** Old token still in use  
**Fix:** Double-check both env files updated, restart service

### Issue: Still getting 409 conflicts
**Cause:** Another location still has old token, OR new token leaked  
**Fix:** Search all machines for old token, rotate again if leaked

### Issue: Bot not responding at all
**Cause:** Service not restarted or wrong token format  
**Fix:** `systemctl restart juris-kai`, check token format (no spaces/quotes)

---

## Expected Outcome

**Before Rotation:**
```
getUpdates failed: Conflict: terminated by other getUpdates request
```

**After Rotation:**
```
✅ Bot polling successfully
✅ Responding to legal queries
✅ NO 409 conflicts
✅ External poller blocked (no longer has valid token)
```

---

**EXECUTE THIS ROTATION IMMEDIATELY TO SECURE YOUR BOT!** 🔒
