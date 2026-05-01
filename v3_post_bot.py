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
DRY_RUN = False  # Set True to test without sending (set False for live)
SHADOW_MODE = True  # Set True to label output as test
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
    dash_count = int((40 - len(section)) / 2)
    return "=" * dash_count + " " + section + " " + "=" * dash_count


# === LLM GENERATION ===

def call_llm(prompt: str, max_tokens: int = 512) -> tuple[str, str, bool]:
    """Call Groq primary, Gemini fallback. Returns (output, provider, success)."""
    # Groq attempt
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": max_tokens
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            if response.status_code == 429:
                wait_time = (2 ** attempt) * 5
                time.sleep(wait_time)
                continue
            if response.status_code != 200:
                time.sleep(2)
                continue
            
            result = response.json()
            if "choices" in result and len(result["choices"]) > 0:
                text = result["choices"][0]["message"]["content"]
                if text:
                    return text.strip(), "groq", True
        except Exception as e:
            time.sleep(2)
            continue
    
    # Gemini fallback
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    gemini_payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens}
    }
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(gemini_url, json=gemini_payload, headers=headers, timeout=120)
        if response.status_code == 200:
            result = response.json()
            if "candidates" in result and len(result["candidates"]) > 0:
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                if text:
                    return text.strip(), "gemini", True
    except:
        pass
    
    return "", "none", False


def build_original_prompt(candidate: Candidate, voice_rules: str) -> str:
    """Build prompt for original builder post generation."""
    prompt = f"""You are writing SHORT ORIGINAL BUILDER POSTS for @SatyaNaaksh.

Base rules from voice layer:
{voice_rules[:500]}

The content is from a real experiment or hands-on test.
The post must feel like a builder sharing a real observation.

FORMAT:
- 2 to 4 lines
- hook -> concrete result/failure -> direct question (when natural)
- no formal structure, but one clear point only
- must reference something specific: a tool, result, failure, timing

TONE:
- lowercase, casual, sarcastic when useful
- "i tested / tried / this broke / this surprised me" energy
- no thought-leader voice

CONTENT:
- prefer operational consequences
- prefer what changes for builders in production
- never fake a result

Candidate content:
{candidate.raw_text[:600]}

Matched keywords: {', '.join(candidate.lane_keywords)}
Source: {candidate.source}

Output just the raw post, one per line if multiple, no explanation."""
    return prompt


def generate_originals(candidates: list, bundle, max_posts: int = 3) -> list:
    """Generate original posts from top candidates."""
    if not candidates:
        return []
    
    # Sort by score, take top N
    sorted_cands = sorted(candidates, key=lambda x: x.computed_score, reverse=True)[:max_posts]
    
    # Load builder prompt
    builder_prompt_path = os.path.join(BASE_DIR, "process_builder.txt")
    builder_rules = safe_read_text_file(builder_prompt_path)
    
    generated = []
    for cand in sorted_cands:
        # Skip if no visual
        if cand.visual_state == "required_missing":
            continue
        
        prompt = build_original_prompt(cand, bundle.voice_rules or builder_rules)
        output, provider, success = call_llm(prompt, max_tokens=256)
        
        if success and output and len(output) > 10:
            generated.append({
                "candidate_id": cand.id,
                "content": output,
                "source": cand.source,
                "score": cand.computed_score
            })
            print(f"[v3] Generated original from {cand.source} via {provider}")
    
    return generated


