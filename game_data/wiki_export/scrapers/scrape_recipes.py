#!/usr/bin/env python3
"""
Scrape recipes from the Walkscape wiki.
Downloads the main Recipes index, then caches and scrapes individual item pages
to extract highly accurate recipe data using Pydantic models.
"""

import json
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import unquote

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from models import Recipe, SkillName
from scraper_utils import *

# Configuration
RESCRAPE = True
RECIPES_URL = 'https://wiki.walkscape.app/wiki/Recipes'
CACHE_DIR = get_cache_dir('recipes')
OUTPUT_FILE = get_output_file('recipes.json')
CACHE_FILE = get_cache_file('recipes_cache.html')

validator = ScraperValidator()

# ============================================================================
# HELPERS
# ============================================================================

def normalize_id(text: str) -> str:
    """Normalize text to snake_case ID."""
    if not text: return "none"
    text = unquote(text)
    text = text.replace('Special:MyLanguage/', '')
    # FIX: Strip whitespace BEFORE replacing spaces with underscores
    return text.strip().lower().replace("'", "").replace("-", "_").replace(" ", "_")

def parse_skill_enum(text: str) -> SkillName:
    try:
        return SkillName(text.lower())
    except ValueError:
        return SkillName.NONE

def clean_text(text: str) -> str:
    return ' '.join(text.split()).strip()

# ============================================================================
# PARSING LOGIC
# ============================================================================

def parse_item_recipe_page(html_content: str, wiki_slug: str) -> list[Recipe]:
    """Parses an individual item's HTML page to extract recipe data."""
    soup = BeautifulSoup(html_content, 'html.parser')
    recipes = []

    # 1. Locate ALL necessary tables by their EXACT captions
    recipe_outputs_tables = []
    recipe_xp_tables = []

    for table in soup.find_all('table', class_='wikitable'):
        caption = table.find('caption')
        if not caption:
            continue
        
        caption_text = caption.get_text(strip=True)
        if caption_text == 'Recipe Outputs':
            recipe_outputs_tables.append(table)
        elif caption_text == 'Recipe Experience':
            recipe_xp_tables.append(table)

    if not recipe_outputs_tables:
        return [] # No craftable recipes found on this page

    # 2. Extract XP and Steps data into a dictionary for safe lookup across ALL XP tables
    xp_data_map = {}
    for xp_table in recipe_xp_tables:
        for row in xp_table.find_all('tr')[1:]:
            cells = row.find_all(['th', 'td'])
            if len(cells) >= 4:
                rec_name = clean_text(cells[1].get_text(separator=' '))
                try:
                    # Strip commas before casting to int/float
                    base_xp = float(clean_text(cells[2].get_text(separator=' ')).replace(',', ''))
                    base_steps = int(clean_text(cells[3].get_text(separator=' ')).replace(',', ''))
                    
                    # Max Work Efficiency is usually column index 6
                    max_eff = 0.0
                    if len(cells) > 6:
                        eff_text = clean_text(cells[6].get_text(separator=' ')).replace('%', '')
                        if eff_text.replace('.', '', 1).isdigit():
                            max_eff = float(eff_text) / 100.0

                    xp_data_map[rec_name] = {
                        "base_xp": base_xp,
                        "base_steps": base_steps,
                        "max_efficiency": max_eff
                    }
                except ValueError:
                    continue

    # 3. Parse ALL Recipe Outputs tables
    for outputs_table in recipe_outputs_tables:
        for row in outputs_table.find_all('tr')[1:]:
            cells = row.find_all(['th', 'td'])
            if len(cells) < 6:
                continue

            recipe_name = clean_text(cells[1].get_text(separator=' '))
            recipe_id = normalize_id(recipe_name)

            # Level and Skill
            skill_str = "none"
            level = 1
            skill_text = clean_text(cells[2].get_text(separator=' '))
            skill_match = re.search(r'([A-Za-z\s]+?)\s*lvl\.\s*(\d+)', skill_text, re.IGNORECASE)
            if skill_match:
                skill_str = normalize_id(skill_match.group(1).strip())
                level = int(skill_match.group(2))

            # Service
            service_id = "none"
            service_text = clean_text(cells[3].get_text(separator=' '))
            service_match = re.search(r'Needs\s+(.+?)\s+service', service_text, re.IGNORECASE)
            if service_match:
                service_id = normalize_id(service_match.group(1).strip())

            # Materials
            materials = []
            materials_cell = cells[4]
            
            # Replace <br> tags with newlines
            for br in materials_cell.find_all('br'):
                br.replace_with('\n')
            
            # Use separator=' ' so text doesn't mash together
            raw_text = materials_cell.get_text(separator=' ')
            
            # Heal 'or' statements that were visually wrapped with <br> on the wiki
            raw_text = re.sub(r'(?i)\s+or\s*\n\s*', ' or ', raw_text)
            raw_text = re.sub(r'(?i)\s*\n\s*or\s+', ' or ', raw_text)
            
            # Now split by \n for the strict AND requirements
            and_lines = raw_text.split('\n')
            
            for line in and_lines:
                line = line.strip()
                if not line:
                    continue
                
                # Now split by OR within the specific AND line
                or_options = re.split(r'\s+or\s+', line, flags=re.IGNORECASE)
                material_group = []
                
                for option in or_options:
                    option = option.replace('\xa0', ' ').strip()
                    # Safely split at the 'x' to allow hyphens and numbers in item names
                    match = re.match(r'([\d,]+)\s*x\s*(.+)', option)
                    if match:
                        qty = int(match.group(1).replace(',', ''))
                        item_name = match.group(2).strip()
                        
                        # Extra safety: strip trailing 'or' if edge case formatting slipped through
                        if item_name.lower().endswith(' or'):
                            item_name = item_name[:-3].strip()
                            
                        material_group.append({
                            "item_id": normalize_id(item_name),
                            "amount": qty
                        })
                
                if material_group:
                    materials.append(material_group)

            # Output Item
            output_qty = 1
            output_item_id = "none"
            output_text = clean_text(cells[5].get_text(separator=' '))
            out_match = re.match(r'([\d,]+)\s*x\s*(.+)', output_text, re.IGNORECASE)
            if out_match:
                output_qty = int(out_match.group(1).replace(',', ''))
                output_item_id = normalize_id(out_match.group(2).strip())

            # Merge with XP Data
            xp_stats = xp_data_map.get(recipe_name, {"base_xp": 0.0, "base_steps": 0, "max_efficiency": 0.0})

            try:
                # Construct Recipe Pydantic Model
                recipe = Recipe(
                    id=recipe_id,
                    wiki_slug=wiki_slug,
                    name=recipe_name,
                    skill=parse_skill_enum(skill_str),
                    level=level,
                    service=service_id,
                    output_item_id=output_item_id,
                    output_quantity=output_qty,
                    materials=materials,
                    base_xp=xp_stats["base_xp"],
                    base_steps=xp_stats["base_steps"],
                    max_efficiency=xp_stats["max_efficiency"]
                )
                recipes.append(recipe)
            except Exception as e:
                print(f"  Error creating recipe {recipe_name}: {e}")

    return recipes

