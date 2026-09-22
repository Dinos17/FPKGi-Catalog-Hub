import json
import requests
import urllib3

# Απενεργοποίηση προειδοποιήσεων SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

def extract_items(data):
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

    print(f"\n--- Processing: {category.upper()} ---")
    for url in urls:
        try:
            res = requests.get(url, headers=headers, timeout=30, verify=False, allow_redirects=True)
            if res.status_code == 200:
                data = res.json()
                items = extract_items(data)
                added = 0
                for item in items:
                    pkg_url = item.get('pkg_url') if isinstance(item, dict) else None
                    if pkg_url and pkg_url not in seen_urls:
                        seen_urls.add(pkg_url)
                        merged_items.append(item)
                        added += 1
                    elif not pkg_url and item not in merged_items:
                        merged_items.append(item)
                        added += 1
                print(f"SUCCESS: {url} | Found: {len(items)} | New Added: {added}")
            else:
                print(f"HTTP ERROR {res.status_code}: {url}")
        except Exception as e:
            print(f"FAILED: {url} | Error: {e}")

    print(f"TOTAL MERGED ({category}): {len(merged_items)}")
    with open(f"{category}.json", "w", encoding="utf-8") as f:
        json.dump(merged_items, f, indent=2, ensure_ascii=False)
