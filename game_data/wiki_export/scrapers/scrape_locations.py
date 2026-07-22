#!/usr/bin/env python3
"""
Scrape locations from the Walkscape wiki.
Generates locations.json using Pydantic models.
"""

import json
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import unquote

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from models import Location
from scraper_utils import *

# Configuration
RESCRAPE = False
SCAN_FOLDER_FOR_NEW_ITEMS = True

# URLs and cache
ROUTES_URL = 'https://wiki.walkscape.app/wiki/Routes'
ROUTES_CACHE = get_cache_file('routes_cache.html')
LOCATION_CACHE_DIR = Path(get_cache_dir('locations'))
OUTPUT_FILE = get_output_file('locations.json')

# Maps specific location names or regions to extra tags
# 'syrenthia' region -> implies 'underwater' tag
# Note: Since we now scrape keywords directly, this serves as a fallback
HARD_CODED_EXTRA_TAGS = {
    'syrenthia': 'underwater',
    'wraithwater': 'spectral',
}

def normalize_id(text: str) -> str:
    """Normalize text to snake_case ID."""
    if not text: return "none"
    text = unquote(text)
    text = text.replace('Special:MyLanguage/', '')
    return text.lower().replace("'", "").replace("-", "_").replace(" ", "_").strip()

def get_location_names_from_routes():
    """Extract unique location names from the routes page."""
    html = download_page(ROUTES_URL, ROUTES_CACHE, rescrape=RESCRAPE)
    if not html: return []
    soup = BeautifulSoup(html, 'html.parser')
    
    locations = set()
    
    tables = soup.find_all('table', class_='wikitable')
    for table in tables:
        rows = table.find_all('tr')[1:]  # Skip header
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 4: continue
            
            start_loc = clean_text(cells[1].get_text())
            end_loc = clean_text(cells[3].get_text())
            
            if start_loc: locations.add(start_loc)
            if end_loc: locations.add(end_loc)
    
    return sorted(list(locations))

