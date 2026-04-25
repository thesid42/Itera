"""
Centralized logging for Itera.

Call setup_logging() once at startup (main.py).
Every other module does:
    import logging
    logger = logging.getLogger(__name__)

Log file: logs/itera.log  (rotates at 5 MB, keeps 3 backups)
Console:  WARNING and above only (keeps TUI uncluttered)
"""
import logging
import logging.handlers
import os

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "itera.log")

_FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.DEBUG) -> None:
    """
    Configure root logger. Safe to call multiple times (idempotent).
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    root.setLevel(level)
    formatter = logging.Formatter(_FMT, datefmt=_DATE_FMT)

    # Rotating file handler — full DEBUG detail
    os.makedirs(_LOG_DIR, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    root.addHandler(fh)

    # Console handler — WARNING+ only so it doesn't disrupt the TUI
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(formatter)
    root.addHandler(ch)
