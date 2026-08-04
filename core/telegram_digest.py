"""Phase 17T: Proactive Telegram status digest via DeepSeek-V4-Flash.

Runs as a scheduled job (systemd timer, daily at 08:00 UTC).  Gathers system
state, delegates to deepseek_native_flash for a concise summary, and sends
the result to the operator's Telegram chat.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


def _load_env():
    """Load .env from the ai-orchestrator directory."""
    env_path = Path("/project/ai-orchestrator/.env")
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    if key not in os.environ:
                        os.environ[key] = val


def _gather_state():
    """Collect the data points the digest needs."""
    from core.kai.planner import gather_signals

    signals = gather_signals()
    return signals


def _generate_summary(signals: dict) -> str:
    """Use deepseek_native_flash to produce a concise summary."""
    from core.ai.ai_router import delegate

    state_json = json.dumps(signals, indent=2, default=str)

    prompt = (
        "You are Kai's status digest writer. Produce a concise, bullet-point "
        "summary of the current system state for the operator's morning briefing. "
        "Include: roadmap progress (completed/total, recently completed phases), "
        "active builds (name + status), pending approvals count, provider health "
        "(any errors or quota issues), and any failed builds in the last 24 hours. "
        "Keep it under 15 lines. Use emoji for visual cues.\n\n"
        f"Current state:\n{state_json}"
    )

    try:
        result = delegate(
            prompt,
            task_type="planning",
            capability="text_task",
            timeout=60,
        )
        return result.get("response", str(result))
    except Exception as e:
        return f"Digest generation failed: {e}"


def _send_telegram(text: str):
    """Send the digest via the existing Telegram bridge."""
    from core.telegram_bridge import send_message

    try:
        send_message(text)
    except Exception as e:
        print(f"Failed to send digest: {e}")


def run_digest():
    """One-shot digest run.  Called by systemd timer."""
    _load_env()

    signals = _gather_state()
    summary = _generate_summary(signals)

    header = f"📊 Kai Morning Briefing — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
    full_text = header + summary

    _send_telegram(full_text)


if __name__ == "__main__":
    run_digest()