def generate_qt_replies(candidates: list, bundle, max_posts: int = 5) -> list:
    """Generate QT/reply drafts from candidates."""
    if not candidates:
        return []
    
    sorted_cands = sorted(candidates, key=lambda x: x.computed_score, reverse=True)[:max_posts]
    
    qt_prompt_path = os.path.join(BASE_DIR, "process_reply_qt.txt")
    qt_rules = safe_read_text_file(qt_prompt_path)
    
    # Default QT rules if file missing
    if not qt_rules:
        qt_rules = """Generate short QT/REPLY drafts.
- Under 280 characters
- Add original insight
- Be punchy, not bloated"""
    
    generated = []
    for cand in sorted_cands:
        prompt = f"""You are generating QT/REPLY DRAFTS for @SatyaNaaksh.

{qt_rules}

Source content:
{cand.raw_text[:400]}

Output just the draft, one per line if multiple."""
        output, provider, success = call_llm(prompt, max_tokens=140)
        
        if success and output and len(output) > 10:
            generated.append({
                "candidate_id": cand.id,
                "content": output,
                "source": cand.source
            })
    
    return generated


# === SELECTION AND VALIDATION ===

def validate_original(post_content: str, candidate) -> tuple[bool, str]:
    """Validate generated original post. Returns (is_valid, rejection_reason)."""
    # Check line count
    lines = [l.strip() for l in post_content.split("\n") if l.strip()]
    if len(lines) > 4:
        return False, "exceeds_4_lines"
    
    # Check for banned patterns
    lower = post_content.lower()
    if any(banned in lower for banned in ["#", "##", "emoji", "em dash"]):
        return False, "contains_banned_formatting"
    
    # Must name something specific (tool, time, result, comparison)
    has_specific = any(word in lower for word in [
        "ms", "seconds", "minutes", "hours", "%", "error", "failed", 
        "cursor", "copilot", "langchain", "autogen", "vscode",
        "benchmark", "latency", "tokens", "lines of code"
    ])
    if not has_specific:
        return False, "no_specific_detail"
    
    # Must have visual backing if candidate required it
    if candidate.visual_state == "required_missing":
        return False, "no_visual_backing"
    
    # Check for generic phrases
    generic_phrases = ["this is huge", "game changer", "so important", "breaking", "revolutionary"]
    if any(p in lower for p in generic_phrases):
        return False, "generic_phrasing"
    
    return True, ""


def validate_qt_reply(post_content: str) -> tuple[bool, str]:
    """Validate QT/reply post. Returns (is_valid, rejection_reason)."""
    # Must be under 280 characters
    if len(post_content) > 280:
        return False, "exceeds_280"
    
    # Check for empty engagement phrases
    lower = post_content.lower()
    empty_phrases = ["this is huge", "so important", "game changer", "let's stop overselling"]
    if any(p in lower for p in empty_phrases):
        return False, "empty_engagement"  # too thin
    
    # Check if it's just paraphrasing (no new observation added)
    # A proper QT should have numbers, comparisons, or specific counter-points
    has_observation = any(word in lower for word in [
        "%", "ms", "seconds", "compared", "but", "however", "actually",
        "i tested", "i found", "data shows", "in my experience"
    ])
    if not has_observation:
        return False, "no_original_observation"
    
    return True, ""


def is_duplicate_angle(new_text: str, posted_history: str, similarity_threshold: float = 0.6) -> bool:
    """Check if new post is too similar to recent posted content."""
    if not posted_history or not new_text:
        return False
    
    new_lower = new_text.lower()
    new_words = set(new_lower.split())
    
    # Check last 40 lines of posted history
    history_lines = posted_history.strip().split("\n")[-40:]
    
    for line in history_lines:
        if not line or "|" not in line:
            continue
        
        # Extract snippet from log line
        parts = line.split("|")
        if len(parts) >= 2:
            old_text = parts[-1].strip().lower()
            old_words = set(old_text.split())
            
            # Calculate word overlap
            if len(new_words) > 0 and len(old_words) > 0:
                intersection = new_words & old_words
                union = new_words | old_words
                similarity = len(intersection) / len(union) if union else 0
                
                if similarity > similarity_threshold:
                    return True
    
    return False


