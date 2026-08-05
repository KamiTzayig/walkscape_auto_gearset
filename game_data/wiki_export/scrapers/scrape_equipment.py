#!/usr/bin/env python3
"""
Scrape equipment data from Walkscape wiki and generate equipment.json
Uses Pydantic models for strict schema validation.
"""

import json
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import unquote

# include root folder
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from models import (
    Equipment, EquipmentQuality, EquipmentSlot, Condition, ConditionType, 
    Modifier, Requirement, RequirementType, StatName, BaseItem
)
from scraper_utils import *

# Configuration
RESCRAPE = True
CACHE_DIR = get_cache_dir('equipment')
EQUIPMENT_URL = 'https://wiki.walkscape.app/wiki/Equipment'
OUTPUT_FILE = get_output_file('equipment.json')

QUALITY_SUFFIX_MAP = {
    EquipmentQuality.NORMAL: "_COMMON",
    EquipmentQuality.GOOD: "_UNCOMMON",
    EquipmentQuality.GREAT: "_RARE",
    EquipmentQuality.EXCELLENT: "_EPIC",
    EquipmentQuality.PERFECT: "_LEGENDARY",
    EquipmentQuality.ETERNAL: "_ETHEREAL"
}

SLOT_MAPPING = {
    'tools': EquipmentSlot.TOOLS,
    'tool': EquipmentSlot.TOOLS,
    'ring': EquipmentSlot.RING,
    'neck': EquipmentSlot.NECK,
    'primary': EquipmentSlot.PRIMARY,
    'secondary': EquipmentSlot.SECONDARY,
    'head': EquipmentSlot.HEAD,
    'chest': EquipmentSlot.CHEST,
    'legs': EquipmentSlot.LEGS,
    'feet': EquipmentSlot.FEET,
    'cape': EquipmentSlot.CAPE,
    'back': EquipmentSlot.BACK,
    'hands': EquipmentSlot.HANDS,
    
}

validator = ScraperValidator()

def generate_id(wiki_slug: str, quality: EquipmentQuality) -> str:
    clean_slug = wiki_slug.replace('Special:MyLanguage/', '')
    clean_slug = unquote(clean_slug)
    base_name = clean_slug.lower().replace("'", "").replace("-", "_").replace(" ", "_")
    
    if quality == EquipmentQuality.NONE:
        return base_name.upper()
    
    suffix = QUALITY_SUFFIX_MAP.get(quality, "")
    return f"{base_name.upper()}{suffix}".lower()

