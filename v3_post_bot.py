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


# === EXPERIMENT SCANNING ===

def scan_experiments_inbox(max_folders: int = 5) -> list[ExperimentBundle]:
    """Scan experiments_inbox/ and return valid ExperimentBundle list."""
    if not os.path.isdir(EXPERIMENTS_DIR):
        return []
    
    folders = list_subdirs(EXPERIMENTS_DIR)
    if not folders:
        return []
    
    # Sort by mtime, newest first
    folders_with_mtime = []
    for f in folders:
        folder_path = os.path.join(EXPERIMENTS_DIR, f)
        mtime = os.path.getmtime(folder_path)
        folders_with_mtime.append((mtime, f))
    folders_with_mtime.sort(reverse=True, key=lambda x: x[0])
    
    experiments = []
    for mtime, folder in folders_with_mtime[:max_folders]:
        exp = parse_experiment_folder(folder, mtime)
        experiments.append(exp)
    
    return experiments


def parse_experiment_folder(folder_name: str, folder_mtime: float) -> ExperimentBundle:
    """Parse one experiment folder into ExperimentBundle."""
    folder_path = os.path.join(EXPERIMENTS_DIR, folder_name)
    meta_path = os.path.join(folder_path, "meta.txt")
    notes_path = os.path.join(folder_path, "notes.txt")
    
    # Check required files
    if not os.path.exists(meta_path) or not os.path.exists(notes_path):
        return ExperimentBundle(
            folder_name=folder_name,
            title="",
            date="",
            status="",
            tags=[],
            visual_files=[],
            notes_raw="",
            what_was_tested="",
            result="",
            key_observation="",
            has_visual=False,
            folder_mtime=folder_mtime,
            skip_reason="missing meta.txt or notes.txt"
        )
    
    # Parse meta.txt
    meta = safe_read_text_file(meta_path)
    meta_dict = parse_meta_txt(meta)
    
    # Parse notes.txt
    notes_raw = safe_read_text_file(notes_path)
    notes_dict = parse_notes_txt(notes_raw)
    
    # Detect visual files
    visual_files = detect_visual_files(folder_path)
    has_visual = len(visual_files) > 0
    
    return ExperimentBundle(
        folder_name=folder_name,
        title=meta_dict.get("title", ""),
        date=meta_dict.get("date", ""),
        status=meta_dict.get("status", ""),
        tags=meta_dict.get("tags", []),
        visual_files=visual_files,
        notes_raw=notes_raw,
        what_was_tested=notes_dict.get("what_was_tested", ""),
        result=notes_dict.get("result", ""),
        key_observation=notes_dict.get("key_observation", ""),
        has_visual=has_visual,
        folder_mtime=folder_mtime,
        skip_reason=""
    )


def parse_meta_txt(meta_text: str) -> dict:
    """Parse meta.txt content into dict."""
    result = {"title": "", "date": "", "status": "", "tags": [], "visual_files": []}
    for line in meta_text.strip().split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "title":
            result["title"] = value
        elif key == "date":
            result["date"] = value
        elif key == "status":
            result["status"] = value
        elif key == "tags":
            result["tags"] = [t.strip() for t in value.split(",")]
        elif key == "visual_files":
            result["visual_files"] = [v.strip() for v in value.split(",") if v.strip()]
    return result


def parse_notes_txt(notes_text: str) -> dict:
    """Parse notes.txt content into dict."""
    result = {"what_was_tested": "", "result": "", "key_observation": ""}
    current_section = ""
    for line in notes_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("what was tested:"):
            current_section = "what_was_tested"
            line = line.split(":", 1)[1].strip() if ":" in line else ""
            if line:
                result[current_section] = line
        elif line.lower().startswith("result:"):
            current_section = "result"
            line = line.split(":", 1)[1].strip() if ":" in line else ""
            if line:
                result[current_section] = line
        elif line.lower().startswith("key observation:"):
            current_section = "key_observation"
            line = line.split(":", 1)[1].strip() if ":" in line else ""
            if line:
                result[current_section] = line
        elif current_section:
            result[current_section] += " " + line
    return result


