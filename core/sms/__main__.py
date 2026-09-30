"""``python -m core.sms [webhook|once|health]`` — SMS worker entry point."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os

from core.sms import health
from core.sms.service import DEFAULT_HOST, DEFAULT_PORT, run_once, serve

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _mirror_logs_to_journald() -> None:
    """Mirror root logs to journald so ``journalctl -u kai-sms-worker`` works.

    The unit redirects stdout/stderr to a file, so without this the worker
    journal stays empty even though the worker is logging.
    """
    if not os.path.exists("/dev/log"):
        return
    root = logging.getLogger()
    if any(isinstance(h, logging.handlers.SysLogHandler) for h in root.handlers):
        return
    handler = logging.handlers.SysLogHandler(address="/dev/log")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)


def main(argv=None) -> int:
    logging.basicConfig(
        level=os.environ.get("KAI_SMS_LOG_LEVEL", "INFO"),
        format=LOG_FORMAT,
    )
    _mirror_logs_to_journald()
    parser = argparse.ArgumentParser(prog="core.sms", description="KAI SMS worker")
    parser.add_argument("command", nargs="?", default="webhook",
                        choices=["webhook", "once", "health"])
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--fixture-dir", default=None)
    args = parser.parse_args(argv)

    if args.command == "health":
        print(json.dumps(health(), indent=2))
        return 0
    if args.command == "once":
        print(json.dumps(run_once(fixture_dir=args.fixture_dir), indent=2, default=str))
        return 0
    serve(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