def get_item_slugs_from_index() -> set:
    """Downloads the main Recipes page and extracts all unique item URLs to scrape."""
    print("Downloading Main Recipes Index...")
    html = download_page(RECIPES_URL, CACHE_FILE, rescrape=RESCRAPE)
    if not html:
        return set()
    
    soup = BeautifulSoup(html, 'html.parser')
    item_slugs = set()
    
    # Find all links in the main table that point to items
    for table in soup.find_all('table', class_='wikitable'):
        for row in table.find_all('tr')[1:]:
            cells = row.find_all(['th', 'td'])
            if len(cells) >= 6:
                # Column 5 is typically "Recipe Outputs"
                links = cells[5].find_all('a')
                for link in links:
                    href = link.get('href')
                    if not href:
                        continue
                    
                    # 1. Reject files (images/icons) immediately
                    if 'File:' in href:
                        continue
                    
                    # 2. Extract item slug, prioritizing the Special:MyLanguage route used for wiki items
                    if 'Special:MyLanguage/' in href:
                        slug = href.split('Special:MyLanguage/')[-1]
                        item_slugs.add(unquote(slug))
                    elif href.startswith('/wiki/'):
                        slug = href.split('/wiki/')[-1]
                        item_slugs.add(unquote(slug))
                        
    print(f"Found {len(item_slugs)} unique item pages to scrape.")
    return item_slugs

def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    all_recipes = []
    
    # 1. Get the list of pages to scrape
    slugs = get_item_slugs_from_index()
    
    # 2. Iterate through each item page, caching and scraping it
    for i, slug in enumerate(slugs, 1):
        url = f"https://wiki.walkscape.app/wiki/{slug}"
        cache_file = CACHE_DIR / (sanitize_filename(slug) + '.html')
        
        html = download_page(url, cache_file, rescrape=RESCRAPE)
        if html:
            item_recipes = parse_item_recipe_page(html, slug)
            if item_recipes:
                print(f"[{i}/{len(slugs)}] Processed: {slug} (Found {len(item_recipes)} recipes)")
                all_recipes.extend(item_recipes)
            else:
                print(f"[{i}/{len(slugs)}] Processed: {slug} (No recipes found)")

    # 3. Deduplicate recipes by ID (in case multiple items share a recipe page)
    unique_recipes = {r.id: r for r in all_recipes}.values()

    # 4. Export to JSON
    print(f"\nExporting {len(unique_recipes)} recipes to {OUTPUT_FILE}...")
    data = [r.model_dump(mode='json') for r in unique_recipes]
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        
    validator.report()
    print("Done.")

if __name__ == "__main__":
    main()