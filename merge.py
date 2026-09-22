import json
import urllib.request

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

headers = {'User-Agent': 'Mozilla/5.0'}

for category, urls in CATEGORIES.items():
    merged_items = []
    seen_urls = set()

    for url in urls:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as response:
                data = json.loads(response.read().decode('utf-8'))
                if isinstance(data, list):
                    for item in data:
                        pkg_url = item.get('pkg_url') if isinstance(item, dict) else None
                        if pkg_url and pkg_url not in seen_urls:
                            seen_urls.add(pkg_url)
                            merged_items.append(item)
                        elif not pkg_url and item not in merged_items:
                            merged_items.append(item)
        except Exception as e:
            print(f"Error fetching {url}: {e}")

    with open(f"{category}.json", "w", encoding="utf-8") as f:
        json.dump(merged_items, f, indent=2, ensure_ascii=False)