def parse_location_page(location_name, from_folder=False, cache_file_path=None) -> Optional[Location]:
    """Parse an individual location page to extract tags."""
    
    LOCATION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    if from_folder and cache_file_path:
        html = read_cached_html(cache_file_path)
    else:
        url = f'https://wiki.walkscape.app/wiki/{location_name.replace(" ", "_")}'
        cache_file = LOCATION_CACHE_DIR / (sanitize_filename(location_name) + '.html')
        html = download_page(url, cache_file, rescrape=RESCRAPE)
    
    if not html:
        print(f"  ⚠ Failed to get HTML for {location_name}")
        return None
    
    soup = BeautifulSoup(html, 'html.parser')
    tags = set()
    
    # 1. Extract Primary Region
    # Text: "is a location that can be found in the X region"
    region_pattern = re.compile(r'is\s+a\s+location\s+that\s+can\s+be\s+found\s+in\s+the\s+(.+?)\s+region', re.IGNORECASE)
    
    primary_region = None
    for p in soup.find_all('p'):
        text = p.get_text()
        match = region_pattern.search(text)
        if match:
            region_text = match.group(1).strip().lower()
            if 'grand duchy of trellin-erdwise' in region_text:
                primary_region = 'gdte'
            else:
                primary_region = region_text.replace(' ', '_')
            break
            
    if not primary_region:
        # Default to Jarvonia if not found (legacy fallback)
        primary_region = 'jarvonia'
        print(f"  ⚠ Could not find region for {location_name}, defaulting to jarvonia")
    
    if primary_region:
        tags.add(normalize_id(primary_region))

    # 2. Extract Keywords (NEW: Handles Spectral, Underwater, Desert, Snowy, etc.)
    # Look for "Keyword" or "Keywords" header
    keyword_heading = soup.find(['h1', 'h2'], id=re.compile(r'^Keywords?$', re.I))
    
    if keyword_heading:
        kw_div = keyword_heading.find_parent('div', class_='mw-heading')
        current = kw_div.find_next_sibling() if kw_div else keyword_heading.find_next_sibling()
        
        while current:
            # Stop at next section
            if current.name == 'div' and 'mw-heading' in current.get('class', []): break
            if current.name in ['h1', 'h2']: break
            
            # Scan links in this section (e.g. table cells)
            for link in current.find_all('a', href=True):
                href = link.get('href', '')
                
                # Filter useful keyword links
                if ('Special:MyLanguage/' in href and 
                    'File:' not in href and 
                    'Keywords' not in href): # Skip the "The following keywords apply..." link
                    
                    parts = href.split('/')
                    if len(parts) >= 2:
                        kw_raw = unquote(parts[-1]).replace("'", "").lower().replace(' ', '_')
                        if kw_raw:
                            tags.add(normalize_id(kw_raw))
                            
            current = current.find_next_sibling()

    # 3. Extract Factions
    # Look for "Faction" header and subsequent links
    faction_heading = soup.find(['h1', 'h2'], id=re.compile(r'^Factions?$', re.I))
    
    if faction_heading:
        faction_div = faction_heading.find_parent('div', class_='mw-heading')
        current = faction_div.find_next_sibling() if faction_div else faction_heading.find_next_sibling()
        
        while current:
            if current.name == 'div' and 'mw-heading' in current.get('class', []): break
            if current.name in ['h1', 'h2']: break
            
            for link in current.find_all('a', href=True):
                href = link.get('href', '')
                if ('Special:MyLanguage/' in href and 
                    'File:' not in href and 
                    'Coat_of_Arms' not in href):
                    
                    parts = href.split('/')
                    if len(parts) >= 2:
                        faction_raw = unquote(parts[-1]).replace("'", "").lower().replace(' ', '_')
                        
                        if 'reputation' in faction_raw or 'faction' in faction_raw: continue
                        if 'halfling' in faction_raw: faction_raw = 'halfling_rebels'
                        
                        if faction_raw:
                            tags.add(normalize_id(faction_raw))
            
            current = current.find_next_sibling()

    # 4. Apply Hardcoded Tags (Fallback)
    # Check existing tags to see if they imply other tags
    current_tags = list(tags)
    for tag in current_tags:
        if tag in HARD_CODED_EXTRA_TAGS:
            tags.add(HARD_CODED_EXTRA_TAGS[tag])
            
    # Check location name itself
    loc_id = normalize_id(location_name)
    if loc_id in HARD_CODED_EXTRA_TAGS:
        tags.add(HARD_CODED_EXTRA_TAGS[loc_id])

    return Location(
        id=loc_id,
        wiki_slug=location_name.replace(" ", "_"),
        name=location_name,
        tags=sorted(list(tags))
    )

def main():
    print("Step 1: Extracting locations from Routes...")
    location_names = get_location_names_from_routes()
    print(f"Found {len(location_names)} locations.")
    
    # Scan folder for extra items
    if SCAN_FOLDER_FOR_NEW_ITEMS:
        folder_locations = scan_cache_folder_for_items(LOCATION_CACHE_DIR, ROUTES_CACHE)
        if folder_locations:
            for loc in folder_locations:
                if loc['name'] not in location_names:
                    location_names.append(loc)
                    print(f"  ✓ Added from folder: {loc['name']}")

    all_locations = []
    
    print("\nStep 2: Parsing individual location pages...")
    for i, loc_item in enumerate(location_names, 1):
        if isinstance(loc_item, dict):
            loc_name = loc_item['name']
            is_folder = True
            cache_path = loc_item.get('cache_file')
        else:
            loc_name = loc_item
            is_folder = False
            cache_path = None
            
        print(f"[{i}/{len(location_names)}] Processing: {loc_name}")
        loc_obj = parse_location_page(loc_name, from_folder=is_folder, cache_file_path=cache_path)
        
        if loc_obj:
            all_locations.append(loc_obj)
            print(f"  -> Tags: {loc_obj.tags}")

    print(f"\nStep 3: Exporting {len(all_locations)} locations to {OUTPUT_FILE}...")
    data = [l.model_dump(mode='json') for l in all_locations]
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print("Done.")

if __name__ == "__main__":
    main()