"""
daily_blog.py — Daily blog article generator

Collects latest tech/AI developments from across the web,
generates an article in a natural personal voice,
saves to blog/YYYY-MM-DD-title.md.

Schedule: daily at 6 PM via Task Scheduler
"""

import os
import re
import time
import requests
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

OUTPUT_DIR = os.path.join(BASE_DIR, "blog")
os.makedirs(OUTPUT_DIR, exist_ok=True)

SOURCES = {
    "rss": [
        "https://techcrunch.com/feed/",
        "https://dev.to/feed",
        "https://hnrss.org/frontpage",
    ],
    "scrape": [
        "https://github.com/trending",
        "https://www.producthunt.com/",
    ],
    "reddit": [
        "artificial",
        "MachineLearning",
        "LocalLLaMA",
        "technology",
        "programming",
        "startups",
    ],
}

TIMEOUT = 20
MAX_RSS_ITEMS = 8
MAX_SOURCES_TOTAL = 40


def fetch_rss(url):
    items = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, timeout=TIMEOUT, headers=headers)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:MAX_RSS_ITEMS]:
            items.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "source": urlparse(url).netloc,
                "summary": (entry.get("summary") or entry.get("description") or "")[:500],
            })
    except Exception as e:
        print(f"  RSS fail {url}: {e}")
    return items


def fetch_hacker_news():
    items = []
    try:
        for query in ["AI", "coding"]:
            resp = requests.get(
                f"https://hn.algolia.com/api/v1/search?query={query}&tags=story&hitsPerPage=15",
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            for hit in resp.json().get("hits", []):
                items.append({
                    "title": hit.get("title", ""),
                    "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
                    "source": "HN",
                    "summary": (hit.get("story_text") or "")[:400],
                })
    except Exception as e:
        print(f"  HN fail: {e}")
    return items


def fetch_reddit(subreddit):
    items = []
    try:
        headers = {"User-Agent": "daily-blog/1.0"}
        resp = requests.get(f"https://www.reddit.com/r/{subreddit}/hot/.json?limit=10", timeout=TIMEOUT, headers=headers)
        resp.raise_for_status()
        for post in resp.json().get("data", {}).get("children", []):
            p = post["data"]
            items.append({
                "title": p.get("title", ""),
                "url": p.get("url", ""),
                "source": f"r/{subreddit}",
                "summary": (p.get("selftext") or "")[:400],
            })
    except Exception as e:
        print(f"  Reddit fail {subreddit}: {e}")
    return items


def scrape_page(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, timeout=20, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))[:800]
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        return {"title": title, "content": text}
    except Exception:
        return None


def collect():
    print("Collecting today's tech/AI news...")
    all_items = []

    for url in SOURCES["rss"]:
        items = fetch_rss(url)
        print(f"  {urlparse(url).netloc}: {len(items)} items")
        all_items.extend(items)

    hn = fetch_hacker_news()
    print(f"  HN: {len(hn)} items")
    all_items.extend(hn)

    for sub in SOURCES["reddit"]:
        items = fetch_reddit(sub)
        print(f"  r/{sub}: {len(items)} items")
        all_items.extend(items)

    for url in SOURCES["scrape"]:
        result = scrape_page(url)
        if result and result["content"]:
            all_items.append({
                "title": result["title"],
                "url": url,
                "source": urlparse(url).netloc,
                "summary": result["content"][:500],
            })
            print(f"  {urlparse(url).netloc}: 1 item")

    seen = set()
    unique = []
    for item in all_items:
        key = item["title"].lower().strip()[:60]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"  Total unique: {len(unique)} items")
    return unique[:MAX_SOURCES_TOTAL]


def summarize_items(items):
    parts = []
    for i, item in enumerate(items[:6], 1):
        summary = item["summary"][:300] if item["summary"] else ""
        parts.append(f"{i}. [{item['source']}] {item['title']}\n   {summary}")
    return "\n".join(parts)


def call_groq(prompt):
    if not GROQ_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 8192,
                "temperature": 0.95,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  Groq fail: {e}")
        return None


def call_gemini(prompt):
    if not GEMINI_API_KEY:
        return None
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.9},
            },
            timeout=180,
        )
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"  Gemini fail: {e}")
        return None


def generate_article(items):
    context = summarize_items(items)
    date_str = datetime.now().strftime("%d %B %Y")
    prompt = f"""Today is {date_str}. Here are the most interesting tech stories:

{context}

Write a blog article on these. Pick 2 stories and go deep on each. Write at least 1000 words.

Open with a concrete detail from one story that caught your attention. Not a greeting, not a general statement. A real detail.

For each story: explain what happened using facts from the text above. Then say why it matters. Be specific about what this means for people building things.

End by connecting the stories. What do they reveal together.

Use short paragraphs. Mix short and long sentences. No em dashes. Write naturally."""
    
    article = call_groq(prompt)
    if not article:
        article = call_gemini(prompt)
        provider = "gemini"
    else:
        provider = "groq"
    return article, provider


def clean_article(text):
    text = re.sub(r"\*\*", "", text)
    text = text.replace("\u2014", ", ").replace("\u2013", ", ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_title(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines[:5]:
        if 20 < len(line) < 120:
            return line.rstrip(".")
    return f"Tech Notes {datetime.now().strftime('%d %B %Y')}"


def save_article(text, title):
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:50].strip("-")
    filename = f"{date_str}-{slug}.md"
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n_Published {datetime.now().strftime('%d %B %Y')}_\n\n{text}")
    print(f"  Saved: {filename}")
    return filename, filepath


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message[:4000], "parse_mode": "HTML"},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        print(f"  Telegram fail: {e}")


def main():
    start = time.time()
    print(f"\n=== Daily Blog {datetime.now().strftime('%d %B %Y %H:%M')} ===\n")
    items = collect()
    if not items:
        print("No content collected. Aborting.")
        return
    article, provider = generate_article(items)
    if not article:
        print("Generation failed. Aborting.")
        return
    article = clean_article(article)
    title = extract_title(article)
    filename, filepath = save_article(article, title)
    duration = time.time() - start
    wc = len(article.split())
    print(f"\nDone. {wc} words via {provider} in {duration:.0f}s")
    print(f"File: {filepath}")
    send_telegram(f"Blog Article Ready\nTitle: {title}\nWords: {wc}\nProvider: {provider}\nFile: {filename}")


if __name__ == "__main__":
    main()
