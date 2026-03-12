"""
Utility functions for the BTC Intelligence adapters.
"""
from __future__ import annotations

import logging
import sys
import time


def get_logger(name: str) -> logging.Logger:
    """Get or create a logger with standard formatting."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    fmt = "%(asctime)s %(levelname)s %(name)s - %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)
    return logger


def now_ms() -> int:
    """Current time in milliseconds since epoch."""
    return int(time.time() * 1000)
