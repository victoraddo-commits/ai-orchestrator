"""Daily Kai Telegram digest of Qwen3-Coder job activity.

2026-08-02: daily companion to scripts/kai_qwen3_digest.py -- same
summarization over the same two provider registrations ("openai",
"qwen3_coding"), just a 24-hour window. All logic lives in
kai_qwen3_digest; this is only a thin cron entry point so the two
digests can never drift apart.

Usage: .venv/bin/python scripts/kai_qwen3_daily_digest.py
"""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import kai_qwen3_digest  # noqa: E402


def main():
    kai_qwen3_digest.main(window=timedelta(hours=24), label="daily")


if __name__ == "__main__":
    main()