def detect_visual_files(folder_path: str) -> list[str]:
    """Detect image files in experiment folder."""
    if not os.path.isdir(folder_path):
        return []
    
    image_extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp")
    visual_files = []
    
    # Check folder root
    for f in os.listdir(folder_path):
        if f.lower().endswith(image_extensions):
            visual_files.append(f)
    
    # Check screenshots subfolder
    screenshots_path = os.path.join(folder_path, "screenshots")
    if os.path.isdir(screenshots_path):
        for f in os.listdir(screenshots_path):
            if f.lower().endswith(image_extensions):
                visual_files.append(f"screenshots/{f}")
    
    return visual_files


# === LANE MATCHING ===

def match_lane_keywords(text: str, keywords: list[str] = None) -> list[str]:
    """Case-insensitive keyword match. Returns list of matched keywords."""
    if keywords is None:
        keywords = LANE_KEYWORDS
    
    text_lower = text.lower()
    matched = []
    for kw in keywords:
        if kw.lower() in text_lower:
            matched.append(kw)
    return matched


def is_generic(text: str) -> bool:
    """Check if text is generic commentary that anyone could write."""
    generic_phrases = [
        "this is huge", "so important", "game changer",
        "breaking news", "big announcement", "exciting development",
        "incredible progress", "revolutionary", "transformational"
    ]
    text_lower = text.lower()
    for phrase in generic_phrases:
        if phrase in text_lower:
            return True
    return False


def first_party_strength_from_text(source_type: str, raw_text: str) -> str:
    """Assign first_party_strength based on content signals."""
    if source_type == "experiment":
        return "high"
    
    # Dump-origin signals for "medium"
    medium_signals = [
        "benchmark", "latency", "ms", "tokens/sec", "error", "traceback",
        "failed", "compared", "workflow", "cursor", "vscode", "tried",
        "debugged", "tested", "output", "prompt", "result", "seconds",
        "lines of code", "function", "api", "sdk", "package"
    ]
    
    text_lower = raw_text.lower()
    for signal in medium_signals:
        if signal in text_lower:
            return "medium"
    
    return "low"


def detect_visual_state(source_type: str, visual_files: list, raw_text: str) -> str:
    """Detect visual state for candidate."""
    if source_type == "experiment":
        if visual_files:
            return "required_present"
        return "required_missing"
    # Dump-origin conservative
    visual_refs = ["screenshot", "image", "see attached", "shown below", "as seen"]
    text_lower = raw_text.lower()
    for ref in visual_refs:
        if ref in text_lower:
            return "optional"
    return "optional"


# === SCORING ===

def compute_freshness(text: str, dump_file_mtime: float = None) -> int:
    """Compute freshness score (0-10). Default to 5 if unknown."""
    # Try to extract date from text
    import re
    date_patterns = [
        r"\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{4}",
        r"\d{4}-\d{2}-\d{2}",
    ]
    
    text_lower = text.lower()
    
    # If content mentions "today", it's very fresh
    if "today" in text_lower:
        return 9
    if "yesterday" in text_lower:
        return 7
    
    # Default
    return 5


def compute_score(candidate: Candidate) -> int:
    """Compute score with all adjustments."""
    score = 0
    
    # Base: source type
    if candidate.source_type == "experiment":
        score += SCORE_EXPERIMENT
    
    # Visual bonus
    if candidate.visual_state == "required_present":
        score += SCORE_VISUAL_PRESENT
    
    # First party strength
    if candidate.first_party_strength == "high":
        score += SCORE_FIRST_PARTY_HIGH
    elif candidate.first_party_strength == "medium":
        score += SCORE_FIRST_PARTY_MEDIUM
    
    # Freshness
    score += candidate.freshness_score
    
    # Penalties
    if is_generic(candidate.raw_text):
        score -= PENALTY_GENERIC
    
    return score


def assign_candidate_type(candidate: Candidate) -> str:
    """Assign candidate type based on scoring rules."""
    if not candidate.lane_match:
        return "discard"
    
    # Experiment source
    if candidate.source_type == "experiment":
        if candidate.computed_score >= THRESHOLD_EXPERIMENT_ORIGINAL and candidate.visual_state == "required_present":
            return "original"
        if ENABLE_THREADS and candidate.computed_score >= THRESHOLD_THREAD:
            return "thread"
        if candidate.computed_score >= THRESHOLD_QT_REPLY:
            return "qt_reply"
        return "discard"
    
    # Dump source
    if ENABLE_DUMP_ORIGINALS:
        if candidate.computed_score >= THRESHOLD_DUMP_ORIGINAL and candidate.visual_state != "required_missing" and candidate.first_party_strength != "low":
            return "original"
    
    if ENABLE_THREADS and candidate.computed_score >= THRESHOLD_THREAD:
        return "thread"
    
    if candidate.computed_score >= THRESHOLD_QT_REPLY:
        return "qt_reply"
    
    return "discard"


