#!/usr/bin/env python3
"""
Scrape materials from Walkscape wiki and generate materials.json.
Uses Pydantic models for strict schema validation.
"""

import json
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import unquote

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from models import Material, SpecialShopSell, Modifier, Condition, ConditionType, StatName, Requirement
from utils.constants import RequirementType
from scraper_utils import *

# Configuration
RESCRAPE = True
MATERIALS_URL = 'https://wiki.walkscape.app/wiki/Materials'
CACHE_DIR = get_cache_dir('materials')
CACHE_FILE = get_cache_file('materials_cache.html')
OUTPUT_FILE = get_output_file('materials.json')
SCAN_FOLDER_FOR_NEW_ITEMS = True

validator = ScraperValidator()

def normalize_id(text: str) -> str:
    """Normalize text to snake_case ID."""
    if not text: return "none"
    text = unquote(text)
    text = text.replace('Special:MyLanguage/', '')
    return text.lower().replace("'", "").replace("-", "_").replace(" ", "_").strip()

def parse_attribute_lines(lines, item_name="Unknown") -> list[Modifier]:
    """Parse a list of attribute strings into Modifier objects."""
    modifiers = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        line_lower = line.lower()
        if 'at least' in line_lower and 'lvl.' in line_lower:
            i += 1
            continue
        if 'walk a total amount of steps' in line_lower:
            i += 1
            continue
            
        conditions = []
        if 'have' in line_lower and 'reputation' in line_lower:
            i += 1
            continue
        
        next_i = i + 1
        
        # Look ahead for conditions
        while next_i < len(lines):
            next_line = lines[next_i].strip()
            next_lower = next_line.lower()
            
            ap_match = re.search(r'[\(\[](\d+)[\)\]]\s*achievement point', next_lower)
            if ap_match:
                conditions.append(Condition(type=ConditionType.ACHIEVEMENT_POINTS, value=int(ap_match.group(1))))
                next_i += 1
                continue

            loc_text, is_negated = extract_location_from_text(next_line)
            if loc_text:
                conditions.append(Condition(type=ConditionType.LOCATION, target=normalize_location_name(loc_text)))
                next_i += 1
                continue
                
            set_match = re.search(r'requires\s+[\(\[](\d+)[\)\]]\s+(?:unique\s+)?(.+?)\s+equipped', next_lower)
            if set_match:
                conditions.append(Condition(type=ConditionType.SET_EQUIPPED, target=set_match.group(2).strip(), value=int(set_match.group(1))))
                next_i += 1
                continue
                
            own_match = re.search(r'own (?:a|an)\s+(.+?)\.?$', next_lower)
            if own_match:
                conditions.append(Condition(type=ConditionType.ITEM_OWNERSHIP, target=own_match.group(1).strip()))
                next_i += 1
                continue

            skill_total_match = re.search(r'have a\s*[\(\[](\d+)[\)\]]\s*total skill level', next_lower)
            if skill_total_match:
                 conditions.append(Condition(type=ConditionType.TOTAL_SKILL_LEVEL, value=int(skill_total_match.group(1))))
                 next_i += 1
                 continue
            
            act_comp_match = re.search(r'have completed the\s+(.+?)\s+activity\s+[\(\[](\d+)[\)\]]\s+times', next_lower)
            if act_comp_match:
                conditions.append(Condition(
                    type=ConditionType.ACTIVITY_COMPLETION,
                    target=act_comp_match.group(1).strip(),
                    value=int(act_comp_match.group(2))
                ))
                next_i += 1
                continue

            if next_lower.startswith('while doing'):
                act_match = re.search(r'while doing\s+([a-z0-9_]+)', next_lower)
                if act_match:
                    activity = act_match.group(1)
                    if 'ACTIVITY_KEYWORDS' in globals() and activity in ACTIVITY_KEYWORDS:
                        conditions.append(Condition(type=ConditionType.SPECIFIC_ACTIVITY, target=activity))
                    else:
                        conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target=activity))
                next_i += 1
                continue
            break

        # --- Parse Stat Line ---
        if 'while doing' in line_lower:
            act_match = re.search(r'while doing\s+([a-z0-9_]+)', line_lower)
            if act_match:
                activity = act_match.group(1)
                if 'ACTIVITY_KEYWORDS' in globals() and activity in ACTIVITY_KEYWORDS:
                    conditions.append(Condition(type=ConditionType.SPECIFIC_ACTIVITY, target=activity))
                else:
                    conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target=activity))
                
        if 'experience on any action' not in line_lower:
            skill_context = extract_skill_from_text(line)
            if skill_context and skill_context != 'global':
                if not any(c.type == ConditionType.SKILL_ACTIVITY and c.target == skill_context for c in conditions):
                    conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target=skill_context))
        
        clean_line = re.sub(r'while doing\s+\w+', '', line_lower)
        value_match = re.search(r'([+-]?\d+(?:\.\d+)?)\s*(%?)', clean_line)
        
        if value_match:
            value_str = value_match.group(1)
            is_percent = value_match.group(2) == '%'
            
            if 'chance to find' in clean_line:
                if 'bird nest' in clean_line: raw_stat_name = "chance_to_find_bird_nest"
                elif 'sea shell' in clean_line: raw_stat_name = "find_sea_shells"
                elif 'skilling chest' in clean_line or 'skill chest' in clean_line: raw_stat_name = "find_skill_chest"
                elif 'rough gem' in clean_line or 'random gem' in clean_line: raw_stat_name = "find_random_gem"
                elif 'crustacean' in clean_line or 'crab' in clean_line: raw_stat_name = "find_crustacean"
                elif 'fibrous plant' in clean_line: raw_stat_name = "find_fibrous_plant"
                else: raw_stat_name = normalize_stat_name(clean_line)
            elif 'experience on any action' in clean_line:
                skill_match = re.search(r'([a-z]+)\s+experience on any action', clean_line)
                if skill_match: raw_stat_name = f"gain_{skill_match.group(1)}_xp"
                else: raw_stat_name = "bonus_xp_add"
            else:
                raw_stat_name = normalize_stat_name(clean_line)
            
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
                    validator.add_unrecognized_stat(item_name, f"Enum conversion failed: {final_stat_key}")
            else:
                validator.add_unrecognized_stat(item_name, line)
        i = next_i
    return modifiers