def select_outputs(
    originals: list, 
    qt_replies: list, 
    threads: list, 
    posted_history: str
) -> tuple[list, list, list]:
    """Select best outputs with caps and deduplication."""
    selected_originals = []
    selected_qt = []
    selected_threads = []
    
    # Select originals (max 3)
    for orig in originals:
        # Validate
        is_valid, reason = validate_original(orig["content"], orig.get("candidate"))
        if not is_valid:
            print(f"[v3] REJECTED original {orig['candidate_id']}: {reason}")
            continue
        
        # Duplicate check
        if is_duplicate_angle(orig["content"], posted_history):
            print(f"[v3] REJECTED original {orig['candidate_id']}: duplicate_angle")
            continue
        
        selected_originals.append(orig)
        if len(selected_originals) >= 3:
            break
    
    # Select QT/replies (max 5)
    for qt in qt_replies:
        is_valid, reason = validate_qt_reply(qt["content"])
        if not is_valid:
            print(f"[v3] REJECTED QT {qt['candidate_id']}: {reason}")
            continue
        
        if is_duplicate_angle(qt["content"], posted_history):
            print(f"[v3] REJECTED QT {qt['candidate_id']}: duplicate_angle")
            continue
        
        selected_qt.append(qt)
        if len(selected_qt) >= 5:
            break
    
    return selected_originals, selected_qt, selected_threads


# === TELEGRAM SENDING ===

def is_valid_telegram_image(path: str) -> tuple[bool, str]:
    """Validate image for Telegram. Returns (is_valid, reason)."""
    import struct
    
    # Check file exists
    if not os.path.exists(path):
        return False, "file_not_found"
    
    # Check file size (must be > 1KB)
    file_size = os.path.getsize(path)
    if file_size < 1024:
        return False, f"file_too_small_{file_size}_bytes"
    
    # Check extension
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return False, f"invalid_extension_{ext}"
    
    # Check file header magic bytes
    try:
        with open(path, "rb") as f:
            header = f.read(16)
            
        # PNG: 89 50 4E 47 0D 0A 1A 0A
        if ext == ".png":
            if not header.startswith(b'\x89PNG\r\n\x1a\n'):
                return False, "invalid_png_header"
        
        # JPEG: FF D8 FF
        elif ext in (".jpg", ".jpeg"):
            if not header.startswith(b'\xff\xd8\xff'):
                return False, "invalid_jpeg_header"
        
        # GIF: 47 49 46 38 39 61 or 47 49 46 38 37 61
        elif ext == ".gif":
            if not (header.startswith(b'GIF89a') or header.startswith(b'GIF87a')):
                return False, "invalid_gif_header"
        
        # WebP: 52 49 46 46 ... 57 45 42 50
        elif ext == ".webp":
            if not (header[:4] == b'RIFF' and header[8:12] == b'WEBP'):
                return False, "invalid_webp_header"
                
    except Exception as e:
        return False, f"read_error_{str(e)}"
    
    return True, "valid"


def find_best_image_for_post(post_content: str, candidate) -> tuple[str, str]:
    """Find best matching image for a post from experiments. Returns (image_path, validation_reason)."""
    if not candidate or candidate.source_type != "experiment":
        return "", "not_experiment_source"
    
    # Extract experiment folder from source
    if not candidate.source.startswith("experiment:"):
        return "", "invalid_source_format"
    
    exp_folder = candidate.source.replace("experiment:", "")
    exp_path = os.path.join(EXPERIMENTS_DIR, exp_folder)
    
    if not os.path.isdir(exp_path):
        return "", "exp_folder_not_found"
    
    # Find screenshots folder
    screenshots_path = os.path.join(exp_path, "screenshots")
    if not os.path.isdir(screenshots_path):
        return "", "screenshots_not_found"
    
    # Get all images
    image_extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp")
    images = [f for f in os.listdir(screenshots_path) if f.lower().endswith(image_extensions)]
    
    if not images:
        return "", "no_images_found"
    
    # Score and validate images
    post_lower = post_content.lower()
    best_image = ""
    best_score = -100
    best_reason = ""
    
    for img in images:
        score = 0
        img_lower = img.lower()
        img_path = os.path.join(screenshots_path, img)
        
        # Validate first
        is_valid, reason = is_valid_telegram_image(img_path)
        if not is_valid:
            print(f"[v3] Image rejected: {img} - {reason}")
            continue
        
        # +50 if from same experiment
        score += 50
        
        # +20 if filename matches keywords in post
        keywords = ["benchmark", "latency", "result", "output", "before", "after", "cursor", "rag", "test", "comparison", "error", "code"]
        for kw in keywords:
            if kw in img_lower or kw in post_lower:
                score += 20
        
        # +15 for primary/featured in name
        if "primary" in img_lower or "featured" in img_lower or "main" in img_lower:
            score += 15
        
        # +10 for newer files (by name containing date/number)
        if any(c.isdigit() for c in img):
            score += 10
        
        # -20 for generic names
        if img_lower in ["screenshot.png", "screenshot1.png", "screenshot2.png", "img.png", "image.png"]:
            score -= 20
        
        if score > best_score:
            best_score = score
            best_image = img_path
            best_reason = f"valid_score_{score}"
    
    # Only return if score is positive
    if best_score > 0 and best_image:
        return best_image, best_reason
    
    return "", "no_valid_image"