# === INPUT LOADING ===

class InputBundle(NamedTuple):
    mode: str
    timestamp: str
    dump_content: str
    experiments: list
    posted_history: str
    voice_rules: str


def load_inputs() -> Optional[InputBundle]:
    """Load all inputs for the current batch."""
    mode = get_mode_from_time()
    if mode is None:
        print("[v3] Outside posting window (6-23 IST)")
        return None
    
    print(f"[v3] Loading inputs for {mode} batch...")
    
    # Load dump
    dump_file = get_dump_file(mode)
    dump_content = ""
    if dump_file:
        dump_content = safe_read_text_file(dump_file)
        print(f"[v3] Dump loaded: {len(dump_content)} chars")
    else:
        print(f"[v3] No {mode}_dump.txt found")
    
    # Scan experiments
    experiments = []
    if ENABLE_EXPERIMENTS:
        experiments = scan_experiments_inbox(max_folders=5)
        valid = sum(1 for e in experiments if not e.skip_reason)
        skipped = sum(1 for e in experiments if e.skip_reason)
        print(f"[v3] Experiments: {valid} valid, {skipped} skipped")
    
    # Load posted history
    posted_history = safe_read_text_file(LOG_FILE)
    
    # Load voice rules
    voice_rules = safe_read_text_file(PROCESS_FILE)
    
    return InputBundle(
        mode=mode,
        timestamp=now_ist_string(),
        dump_content=dump_content,
        experiments=experiments,
        posted_history=posted_history,
        voice_rules=voice_rules
    )


# === CANDIDATE PREPARATION ===

def split_dump_into_blocks(dump_content: str, max_blocks: int = 15) -> list[str]:
    """Split dump content into coarse text blocks."""
    if not dump_content:
        return []
    
    # Split on common separators - use lines starting with markers as potential block starts
    blocks = []
    current = []
    
    for line in dump_content.split("\n"):
        line = line.strip()
        
        # Blank line cluster = block boundary
        if not line:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        
        # Skip very short lines and URLs
        if len(line) < 20 or line.startswith("http"):
            continue
        
        current.append(line)
    
    # Last block
    if current:
        blocks.append("\n".join(current))
    
    # Limit and return
    return blocks[:max_blocks]


def prepare_candidates(bundle: InputBundle) -> list[Candidate]:
    """Prepare candidates from inputs."""
    candidates = []
    cand_id = 0
    
    # Process experiments
    for exp in bundle.experiments:
        if exp.skip_reason:
            continue
        
        cand_id += 1
        raw_text = f"{exp.title}. {exp.what_was_tested}. {exp.result}. {exp.key_observation}"
        
        lane_keywords = match_lane_keywords(raw_text)
        lane_match = len(lane_keywords) > 0
        first_party = first_party_strength_from_text("experiment", raw_text)
        visual_state = "required_present" if exp.has_visual else "required_missing"
        freshness = compute_freshness(raw_text)
        
        candidate = Candidate(
            id=f"cand_{cand_id:03d}",
            source=f"experiment:{exp.folder_name}",
            source_type="experiment",
            first_party_strength=first_party,
            raw_text=raw_text,
            lane_match=lane_match,
            lane_keywords=lane_keywords,
            freshness_score=freshness,
            visual_state=visual_state,
            candidate_type="",
            computed_score=0,
            reason_codes=[]
        )
        candidate = Candidate(
            **{**candidate._asdict(), "computed_score": compute_score(candidate)}
        )
        candidate_type = assign_candidate_type(candidate)
        candidates.append(Candidate(
            **{**candidate._asdict(), "candidate_type": candidate_type}
        ))
    
    # Process dump blocks
    dump_blocks = split_dump_into_blocks(bundle.dump_content, max_blocks=15)
    for block in dump_blocks:
        if not block or len(block) < 50:
            continue
        
        cand_id += 1
        lane_keywords = match_lane_keywords(block)
        lane_match = len(lane_keywords) > 0
        first_party = first_party_strength_from_text("dump", block)
        visual_state = detect_visual_state("dump", [], block)
        freshness = compute_freshness(block)
        
        candidate = Candidate(
            id=f"cand_{cand_id:03d}",
            source="dump",
            source_type="reddit" if "reddit" in block.lower() else "rss",
            first_party_strength=first_party,
            raw_text=block[:500],  # Truncate for scoring
            lane_match=lane_match,
            lane_keywords=lane_keywords,
            freshness_score=freshness,
            visual_state=visual_state,
            candidate_type="",
            computed_score=0,
            reason_codes=[]
        )
        candidate = Candidate(
            **{**candidate._asdict(), "computed_score": compute_score(candidate)}
        )
        candidate_type = assign_candidate_type(candidate)
        candidates.append(Candidate(
            **{**candidate._asdict(), "candidate_type": candidate_type}
        ))
    
    return candidates


