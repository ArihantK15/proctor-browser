import logging
import os
import sys
from collections import OrderedDict
from threading import Lock

LOG_DIR = os.getenv("LOG_DIR", "/data/logs")
os.makedirs(LOG_DIR, exist_ok=True)

_formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s')

# Cap open loggers/file-handles to prevent fd exhaustion under high concurrency.
# LRU eviction: when the cache is full, close the least-recently-used logger.
_MAX_LOGGERS = 200
_logger_cache: OrderedDict[str, logging.Logger] = OrderedDict()
_cache_lock = Lock()


def _evict_lru():
    """Close and remove the oldest logger from the cache (call with lock held)."""
    _, old_logger = _logger_cache.popitem(last=False)
    for h in old_logger.handlers[:]:
        try:
            h.close()
        except Exception:
            pass
        old_logger.removeHandler(h)
    logging.Logger.manager.loggerDict.pop(old_logger.name, None)


def get_logger(session_id: str) -> logging.Logger:
    with _cache_lock:
        if session_id in _logger_cache:
            _logger_cache.move_to_end(session_id)
            return _logger_cache[session_id]

        if len(_logger_cache) >= _MAX_LOGGERS:
            _evict_lru()

        logger = logging.getLogger(session_id)
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            # Stdout — captured by Railway / Docker logs
            stdout_handler = logging.StreamHandler(sys.stdout)
            stdout_handler.setFormatter(_formatter)
            logger.addHandler(stdout_handler)

            # File — written to the persistent volume (/data/logs)
            log_file = os.path.join(LOG_DIR, f"{session_id}.log")
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(_formatter)
            logger.addHandler(file_handler)

        _logger_cache[session_id] = logger
        return logger
