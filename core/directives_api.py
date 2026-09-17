"""Kai Directives — a small upload service for operator directives.

Runs on the Kai runner (LXC 111) next to the Command Center. The operator
uploads a directive (file or pasted text); it lands in KAI_DIRECTIVES_DIR
where the scheduler/OpenCode can pick it up and follow it.

Auth (2026-09-17): human access to the standalone page is gated by a **Duo
push** approval (fail-closed); the push mints a short-lived signed session
cookie (scope ``directives``). The legacy static token survives only as an
internal machine credential and is honoured **from loopback only** — that's
how the Command Center backend proxy reaches the service.

    uvicorn core.directives_api:app --host 0.0.0.0 --port 8099
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

DIR = Path(os.environ.get("KAI_DIRECTIVES_DIR", "/opt/ai-orchestrator/directives"))
TOKEN_FILE = Path(os.environ.get("KAI_DIRECTIVES_TOKEN_FILE", str(DIR / ".token")))
ACK_FILE = DIR / ".acked.json"
AUDIT_FILE = DIR / ".audit.jsonl"
ALLOWED_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".html", ".rst", ".csv"}
MAX_BYTES = 5 * 1024 * 1024

SESSION_COOKIE = "directives_session"
SESSION_SCOPE = "directives"
DUO_PURPOSE = "directives"
SESSION_TTL = int(os.environ.get("KAI_DIRECTIVES_SESSION_TTL", "300"))
DUO_TIMEOUT = int(os.environ.get("KAI_DIRECTIVES_DUO_TIMEOUT", "45"))
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

DIR.mkdir(parents=True, exist_ok=True)


def _token() -> str:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    token = secrets.token_hex(24)
    TOKEN_FILE.write_text(token)
    os.chmod(TOKEN_FILE, 0o600)
    return token


def _duo() -> tuple:
    """Return (duo_sso module, username) or (None, '') when unconfigured."""
    try:
        from core.auth import duo_sso
        from core.vault.duo import config_from_env
        username = os.environ.get("DUO_USERNAME") or config_from_env().get("username") or ""
        return duo_sso, username
    except Exception:  # noqa: BLE001 - fail closed
        return None, ""


def _duo_configured() -> bool:
    duo_sso, username = _duo()
    if duo_sso is None:
        return False
    try:
        return bool(username) and duo_sso.enabled()
    except Exception:  # noqa: BLE001
        return False


def _is_loopback(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = (client.host if client else "") or ""
    return host in _LOOPBACK or host.startswith("127.")


def _machine_token_ok(request: Request) -> bool:
    supplied = (request.headers.get("x-directive-token")
                or request.query_params.get("token"))
    return bool(supplied) and secrets.compare_digest(supplied, _token())


def _session_claims(request: Request) -> dict | None:
    duo_sso, _ = _duo()
    if duo_sso is None:
        return None
    sess = (request.cookies.get(SESSION_COOKIE)
            or request.headers.get("x-directives-session"))
    if not sess:
        return None
    try:
        claims = duo_sso.verify_session(sess)
    except Exception:  # noqa: BLE001
        return None
    if not claims or SESSION_SCOPE not in (claims.get("scopes") or []):
        return None
    return claims


def _authorized(request: Request) -> bool:
    """Allow either a Duo session (humans) or, from loopback only, the machine token."""
    if _session_claims(request):
        return True
    if _is_loopback(request) and _machine_token_ok(request):
        return True
    return False


def _set_session(resp: Response, session: str) -> Response:
    """Attach the sliding session cookie (persists across refresh; expires after idle)."""
    resp.set_cookie(SESSION_COOKIE, session, httponly=True, samesite="lax",
                    max_age=SESSION_TTL)
    return resp


def _refresh_from_request(request: Request) -> Response | None:
    """Re-issue a sliding session cookie for an authenticated session request."""
    claims = _session_claims(request)
    if not claims:
        return None
    duo_sso, _ = _duo()
    if duo_sso is None:
        return None
    new = duo_sso.sign_session(claims.get("sub") or "", scopes=[SESSION_SCOPE], ttl=SESSION_TTL)
    return _set_session(JSONResponse({"ok": True, "expires_in": SESSION_TTL}), new)


def _sanitize(name: str) -> str:
    name = os.path.basename((name or "").strip())
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]
    return name or "directive.txt"


def _load_acked() -> set:
    if ACK_FILE.exists():
        try:
            return set(json.loads(ACK_FILE.read_text()))
        except Exception:
            return set()
    return set()


def _audit(entry: dict) -> None:
    with AUDIT_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


app = FastAPI(title="Kai Directives")

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Kai Directives</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#0d1117;color:#e6edf3}
 header{padding:16px 24px;background:#161b22;border-bottom:1px solid #30363d}
 h1{margin:0;font-size:18px} small{color:#8b949e}
 main{max-width:900px;margin:0 auto;padding:24px}
 .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
 input,textarea{width:100%;box-sizing:border-box;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:8px;margin:6px 0}
 textarea{min-height:140px;font-family:ui-monospace,monospace}
 button{background:#238636;color:#fff;border:0;border-radius:6px;padding:10px 16px;cursor:pointer}
 button.sec{background:#30363d}
 #drop{border:2px dashed #30363d;border-radius:8px;padding:24px;text-align:center;color:#8b949e}
 #drop.hover{border-color:#238636;color:#e6edf3}
 ul{list-style:none;padding:0} li{padding:8px 0;border-bottom:1px solid #21262d;display:flex;justify-content:space-between}
 .muted{color:#8b949e}.ok{color:#3fb950}.err{color:#f85149}
</style></head><body>
<header><h1>Kai Directives</h1><small>Upload a directive for Kai to follow. Duo push approval required.</small></header>
<main>
 <div class="card">
   <label>Access</label>
   <button class="sec" onclick="unlock()">Approve with Duo</button> <span id="ustat" class="muted"></span>
   <div class="muted" style="font-size:12px;margin-top:6px">A push is sent to your Duo device. Once approved you stay signed in across refreshes until <b>5 minutes of inactivity</b>.</div>
 </div>
 <div class="card">
   <div id="drop">Drop a directive file here (or click)
     <input id="file" type="file" style="display:none" multiple>
   </div>
   <label>…or paste the directive text</label>
   <textarea id="paste" placeholder="# Directive&#10;Describe what Kai should do..."></textarea>
   <input id="name" placeholder="filename (optional), e.g. my-directive.md">
   <button onclick="sendPaste()">Upload pasted directive</button>
   <div id="stat" class="muted"></div>
 </div>
 <div class="card"><h3>Uploaded directives</h3><ul id="list"></ul></div>
</main>
<script>
let unlocked=false, lastTouch=0;
async function touch(){try{await fetch('/touch',{method:'POST',credentials:'same-origin'});}catch(e){}}
async function unlock(){
 const st=document.getElementById('ustat');
 st.textContent='sending push… check your phone'; st.className='muted';
 try{
  const r=await fetch('/unlock',{method:'POST',credentials:'same-origin'});
  const j=await r.json().catch(()=>({error:r.status}));
  if(r.ok){unlocked=true; st.textContent='unlocked — stays signed in until 5 min idle'; st.className='ok'; load();}
  else {unlocked=false; st.textContent='not approved: '+((j&&j.detail)||(j&&j.error)||r.status); st.className='err'; document.getElementById('list').innerHTML='<li class="muted">locked</li>';}
 }catch(e){unlocked=false; st.textContent='error: '+e.message; st.className='err';}
}
async function uploadBlob(blob,name){
 const r=await fetch('/api/upload',{method:'POST',credentials:'same-origin',headers:{'X-Filename':name,'Content-Type':'application/octet-stream'},body:blob});
 const j=await r.json().catch(()=>({error:r.status}));
 document.getElementById('stat').innerHTML = r.ok?('<span class="ok">uploaded '+j.name+'</span>'):('<span class="err">'+JSON.stringify(j)+'</span>');
 if(r.ok){load();}
}
function sendPaste(){
 const t=document.getElementById('paste').value; if(!t){return}
 const n=document.getElementById('name').value||'pasted-directive.md';
 uploadBlob(new Blob([t],{type:'text/plain'}),n);
}
const drop=document.getElementById('drop'),file=document.getElementById('file');
drop.onclick=()=>file.click(); file.onchange=()=>[...file.files].forEach(f=>uploadBlob(f,f.name));
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hover')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hover')}));
drop.addEventListener('drop',ev=>{[...ev.dataTransfer.files].forEach(f=>uploadBlob(f,f.name))});
async function load(){
 const r=await fetch('/api/directives',{credentials:'same-origin'});
 if(!r.ok){document.getElementById('list').innerHTML='<li class="muted">approve with Duo to list</li>';return}
 const j=await r.json();
 document.getElementById('list').innerHTML=j.directives.map(d=>'<li><span>'+(d.acked?'✅ ':'📄 ')+d.name+'</span><span class="muted">'+d.bytes+'B · '+d.modified+' · <a href="/api/directives/'+encodeURIComponent(d.id)+'/download">download</a></span></li>').join('')||'<li class="muted">none yet</li>';
}
// Sliding session: any real activity keeps the session alive (throttled to 1/min).
['mousemove','keydown','click','touchstart','scroll'].forEach(e=>document.addEventListener(e,()=>{
 if(Date.now()-lastTouch>60000){lastTouch=Date.now();touch();}
},{passive:true}));
document.addEventListener('visibilitychange',()=>{if(!document.hidden)touch();});
window.addEventListener('focus',()=>touch());
// On load: refresh the idle timer, then list (401 => locked prompt).
(async()=>{
 try{const s=await fetch('/session',{credentials:'same-origin'}).then(r=>r.json());
     if(s.authenticated){unlocked=true; const st=document.getElementById('ustat'); st.textContent='session active (5 min idle)'; st.className='ok';}}
 catch(e){}
 load(); touch();
})();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.get("/health")
def health() -> dict:
    _, username = _duo()
    return {
        "ok": True,
        "dir": str(DIR),
        "token_set": TOKEN_FILE.exists(),
        "duo_configured": _duo_configured(),
        "duo_username": username,
        "session_ttl": SESSION_TTL,
    }


@app.post("/unlock")
def unlock(request: Request):
    """Duo-push approval. Fail-closed: no approval => no session."""
    duo_sso, username = _duo()
    if duo_sso is None or not _duo_configured():
        raise HTTPException(503, "Duo is not configured on this service")
    ap = duo_sso.approve(username, purpose=DUO_PURPOSE, timeout=DUO_TIMEOUT)
    if not ap.get("approved"):
        _audit({"at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                "action": "unlock_denied", "user": username,
                "detail": str(ap.get("error") or ap.get("result") or "denied")})
        raise HTTPException(403, str(ap.get("error") or ap.get("result") or "not approved"))
    session = duo_sso.sign_session(username, scopes=[SESSION_SCOPE], ttl=SESSION_TTL)
    _audit({"at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "action": "unlock_approved", "user": username,
            "cached": bool(ap.get("cached"))})
    resp = JSONResponse({"ok": True, "user": username, "expires_in": SESSION_TTL})
    return _set_session(resp, session)


@app.post("/touch")
def touch(request: Request):
    """Sliding-session keepalive: refresh the idle timer while the operator is active."""
    resp = _refresh_from_request(request)
    if resp is None:
        raise HTTPException(401, "no active session")
    return resp


@app.get("/session")
def session_info(request: Request):
    """Is the caller currently authenticated? (used by the page to avoid a re-push)."""
    return {"authenticated": bool(_session_claims(request)), "session_ttl": SESSION_TTL}


@app.post("/api/upload")
async def upload(request: Request, x_filename: str = Header(default=None)):
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty upload")
    if len(body) > MAX_BYTES:
        raise HTTPException(413, "too large")
    name = _sanitize(x_filename or "pasted-directive.txt")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"extension {ext!r} not allowed")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = DIR / f"{ts}-{name}"
    out.write_bytes(body)
    _audit({"at": ts, "action": "upload", "name": out.name,
            "bytes": len(body)})
    return {"ok": True, "id": out.name, "name": name, "bytes": len(body),
            "path": str(out)}


@app.get("/api/directives")
def list_directives(request: Request):
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    acked = _load_acked()
    items = []
    for p in sorted(DIR.glob("*")):
        if p.name.startswith(".") or not p.is_file():
            continue
        st = p.stat()
        items.append({
            "id": p.name, "name": p.name, "bytes": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc)
            .strftime("%Y-%m-%dT%H:%MZ"),
            "acked": p.name in acked,
        })
    # Sliding session: an authenticated page load also refreshes the idle timer.
    payload = {"directives": items}
    claims = _session_claims(request)
    if claims:
        duo_sso, _ = _duo()
        new = duo_sso.sign_session(claims.get("sub") or "", scopes=[SESSION_SCOPE], ttl=SESSION_TTL)
        return _set_session(JSONResponse(payload), new)
    return payload


@app.get("/api/directives/{directive_id}")
def get_directive(directive_id: str, request: Request):
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    target = _resolve(directive_id)
    return {"id": target.name, "content": target.read_text(errors="ignore")}


@app.get("/api/directives/{directive_id}/download")
def download_directive(directive_id: str, request: Request):
    """Download a directive as a file attachment."""
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    target = _resolve(directive_id)
    data = target.read_bytes()
    _audit({"at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "action": "download", "name": target.name, "bytes": len(data)})
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )


@app.post("/api/ack/{directive_id}")
def ack_directive(directive_id: str, request: Request):
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    target = _resolve(directive_id)
    acked = _load_acked()
    acked.add(target.name)
    ACK_FILE.write_text(json.dumps(sorted(acked)))
    _audit({"action": "ack", "name": target.name})
    return {"ok": True, "id": target.name, "acked": True}


def _resolve(directive_id: str) -> Path:
    name = _sanitize(directive_id)
    target = (DIR / name).resolve()
    if target.parent != DIR.resolve() or not target.exists():
        raise HTTPException(404, "not found")
    return target
