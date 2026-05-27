"""
v3_utils.py — Shared utilities extracted from post_bot.py
Only safe utilities that do not depend on Telegram, archive, or log logic.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional


# === TIME === #

def get_ist_hour() -> int:
    """Get current IST hour (0-23)."""
    utc_hour = datetime.utcnow().hour
    ist_hour = utc_hour + 5
    if ist_hour >= 24:
        ist_hour -= 24
    return ist_hour


def get_mode_from_time() -> Optional[str]:
    """Determine batch mode from current IST hour."""
    hour = get_ist_hour()
    if 8 <= hour <= 12:
        return "morning"
    elif 13 <= hour <= 17:
        return "noon"
    elif 18 <= hour <= 22:
        return "evening"
    return None


def get_dump_file(mode: Optional[str], dump_dir: str = "dump") -> Optional[str]:
    """Get dump file path for given mode."""
    if mode is None:
        return None
    dump_file = os.path.join(dump_dir, f"{mode}_dump.txt")
    if os.path.exists(dump_file):
        return dump_file
    return None


def now_ist_string() -> str:
    """Return current IST timestamp string."""
    utc_now = datetime.utcnow()
    ist_now = utc_now.replace(hour=(utc_now.hour + 5) % 24)
    return ist_now.strftime("%d %b %Y %H:%M")


# === FILE UTILS === #

def safe_read_text_file(path: str) -> str:
    """Read text file safely, return empty string if missing."""
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def list_subdirs(path: str) -> list[str]:
    """List immediate subdirectories of a path."""
    if not os.path.isdir(path):
        return []
    return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]


def clean_text(text: str) -> str:
    """Clean text: strip, normalize whitespace."""
    if not text:
        return ""
    return " ".join(text.split())


def short_snippet(text: str, n: int = 120) -> str:
    """Get first n characters of text for display."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n-3] + "..."