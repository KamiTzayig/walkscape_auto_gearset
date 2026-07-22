#!/usr/bin/env python3
"""
Scrape pets from Walkscape wiki.
Generates pets.json using Pydantic models.
"""

import json
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import unquote

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from models import (
    Pet, PetLevel, PetAbility, Modifier, StatName, 
    Condition, ConditionType
)
from scraper_utils import *

# Configuration
RESCRAPE = True
PETS_URL = 'https://wiki.walkscape.app/wiki/Pets'
CACHE_DIR = get_cache_dir('pets')
CACHE_FILE = get_cache_file('pets_cache.html')
OUTPUT_FILE = get_output_file('pets.json')
SCAN_FOLDER_FOR_NEW_ITEMS = True

validator = ScraperValidator()

def normalize_id(text: str) -> str:
    """Normalize text to snake_case ID."""
    if not text: return "none"
    text = unquote(text)
    text = text.replace('Special:MyLanguage/', '')
    return text.lower().replace("'", "").replace("-", "_").replace(" ", "_").strip()

def parse_attribute_lines(lines) -> list[Modifier]:
    """
    Parse a list of attribute strings into Modifier objects.
    Logic adapted from scrape_equipment to ensure consistency.
    """
    modifiers = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or 'None' in line:
            i += 1
            continue

        line_lower = line.lower()
        conditions = []
        
        # Look ahead for conditions (Location, Skill, etc.)
        next_i = i + 1
        
        while next_i < len(lines):
            next_line = lines[next_i].strip()
            next_lower = next_line.lower()

            # Location Check
            loc_text, is_negated = extract_location_from_text(next_line)
            if loc_text:
                conditions.append(Condition(type=ConditionType.LOCATION, target=normalize_location_name(loc_text)))
                next_i += 1
                continue
            
            # Inline "While doing" check on next line
            if next_lower.startswith('while doing'):
                act_match = re.search(r'while doing\s+(\w+)', next_lower)
                if act_match:
                    activity = act_match.group(1).lower()
                    if activity in ACTIVITY_KEYWORDS:
                        conditions.append(Condition(type=ConditionType.SPECIFIC_ACTIVITY, target=activity))
                    else:
                        conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target=activity))
                next_i += 1
                continue
            
            # Skill groupings
            if 'gathering skills' in next_lower:
                conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target='gathering'))
                next_i += 1
                continue
            if 'artisan skills' in next_lower:
                conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target='artisan'))
                next_i += 1
                continue
            
            # Generic "while doing X" on next line not caught by previous checks
            skill_context = extract_skill_from_text(next_line)
            if skill_context and skill_context != 'global' and len(next_line.split()) < 4:
                 conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target=skill_context))
                 next_i += 1
                 continue
                 
            break

        # Check current line for conditions too
        if 'while doing' in line_lower:
            act_match = re.search(r'while doing\s+(\w+)', line_lower)
            if act_match:
                activity = act_match.group(1).lower()
                if activity in ACTIVITY_KEYWORDS:
                    conditions.append(Condition(type=ConditionType.SPECIFIC_ACTIVITY, target=activity))
                else:
                    conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target=activity))

        if 'gathering skills' in line_lower:
            if not any(c.target == 'gathering' for c in conditions):
                conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target='gathering'))
        elif 'artisan skills' in line_lower:
             if not any(c.target == 'artisan' for c in conditions):
                conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target='artisan'))
        else:
             skill_context = extract_skill_from_text(line)
             if skill_context and skill_context != 'global':
                 if not any(c.type == ConditionType.SKILL_ACTIVITY and c.target == skill_context for c in conditions):
                     conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target=skill_context))

        # Clean line for value parsing
        clean_line = re.sub(r'while doing\s+\w+', '', line_lower)
        clean_line = re.sub(r'while in\s+[\w\s]+', '', clean_line)
        clean_line = re.sub(r'gathering skills', '', clean_line)
        clean_line = re.sub(r'artisan skills', '', clean_line)
        
        value_match = re.search(r'([+-]?\d+(?:\.\d+)?)\s*(%?)', clean_line)
        
        if value_match:
            value_str = value_match.group(1)
            is_percent = value_match.group(2) == '%'
            
            # Remove the numbers to find the stat name
            stat_text = re.sub(r'[+-]?\d+(?:\.\d+)?%?', '', clean_line).strip()
            raw_stat_name = normalize_stat_name(stat_text)
            
            if raw_stat_name:
                final_stat_key = raw_stat_name
                if raw_stat_name in DUAL_FORMAT_STATS:
                     final_stat_key = f"{raw_stat_name}_{'percent' if is_percent else 'add'}"

                try:
                    stat_enum = StatName(final_stat_key)
                    if not conditions:
                        conditions.append(Condition(type=ConditionType.GLOBAL))
                    
                    modifiers.append(Modifier(stat=stat_enum, value=float(value_str), conditions=conditions))
                except ValueError:
                    validator.add_unrecognized_stat("Parsing", f"Enum conversion failed: {final_stat_key}")
            else:
                if len(clean_line) > 3:
                     validator.add_unrecognized_stat("Parsing", line)
        
        i = next_i
        
    return modifiers

