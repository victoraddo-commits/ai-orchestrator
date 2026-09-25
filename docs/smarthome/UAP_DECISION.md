# UAP-AC-M-Pro (UniFi AP) — decision: LEAVE AS-IS (Option C)

**Date:** 2026-09-25
**Device:** `UAP-AC-M-Pro` (Ubiquiti UniFi AC Mesh Pro), 192.168.1.61, MAC d0:21:f9:2c:68:91
**State:** functioning as a mesh AP; only SSH (22) exposed; creds lost; defaults rejected.

**Decision (owner):** Do NOT reset, do NOT recover credentials. Leave it running
unmanaged. It remains a working AP; KAI cannot manage/query it.

**Consequence for KAI:** registered as an inert inventory entity
(`lan:192.168.1.61`), provider `lan` (read-only identity, no control). Revisit only
if the owner resets it or supplies a controller backup.
