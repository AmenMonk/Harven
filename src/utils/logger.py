"""
Simple logger utility for Harven AI.
Provides get_logger() compatible with the monitor module.
"""
import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """Get a named logger instance."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            '[%(asctime)s] %(name)s %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