def parse_pets_list():
    """Parse the main pets page to get list of pets."""
    html = download_page(PETS_URL, CACHE_FILE, rescrape=RESCRAPE)
    if not html: return []
    
    soup = BeautifulSoup(html, 'html.parser')
    pets = []
    
    # Strategy: Find the main table with "Pet Name" header
    target_table = None
    tables = soup.find_all('table', class_='wikitable')
    
    for table in tables:
        headers = [th.get_text(strip=True) for th in table.find_all('th')]
        if 'Pet Name' in headers:
            target_table = table
            break
            
    if target_table:
        print("Found Pets table.")
        rows = target_table.find_all('tr')[1:] # Skip header
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 2: continue
            
            link_cell = cells[1]
            link = link_cell.find('a')
            
            if link:
                raw_href = link.get('href', '')
                clean_href = raw_href.replace('/Special:MyLanguage', '')
                url = 'https://wiki.walkscape.app' + clean_href
                name = clean_text(link.get_text())
                
                if name:
                    pets.append({'name': name, 'url': url})
    else:
        print("Warning: Could not find 'Pet Name' table. Fallback to generic link search.")
        content = soup.find('div', class_='mw-parser-output')
        if content:
            for link in content.find_all('a'):
                href = link.get('href', '')
                if not href.startswith('/wiki/'): continue
                
                if '/wiki/Special:' in href and 'MyLanguage' not in href: continue
                
                # Strict exclusions
                if any(skip in href.lower() for skip in ['file:', 'category:', 'pets', 'activities', 'egg', 'gear_sets', 'shops']):
                    continue
                
                name = clean_text(link.get_text())
                if not name or len(name) < 3: continue
                
                # Strict name exclusions
                if name.lower() in ['pets', 'pet eggs', 'activities', 'shops', 'arenum', 'recipes']: continue
                
                clean_href = href.replace('/Special:MyLanguage', '')
                url = 'https://wiki.walkscape.app' + clean_href
                pets.append({'name': name, 'url': url})

    seen_urls = set()
    unique_pets = []
    for pet in pets:
        if pet['url'] not in seen_urls:
            seen_urls.add(pet['url'])
            unique_pets.append(pet)
    
    return unique_pets

