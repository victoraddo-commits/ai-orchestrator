"""Kai Vision — local VLM (M3 / Qwen3-VL-8B-Aggressive) via the VM104 A4 GPU proxy.

Reusable image tasks for any Kai component:
    from core.kai_vision import describe_image, ocr_image, extract_json, gui_parse

The proxy (0.0.0.0:5010) serves M3 on demand; on the single P40 it swaps the
brain out and auto-restores it after 60s of vision idle.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request

URL = os.environ.get("KAI_VISION_URL", "http://192.168.1.241:5010").rstrip("/")
MODEL = os.environ.get("KAI_VISION_MODEL", "M3")
TIMEOUT = int(os.environ.get("KAI_VISION_TIMEOUT", "300"))


def _vision(prompt: str, image_path: str, max_tokens: int = 500, timeout: int = TIMEOUT) -> str:
    with open(image_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    body = {"model": MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}],
            "max_tokens": max_tokens, "temperature": 0.15}
    req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def describe_image(image_path: str) -> str:
    """Factual scene description (people, vehicles, animals, objects, scene)."""
    return _vision("Describe factually and specifically what is visible: people (count/action), "
                   "vehicles (type, moving vs parked), animals, other objects, and the scene "
                   "(location, time, lighting). Never invent objects.", image_path)


def ocr_image(image_path: str) -> str:
    """Transcribe all visible text exactly, preserving layout."""
    return _vision("Transcribe ALL visible text exactly as it appears, preserving line breaks "
                   "and layout. Output only the text.", image_path, max_tokens=1200)


def extract_json(image_path: str, schema_hint: str = "") -> dict:
    """Extract structured content from a document/image as JSON."""
    prompt = ("Extract the information in this image as a single JSON object."
              + (f" Fields: {schema_hint}" if schema_hint else "")
              + " Return ONLY JSON.")
    out = _vision(prompt, image_path, max_tokens=800)
    import re
    m = re.search(r"\{.*\}", out, re.S)
    return json.loads(m.group(0)) if m else {"_raw": out}


def gui_parse(image_path: str) -> str:
    """List UI elements (buttons, fields, labels, menus) and their text."""
    return _vision("List every user-interface element visible (buttons, input fields, labels, "
                   "menus, icons) with their exact text and approximate position.", image_path)


def available() -> bool:
    try:
        with urllib.request.urlopen(URL + "/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        fn = {"describe": describe_image, "ocr": ocr_image, "gui": gui_parse}.get(sys.argv[1])
        print(fn(sys.argv[2]) if fn else "unknown task")
    else:
        print(available())