def send_photo_to_telegram(chat_id: str, caption: str, image_path: str) -> bool:
    """Send a photo with caption to Telegram."""
    # Defensive validation before upload
    is_valid, reason = is_valid_telegram_image(image_path)
    if not is_valid:
        print(f"[v3] Image rejected pre-upload: {os.path.basename(image_path)} - {reason}")
        return False
    
    # Truncate caption to Telegram limit (1024 chars max)
    if len(caption) > 1024:
        caption = caption[:1021] + "..."
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    file_size = os.path.getsize(image_path)
    try:
        with open(image_path, "rb") as photo:
            files = {"photo": photo}
            data = {"chat_id": chat_id, "caption": caption}
            response = requests.post(url, files=files, data=data, timeout=60)
            print(f"[v3] sendPhoto | path={os.path.basename(image_path)} | size={file_size} | status={response.status_code}")
            if response.status_code != 200:
                print(f"[v3] sendPhoto response: {response.text[:500]}")
            response.raise_for_status()
            return True
    except requests.exceptions.HTTPError as e:
        print(f"[v3] sendPhoto failed | path={os.path.basename(image_path)} | size={file_size} | status={e.response.status_code} | response={e.response.text[:300]}")
        return False
    except Exception as e:
        print(f"[v3] Telegram photo error: {e}")
        return False