def parse_requirements_section(soup) -> list[Requirement]:
    """Parse the requirements section for the material's minimum level."""
    reqs = []
    req_heading = soup.find('h1', id='Requirement') or soup.find('h1', id='Requirements')
    if not req_heading: 
        return reqs
    
    parent_div = req_heading.parent
    if parent_div and 'mw-heading' in parent_div.get('class', []):
        next_elem = parent_div.find_next_sibling()
    else:
        next_elem = req_heading.find_next_sibling()
        
    while next_elem:
        # Stop parsing if we hit the next header
        if next_elem.name in ['h1', 'h2'] or (next_elem.name == 'div' and 'mw-heading' in next_elem.get('class', [])): 
            break
        
        text = next_elem.get_text()
        # Matches: "At least Hunting lvl. 30."
        skill_matches = re.findall(r'At least.*?([A-Za-z]+)\s+lvl?\.\s*(\d+)', text, re.IGNORECASE)
        for skill_name, level in skill_matches:
            reqs.append(Requirement(
                type=RequirementType.SKILL_LEVEL, 
                target=skill_name.lower(), 
                value=int(level)
            ))
            
        next_elem = next_elem.find_next_sibling()
        
    return reqs

def extract_material_details(url, name):
    """Download individual material page to get its Coin Value, Special Sell, and Input Modifiers."""
    slug = url.split('/')[-1]
    cache_file = CACHE_DIR / (sanitize_filename(slug) + '.html')
    html = download_page(url, cache_file, rescrape=RESCRAPE)
    
    value, fine_value = 0, 0
    special_sell_normal, special_sell_fine = None, None
    normal_mods, fine_mods = [], []
    
    if html:
        soup = BeautifulSoup(html, 'html.parser')
        item_reqs = parse_requirements_section(soup)
        # 1. Parse Value from Infobox
        infobox = soup.find('table', class_='ItemInfobox')
        if infobox:
            for row in infobox.find_all('tr'):
                header = row.find('th')
                if not header: continue
                text = header.get_text()
                
                if 'Value' in text and 'Fine Value' not in text:
                    val_cell = row.find('td')
                    if val_cell:
                        v_match = re.search(r'(\d+)', val_cell.get_text())
                        if v_match: value = int(v_match.group(1))
                elif 'Fine Value' in text:
                    val_cell = row.find('td')
                    if val_cell:
                        v_match = re.search(r'(\d+)', val_cell.get_text())
                        if v_match: fine_value = int(v_match.group(1))

        # 2. Parse Special Sale (Skipped here for brevity, identical to your existing code)
        special_heading = soup.find('h2', id='Special_Sale')
        if special_heading:
            table = special_heading.find_next('table', class_='wikitable')
            if table:
                rows = table.find_all('tr')[1:]
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) < 2: continue
                    item_type_cell, value_cell = cells[-2], cells[-1]
                    
                    is_fine = False
                    img = item_type_cell.find('img')
                    if img and 'Fine' in img.get('alt', ''): is_fine = True
                        
                    price_text = value_cell.get_text(strip=True)
                    qty_match = re.search(r'(\d+)', price_text)
                    if qty_match:
                        qty = int(qty_match.group(1))
                        currency_name = "Unknown"
                        link = value_cell.find('a')
                        if link: currency_name = clean_text(link.get('title', link.get_text()))
                        else:
                            curr_img = value_cell.find('img')
                            if curr_img: currency_name = clean_text(curr_img.get('alt', ''))
                        
                        special_obj = SpecialShopSell(item_id=normalize_id(currency_name), amount=qty)
                        if is_fine: special_sell_fine = special_obj
                        else: special_sell_normal = special_obj

        # 3. Parse Attributes (Input)
        attr_heading = soup.find('h1', id='Attributes_(Input)')
        if attr_heading:
            attr_table = attr_heading.find_next('table', class_='wikitable')
            if attr_table:
                rows = attr_table.find_all('tr')[1:] # Skip header
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 3:
                        qual_cell, mod_cell = cells[1], cells[2]
                        for br in mod_cell.find_all('br'): br.replace_with('\n')
                        stat_lines = [l.strip() for l in mod_cell.get_text().split('\n') if l.strip()]
                        
                        qual_text = str(qual_cell)
                        if 'Quality_None' in qual_text or 'Quality_Normal' in qual_text:
                            normal_mods = parse_attribute_lines(stat_lines, f"{name}")
                        elif 'Quality_Fine' in qual_text:
                            fine_mods = parse_attribute_lines(stat_lines, f"{name} (Fine)")

    return value, fine_value, special_sell_normal, special_sell_fine, normal_mods, fine_mods, item_reqs

