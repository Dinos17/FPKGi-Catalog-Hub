import json
import urllib.request
import ssl

# Παράκαμψη SSL ελέγχων για το Archive.org & GitHub
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

CATEGORIES = {
    "games": [
        "https://raw.githubusercontent.com/gop753811-netizen/fpkgi/main/GAMES-archive.org.json",
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/GAMES.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/GAMES.json"
    ],
    "updates": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/UPDATES.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/UPDATES.json"
    ],
    "dlc": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/DLC.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/DLC.json"
    ],
    "homebrew": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/HOMEBREW.json"
    ],
    "demos": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/DEMOS.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/DEMO.json"
    ],
    "emulators": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/EMULATORS.json"
    ],
    "themes": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/THEMES.json"
    ],
    "ps1": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PS1.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/PS1.json"
    ],
    "ps2": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PS2.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/PS2.json"
    ],
    "psp": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PSP.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/PSP.json"
    ],
    "apps": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/APPS.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/APPS.json"
    ]
}

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def get_items_list(data):
    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        for key in ['items', 'games', 'pkgs', 'data', 'files']:
            if key in data and isinstance(data[key], list):
                return data[key]
        for val in data.values():
            if isinstance(val, list):
                return val
    return []

for category, urls in CATEGORIES.items():
    merged_items = []
    seen_urls = set()

    print(f"--- Processing {category} ---")
    for url in urls:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25, context=ssl_context) as response:
                raw_data = response.read().decode('utf-8')
                data = json.loads(raw_data)
                items = get_items_list(data)
                
                added_count = 0
                for item in items:
                    pkg_url = item.get('pkg_url') if isinstance(item, dict) else None
                    if pkg_url and pkg_url not in seen_urls:
                        seen_urls.add(pkg_url)
                        merged_items.append(item)
                        added_count += 1
                    elif not pkg_url and item not in merged_items:
                        merged_items.append(item)
                        added_count += 1
                
                print(f"Success: {url} -> {len(items)} items found ({added_count} new)")
        except Exception as e:
            print(f"Error fetching {url}: {e}")

    print(f"Total merged for {category}: {len(merged_items)}")
    with open(f"{category}.json", "w", encoding="utf-8") as f:
        json.dump(merged_items, f, indent=2, ensure_ascii=False)