def send_telegram(message: str) -> bool:
    """Send a message to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"[v3] Telegram error: {e}")
        return False


def send_to_telegram(messages: list, posts_with_images: dict, batch_label: str = "") -> int:
    """Send messages to Telegram with delays. posts_with_images maps index to image_path."""
    sent_count = 0
    
    for i, msg in enumerate(messages):
        if msg.strip():
            # Check if this message has an image
            if i in posts_with_images and posts_with_images[i]:
                image_path = posts_with_images[i]
                if send_photo_to_telegram(TELEGRAM_CHAT_ID, msg, image_path):
                    sent_count += 1
                    print(f"[v3] Sent photo: {os.path.basename(image_path)}")
            else:
                if send_telegram(msg):
                    sent_count += 1
            time.sleep(1)  # Rate limit
    
    return sent_count


# === LOGGING ===

def update_log_v3(
    posts: list,
    mode: str,
    bundle_timestamp: str
) -> None:
    """Append to posted_log.txt with richer format."""
    timestamp = bundle_timestamp
    
    for post in posts:
        post_type = post.get("type", "original")
        source = post.get("source", "unknown")
        content = post.get("content", "")[:80]
        image_used = post.get("image", "")
        
        # Determine visual state
        visual_indicator = "visual=present" if "experiment" in source else "visual=optional"
        
        # Include image info if present
        image_log = f" | image={os.path.basename(image_used)}" if image_used else ""
        
        log_line = f"{timestamp} | {mode} | type={post_type} | {visual_indicator}{image_log} | source={source} | snippet={content}...\n"
        
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line)


def package_with_generated(originals: list, qt_replies: list, threads: list, mode: str, is_shadow: bool = False) -> list[str]:
    """Package generated outputs for Telegram delivery."""
    messages = []
    
    # Batch header
    shadow_tag = " [v3 TEST]" if is_shadow else ""
    messages.append(f"[v3 {mode} batch]{shadow_tag}")
    messages.append("")
    
    # Original posts section
    if originals:
        messages.append("======== ORIGINAL POSTS ========")
        messages.append("(image required)")
        messages.append("")
        for orig in originals:
            messages.append(orig)
            messages.append("")
    else:
        messages.append("ORIGINAL POSTS: (none passed validation)")
        messages.append("")
    
    # QT/reply section
    if qt_replies:
        messages.append("======== QT / REPLY DRAFTS ========")
        messages.append("")
        for qt in qt_replies:
            messages.append(qt)
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
    
    print(f"[v3] Generating from {len(originals)} originals, {len(qt_replies)} qt_replies...")
    
    # Generate posts (skip in DRY_RUN)
    generated_originals = []
    generated_qt = []
    
    if DRY_RUN:
        print("[v3] DRY_RUN - skipping generation")
    else:
        if originals:
            generated_originals = generate_originals(originals, bundle, max_posts=3)
        
        if qt_replies:
            generated_qt = generate_qt_replies(qt_replies, bundle, max_posts=5)
    
    # Add candidate info to generated posts for validation
    for orig in generated_originals:
        # Find matching candidate for visual check
        for c in candidates:
            if c.id == orig["candidate_id"]:
                orig["candidate"] = c
                break
    
    # Selection and validation
    selected_originals, selected_qt, _ = select_outputs(
        generated_originals, 
        generated_qt, 
        [], 
        bundle.posted_history
    )
    
    print(f"[v3] Selected: {len(selected_originals)} originals, {len(selected_qt)} qt_replies")
    
    # Find images for selected originals
    posts_with_images = {}
    for i, orig in enumerate(selected_originals):
        candidate = orig.get("candidate")
        image_path, reason = find_best_image_for_post(orig["content"], candidate)
        if image_path:
            is_valid, valid_reason = is_valid_telegram_image(image_path)
            if is_valid:
                posts_with_images[i] = image_path
                orig["image"] = image_path
                print(f"[v3] Image matched for original {i}: {os.path.basename(image_path)} ({valid_reason})")
            else:
                print(f"[v3] Image skipped for original {i}: {os.path.basename(image_path)} - {valid_reason}")
                print(f"[v3] Falling back to text-only for original {i}")
        else:
            print(f"[v3] No image found for original {i}: {reason}")
    
    # Package output
    orig_texts = [g["content"] for g in selected_originals]
    qt_texts = [g["content"] for g in selected_qt]
    messages = package_with_generated(orig_texts, qt_texts, [], bundle.mode, SHADOW_MODE)
    
    # Print sample output in DRY_RUN
    if DRY_RUN or True:  # Always show for now
        print("[v3] === Generated Output ===")
        for msg in messages[:20]:
            print(msg)
        print("[v3] === End ===")
    
    # Send to Telegram (skip in DRY_RUN)
    if not DRY_RUN and messages:
        sent = send_to_telegram(messages, posts_with_images, bundle.mode)
        print(f"[v3] Sent {sent} messages to Telegram")
        
        # Log selected posts
        all_posts = []
        for o in selected_originals:
            all_posts.append({**o, "type": "original"})
        for q in selected_qt:
            all_posts.append({**q, "type": "qt_reply"})
        
        if all_posts:
            update_log_v3(all_posts, bundle.mode, bundle.timestamp)
            print(f"[v3] Logged {len(all_posts)} posts")


if __name__ == "__main__":
    main()