# === DIAGNOSTICS AND TELEGRAM ===

def print_candidate_diagnostics(candidates: list[Candidate], bundle: InputBundle):
    """Print console diagnostics."""
    by_type = {"original": 0, "thread": 0, "qt_reply": 0, "discard": 0}
    for c in candidates:
        by_type[c.candidate_type] = by_type.get(c.candidate_type, 0) + 1
    
    top_orig_score = 0
    top_orig_source = ""
    for c in candidates:
        if c.candidate_type == "original" and c.computed_score > top_orig_score:
            top_orig_score = c.computed_score
            top_orig_source = c.source
    
    top_qt_score = 0
    top_qt_source = ""
    for c in candidates:
        if c.candidate_type == "qt_reply" and c.computed_score > top_qt_score:
            top_qt_score = c.computed_score
            top_qt_source = c.source
    
    print(f"[v3] mode={bundle.mode} | candidates={len(candidates)}")
    print(f"[v3] candidates -> original:{by_type['original']}  qt_reply:{by_type['qt_reply']}  thread:{by_type['thread']}  discard:{by_type['discard']}")
    if top_orig_score > 0:
        print(f"[v3] top original score: {top_orig_score} (source={top_orig_source})")
    if top_qt_score > 0:
        print(f"[v3] top qt_reply score: {top_qt_score} (source={top_qt_source})")


def build_batch_header(mode: str, is_shadow: bool = False) -> str:
    """Build batch header message."""
    shadow_tag = " [v3 TEST — do not post]" if is_shadow else ""
    return f"[v3 — {mode} batch]{shadow_tag}"


def build_section_header(section: str) -> str:
    """Build section header."""
    return f"═══ {section} ═══"


def package_outputs(originals: list, qt_replies: list, threads: list, mode: str, is_shadow: bool = False) -> list[str]:
    """Package outputs for Telegram delivery."""
    messages = []
    
    # Batch header
    messages.append(build_batch_header(mode, is_shadow))
    messages.append("")
    
    # Original posts section
    if originals:
        messages.append(build_section_header("ORIGINAL POSTS"))
        messages.append("(image required)")
        messages.append("")
        for orig in originals:
            messages.append(short_snippet(orig.raw_text, 200))
            messages.append("")
    
    # QT/reply section
    if qt_replies:
        messages.append(build_section_header("QT / REPLY DRAFTS"))
        messages.append("")
        for qt in qt_replies:
            messages.append(short_snippet(qt.raw_text, 200))
            messages.append("")
    
    # Threads section
    if threads and ENABLE_THREADS:
        messages.append(build_section_header("THREAD CANDIDATES"))
        messages.append("")
    
    return messages


# === MAIN ===

def main():
    """Main v3 pipeline."""
    print(f"[v3] Starting v3 post bot at {now_ist_string()}...")
    
    # Load inputs
    bundle = load_inputs()
    if bundle is None:
        return
    
    # Prepare candidates
    candidates = prepare_candidates(bundle)
    
    # Print diagnostics
    print_candidate_diagnostics(candidates, bundle)
    
    # Group by type
    originals = [c for c in candidates if c.candidate_type == "original"]
    qt_replies = [c for c in candidates if c.candidate_type == "qt_reply"]
    threads = [c for c in candidates if c.candidate_type == "thread"]
    
    print(f"[v3] Ready for generation: {len(originals)} originals, {len(qt_replies)} qt_replies, {len(threads)} threads")
    
    # Package for Telegram (placeholder - generation not implemented yet)
    messages = package_outputs(originals, qt_replies, threads, bundle.mode, SHADOW_MODE)
    
    print("[v3] Telegram packaging ready (generation not yet implemented)")


if __name__ == "__main__":
    main()