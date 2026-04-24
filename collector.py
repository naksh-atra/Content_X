"""
Content Collector for @SatyaNaaksh
Collects raw content from configured sources based on schedule mode.
Morning: TechCrunch + ET CIO + scrape targets
Noon: Hacker News + Algolia + Reddit
Evening: YouTube channels
"""

import os
import sys
import json
import time
import glob
import re
import random
import requests
import feedparser
from datetime import datetime, timedelta
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

SOURCES_FILE = os.path.join(BASE_DIR, "sources.json")
DEDUPE_FILE = os.path.join(BASE_DIR, "seen_urls.json")
DUMP_DIR = os.path.join(BASE_DIR, "dump")

RSS_TIMEOUT = 15
API_TIMEOUT = 20
SCRAPE_TIMEOUT = 30
MAX_ITEMS_PER_SOURCE = 15

# Max items per source per mode
MAX_ITEMS = {
    "morning": 8,
    "noon": 4,
    "evening": 8
}


def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def is_seen(url, seen_store):
    return url in seen_store


def mark_seen(url, seen_store):
    seen_store[url] = {"seen_at": datetime.now().isoformat()}


def fetch_rss_feed(url, max_items=MAX_ITEMS_PER_SOURCE):
    items = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(url, timeout=RSS_TIMEOUT, headers=headers)
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        for entry in feed.entries[:max_items]:
            content = getattr(entry, "summary", "") or getattr(entry, "description", "")
            items.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "published": entry.get("published", ""),
                "source": url,
                "content": content
            })
    except Exception as e:
        print(f"RSS fetch failed for {url}: {e}")
    return items


def fetch_hacker_news_top(stories=20):
    items = []
    try:
        api_url = f"https://hn.algolia.com/api/v1/search?query=&tags=story&hitsPerPage={stories}"
        response = requests.get(api_url, timeout=API_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        for hit in data.get("hits", []):
            story_url = hit.get("url", "")
            if not story_url:
                story_url = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            items.append({
                "title": hit.get("title", ""),
                "url": story_url,
                "published": hit.get("created_at", ""),
                "source": "HN",
                "content": hit.get("story_text", "")
            })
    except Exception as e:
        print(f"HN fetch failed: {e}")
    return items


def fetch_reddit_rss(subreddit, max_items=MAX_ITEMS_PER_SOURCE):
    items = []
    try:
        url = f"https://www.reddit.com/r/{subreddit}/.rss"
        headers = {"User-Agent": "SatyaNaakshCollector/1.0"}
        response = requests.get(url, headers=headers, timeout=RSS_TIMEOUT)
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        for entry in feed.entries[:max_items]:
            content = getattr(entry, "summary", "") or getattr(entry, "description", "")
            items.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "published": entry.get("published", ""),
                "source": f"r/{subreddit}",
                "content": content
            })
    except Exception as e:
        print(f"Reddit fetch failed for {subreddit}: {e}")
    return items


def scrape_page(url):
    try:
        import trafilatura
        response = requests.get(url, timeout=SCRAPE_TIMEOUT)
        response.raise_for_status()
        text = trafilatura.extract(response.text, include_comments=False, include_tables=False)
        if text and len(text) > 100:
            soup = BeautifulSoup(response.text, "html.parser")
            title = soup.title.string if soup.title else ""
            return {
                "title": title,
                "url": url,
                "published": "",
                "source": urlparse(url).netloc,
                "content": text
            }
    except Exception as e:
        print(f"Page scrape failed for {url}: {e}")
    return None


def fetch_youtube_feed(channel_id):
    items = []
    try:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        response = requests.get(url, timeout=RSS_TIMEOUT)
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        for entry in feed.entries[:5]:
            content = getattr(entry, "summary", "") or getattr(entry, "description", "")
            items.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "published": entry.get("published", ""),
                "source": f"YouTube:{channel_id}",
                "content": content
            })
    except Exception as e:
        print(f"YouTube fetch failed for {channel_id}: {e}")
    return items


def resolve_youtube_channel_id(handle_or_url):
    try:
        if "channel_id=" in handle_or_url:
            return handle_or_url.split("channel_id=")[1].split("&")[0]
        if "@" in handle_or_url:
            handle = handle_or_url.split("@")[1].split("?")[0]
        else:
            handle = handle_or_url.split("/@")[1].split("?")[0]
        page_url = f"https://www.youtube.com/@{handle}"
        response = requests.get(page_url, timeout=SCRAPE_TIMEOUT)
        response.raise_for_status()
        match = re.search(r'"channelId":"([^"]+)"', response.text)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"Channel ID resolution failed for {handle_or_url}: {e}")
    return None


