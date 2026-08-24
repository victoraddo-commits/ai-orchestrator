"""Telegram commands for the hardware-gated enhancement registry.

  /enhancements          — status table
  enable <key>           — opt in (works pre-hardware → BLOCKED)
  disable <key>          — turn off

Keys: wake_word, streaming_voice, home_assistant, telephony
"""

from __future__ import annotations


def handle_enhancement_command(text: str):
    """Return reply string if this is an enhancement command, else None."""
    t = text.strip().lower()
    if not t.startswith("/enh"):
        # also accept bare "enable <x>" / "disable <x>" for known keys only
        parts = t.split()
        if len(parts) == 2 and parts[0] in ("enable", "disable") and \
           parts[1] in ("wake_word", "streaming_voice", "home_assistant", "telephony"):
            return _do(parts[0], parts[1])
        return None
    from core.kai_enhancements import status, ENHANCEMENTS
    parts = t.split()
    if len(parts) == 1:
        st = status()
        lines = ["🧩 Optional enhancements (hardware-gated):", ""]
        for key, info in st.items():
            icon = {"ENABLED": "✅", "BLOCKED": "⏸", "DISABLED": "⭕"}.get(info["state"], "·")
            lines.append(f"{icon} {info['name']} — {info['state']}")
            if info["missing"]:
                lines.append(f"    needs: {info['missing'][0][:90]}")
        lines.append("")
        lines.append("Enable: 'enable wake_word' | Disable: 'disable <name>'")
        return "\n".join(lines)
    sub = parts[1]
    if sub in ENHANCEMENTS and len(parts) >= 2:
        pass
    return None


def _do(action: str, key: str) -> str:
    from core.kai_enhancements import enable, disable
    r = enable(key) if action == "enable" else disable(key)
    if not r.get("ok"):
        return f"❌ {r.get('error')}"
    if r.get("missing_requirements"):
        return (f"⏸ {key} ENABLED but BLOCKED — will auto-activate when:\n"
                f"  • " + "\n  • ".join(r["missing_requirements"]))
    return f"✅ {key} is now {r.get('state', 'ENABLED')}"
