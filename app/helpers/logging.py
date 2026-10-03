"""Logging without personal data.

GDPR guide §5: no document content, prompts, emails or names in logs. Log ids, counts,
durations and status codes only. `scrub()` is a last line of defence for free text.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s]{7,}\d")
_IBAN = re.compile(r"\bIT\d{2}[A-Z]\d{10}[0-9A-Z]{12}\b", re.I)
_CF = re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b", re.I)


def scrub(text: str) -> str:
    for pattern, repl in ((_EMAIL, "<email>"), (_IBAN, "<iban>"), (_CF, "<cf>"), (_PHONE, "<phone>")):
        text = pattern.sub(repl, text)
    return text


class ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        return True


def configure_logging(log_dir: Path, level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_husk_configured", False):
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(ScrubFilter())
    root.addHandler(console)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(log_dir / "husk.log", maxBytes=5_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    file_handler.addFilter(ScrubFilter())
    root.addHandler(file_handler)
    root._husk_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"husk.{name}")