def extract_full_content(item):
    if item.get("content") and len(item.get("content", "")) > 100:
        return item
    if item.get("url") and not item.get("url", "").startswith("https://news.ycombinator.com"):
        result = scrape_page(item["url"])
        if result:
            item["title"] = result.get("title", item["title"])
            item["content"] = result.get("content", "")
    return item


def process_items(items, seen_store, mode="morning"):
    # Cap items per source based on mode
    max_per_mode = MAX_ITEMS.get(mode, 8)
    
    # Group by source
    by_source = {}
    for item in items:
        source = item.get("source", "unknown")
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(item)
    
    # Select random items per source
    limited_items = []
    for source, source_items in by_source.items():
        if len(source_items) > max_per_mode:
            selected = random.sample(source_items, max_per_mode)
        else:
            selected = source_items
        limited_items.extend(selected)
    
    # Continue with normal processing
    processed = []
    for item in limited_items:
        url = item.get("url", "")
        title = (item.get("title", "") or "")[:100].strip()
        if not url or not title:
            continue
        if is_seen(url, seen_store) or is_seen(title.lower(), seen_store):
            continue
        item = extract_full_content(item)
        content = (item.get("content") or "").strip()
        if content and len(content) > 50:
            mark_seen(url, seen_store)
            mark_seen(title.lower(), seen_store)
            processed.append(item)
    return processed


def format_item(item):
    parts = []
    title = (item.get("title") or "").strip()
    source = (item.get("source") or "").strip()
    content = (item.get("content") or "").strip()
    parts.append(f"[{source}] {title}")
    if content:
        content = " ".join(content.split())
        if len(content) > 2000:
            content = content[:2000]
        parts.append(content)
    return "\n".join(parts)


def write_dump_file(mode, items):
    timestamp = datetime.now().strftime("%d %b %Y %H:%M")
    output_file = os.path.join(DUMP_DIR, f"{mode}_dump.txt")
    lines = [f"Collected: {timestamp}", ""]
    for item in items:
        lines.append(format_item(item))
        lines.append("")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return len(items)


def resolve_pending_channels():
    sources = load_json(SOURCES_FILE)
    pending = sources.get("youtube_pending", [])
    resolved = []
    for handle in pending:
        channel_id = resolve_youtube_channel_id(handle)
        if channel_id:
            resolved.append(channel_id)
            print(f"Resolved {handle} -> {channel_id}")
            existing = sources.get("youtube_channels", [])
            if channel_id not in existing:
                existing.append(channel_id)
            sources["youtube_channels"] = existing
            sources["youtube_pending"] = [h for h in pending if h != handle]
    if resolved:
        save_json(SOURCES_FILE, sources)
    return resolved


def run_collector(mode):
    print(f"[{datetime.now().strftime('%H:%M')}] Starting {mode} collector...")
    seen_store = load_json(DEDUPE_FILE)
    sources = load_json(SOURCES_FILE)
    all_items = []

    if mode == "morning":
        for url in sources.get("morning_rss", []):
            all_items.extend(fetch_rss_feed(url))
        for url in sources.get("morning_scrape", []):
            result = scrape_page(url)
            if result:
                all_items.append(result)
    elif mode == "noon":
        all_items.extend(fetch_hacker_news_top())
        for sub in sources.get("reddit_subs", []):
            all_items.extend(fetch_reddit_rss(sub))
    elif mode == "evening":
        for sub in sources.get("reddit_subs_evening", []):
            all_items.extend(fetch_reddit_rss(sub))

    processed = process_items(all_items, seen_store, mode)
    save_json(DEDUPE_FILE, seen_store)
    count = write_dump_file(mode, processed)
    print(f"[{datetime.now().strftime('%H:%M')}] Done. {count} items to {mode}_dump.txt")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python collector.py <morning|noon|evening>")
        sys.exit(1)
    if sys.argv[1] == "--resolve-channels":
        resolved = resolve_pending_channels()
        print(f"Resolved {len(resolved)} channel(s)")
    else:
        run_collector(sys.argv[1])