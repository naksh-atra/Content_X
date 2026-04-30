"""
v3_post_bot.py — v3 Generation Layer for @SatyaNaaksh

Phase 0 — Data Contracts and Runtime Flags
Do NOT replace post_bot.py until v3 proves stable.

=== DATA CONTRACTS ===

class InputBundle:
    mode: str                          # "morning" | "noon" | "evening"
    timestamp: str                     # "30 Apr 2026 19:30"
    dump_content: str                  # Raw dump file text
    experiments: list[ExperimentBundle]
    posted_history: str                # posted_log.txt content
    voice_rules: str                   # process_mini.txt content

class ExperimentBundle:
    folder_name: str
    title: str
    date: str
    status: str                        # "success" | "failure" | "partial"
    tags: list[str]
    visual_files: list[str]
    notes_raw: str
    what_was_tested: str
    result: str
    key_observation: str
    has_visual: bool
    folder_mtime: float
    skip_reason: str                   # Empty if valid

class Candidate:
    id: str                            # "cand_001"
    source: str                        # "dump" | "experiment:exp_001"
    source_type: str                   # "rss" | "reddit" | "experiment" | "scrape" | "unknown"
    first_party_strength: str          # "high" | "medium" | "low"
    raw_text: str
    lane_match: bool
    lane_keywords: list[str]
    freshness_score: int               # 0-10
    visual_state: str                  # "required_present" | "required_missing" | "optional"
    candidate_type: str                # "original" | "thread" | "qt_reply" | "discard"
    computed_score: int
    reason_codes: list[str]

class GeneratedPost:
    id: str                            # "post_001"
    candidate_id: str
    post_type: str                     # "original" | "thread" | "qt_reply"
    content: str
    visual_needed: bool
    format_notes: str                  # "single" | "thread:3" | "qt:reply"

class SelectedPost:
    post: GeneratedPost
    selection_reason: str
    rejected: bool
    rejection_reason: str

=== RUNTIME FLAGS ===

DRY_RUN = False            # Print everything, send nothing, do not archive
SHADOW_MODE = False        # Label Telegram output as test, keep v2 as production
ENABLE_THREADS = False     # Start False — enable only in Phase 4
ENABLE_DUMP_ORIGINALS = True
ENABLE_EXPERIMENTS = True

=== ROUTING LOGIC ===

STEP 1 — Lane filter
  No lane match → discard (or qt_reply if discourse-heavy)
  Lane match → continue

STEP 2 — Source priority
  experiment source → ORIGINAL candidate, +20 score
  dump source with engineering substance → ORIGINAL candidate
  dump source with hot discourse only → QT_REPLY candidate

STEP 3 — Visual check for originals
  experiment with screenshot/image present → required_present
  experiment with explicit visual reference → evaluate conservatively
  no visual support → required_missing → demote to qt_reply

STEP 4 — Thread check
  sequential arc with genuine substance → THREAD candidate (ENABLE_THREADS gate)
  otherwise → single post format

STEP 5 — Score and threshold
  experiment original: score >= 25 + visual required
  dump original: score >= 40 + visual required + first_party_strength != low
  thread: score >= 20
  qt_reply: score >= 10

=== SCORE THRESHOLDS ===
| Post Type | Min Score | Visual Required | Notes |
|---|---||---|
| Experiment original | 25 | Yes | Lower threshold — first-party builder value |
| Dump original | 40 | Yes | Stricter |
| Thread | 20 | No | Only when real narrative arc |
| QT/reply | 10 | No | Lowest-risk format |

=== HARD REJECT CODES ===
- generic
- stale
- duplicate_angle
- off_lane
- invented_opinion_risk
- voice_mismatch
- no_visual_for_original

=== ROLLBACK RULE ===
If v3 produces empty/generic/broken output on live batches:
→ Keep v2 in production, continue v3 in shadow only
"""

import os
import re
import time
import json
import random
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional, NamedTuple
from dotenv import load_dotenv

# === CONFIGURATION ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DUMP_DIR = os.path.join(BASE_DIR, "dump")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")
EXPERIMENTS_DIR = os.path.join(BASE_DIR, "experiments_inbox")
PROCESS_FILE = os.path.join(BASE_DIR, "process_mini.txt")
LOG_FILE = os.path.join(BASE_DIR, "posted_log.txt")

# === RUNTIME FLAGS ===
DRY_RUN = False
SHADOW_MODE = False
ENABLE_THREADS = False
ENABLE_DUMP_ORIGINALS = True
ENABLE_EXPERIMENTS = True

# === LANE KEYWORDS ===
LANE_KEYWORDS = [
    "ai agent", "agents", "cursor", "github copilot", "vscode", "autogen",
    "langchain", "llamaindex", "crewai", "smolagents", "prompt engineering",
    "tool use", "dev workflow", "testing", "debugging", "benchmark",
    "experiment", "trial", "eval", "latency", "inference", "codegen"
]

# === SCORE ADJUSTMENTS ===
SCORE_EXPERIMENT = 20
SCORE_VISUAL_PRESENT = 10
SCORE_FIRST_PARTY_HIGH = 15
SCORE_FIRST_PARTY_MEDIUM = 5
SCORE_FRESHNESS_MAX = 10
PENALTY_GENERIC = 50
PENALTY_DUPLICATE = 20
PENALTY_WEAK_LANE = 10
PENALTY_NO_VISUAL = 15

# === THRESHOLDS ===
THRESHOLD_EXPERIMENT_ORIGINAL = 25
THRESHOLD_DUMP_ORIGINAL = 40
THRESHOLD_THREAD = 20
THRESHOLD_QT_REPLY = 10


# === DATA CLASSES ===

class ExperimentBundle(NamedTuple):
    folder_name: str
    title: str
    date: str
    status: str
    tags: list
    visual_files: list
    notes_raw: str
    what_was_tested: str
    result: str
    key_observation: str
    has_visual: bool
    folder_mtime: float
    skip_reason: str


class Candidate(NamedTuple):
    id: str
    source: str
    source_type: str
    first_party_strength: str
    raw_text: str
    lane_match: bool
    lane_keywords: list
    freshness_score: int
    visual_state: str
    candidate_type: str
    computed_score: int
    reason_codes: list


# === PHASE 1: FOUNDATION ===

def get_ist_hour() -> int:
    utc_hour = datetime.utcnow().hour
    ist_hour = utc_hour + 5
    if ist_hour >= 24:
        ist_hour -= 24
    return ist_hour


def get_mode_from_time() -> Optional[str]:
    hour = get_ist_hour()
    if 8 <= hour <= 12:
        return "morning"
    elif 13 <= hour <= 17:
        return "noon"
    elif 18 <= hour <= 22:
        return "evening"
    return None


def get_dump_file(mode: Optional[str]) -> Optional[str]:
    if mode is None:
        return None
    dump_file = os.path.join(DUMP_DIR, f"{mode}_dump.txt")
    if os.path.exists(dump_file):
        return dump_file
    return None


def now_ist_string() -> str:
    return datetime.utcnow().strftime("%d %b %Y %H:%M")


def safe_read_text_file(path: str) -> str:
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def list_subdirs(path: str) -> list[str]:
    if not os.path.isdir(path):
        return []
    return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]


def clean_text(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split())


def short_snippet(text: str, n: int = 120) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n-3] + "..."