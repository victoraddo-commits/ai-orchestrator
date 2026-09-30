"""``python -m core.mail [service|once|health]`` — mail worker entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os

from core.mail import health
from core.mail.service import DEFAULT_INTERVAL, run_once, serve


def main(argv=None) -> int:
    logging.basicConfig(
        level=os.environ.get("KAI_MAIL_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(prog="core.mail", description="KAI email worker")
    parser.add_argument("command", nargs="?", default="service",
                        choices=["service", "once", "health"])
    parser.add_argument("--interval", type=float,
                        default=float(os.environ.get("KAI_MAIL_POLL_INTERVAL", DEFAULT_INTERVAL)))
    parser.add_argument("--account-id", default=None)
    args = parser.parse_args(argv)

    if args.command == "health":
        print(json.dumps(health(), indent=2))
        return 0
    if args.command == "once":
        print(json.dumps(run_once(args.account_id), indent=2, default=str))
        return 0
    serve(interval=args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
