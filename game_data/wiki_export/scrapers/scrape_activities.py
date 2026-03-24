#!/usr/bin/env python3
"""
Scrape Activities from Walkscape wiki and generate activities.json
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
    Activity, Requirement, RequirementType, DropEntry, 
    FactionReward, SkillName, LootTable, ActivityLootTableType
)
from scraper_utils import *

# Configuration
RESCRAPE = False
ACTIVITIES_URL = 'https://wiki.walkscape.app/wiki/Activities'
CACHE_DIR = get_cache_dir('activities')
OUTPUT_FILE = get_output_file('activities.json')
MAIN_CACHE_FILE = CACHE_DIR / 'activities_list.html'

SKIP_ACTIVITIES = ['Traveling']
validator = ScraperValidator()

# ============================================================================
# HELPERS
# ============================================================================

def normalize_id(text: str) -> str:
    """Normalize text to snake_case ID."""
    if not text: return "none"
    text = unquote(text)
    text = text.replace('Special:MyLanguage/', '')
    return text.lower().replace("'", "").replace("-", "_").replace(" ", "_").strip()

def parse_skill_enum(text: str) -> SkillName:
    try:
        return SkillName(text.lower())
    except ValueError:
        return SkillName.NONE

def clean_text(text: str) -> str:
    return ' '.join(text.split()).strip()

def parse_number(text: str) -> float:
    text = text.strip().replace('+', '')
    try:
        return float(text)
    except ValueError:
        return 0.0

def map_loot_table_type(header_text: str) -> ActivityLootTableType:
    """Map wiki header text to ActivityLootTableType enum."""
    lower = header_text.lower()
    if 'main' in lower: return ActivityLootTableType.MAIN
    if 'secondary' in lower: return ActivityLootTableType.SECONDARY
    if 'gem' in lower: return ActivityLootTableType.GEM
    return ActivityLootTableType.OTHER

def get_next_content_sibling(element):
    """
    Get the next sibling, handling MediaWiki 1.44+ header wrappers.
    If the element is a header wrapped in a div.mw-heading, return the div's sibling.
    """
    if element.parent and 'mw-heading' in element.parent.get('class', []):
        return element.parent.find_next_sibling()
    return element.find_next_sibling()

# ============================================================================
# PARSING LOGIC
# ============================================================================

def parse_activities_list():
    """Parse the main activities list to get all activity names and basic info."""
    print("Downloading activities list...")
    html = download_page(ACTIVITIES_URL, MAIN_CACHE_FILE, rescrape=RESCRAPE)
    if not html: return []
    
    soup = BeautifulSoup(html, 'html.parser')
    activities = []
    
    tables = soup.find_all('table', class_='wikitable')
    if len(tables) < 2: return []
    
    table = tables[1]
    rows = table.find_all('tr')[1:]
    
    for row in rows:
        cols = row.find_all('td')
        if len(cols) < 4: continue
        
        link = cols[1].find('a')
        if not link: continue
        
        name = link.get_text(strip=True)
        if name in SKIP_ACTIVITIES: continue
        
        href = link.get('href', '')
        url = 'https://wiki.walkscape.app' + href
        slug = href.split('/wiki/')[-1]
        
        activities.append({'name': name, 'url': url, 'slug': slug})
    
    return activities

def parse_infobox(infobox, activity_data):
    """Parse an infobox table for activity details."""
    rows = infobox.find_all('tr')
    
    for row in rows:
        header = row.find('th')
        data = row.find('td')
        
        if not header or not data: continue
        
        header_text = clean_text(header.get_text())
        data_text = clean_text(data.get_text())
        
        if 'Main Skill' in header_text:
            skill_link = data.find('a', href=re.compile(r'/wiki/(Special:MyLanguage/)?(Agility|Carpentry|Cooking|Crafting|Fishing|Foraging|Mining|Smithing|Trinketry|Woodcutting)'))
            if skill_link:
                activity_data['primary_skill'] = skill_link.get_text(strip=True)
        
        elif 'Location' in header_text:
            location_links = data.find_all('a')
            for link in location_links:
                loc_name = clean_text(link.get_text())
                if loc_name and loc_name not in activity_data['locations']:
                    activity_data['locations'].append(loc_name)
        
        elif 'Skill' in header_text and 'Level' in header_text:
            skill_links = data.find_all('a', href=re.compile(r'/wiki/(Agility|Carpentry|Cooking|Crafting|Fishing|Foraging|Mining|Smithing|Trinketry|Woodcutting)'))
            for link in skill_links:
                skill_name = link.get('title', '').strip()
                if not skill_name: continue
                parent = link.parent
                if parent:
                    text = parent.get_text()
                    level_match = re.search(r'lvl?\s*(\d+)', text, re.IGNORECASE)
                    if level_match:
                        activity_data['skill_requirements'][skill_name] = int(level_match.group(1))

        elif 'Requirements' in header_text or 'Requirement' in header_text:
            parse_requirements_text(data_text, activity_data)
        
        elif 'Base Steps' in header_text or 'Steps' in header_text:
            try:
                steps = parse_number(data_text)
                if steps: activity_data['base_steps'] = int(steps)
            except: pass
        
        elif 'Base XP' in header_text or 'Experience' in header_text:
            try:
                xp = parse_number(data_text)
                if xp: activity_data['base_xp'] = xp
            except: pass
        
        elif 'Max Efficiency' in header_text or 'Maximum Efficiency' in header_text:
            try:
                max_eff_match = re.search(r'(\d+(?:\.\d+)?)\s*%', data_text)
                if max_eff_match:
                    max_eff_pct = float(max_eff_match.group(1))
                    activity_data['max_efficiency'] = round((max_eff_pct / 100.0) - 1.0, 2)
            except: pass
        
        elif 'Reputation' in header_text and 'Faction' not in header_text:
            try:
                rep_matches = re.findall(r'([A-Za-z\s]+?)\s*[:\+]\s*\+?(\d+)', data_text)
                for faction, amount in rep_matches:
                    faction = faction.strip()
                    if faction and amount:
                        activity_data['faction_reputation'][faction] = float(amount)
            except: pass

def parse_requirements_text(req_text, activity_data):
    """Parse text requirements into the dict structure."""
    if not req_text or req_text == 'None': return

    if 'diving gear' in req_text.lower():
        match = re.search(r'(\d+)\s+diving gear', req_text, re.IGNORECASE)
        count = int(match.group(1)) if match else 1
        activity_data['requirements']['keyword_counts']['diving_gear'] = count
    
    if 'tool' in req_text.lower():
        tool_match = re.search(r'Have\s+(\w+)\s+tool\s+equipped', req_text, re.IGNORECASE)
        if tool_match:
            activity_data['requirements']['tool_equipped'] = tool_match.group(1)
        unique_match = re.search(r'(\d+)\s+unique\s+tools?', req_text, re.IGNORECASE)
        if unique_match:
            activity_data['requirements']['unique_tools'] = int(unique_match.group(1))
    
    light_match = re.search(r'(\d+)\s+(?:unique\s+)?light\s+sources?', req_text, re.IGNORECASE)
    if light_match:
        activity_data['requirements']['keyword_counts']['light_source'] = int(light_match.group(1))
    
    rep_match = re.search(r'(\d+)\s+reputation\s+with\s+([^,\.]+)', req_text, re.IGNORECASE)
    if rep_match:
        amount = int(rep_match.group(1))
        faction = clean_text(rep_match.group(2))
        activity_data['requirements']['reputation'][faction] = amount
    
    completion_match = re.search(r'completed?\s+(?:the\s+)?(.+?)\s+activity\s+\((\d+)\)\s+times', req_text, re.IGNORECASE)
    if completion_match:
        activity = clean_text(completion_match.group(1))
        count = int(completion_match.group(2))
        activity_data['requirements']['activity_completions'][activity] = count
    exact_item_match = re.search(r'Have\s+item\s+(.+?)\s+equipped', text, re.IGNORECASE)
    if exact_item_match:
        item_name = exact_item_match.group(1).strip().lower().replace(" ", "_")
        activity_data['requirements']['keyword_counts'][f'exact_item_{item_name}'] = 1

    # 2. Tool Level Requirement (e.g., "Have Hatchet Hatchet equipped that requires at least Woodcutting Woodcutting level [50]")
    level_req_match = re.search(r'Have\s+(?:[A-Za-z]+\s+)?([A-Za-z]+)\s+equipped\s+that\s+requires\s+at\s+least\s+(?:[A-Za-z]+\s+)?([A-Za-z]+)\s+level\s*\[?(\d+)\]?', text, re.IGNORECASE)
    if level_req_match:
        keyword = level_req_match.group(1).lower()
        skill = level_req_match.group(2).lower()
        level = int(level_req_match.group(3))
        activity_data['requirements']['keyword_counts'][f'req_{skill}_{level}_{keyword}'] = 1
def parse_locations_section(soup, activity_data):
    """Parse locations from the Location section."""
    content = soup.find('div', class_='mw-parser-output')
    if not content: return
    
    location_heading = content.find('h1', id='Location') or content.find('h1', id='Locations')
    if not location_heading: return
    
    next_elem = get_next_content_sibling(location_heading)
    while next_elem:
        if next_elem.name == 'ul':
            for li in next_elem.find_all('li'):
                location_link = li.find('a', href=re.compile(r'/wiki/(?!File:)'))
                if location_link:
                    loc_name = clean_text(location_link.get_text())
                    if loc_name and loc_name not in activity_data['locations']:
                        activity_data['locations'].append(loc_name)
            break
        elif next_elem.name in ['h1', 'h2']: break
        next_elem = next_elem.find_next_sibling()

def parse_requirements_section(soup, activity_data):
    """Parse requirements section."""
    content = soup.find('div', class_='mw-parser-output')
    if not content: return
    
    req_heading = content.find('h1', id='Requirement') or content.find('h1', id='Requirements')
    if not req_heading: return
    
    next_elem = get_next_content_sibling(req_heading)
    while next_elem:
        if next_elem.name in ['h1', 'h2']: break
        
        text = next_elem.get_text()
        
        skill_matches = re.findall(r'At least.*?(\w+)\s+lvl?\.\s*(\d+)', text, re.IGNORECASE)
        for skill_name, level in skill_matches:
            activity_data['skill_requirements'][skill_name] = int(level)
        
        if next_elem.name == 'ul':
            for li in next_elem.find_all('li'):
                li_text = li.get_text()
                keyword_links = li.find_all('a', href=re.compile(r'Keyword', re.I))
                for keyword_link in keyword_links:
                    if '/wiki/File:' in keyword_link.get('href', ''): continue
                    keyword_name = clean_text(keyword_link.get_text())
                    if keyword_name:
                        keyword_lower = normalize_id(keyword_name)
                        count_match = re.search(r'\[(\d+)\].*?' + re.escape(keyword_name), li_text, re.IGNORECASE)
                        count = int(count_match.group(1)) if count_match else 1
                        
                        current = activity_data['requirements']['keyword_counts'].get(keyword_lower, 0)
                        activity_data['requirements']['keyword_counts'][keyword_lower] = max(current, count)
                
                if 'achievement point' in li_text.lower():
                    ap_match = re.search(r'\[(\d+)\].*?achievement point', li_text, re.IGNORECASE)
                    if ap_match:
                        activity_data['requirements']['achievement_points'] = int(ap_match.group(1))

        if 'light source' in text.lower():
            match = re.search(r'\[(\d+)\].*?light\s+sources?', text, re.IGNORECASE)
            if match:
                count = int(match.group(1))
                current = activity_data['requirements']['keyword_counts'].get('light_source', 0)
                activity_data['requirements']['keyword_counts']['light_source'] = max(current, count)

        rep_match = re.search(r'(\d+)\s+reputation\s+with\s+([^,\.]+)', text, re.IGNORECASE)
        if rep_match:
            amount = int(rep_match.group(1))
            faction = clean_text(rep_match.group(2))
            activity_data['requirements']['reputation'][faction] = amount
        
        completion_match = re.search(r'completed?\s+(?:the\s+)?(.+?)\s+activity\s+\((\d+)\)\s+times', text, re.IGNORECASE)
        if completion_match:
            activity_name = clean_text(completion_match.group(1))
            count = int(completion_match.group(2))
            activity_data['requirements']['activity_completions'][activity_name] = count
        exact_item_match = re.search(r'Have\s+item\s+(.+?)\s+equipped', text, re.IGNORECASE)
        if exact_item_match:
            item_name = exact_item_match.group(1).strip().lower().replace(" ", "_")
            activity_data['requirements']['keyword_counts'][f'exact_item_{item_name}'] = 1

        # 2. Tool Level Requirement (e.g., "Have Hatchet Hatchet equipped that requires at least Woodcutting Woodcutting level [50]")
        level_req_match = re.search(r'Have\s+(?:[A-Za-z]+\s+)?([A-Za-z]+)\s+equipped\s+that\s+requires\s+at\s+least\s+(?:[A-Za-z]+\s+)?([A-Za-z]+)\s+level\s*\[?(\d+)\]?', text, re.IGNORECASE)
        if level_req_match:
            keyword = level_req_match.group(1).lower()
            skill = level_req_match.group(2).lower()
            level = int(level_req_match.group(3))
            activity_data['requirements']['keyword_counts'][f'req_{skill}_{level}_{keyword}'] = 1
        next_elem = next_elem.find_next_sibling()

def parse_experience_table(soup, activity_data):
    """Parse base XP and steps from Experience Information table."""
    content = soup.find('div', class_='mw-parser-output')
    if not content: return
    
    exp_heading = content.find('h1', id='Experience_Information')
    if not exp_heading: return
    
    next_elem = get_next_content_sibling(exp_heading)
    while next_elem:
        if next_elem.name == 'table' and 'wikitable' in next_elem.get('class', []):
            rows = next_elem.find_all('tr')
            if len(rows) < 2: break
            
            data_rows = rows[1:]
            skills_and_xp = []
            base_steps = None
            
            for row_idx, row in enumerate(data_rows):
                cols = row.find_all('td')
                if len(cols) < 3: continue
                
                skill_link = cols[1].find('a', href=re.compile(r'/wiki/(Special:MyLanguage/)?(Agility|Carpentry|Cooking|Crafting|Fishing|Foraging|Mining|Smithing|Trinketry|Woodcutting)'))
                if not skill_link: continue
                
                skill_name = skill_link.get_text(strip=True)
                try:
                    xp = int(clean_text(cols[2].get_text()))
                    skills_and_xp.append((skill_name, xp))
                except: pass
                
                if base_steps is None and row_idx == 0 and len(cols) > 3:
                    try:
                        steps_text = clean_text(cols[3].get_text())
                        base_steps = int(steps_text)
                    except: pass
            
            if base_steps:
                activity_data['base_steps'] = base_steps
            
            if skills_and_xp:
                primary_skill, primary_xp = skills_and_xp[0]
                activity_data['base_xp'] = primary_xp
                for skill_name, xp in skills_and_xp[1:]:
                    activity_data['secondary_xp'][skill_name] = xp
            break
        elif next_elem.name in ['h1', 'h2']: break
        next_elem = next_elem.find_next_sibling()

def parse_faction_reputation(soup, activity_data):
    """Parse faction reputation from headers."""
    content = soup.find('div', class_='mw-parser-output')
    if not content: return
    
    rep_heading = None
    for level in ['h3', 'h2', 'h1']:
        rep_heading = content.find(level, id='Faction_Reputation_Reward')
        if rep_heading: break
    
    if not rep_heading:
        all_headings = content.find_all(['h1', 'h2', 'h3'])
        for heading in all_headings:
            if 'faction' in heading.get_text().lower() and 'reward' in heading.get_text().lower():
                rep_heading = heading
                break
    
    if not rep_heading: return
    
    next_elem = get_next_content_sibling(rep_heading)
    while next_elem:
        if next_elem.name == 'table' and 'wikitable' in next_elem.get('class', []):
            rows = next_elem.find_all('tr')
            if len(rows) >= 2:
                data_row = rows[1]
                cols = data_row.find_all('td')
                if len(cols) >= 3:
                    faction_name = clean_text(cols[1].get_text())
                    amount_text = clean_text(cols[2].get_text())
                    try:
                        amount_match = re.search(r'\+?([\d.]+)', amount_text)
                        if amount_match and faction_name:
                            activity_data['faction_reputation'][faction_name] = float(amount_match.group(1))
                    except: pass
            break
        elif next_elem.name in ['h1', 'h2', 'h3']: break
        next_elem = next_elem.find_next_sibling()

def parse_drop_tables(soup, activity_data):
    """Dynamically parse all drop tables based on headers."""
    content = soup.find('div', class_='mw-parser-output')
    if not content: return
    
    # Find all headers containing "Drop" or "Drops" (case insensitive)
    drop_headers = soup.find_all(lambda tag: tag.name in ['h2', 'h3', 'h4'] and 'Drop' in tag.get_text())
    
    skill_names = [s.value.capitalize() for s in SkillName]
    faction_names = ['Erdwise', 'Halfling Rebels', 'Jarvonia', 'Syrenthia', 'Trellin']

    for header in drop_headers:
        header_text = clean_text(header.get_text())
        if 'Drop Tables' in header_text: continue 

        table_type = map_loot_table_type(header_text)
        
        # Use get_next_content_sibling to handle wrapped headers
        next_elem = get_next_content_sibling(header)
        target_table = None
        
        while next_elem:
            if next_elem.name == 'table' and 'wikitable' in next_elem.get('class', []):
                target_table = next_elem
                break
            if next_elem.name in ['h1', 'h2', 'h3', 'h4']: break 
            next_elem = next_elem.find_next_sibling()
            
        if not target_table: continue

        # Calculate actual column indices based on header colspan
        header_row = target_table.find('tr')
        if not header_row: continue
        
        th_elements = header_row.find_all('th')
        col_map = {}
        current_idx = 0
        
        for th in th_elements:
            text = th.get_text().strip()
            col_map[text] = current_idx
            
            # Check colspan (default 1)
            colspan = int(th.get('colspan', 1))
            current_idx += colspan
            
        # Helper to find index by exact or partial name
        def get_column_index(target_names, exclude_substrings=None):
            if exclude_substrings is None: exclude_substrings = []
            
            # 1. Exact match attempt
            for name, idx in col_map.items():
                if name.lower() in [t.lower() for t in target_names]:
                    return idx
            
            # 2. Substring match attempt
            for name, idx in col_map.items():
                name_lower = name.lower()
                # Check if any target is in the name
                if any(t.lower() in name_lower for t in target_names):
                    # Check if any exclusion is in the name
                    if not any(ex.lower() in name_lower for ex in exclude_substrings):
                        return idx
            return -1

        # Find Quantity and Chance indices
        
        # Quantity
        qty_idx = get_column_index(['Quantity', 'Qty'])

        # Chance logic:
        # 1. Prioritize "Final Chance" (max skill level values)
        chance_idx = get_column_index(['Final Chance'])
        
        # 2. Fallback to generic "Chance", but strictly exclude "Level" (e.g., "Max Chance Level")
        if chance_idx == -1:
            chance_idx = get_column_index(['Chance'], exclude_substrings=['Level', 'Max', 'Initial'])

        # 3. Last resort fallback: Allow "Initial" if "Final" didn't exist, but still avoid "Level"
        if chance_idx == -1:
             chance_idx = get_column_index(['Chance'], exclude_substrings=['Level', 'Max'])

        # Fallback if headers aren't standard text (e.g. secondary tables sometimes vary)
        if qty_idx == -1 or chance_idx == -1:
            if table_type == ActivityLootTableType.SECONDARY:
                # Icon(0), Name(1), Type(2), Qty(3), Chance(4)
                if qty_idx == -1: qty_idx = 3
                if chance_idx == -1: chance_idx = 4
            else:
                # Icon+Name(0,1), Qty(2), Chance(3)
                if qty_idx == -1: qty_idx = 2
                if chance_idx == -1: chance_idx = 3

        # Parse table rows
        rows = target_table.find_all('tr')[1:] # Skip header
        current_drops = []
        
        for row in rows:
            cols = row.find_all('td')
            # Check we have enough columns for the indices we found
            if len(cols) <= max(qty_idx, chance_idx): continue

            # Name is usually at index 1 (0 is Icon)
            name_idx = 1
            
            item_link = cols[name_idx].find('a')
            item_name = clean_text(item_link.get_text()) if item_link else clean_text(cols[name_idx].get_text())
            
            if not item_name or item_name in skill_names or item_name in faction_names: continue
            if item_name.endswith('%') or re.match(r'^\d', item_name): continue 
            
            qty_text = clean_text(cols[qty_idx].get_text())
            chance_text = clean_text(cols[chance_idx].get_text())
            
            min_q, max_q = 0, 0
            if qty_text and qty_text != 'N/A':
                range_match = re.match(r'(\d+)-(\d+)', qty_text)
                if range_match:
                    min_q, max_q = int(range_match.group(1)), int(range_match.group(2))
                else:
                    num_match = re.match(r'(\d+)', qty_text)
                    if num_match: min_q, max_q = int(num_match.group(1)), int(num_match.group(1))

            chance_val = None
            if chance_text:
                try: chance_val = float(chance_text.strip().replace('%', ''))
                except: pass
            
            current_drops.append(DropEntry(
                item_id=normalize_id(item_name),
                min_quantity=min_q,
                max_quantity=max_q,
                chance=chance_val
            ))
            
        if current_drops:
            activity_data['loot_tables'].append(LootTable(
                type=table_type,
                drops=current_drops
            ))

# ============================================================================
# MAIN PARSING LOOP
# ============================================================================

def parse_activity_page(activity_info) -> Optional[Activity]:
    name = activity_info['name']
    url = activity_info['url']
    slug = activity_info['slug']
    
    cache_file = CACHE_DIR / (sanitize_filename(name) + '.html')
    html = download_page(url, cache_file)
    if not html: return None
    
    soup = BeautifulSoup(html, 'html.parser')
    
    data = {
        'name': name,
        'primary_skill': None,
        'locations': [],
        'skill_requirements': {},
        'requirements': {
            'keyword_counts': {},
            'achievement_points': 0,
            'reputation': {},
            'activity_completions': {},
            'tool_equipped': None,
            'unique_tools': 0
        },
        'loot_tables': [], 
        'base_steps': 0,
        'base_xp': 0.0,
        'secondary_xp': {},
        'max_efficiency': 0.0,
        'faction_reputation': {}
    }
    
    content = soup.find('div', class_='mw-parser-output')
    if content:
        paragraphs = content.find_all('p')
        for p in paragraphs:
            text = p.get_text()
            if 'activity' in text.lower():
                skill_match = re.search(r'is\s+an?\s+(\w+)\s+activity', text, re.IGNORECASE)
                if skill_match:
                    data['primary_skill'] = skill_match.group(1)
                break
    
    infobox = soup.find('table', class_='ItemInfobox')
    if infobox:
        parse_infobox(infobox, data)
        
    parse_locations_section(soup, data)
    parse_requirements_section(soup, data)
    parse_experience_table(soup, data)
    parse_faction_reputation(soup, data)
    parse_drop_tables(soup, data)
    
    reqs_list = []
    
    for sname, level in data['skill_requirements'].items():
        reqs_list.append(Requirement(type=RequirementType.SKILL_LEVEL, target=sname.lower(), value=level))
    
    for kw, count in data['requirements']['keyword_counts'].items():
        reqs_list.append(Requirement(type=RequirementType.KEYWORD_COUNT, target=kw, value=count))
        
    if data['requirements']['achievement_points'] > 0:
        reqs_list.append(Requirement(type=RequirementType.ACHIEVEMENT_POINTS, value=data['requirements']['achievement_points']))
        
    for fac, amt in data['requirements']['reputation'].items():
        reqs_list.append(Requirement(type=RequirementType.REPUTATION, target=normalize_id(fac), value=amt))
        
    for act, count in data['requirements']['activity_completions'].items():
        reqs_list.append(Requirement(type=RequirementType.ACTIVITY_COMPLETION, target=normalize_id(act), value=count))
        
    if data['requirements']['tool_equipped']:
        reqs_list.append(Requirement(type=RequirementType.TOOL_EQUIPPED, target=data['requirements']['tool_equipped'].lower(), value=1))
    if data['requirements']['unique_tools'] > 0:
        reqs_list.append(Requirement(type=RequirementType.UNIQUE_TOOLS, value=data['requirements']['unique_tools']))

    rewards_list = []
    for fac, amt in data['faction_reputation'].items():
        rewards_list.append(FactionReward(faction_id=normalize_id(fac), amount=amt))
        
    sec_xp_enum = {}
    for sname, xp in data['secondary_xp'].items():
        sec_xp_enum[parse_skill_enum(sname)] = float(xp)

    return Activity(
        id=normalize_id(name),
        wiki_slug=slug,
        name=name,
        value=0, 
        primary_skill=parse_skill_enum(data['primary_skill'] or "none"),
        locations=[normalize_id(l) for l in data['locations']],
        base_steps=data['base_steps'] or 0,
        base_xp=float(data['base_xp'] or 0),
        secondary_xp=sec_xp_enum,
        max_efficiency=data['max_efficiency'] or 0.0,
        requirements=reqs_list,
        faction_rewards=rewards_list,
        loot_tables=data['loot_tables'] 
    )
def load_ev_values() -> tuple[dict[str, int], dict[str, float]]:
    """Loads item base values and pre-calculated container EVs."""
    item_values = {"coins": 1}
    container_evs = {}
    
    # 1. Load Standard Items
    files_to_load = ["materials.json", "consumables.json", "equipment.json"]
    for filename in files_to_load:
        path = Path(get_output_file(filename))
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data:
                        i_id = item.get("id")
                        if i_id:
                            val = item.get("value", 0)
                            item_values[i_id] = val
                            # Equipment ID Aliases
                            if i_id.endswith("_common"):
                                item_values[i_id.replace("_common", "")] = val
                            elif i_id.isupper():
                                item_values[i_id.lower()] = val
            except Exception as e:
                print(f"Warning: Could not load {filename}: {e}")

    # 2. Load Containers
    cont_path = Path(get_output_file("containers.json"))
    if cont_path.exists():
        try:
            with open(cont_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for c in data:
                    c_id = c.get("id")
                    if c_id:
                        container_evs[c_id] = c.get("total_expected_value", 0.0)
        except Exception as e:
            print(f"Warning: Could not load containers.json: {e}")

    return item_values, container_evs

def calculate_activity_evs(activities: list[Activity], item_values: dict[str, int], container_evs: dict[str, float]) -> list[Activity]:
    """Calculates normal, chest, and fine roll worths for activities."""
    updated_activities = []
    
    for act in activities:
        normal_worth = 0.0
        chest_worth = 0.0
        fine_worth = 0.0

        for table in act.loot_tables:
            for drop in table.drops:
                chance = (drop.chance or 0.0) / 100.0
                avg_qty = (drop.min_quantity + drop.max_quantity) / 2.0
                item_id = drop.item_id
                
                # 1. Chests & Containers
                if item_id in container_evs:
                    chest_worth += chance * avg_qty * container_evs[item_id]
                
                # 2. Standard Items
                else:
                    base_val = float(item_values.get(item_id, 0))
                    fine_id = f"{item_id}_fine"
                    fine_val = float(item_values.get(fine_id, base_val)) 

                    normal_worth += chance * avg_qty * base_val
                    fine_worth += chance * avg_qty * fine_val
        
        # Rebuild frozen Activity model with new EV values
        act_dict = act.model_dump()
        act_dict["normal_roll_worth"] = normal_worth
        act_dict["chest_roll_worth"] = chest_worth
        act_dict["fine_roll_worth"] = fine_worth
        
        updated_activities.append(Activity(**act_dict))
        
    return updated_activities
def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    print("Step 1: Downloading Activities list...")
    activities_list = parse_activities_list()
    print(f"Found {len(activities_list)} activities.")
    
    all_data = []
    
    for i, item in enumerate(activities_list):
        print(f"[{i+1}/{len(activities_list)}] Parsing {item['name']}...")
        try:
            activity = parse_activity_page(item)
            if activity:
                all_data.append(activity)
                tables_found = [t.type.value for t in activity.loot_tables]
                if tables_found:
                    print(f"  -> Found tables: {', '.join(tables_found)}")
        except Exception as e:
            print(f"  Error parsing {item['name']}: {e}")
    
    print("\nStep 2: Calculating Activity Expected Values...")
    item_vals, container_evs = load_ev_values()
    all_data = calculate_activity_evs(all_data, item_vals, container_evs=container_evs)
    
    print(f"\nStep 3: Exporting {len(all_data)} activities to {OUTPUT_FILE}...")
    
    data = [a.model_dump(mode='json') for a in all_data]
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print("\nStep 4: Overlaying gear API data for exact drop rates...")
    try:
        from overlay_gear_api import overlay
        overlay()
    except Exception as e:
        import traceback
        print(f"Warning: Could not overlay API data: {e}")
        traceback.print_exc()
        print("Continuing with wiki data only...")
        
    print("Done.")

if __name__ == "__main__":
    main()