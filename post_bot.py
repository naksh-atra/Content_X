"""
X Post Bot - Automated post generator for @SatyaNaaksh
Deliver posts to Telegram for manual posting on X
Uses Groq API (primary) with Gemini fallback
"""

import os
import glob
import time
import random
import requests
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
PROCESS_FILE = os.path.join(BASE_DIR, "process.txt")
LOG_FILE = os.path.join(BASE_DIR, "posted_log.txt")

# === TELEGRAM ===
def send_telegram(message):
    """Send a message to Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error sending telegram: {e}")
        return False

# === GROQ ===
def generate_posts_groq(dump_content, rules, max_retries=3):
    """Generate posts using Groq API (primary)"""
    prompt = f"""You are writing X posts for Nakshatra. Follow these rules strictly:

{rules}

Content to turn into posts:
{dump_content}

Output format:
- Just the posts, one per line
- No numbering
- No explanation
- No analysis
- If there are multiple topics, separate them clearly with double newlines
- No emojis
- No hashtags
- Use lowercase, casual tone
- Make them sharp and memorable"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 1024
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    for attempt in range(max_retries):
        try:
            print(f"Groq attempt {attempt + 1}...")
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            print(f"Groq response: {response.status_code} - {response.text[:300]}")
            
            if response.status_code == 429:
                wait_time = (2 ** attempt) * 5
                print(f"Groq rate limited. Waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            elif response.status_code != 200:
                print(f"Groq error: {response.status_code}")
                time.sleep(5)
                continue
                
            result = response.json()
            
            if "choices" in result and len(result["choices"]) > 0:
                text = result["choices"][0]["message"]["content"]
                if text:
                    print("Generated via Groq")
                    return text
                    
        except requests.exceptions.RequestException as e:
            print(f"Groq connection error: {e}")
            time.sleep(5)
            continue

    print("Groq failed, trying Gemini...")
    return None

# === GEMINI FALLBACK ===
def generate_posts_gemini(dump_content, rules, max_retries=3):
    """Generate posts using Gemini API (fallback)"""
    prompt = f"""You are writing X posts for Nakshatra. Follow these rules strictly:

{rules}

Content to turn into posts:
{dump_content}

Output format:
- Just the posts, one per line
- No numbering
- No explanation
- No analysis
- If there are multiple topics, separate them clearly with double newlines
- No emojis
- No hashtags
- Use lowercase, casual tone
- Make them sharp and memorable"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 4096
        }
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=120)
            
            if response.status_code == 429:
                wait_time = (2 ** attempt) * 10
                print(f"Gemini rate limited. Waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            elif response.status_code != 200:
                print(f"Gemini error: {response.status_code}")
                continue
                
            result = response.json()

            if "candidates" in result and len(result["candidates"]) > 0:
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                if text:
                    print("Generated via Gemini")
                    return text
                    
        except requests.exceptions.RequestException as e:
            print(f"Gemini connection error: {e}")
            continue

    print("Gemini also failed")
    return ""

# === MAIN GENERATOR ===
def generate_posts(dump_content, rules):
    """Try Groq first, fallback to Gemini"""
    # Try Groq first
    result = generate_posts_groq(dump_content, rules)
    if result:
        return result
    
    # Fallback to Gemini
    return generate_posts_gemini(dump_content, rules)

# === UTILS ===
def is_already_posted(content_line, log_file):
    """Check if content has already been posted"""
    if not os.path.exists(log_file):
        return False

    with open(log_file, "r", encoding="utf-8") as f:
        log_content = f.read().lower()
        return content_line.lower()[:50] in log_content

def update_log(topics, log_file):
    """Update the posted log"""
    timestamp = time.strftime("%d %b %Y %H:%M")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n{timestamp} | ")
        f.write(" | ".join(topics))

def archive_files(files):
    """Move processed files to archive"""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    for f in files:
        basename = os.path.basename(f)
        archive_path = os.path.join(ARCHIVE_DIR, basename)
        if os.path.exists(archive_path):
            base, ext = os.path.splitext(basename)
            counter = 1
            while os.path.exists(archive_path):
                archive_path = os.path.join(ARCHIVE_DIR, f"{base}_{counter}{ext}")
                counter += 1
        os.rename(f, archive_path)

# === MAIN ===
def main():
    print(f"[{time.strftime('%H:%M')}]Starting post bot...")

    if not os.path.exists(PROCESS_FILE):
        print("ERROR: process.txt not found")
        return

    with open(PROCESS_FILE, "r", encoding="utf-8") as f:
        rules = f.read()

    files = sorted(glob.glob(os.path.join(DUMP_DIR, "*.txt")))
    if not files:
        print("No dump files found")
        return

    print(f"Found {len(files)} file(s) to process")

    dump_content = ""
    for f in files:
        with open(f, "r", encoding="utf-8") as file:
            dump_content += f"\n--- {os.path.basename(f)} ---\n"
            dump_content += file.read()

    posts_text = generate_posts(dump_content, rules)

    if not posts_text:
        print("No posts generated")
        return

    lines = posts_text.strip().split("\n")

    send_telegram("Post on X. Don't lag.")

    topics_logged = []
    in_thread = False
    thread_parts = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.upper() == "THREAD":
            in_thread = True
            thread_parts = []
            continue
        elif line.upper() == "/THREAD":
            in_thread = False
            thread_msg = "THREAD\n" + "\n".join(thread_parts) + "\n/THREAD"
            send_telegram(thread_msg)
            topics_logged.append("Thread post")
            time.sleep(60)
            continue
        elif in_thread:
            thread_parts.append(line)
            continue

        if any(skip in line.lower() for skip in ["topic:", "option", "post", "---", "here are"]):
            continue

        send_telegram(line)
        topics_logged.append(line[:30])
        time.sleep(60)

    if topics_logged:
        update_log(topics_logged, LOG_FILE)

    archive_files(files)

    print(f"Done. Processed {len(files)} file(s)")


if __name__ == "__main__":
    main()