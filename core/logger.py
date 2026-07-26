import logging
import sys
from pathlib import Path


LOG_DIR = Path("logs")

LOG_DIR.mkdir(exist_ok=True)


logger = logging.getLogger("orchestrator")

logger.setLevel(logging.INFO)


formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s"
)


file_handler = logging.FileHandler(
    LOG_DIR / "orchestrator.log"
)

file_handler.setFormatter(formatter)


console_handler = logging.StreamHandler(
    sys.stdout
)

console_handler.setFormatter(formatter)


logger.addHandler(file_handler)
logger.addHandler(console_handler)



def info(message):

    logger.info(message)



def error(message):

    logger.error(message)



def warning(message):

    logger.warning(message)
