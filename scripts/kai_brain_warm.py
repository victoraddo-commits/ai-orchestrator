#!/usr/bin/env python3
"""Keep the Kai brain model resident on VM104 (kills ~21s cold-start).

Sends a 1-token request with a long keep_alive so ollama never unloads
qwen3-coder:kai between sparse user sessions. Local-only.
"""
import json, os, sys, time
import urllib.request

OLLAMA = os.environ.get("KAI_OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("KAI_WARM_MODEL", "qwen3-coder:kai")
KEEP = os.environ.get("KAI_WARM_KEEP_ALIVE", "24h")

def post(path, payload, timeout=180):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def main():
    t0 = time.time()
    try:
        r = post("/api/generate", {"model": MODEL, "prompt": "ok", "stream": False,
                                   "keep_alive": KEEP, "options": {"num_predict": 1}})
        dt = time.time() - t0
        print(json.dumps({"ok": True, "model": MODEL, "keep_alive": KEEP,
                          "load_or_warm_s": round(dt, 2)}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()