def parse_pet_page(pet_info) -> Optional[Pet]:
    name = pet_info['name']
    
    if pet_info.get('from_folder'):
        html = read_cached_html(pet_info['cache_file'])
    else:
        slug = pet_info['url'].split('/')[-1]
        cache_file = CACHE_DIR / (sanitize_filename(slug) + '.html')
        html = download_page(pet_info['url'], cache_file, rescrape=RESCRAPE)
        
    if not html: return None
    soup = BeautifulSoup(html, 'html.parser')

    # Basic Info
    egg_name = None
    xp_desc = None
    
    intro = soup.find('div', class_='mw-parser-output')
    if intro:
        for p in intro.find_all('p'):
            text = p.get_text()
            if 'hatches from' in text.lower() or 'egg' in text.lower():
                egg_link = p.find('a', href=re.compile(r'egg', re.I))
                if egg_link:
                    egg_name = clean_text(egg_link.get_text())
                    break

    # Find "Requirement To Gain Experience"
    req_header = soup.find(lambda tag: tag.name in ['h1', 'h2'] and 'Requirement To Gain Experience' in tag.get_text())
    if req_header:
        current = req_header.next_sibling
        while current:
            if current.name == 'p' and current.get_text().strip():
                xp_desc = clean_text(current.get_text())
                break
            # Handle the case where it's a list immediately
            if current.name == 'ul':
                xp_desc = clean_text(current.get_text())
                break
            if current.name in ['h1', 'h2']: break
            current = current.next_sibling

    levels_data = {} # level -> {total_xp, modifiers, abilities}

    # 1. Parse XP Table / Text (Experience To Hatch/Grow)
    for h2 in soup.find_all('h2'):
        h2_text = clean_text(h2.get_text())
        if 'Experience To' in h2_text:
            parent = h2.find_parent()
            search_start = parent if parent else h2
            ul = search_start.find_next_sibling('ul')
            
            if ul:
                for li in ul.find_all('li'):
                    text = clean_text(li.get_text())
                    lvl_match = re.search(r'level\s+(\d+)', text, re.I)
                    
                    total_xp = 0
                    total_match = re.search(r'\(([\d,]+)\s+total', text)
                    if total_match:
                        total_xp = int(total_match.group(1).replace(',', ''))
                    
                    if lvl_match:
                        lvl = int(lvl_match.group(1))
                        if lvl not in levels_data: levels_data[lvl] = {'total_xp': 0, 'modifiers': [], 'abilities': []}
                        if total_xp > 0: levels_data[lvl]['total_xp'] = total_xp

    # 2. Parse Attributes Table(s) - Iterate ALL "Attributes" headers
    attr_headers = soup.find_all(lambda tag: tag.name in ['h1', 'h2'] and 'Attributes' in tag.get_text())
    
    for attr_header in attr_headers:
        parent = attr_header.find_parent()
        search_start = parent if parent else attr_header
        
        # Find next table
        table = search_start.find_next_sibling('table')
        if not table:
            # Fallback scan for table
            curr = search_start.next_sibling
            while curr and curr.name != 'table' and curr.name not in ['h1', 'h2']:
                curr = curr.next_sibling
            if curr and curr.name == 'table':
                table = curr

        if table:
            rows = table.find_all('tr')[1:]
            for row in rows:
                cells = row.find_all('td')
                if len(cells) < 2: continue
                
                try:
                    lvl = int(clean_text(cells[0].get_text()))
                except: continue
                
                attr_cell = cells[1]
                for br in attr_cell.find_all('br'): br.replace_with('\n')
                lines = [l.strip() for l in attr_cell.get_text().split('\n') if l.strip()]
                
                mods = parse_attribute_lines(lines)
                
                if lvl not in levels_data: levels_data[lvl] = {'total_xp': 0, 'modifiers': [], 'abilities': []}
                # Append mods, don't overwrite if we somehow hit the same level twice (unlikely but safe)
                levels_data[lvl]['modifiers'].extend(mods)

    # 3. Parse Abilities - Iterate ALL "Ability" headers
    ability_headers = soup.find_all(lambda tag: tag.name in ['h1', 'h2'] and 'Ability' in tag.get_text())
    
    for ab_header in ability_headers:
        parent = ab_header.find_parent()
        curr = (parent if parent else ab_header).find_next_sibling()
        
        # Look forward until next header
        while curr:
            if curr.name in ['h1', 'h2'] or (curr.name == 'div' and 'mw-heading' in curr.get('class', [])):
                break
                
            if curr.name == 'p':
                text = clean_text(curr.get_text())
                lvl_match = re.search(r'level\s+(\d+)', text, re.I)
                if lvl_match:
                    lvl = int(lvl_match.group(1))
                    
                    # Ability table usually follows
                    ab_table = curr.find_next_sibling('table')
                    if ab_table:
                        # Parse Name
                        ab_name = "Unknown"
                        caption = ab_table.find('caption')
                        if caption:
                            ab_name = clean_text(caption.get_text())
                        
                        # Parse Details
                        rows = ab_table.find_all('tr')
                        if len(rows) >= 2:
                            cols = rows[1].find_all(['th', 'td'])
                            # Sometimes table format varies slightly, verify cols
                            if len(cols) >= 5:
                                effect = clean_text(cols[1].get_text())
                                reqs = clean_text(cols[2].get_text())
                                cd_str = clean_text(cols[3].get_text())
                                chg_str = clean_text(cols[4].get_text())
                                
                                if 'no requirement' in reqs.lower(): reqs = None
                                if 'no cooldown' in cd_str.lower(): cd_str = None
                                
                                charges = None
                                if chg_str and 'infinite' not in chg_str.lower():
                                    c_match = re.search(r'(\d+)', chg_str)
                                    if c_match: charges = int(c_match.group(1))

                                ability = PetAbility(
                                    name=ab_name,
                                    effect=effect,
                                    requirements=reqs,
                                    cooldown=cd_str,
                                    charges=charges
                                )
                                
                                if lvl not in levels_data: levels_data[lvl] = {'total_xp': 0, 'modifiers': [], 'abilities': []}
                                levels_data[lvl]['abilities'].append(ability)

                        # Check for passive modifier rows inside ability table (e.g. Mummy's Global +7% Find Linens)
                        for extra_row in rows[2:]:
                            text_lines = [l.strip() for l in extra_row.get_text().split('\n') if l.strip()]
                            extra_mods = parse_attribute_lines(text_lines)
                            if extra_mods:
                                if lvl not in levels_data: levels_data[lvl] = {'total_xp': 0, 'modifiers': [], 'abilities': []}
                                for em in extra_mods:
                                    if not any(m.stat == em.stat and m.value == em.value for m in levels_data[lvl]['modifiers']):
                                        levels_data[lvl]['modifiers'].append(em)
            curr = curr.find_next_sibling()

    # Construct Object
    pet_levels = []
    sorted_lvls = sorted(levels_data.keys())
    
    for lvl in sorted_lvls:
        data = levels_data[lvl]
        pet_levels.append(PetLevel(
            level=lvl,
            total_xp=data['total_xp'],
            modifiers=data['modifiers'],
            abilities=data['abilities']
        ))

    wiki_slug = pet_info['url'].split('/')[-1] if pet_info.get('url') else name.replace(' ', '_')
    
    return Pet(
        id=normalize_id(name),
        wiki_slug=wiki_slug,
        name=name,
        egg_item_id=normalize_id(egg_name),
        xp_requirement_desc=xp_desc,
        levels=pet_levels
    )

def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    print("Step 1: finding pets...")
    pets_list = parse_pets_list()
    print(f"Found {len(pets_list)} unique pets.")
    
    if SCAN_FOLDER_FOR_NEW_ITEMS:
        folder_pets = scan_cache_folder_for_items(CACHE_DIR, CACHE_FILE)
        if folder_pets:
            pets_list = merge_folder_items_with_main_list(pets_list, folder_pets)

    all_pets = []
    print(f"\nStep 2: Parsing {len(pets_list)} pets...")
    
    for i, p_info in enumerate(pets_list):
        print(f"[{i+1}/{len(pets_list)}] {p_info['name']}...")
        try:
            pet = parse_pet_page(p_info)
            if pet:
                all_pets.append(pet)
                print(f"  -> Levels parsed: {len(pet.levels)}")
        except Exception as e:
            print(f"  Error parsing {p_info['name']}: {e}")

    print(f"\nStep 3: Exporting {len(all_pets)} pets to {OUTPUT_FILE}...")
    data = [p.model_dump(mode='json') for p in all_pets]
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    validator.report()
    print("Done.")

if __name__ == "__main__":
    main()