def parse_materials_list():
    print("Downloading Materials page...")
    html = download_page(MATERIALS_URL, CACHE_FILE, rescrape=RESCRAPE)
    if not html: return []
    
    soup = BeautifulSoup(html, 'html.parser')
    materials = []
    
    tables = soup.find_all('table', class_='wikitable')
    print(f"Found {len(tables)} tables.")
    
    for table in tables:
        rows = table.find_all('tr')[1:] 
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 2: continue
            
            link = cells[1].find('a')
            if not link: continue
            
            name = clean_text(link.get_text())
            url = 'https://wiki.walkscape.app' + link.get('href', '')
            slug = link.get('href', '').split('/')[-1]
            
            keywords = []
            if len(cells) > 2:
                kw_cell = cells[2]
                for kw_link in kw_cell.find_all('a'):
                    if 'Keyword' in kw_link.get('title', ''):
                        keywords.append(clean_text(kw_link.get_text()))
                if not keywords:
                    raw_text = kw_cell.get_text()
                    if raw_text.strip().lower() != 'none':
                        keywords = [clean_text(k) for k in raw_text.split(',') if k.strip()]

            val, fine_val, special_norm, special_fine, n_mods, f_mods, reqs = extract_material_details(url, name)
            
            materials.append(Material(
                id=normalize_id(name), wiki_slug=slug, name=name, value=val,
                keywords=keywords, special_sell=special_norm, modifiers=n_mods,
                requirements=reqs
            ))
            
            materials.append(Material(
                id=normalize_id(name + "_fine"), wiki_slug=slug, name=f"{name} (Fine)",
                value=fine_val if fine_val else val, keywords=keywords, 
                special_sell=special_fine, modifiers=f_mods, requirements=reqs
            ))
            print(f"  Processed: {name} (Mods: N:{len(n_mods)} F:{len(f_mods)})")

    return materials

def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    materials = parse_materials_list()
    print(f"\nExporting {len(materials)} materials to {OUTPUT_FILE}...")
    data = [m.model_dump(mode='json') for m in materials]
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    validator.report()
    print("Done.")

if __name__ == "__main__":
    main()