def extract_equipment_links(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    equipment_links = []
    
    tables = soup.find_all('table', class_='wikitable')
    for table in tables:
        rows = table.find_all('tr')[1:] 
        for row in rows:
            cells = row.find_all('td')
            if len(cells) >= 2:
                link = cells[1].find('a')
                if link and link.get('href'):
                    item_name = link.get_text().strip()
                    item_url = 'https://wiki.walkscape.app' + link['href']
                    slug = link['href'].split('/wiki/')[-1]
                    # Extract the UUID from the data-achievement-id attribute
                    uuid = row.get('data-achievement-id', '')
                    equipment_links.append((item_name, item_url, slug, uuid))
    return equipment_links

def parse_requirements(soup) -> list[Requirement]:
    requirements = []
    req_header = soup.find('h1', id='Requirement') or soup.find('h1', id='Requirements')
    if not req_header: return requirements

    req_list = req_header.parent.find_next('ul')
    if not req_list: return requirements

    for li in req_list.find_all('li', recursive=False):
        text = li.get_text().strip()
        
        skill_match = re.search(r'At least\s+(?:(\w+)\s+lvl\.\s+(\d+)|(\d+)\s+lvl\.\s+(\w+))', text, re.IGNORECASE)
        if skill_match:
            if skill_match.group(1):
                skill, level = skill_match.group(1), int(skill_match.group(2))
            else:
                level, skill = int(skill_match.group(3)), skill_match.group(4)
            requirements.append(Requirement(type=RequirementType.SKILL_LEVEL, target=skill.lower(), value=level))
            continue
            
        char_match = re.search(r'Have character level\s+\[?(\d+)\]?', text, re.IGNORECASE)
        if char_match:
            requirements.append(Requirement(type=RequirementType.CHARACTER_LEVEL, value=int(char_match.group(1))))
            continue

        rep_match = re.search(r'Have\s*[\(\[](\d+)[\)\]]\s*([^f]+?)\s+faction\s+reputation', text, re.IGNORECASE)
        if rep_match:
            requirements.append(Requirement(
                type=RequirementType.REPUTATION,
                target=rep_match.group(2).strip().lower().replace(' ', '_'),
                value=int(rep_match.group(1))
            ))
            continue

        ap_match = re.search(r'Have\s*[\(\[](\d+)[\)\]]\s*achievement points?', text, re.IGNORECASE)
        if ap_match:
            requirements.append(Requirement(
                type=RequirementType.ACHIEVEMENT_POINTS,
                value=int(ap_match.group(1))
            ))
            continue

        # Total skill level (e.g. "Have a [500] total skill level.")
        total_skill_match = re.search(r'Have a\s*\[?(\d+)\]?\s*total skill level', text, re.IGNORECASE)
        if total_skill_match:
            requirements.append(Requirement(
                type=RequirementType.TOTAL_SKILL_LEVEL, 
                value=int(total_skill_match.group(1))
            ))
            continue

        #% towards maximum skill group level (e.g. "Have 55% towards maximum Gathering level [270].")
        max_skill_match = re.search(r'towards maximum\s+(\w+)\s+level\s*\[?(\d+)\]?', text, re.IGNORECASE)
        if max_skill_match:
            requirements.append(Requirement(
                type=RequirementType.SKILL_GROUP_LEVEL, 
                target=max_skill_match.group(1).lower(), 
                value=int(max_skill_match.group(2))
            ))
            continue

    return requirements

def parse_attribute_lines(lines, item_name="Unknown") -> list[Modifier]:
    modifiers = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        line_lower = line.lower()
        
        # Skip accidental requirement lines that slip into the attributes section
        if 'at least' in line_lower and 'lvl.' in line_lower:
            i += 1
            continue
        if 'walk a total amount of steps' in line_lower:
            i += 1
            continue
        if line_lower.startswith('explore ') and 'completely' in line_lower:
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
            
            # Skip exploration text when looking for conditions
            if next_lower.startswith('explore ') and 'completely' in next_lower:
                next_i += 1
                continue

            # AP Check
            ap_match = re.search(r'[\(\[](\d+)[\)\]]\s*achievement point', next_lower)
            if ap_match:
                conditions.append(Condition(type=ConditionType.ACHIEVEMENT_POINTS, value=int(ap_match.group(1))))
                next_i += 1
                continue

            # Location Check
            loc_text, is_negated = extract_location_from_text(next_line)
            if loc_text:
                conditions.append(Condition(type=ConditionType.LOCATION, target=normalize_location_name(loc_text)))
                next_i += 1
                continue
                
            # Set Piece Check
            set_match = re.search(r'requires\s+[\(\[](\d+)[\)\]]\s+(?:unique\s+)?(.+?)\s+equipped', next_lower)
            if set_match:
                conditions.append(Condition(type=ConditionType.SET_EQUIPPED, target=set_match.group(2).strip(), value=int(set_match.group(1))))
                next_i += 1
                continue
                
            # Item Ownership
            own_match = re.search(r'own (?:a|an)\s+(.+?)\.?$', next_lower)
            if own_match:
                conditions.append(Condition(type=ConditionType.ITEM_OWNERSHIP, target=own_match.group(1).strip()))
                next_i += 1
                continue

            # Total Skill Level
            skill_total_match = re.search(r'have a\s*[\(\[](\d+)[\)\]]\s*total skill level', next_lower)
            if skill_total_match:
                 conditions.append(Condition(type=ConditionType.TOTAL_SKILL_LEVEL, value=int(skill_total_match.group(1))))
                 next_i += 1
                 continue
            
            # Activity Completion
            act_comp_match = re.search(r'have completed the\s+(.+?)\s+activity\s+[\(\[](\d+)[\)\]]\s+times', next_lower)
            if act_comp_match:
                conditions.append(Condition(
                    type=ConditionType.ACTIVITY_COMPLETION,
                    target=act_comp_match.group(1).strip(),
                    value=int(act_comp_match.group(2))
                ))
                next_i += 1
                continue

            # Inline "While doing" check on next line
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
                
        # FIX: Do not extract a skill condition if this is an "any action" global buff
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
            
            # --- CUSTOM: Override for specific drops ---
            if 'chance to find' in clean_line:
                if 'bird nest' in clean_line:
                    raw_stat_name = "chance_to_find_bird_nest"
                elif 'sea shell' in clean_line:
                    raw_stat_name = "find_sea_shells"
                elif 'skilling chest' in clean_line or 'skill chest' in clean_line:
                    raw_stat_name = "find_skill_chest"
                elif 'rough gem' in clean_line or 'random gem' in clean_line:
                    raw_stat_name = "find_random_gem"
                elif 'crustacean' in clean_line or 'crab' in clean_line:
                    raw_stat_name = "find_crustacean"
                elif 'fibrous plant' in clean_line:
                    raw_stat_name = "find_fibrous_plant"
                else:
                    raw_stat_name = normalize_stat_name(clean_line)
                    
            # FIX: Catch the "Any Action" XP and map to the specific skill
            elif 'experience on any action' in clean_line:
                skill_match = re.search(r'([a-z]+)\s+experience on any action', clean_line)
                if skill_match:
                    raw_stat_name = f"gain_{skill_match.group(1)}_xp"
                else:
                    raw_stat_name = "bonus_xp_add"
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




def parse_item_page(html_content, item_name, slug, uuid) -> list[Equipment]:
    soup = BeautifulSoup(html_content, 'html.parser')
    keywords = []
    base_value = 0
    slot = EquipmentSlot.UNKNOWN
    values_by_quality = {}
    
    # 1. FIND ALL INFOBOXES (to capture tier-specific values)
    infoboxes = soup.find_all('table', class_='ItemInfobox')
    
    for idx, infobox in enumerate(infoboxes):
        current_quality_str = None
        current_value = 0
        
        for row in infobox.find_all('tr'):
            header = row.find('th')
            if not header: continue
            
            text = header.get_text()
            
            # --- Detect Quality Tier ---
            # Try finding quality in the main header: e.g., <th colspan="2"><b>Item</b><br/>(Normal)</th>
            if header.has_attr('colspan'):
                match = re.search(r'\((Normal|Good|Great|Excellent|Perfect|Eternal)\)', text, re.IGNORECASE)
                if match:
                    current_quality_str = match.group(1).capitalize()
            
            # Fallback: Look inside the "Quality:" row
            if 'Quality:' in text and not current_quality_str:
                q_cell = row.find('td')
                if q_cell:
                    q_link = q_cell.find('a')
                    if q_link and q_link.get('title'):
                        match = re.search(r'(Normal|Good|Great|Excellent|Perfect|Eternal)', q_link.get('title'), re.IGNORECASE)
                        if match:
                            current_quality_str = match.group(1).capitalize()

            # --- Extract Shared Properties (Only needed from the 1st infobox) ---
            if 'Slot' in text and idx == 0:
                slot_cell = row.find('td')
                if slot_cell:
                    slot_txt = slot_cell.get_text().strip().lower()
                    if 'tool' in slot_txt: slot = EquipmentSlot.TOOLS
                    elif 'ring' in slot_txt: slot = EquipmentSlot.RING
                    else: 
                        clean_slot = slot_txt.replace(' ', '_')
                        if clean_slot in SLOT_MAPPING:
                            slot = SLOT_MAPPING[clean_slot]
                        elif clean_slot in [e.value for e in EquipmentSlot]:
                            slot = EquipmentSlot(clean_slot)
            
            if 'Keyword' in text and 'Search' not in text and idx == 0:
                kw_cell = row.find('td')
                if kw_cell:
                    raw_kws = [k.get_text().strip() for k in kw_cell.find_all('a')]
                    new_keywords = [k for k in raw_kws if k]
                    if new_keywords:
                        keywords.extend(new_keywords)
            
            # --- Extract Value for THIS specific infobox ---
            if 'Value' in text and 'Fine Value' not in text:
                val_cell = row.find('td')
                if val_cell:
                    v_text = val_cell.get_text().replace(',', '') # Handle 1,000s
                    v_match = re.search(r'(\d+)', v_text)
                    if v_match: 
                        current_value = int(v_match.group(1))
                        # Save the first one as our fallback/base value
                        if idx == 0:
                            base_value = current_value
        
        # Store the value keyed by its EquipmentQuality Enum
        if current_quality_str:
            try:
                enum_q = EquipmentQuality(current_quality_str)
                values_by_quality[enum_q] = current_value
            except ValueError:
                pass

    # --- Synthesize exact_item keyword ---
    clean_slug = slug.replace('Special:MyLanguage/', '')
    clean_slug = unquote(clean_slug)
    base_name = clean_slug.lower().replace("'", "").replace("-", "_").replace(" ", "_")
    keywords.append(f"exact_item_{base_name}")

    requirements = parse_requirements(soup)
    attr_section = soup.find('h1', id='Attributes') or soup.find('h1', id='Attribute')

    if not attr_section:
        return [Equipment(
            id=generate_id(slug, EquipmentQuality.NONE),
            wiki_slug=slug,
            name=item_name,
            uuid=uuid,
            value=base_value,
            keywords=tuple(keywords), 
            slot=slot,
            quality=EquipmentQuality.NONE,
            requirements=requirements,
            modifiers=[]
        )]

    current = attr_section.parent
    results = []

    while current:
        current = current.find_next_sibling()
        if not current: break
        
        # 2. GENERATE TIERED EQUIPMENT
        if current.name == 'table' and 'wikitable' in current.get('class', []):
            rows = current.find_all('tr')[1:]
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 3:
                    q_img = cells[1].find('img')
                    quality_str = q_img.get('alt', '').strip() if q_img else "Normal"
                    try:
                        quality_enum = EquipmentQuality(quality_str)
                    except ValueError:
                        quality_enum = EquipmentQuality.NORMAL
                    
                    # Fetch tier-specific value (fallback to base_value if missing)
                    item_value = values_by_quality.get(quality_enum, base_value)
                    
                    attr_cell = cells[2]
                    for br in attr_cell.find_all('br'): br.replace_with('\n')
                    stat_lines = [l.strip() for l in attr_cell.get_text().split('\n') if l.strip()]
                    modifiers = parse_attribute_lines(stat_lines)
                    
                    results.append(Equipment(
                        id=generate_id(slug, quality_enum),
                        wiki_slug=slug,
                        name=f"{item_name} ({quality_str})",
                        uuid=uuid,
                        value=item_value,
                        keywords=tuple(keywords), 
                        slot=slot,
                        quality=quality_enum,
                        requirements=requirements,
                        modifiers=modifiers
                    ))
            break
        
        # GENERATE SINGLE/NO TIER EQUIPMENT
        elif current.name == 'p':
            for br in current.find_all('br'): br.replace_with('\n')
            stat_lines = [l.strip() for l in current.get_text().split('\n') if l.strip()]
            modifiers = parse_attribute_lines(stat_lines)
            
            results.append(Equipment(
                id=generate_id(slug, EquipmentQuality.NONE),
                wiki_slug=slug,
                name=item_name,
                uuid=uuid,
                value=base_value,
                keywords=tuple(keywords), 
                slot=slot,
                quality=EquipmentQuality.NONE,
                requirements=requirements,
                modifiers=modifiers
            ))
            break
            
    return results
def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    print("Step 1: Downloading Equipment page...")
    equipment_cache = get_cache_file('equipment_cache.html')
    equipment_html = download_page(EQUIPMENT_URL, equipment_cache, rescrape=RESCRAPE)
    
    if not equipment_html: return
    
    links = extract_equipment_links(equipment_html)
    print(f"Found {len(links)} items.")

    all_equipment = []
    for i, (name, url, slug, uuid) in enumerate(links):
        print(f"[{i+1}/{len(links)}] Processing {name}...")
        cache_file = CACHE_DIR / (sanitize_filename(name) + '.html')
        html = download_page(url, cache_file)
        if not html: continue
        try:
            items = parse_item_page(html, name, slug, uuid)
            if items:
                all_equipment.extend(items)
                print(f"  -> Extracted {len(items)} variants")
        except Exception as e:
            print(f"  !! Error parsing {name}: {e}")

    print(f"\nStep 3: Saving {len(all_equipment)} equipment entries to {OUTPUT_FILE}...")
    data = [item.model_dump(mode='json') for item in all_equipment]
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    validator.report()
    print("Done.")

if __name__ == "__main__":
    main()