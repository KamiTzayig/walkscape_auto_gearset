#!/usr/bin/env python3
"""
Scrape collectibles from the Walkscape wiki.
Generates collectibles.json using Pydantic models.
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
    Collectible, Modifier, StatName, 
    Condition, ConditionType
)
from scraper_utils import *

# Configuration
RESCRAPE = True
COLLECTIBLES_URL = 'https://wiki.walkscape.app/wiki/Collectibles'
CACHE_FILE = get_cache_file('collectibles_cache.html')
OUTPUT_FILE = get_output_file('collectibles.json')

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
    Adapts logic from scrape_equipment to ensure consistency.
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
        
        # Look ahead for context/conditions (similar to scrape_equipment)
        next_i = i + 1
        
        # Collectibles often have the condition on the line *before* or *with* the stat in wiki text
        # But the split list passed here usually has "While doing X" as a separate line if <br> was used.
        
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

            # Skill Context (e.g. "Fishing") on its own line
            skill_context = extract_skill_from_text(next_line)
            if skill_context and skill_context != 'global' and len(next_line.split()) < 3:
                 conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target=skill_context))
                 next_i += 1
                 continue
                 
            break

        # Check current line for conditions too (sometimes "While doing X" is the current line, stat is next?)
        # Collectibles table usually has "Stat <br> Condition" or "Condition <br> Stat"
        # The scrape_equipment logic assumes Stat is current line.
        
        # Check if THIS line is just a condition
        current_skill = extract_skill_from_text(line)
        current_loc, _ = extract_location_from_text(line)
        
        # If the line is JUST a condition line (no numbers), it applies to following lines?
        # The logic in extract_attributes (old scraper) implies nested parsing.
        # We'll use a simpler heuristic: If line has a number, it's a stat.
        
        has_number = re.search(r'\d', line)
        if not has_number:
            # It might be a header line for the following stats
            i += 1
            continue

        # Parse the stat
        clean_line = re.sub(r'while doing\s+\w+', '', line_lower)
        clean_line = re.sub(r'while in\s+[\w\s]+', '', clean_line)
        
        value_match = re.search(r'([+-]?\d+(?:\.\d+)?)\s*(%?)', clean_line)
        
        if value_match:
            value_str = value_match.group(1)
            is_percent = value_match.group(2) == '%'
            
            # Try to find stat name
            stat_text = re.sub(r'[+-]?\d+(?:\.\d+)?%?', '', clean_line).strip()
            raw_stat_name = normalize_stat_name(stat_text) or normalize_stat_name(clean_line)
            
            if raw_stat_name:
                final_stat_key = raw_stat_name
                if raw_stat_name in DUAL_FORMAT_STATS:
                     final_stat_key = f"{raw_stat_name}_{'percent' if is_percent else 'add'}"

                try:
                    stat_enum = StatName(final_stat_key)
                    
                    # Apply context found in this line or previous context
                    # (Simplified: Collectibles usually explicitly state conditions)
                    
                    # Check for inline skill context
                    inline_skill = extract_skill_from_text(line)
                    if inline_skill and inline_skill != 'global':
                         if not any(c.type == ConditionType.SKILL_ACTIVITY for c in conditions):
                             conditions.append(Condition(type=ConditionType.SKILL_ACTIVITY, target=inline_skill))

                    if not conditions:
                        conditions.append(Condition(type=ConditionType.GLOBAL))
                    
                    modifiers.append(Modifier(stat=stat_enum, value=float(value_str), conditions=conditions))
                except ValueError:
                    validator.add_unrecognized_stat("Parsing", f"Enum conversion failed: {final_stat_key}")
            else:
                validator.add_unrecognized_stat("Parsing", line)
        
        i = next_i
        
    return modifiers

def parse_collectibles():
    """Parse all collectibles from the cached HTML file."""
    print("Downloading Collectibles page...")
    html = download_page(COLLECTIBLES_URL, CACHE_FILE, rescrape=RESCRAPE)
    if not html: return []
    
    soup = BeautifulSoup(html, 'html.parser')
    collectibles = []
    
    content_div = soup.find('div', class_='mw-parser-output')
    if not content_div: return []
    
    tables = content_div.find_all('table', class_='wikitable')
    print(f"Found {len(tables)} tables.")
    
    for table in tables:
        # Skip tables that don't look like collectible lists (heuristic)
        if not table.find('tr', {'data-achievement-id': True}):
            continue

        rows = table.find_all('tr', {'data-achievement-id': True})
        rowspan_tracker = {}
        
        for row in rows:
            cells = row.find_all('td')
            actual_cells = []
            
            # Handle rowspan logic
            col_idx = 0
            cell_iter = iter(cells)
            while len(actual_cells) < 10: # Safety limit
                if col_idx in rowspan_tracker and rowspan_tracker[col_idx] > 0:
                    rowspan_tracker[col_idx] -= 1
                    col_idx += 1
                    continue
                
                try:
                    cell = next(cell_iter)
                    actual_cells.append(cell)
                    if cell.get('rowspan'):
                        rowspan_tracker[col_idx] = int(cell.get('rowspan')) - 1
                    col_idx += 1
                except StopIteration:
                    break
            
            if len(actual_cells) < 3: continue
            
            # Extract Name
            name_cell = actual_cells[1]
            name_link = name_cell.find('a')
            if name_link:
                title = name_link.get('title', '').replace('Special:MyLanguage/', '')
                name = clean_text(title) or clean_text(name_link.get_text())
                slug = name_link.get('href', '').split('/')[-1]
            else:
                name = clean_text(name_cell.get_text())
                slug = name.replace(' ', '_')
            
            # Extract Modifiers
            attr_cell = actual_cells[2]
            for br in attr_cell.find_all('br'): br.replace_with('\n')
            stat_lines = [l.strip() for l in attr_cell.get_text().split('\n') if l.strip()]
            
            modifiers = parse_attribute_lines(stat_lines)
            
            col_id = normalize_id(name)
            
            try:
                col = Collectible(
                    id=col_id,
                    wiki_slug=slug,
                    name=name,
                    value=0, # Collectibles typically have no sell value
                    modifiers=modifiers
                )
                collectibles.append(col)
                print(f"  Processed: {name} ({len(modifiers)} mods)")
            except Exception as e:
                print(f"  Error creating collectible {name}: {e}")

    return collectibles

def main():
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    data = parse_collectibles()
    
    print(f"\nExporting {len(data)} collectibles to {OUTPUT_FILE}...")
    json_data = [item.model_dump(mode='json') for item in data]
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    
    validator.report()
    print("Done.")

if __name__ == "__main__":
    main()