import logging
from pathlib import Path


LOG_DIR = Path("logs")

LOG_DIR.mkdir(exist_ok=True)


logging.basicConfig(
    filename=LOG_DIR / "orchestrator.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


def info(message):
    logging.info(message)


def error(message):
    logging.error(message)


def warning(message):
    logging.warning(message)
