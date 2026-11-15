"""
Simple logger utility for Harven AI.
Provides get_logger() compatible with the monitor module.
"""
import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """Get a named logger instance.
    ★ #1447（2026-11-05 r50）黑框去重：web_app 已 basicConfig（控制台+落盘双 handler），
    此处不再自挂 handler——否则同一条日志走自己 handler + root 冒泡各打一遍，
    黑框里每行重复两次（实锤）。仅当 root 无 handler（独立脚本场景）才自配。"""
    logger = logging.getLogger(name)
    if not logger.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            '[%(asctime)s] %(name)s %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        ))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
