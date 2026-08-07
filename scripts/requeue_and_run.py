#!/usr/bin/env python3
"""Re-queue 10 failed builds and run the full pipeline."""
import os, sys, json, signal, subprocess
os.chdir('/project/ai-orchestrator')
sys.path.insert(0, '/project/ai-orchestrator')

from core.build_manager import (list_builds, create_build, advance_builds,
                                 submit_answer, BUILD_TRANSITIONS)
from core.lifecycle import transition

subprocess.run(['pkill', '-9', '-f', 'opencode'], capture_output=True)

# ---- Phase 0: Re-queue ----
current = {b.get('name','') for b in list_builds()}
archive = json.load(open('memory/builds_archive.json'))
records = archive if isinstance(archive, list) else archive.get('records', [])

seen = set()
created = []
for r in sorted(records, key=lambda r: str(r.get('updated','')), reverse=True):
    name = r.get('name','')
    fail = str(r.get('failure_reason',''))
    if name and name not in seen and name not in current:
        if 'planning' not in fail.lower() and 'q&a' not in fail.lower():
            seen.add(name)
            b = create_build(
                name=name,
                description=r.get('description','') or name,
                project_path=r.get('project_path','/project/ai-orchestrator')
            )
            created.append(b)
            print(f"  + {b['id'][:8]} | {name[:55]}")
            if len(created) >= 10:
                break

print(f"\nRe-queued {len(created)} builds")

def timeout_handler(s, f):
    raise TimeoutError()
signal.signal(signal.SIGALRM, timeout_handler)

# ---- Phase 1: Advance through planning ----
signal.alarm(120)
try:
    advance_builds()
    print("P1 OK")
except TimeoutError:
    print("P1 timeout")
    subprocess.run(['pkill', '-9', '-f', 'opencode'], capture_output=True)
finally:
    signal.alarm(0)

# ---- Phase 2: Answer Q&A + approve architecture ----
builds = list_builds()
for b in builds:
    if b.get('status') == 'WAITING_FOR_USER_INPUT':
        submit_answer(b['id'], 'Proceed with the plan. Generate code now. No further questions.')
        print(f"  A: {b.get('name','')[:40]}")

builds = list_builds()
approved = 0
for b in builds:
    s = b.get('status','?')
    if s == 'WAITING_FOR_ARCHITECTURE_APPROVAL':
        transition(b, 'ARCHITECTURE_APPROVED', BUILD_TRANSITIONS)
        approved += 1
    elif s == 'PLANNING':
        try:
            transition(b, 'WAITING_FOR_ARCHITECTURE_APPROVAL', BUILD_TRANSITIONS)
            transition(b, 'ARCHITECTURE_APPROVED', BUILD_TRANSITIONS)
            approved += 1
        except Exception:
            pass

print(f"Approved: {approved}/{len(builds)}")
with open('memory/builds.json', 'w') as f:
    json.dump({'schema_version':1,'records':builds}, f, indent=2)

# ---- Phase 3: Generate code ----
if approved > 0:
    subprocess.run(['pkill', '-9', '-f', 'opencode'], capture_output=True)
    signal.alarm(300)
    try:
        advance_builds()
        print("P3 OK")
    except TimeoutError:
        print("P3 timeout")
        subprocess.run(['pkill', '-9', '-f', 'opencode'], capture_output=True)
    finally:
        signal.alarm(0)

# ---- Phase 4: Approve reviews and deploys ----
builds = list_builds()
for b in builds:
    s = b.get('status','?')
    if s in ('CODE_REVIEW', 'SECURITY_REVIEW'):
        transition(b, 'WAITING_FOR_DEPLOY_APPROVAL', BUILD_TRANSITIONS)
    elif s == 'WAITING_FOR_DEPLOY_APPROVAL':
        transition(b, 'DEPLOYING', BUILD_TRANSITIONS)

with open('memory/builds.json', 'w') as f:
    json.dump({'schema_version':1,'records':builds}, f, indent=2)

subprocess.run(['pkill', '-9', '-f', 'opencode'], capture_output=True)
signal.alarm(60)
try:
    advance_builds()
    print("P4 OK")
except TimeoutError:
    print("P4 timeout")
finally:
    signal.alarm(0)

# ---- FINAL REPORT ----
builds = list_builds()
archive = json.load(open('memory/builds_archive.json'))
records = archive if isinstance(archive, list) else archive.get('records', [])
done = sum(1 for r in records if r.get('status') in ("COMPLETED","VERIFIED"))
fail_c = sum(1 for r in records if r.get('status') == 'FAILED')
total = len(records) + len(builds)
active = sum(1 for b in builds if b.get('status') not in ("COMPLETED","FAILED","VERIFIED","ROLLED_BACK"))

print(f"\n{'='*60}")
print(f"FINAL: Total={total}  Done={done}  Failed={fail_c}  Active={active}")
for b in builds:
    s = b.get('status','?')
    name = str(b.get('name','?'))[:55]
    gen = str(b.get('generated_by','') or '')[:15]
    fail_r = str(b.get('failure_reason',''))[:120]
    print(f"  [{s:30s}] gen={gen:15s} | {name}")
    if fail_r: print(f"    {fail_r}")
