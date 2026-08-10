"""Entry point for the Kai Betting worker — run via systemd."""
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

from core.kai_betting.workers import get_workers
from core.kai_betting.db import init_db

init_db()
workers = get_workers()
logging.getLogger("kai_betting").info("Kai Betting worker started")

while True:
    try:
        results = workers.run_cycle()
        logging.getLogger("kai_betting").info(f"Cycle complete: {results}")
    except Exception as e:
        logging.getLogger("kai_betting").error(f"Cycle error: {e}")
    time.sleep(300)
