# PROCESS NOTE — command_center.html deploy path (fixed 2026-09-25)

The push helper `push_ct111.sh` MUST write **inside CT111** via
`pct exec 111 -- sh -c 'base64 -d > <path>'`. An earlier version wrote to
`/opt/ai-orchestrator/...` on **PVE-B's** own filesystem (a different /opt),
so edits appeared to deploy but were never served. Symptom: edited file hash !=
container file hash, and the served page lacked the new element.

Always verify after deploy:
```
md5sum <local>; pct exec 111 -- md5sum <container path>
```
and confirm `grep -c '<new marker>'` on the *served* page.
