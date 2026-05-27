import requests, re, json, os

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

channels = [
    'https://www.youtube.com/@AIDailyBrief',
    'https://www.youtube.com/@aiexplained-official',
]

resolved = {}
for url in channels:
    try:
        r = requests.get(url, timeout=15, headers=headers)
        text = r.text
        # Try externalId pattern
        match = re.search(r'"externalId":"([^"]+)"', text)
        if match:
            resolved[url] = match.group(1)
            print(f"FOUND: {url.split('/@')[1]} -> {match.group(1)}")
            continue
        # Try channel path
        match = re.search(r'/channel/([a-zA-Z0-9_-]+)"', text)
        if match:
            resolved[url] = match.group(1)
            print(f"FOUND: {url.split('/@')[1]} -> {match.group(1)}")
            continue
        print(f"NOT FOUND: {url}")
    except Exception as e:
        print(f"ERROR {url}: {e}")

print("\nUpdating sources.json...")
base_dir = os.path.dirname(os.path.abspath(__file__))
sources_file = os.path.join(base_dir, "sources.json")

with open(sources_file, "r") as f:
    sources = json.load(f)

youtube_channels = sources.get("youtube_channels", [])
if "UChpleBmo18P08aKCIgti38g" not in youtube_channels:
    youtube_channels.append("UChpleBmo18P08aKCIgti38g")

for url, channel_id in resolved.items():
    if channel_id and channel_id not in youtube_channels:
        youtube_channels.append(channel_id)
        print(f"Added {url.split('/@')[1]} = {channel_id}")

sources["youtube_channels"] = youtube_channels
sources["youtube_pending"] = []

with open(sources_file, "w") as f:
    json.dump(sources, f, indent=2)

print("Done!")
print("YouTube channels:", sources["youtube_channels"])