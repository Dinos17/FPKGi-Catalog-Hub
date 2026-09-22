import json
import re
import ast
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
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/UPDATES.json"
    ],
    "dlc": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/DLC.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/DLC.json"
    ],
    "homebrew": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/HOMEBREW.json"
    ],
    "demos": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/DEMOS.json"
    ],
    "emulators": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/EMULATORS.json"
    ],
    "themes": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/THEMES.json"
    ],
    "ps1": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PS1.json"
    ],
    "ps2": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PS2.json"
    ],
    "psp": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PSP.json"
    ],
    "apps": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/APPS.json"
    ]
}

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def parse_json_robust(text):
    text = text.lstrip('\ufeff').strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    # Καθαρισμός σχολίων και trailing commas
    cleaned = re.sub(r'//.*', '', text)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r',(?=\s*[\}\]])', '', cleaned)

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Fallback σε Python literal evaluation αν έχει single quotes ή Python dict format
    try:
        py_text = cleaned.replace('true', 'True').replace('false', 'False').replace('null', 'None')
        return ast.literal_eval(py_text)
    except Exception:
        pass

    return None

def extract_items(data):
    if isinstance(data, list):
        return data
    
    if isinstance(data, dict):
        # 1. Έλεγχος για κλειδιά-κοντέινερ
        for key in ['items', 'games', 'pkgs', 'data', 'files', 'list', 'updates', 'dlc']:
            if key in data and isinstance(data[key], list):
                return data[key]
        
        # 2. Έλεγχος αν τα values του dict είναι τα ίδια τα αντικείμενα (π.χ. {"CUSA001": {...}})
        dict_vals = list(data.values())
        if dict_vals and all(isinstance(v, dict) for v in dict_vals[:10]):
            first_val = dict_vals[0]
            if any(k in first_val for k in ['pkg_url', 'name', 'url', 'title_id', 'title', 'link']):
                return dict_vals
        
        # 3. Αναδρομικός έλεγχος
        for val in dict_vals:
            if isinstance(val, list):
                return val
            elif isinstance(val, dict):
                sub = extract_items(val)
                if sub:
                    return sub
    return []

def get_item_url(item):
    if isinstance(item, dict):
        for key in ['pkg_url', 'pkg_direct_link', 'url', 'link', 'pkg', 'download']:
            if key in item and item[key]:
                return str(item[key])
    return None

for category, urls in CATEGORIES.items():
    merged_items = []
    seen_urls = set()

    print(f"\n--- Processing: {category.upper()} ---", flush=True)
    for url in urls:
        try:
            res = requests.get(url, headers=headers, timeout=20, verify=False, allow_redirects=True)
            if res.status_code == 200:
                data = parse_json_robust(res.text)
                if data is None:
                    print(f"FAILED PARSING: {url}", flush=True)
                    continue
                
                items = extract_items(data)
                added = 0
                for item in items:
                    pkg_url = get_item_url(item)
                    if pkg_url and pkg_url not in seen_urls:
                        seen_urls.add(pkg_url)
                        merged_items.append(item)
                        added += 1
                    elif not pkg_url and isinstance(item, dict) and item not in merged_items:
                        merged_items.append(item)
                        added += 1
                
                print(f"SUCCESS: {url} | Found: {len(items)} items | New Added: {added}", flush=True)
            else:
                print(f"HTTP ERROR {res.status_code}: {url}", flush=True)
        except Exception as e:
            print(f"FAILED: {url} | Error: {e}", flush=True)

    print(f"TOTAL MERGED ({category}): {len(merged_items)}", flush=True)
    with open(f"{category}.json", "w", encoding="utf-8") as f:
        json.dump(merged_items, f, indent=2, ensure_ascii=False)
