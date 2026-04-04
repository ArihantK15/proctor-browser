import logging
import os
import sys

LOG_DIR = os.getenv("LOG_DIR", "/data/logs")
os.makedirs(LOG_DIR, exist_ok=True)

_formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s')

def get_logger(session_id: str) -> logging.Logger:
    logger = logging.getLogger(session_id)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        # Stdout — captured by Railway (and Docker logs)
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(_formatter)
        logger.addHandler(stdout_handler)

        # File — written to the persistent volume (/data/logs)
        log_file = os.path.join(LOG_DIR, f"{session_id}.log")
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(_formatter)
        logger.addHandler(file_handler)

    return logger
