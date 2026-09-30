"""Run the CT110 browser service: ``python -m core.browser``."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    from core.browser.server import create_app

    host = os.environ.get("KAI_BROWSER_HOST", "0.0.0.0")
    port = int(os.environ.get("KAI_BROWSER_PORT", "8090"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
