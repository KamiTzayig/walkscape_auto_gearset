#!/usr/bin/env python3
"""
Overlay precise drop rate data from gear.walkscape.app API onto activities.json.

The wiki gives rounded drop percentages (e.g., 0.1% for Wire Saw).
The gear API gives exact game values: noDropChance, rowWeight per item, and
the total table weight. This script patches activities.json with those raw
values so that drop chances can be computed exactly at runtime.

New fields added to each drop entry:
  - no_drop_chance: float (0-100), percentage chance the table rolls "nothing"
  - row_weight: float, this item's weight in the loot table
  - table_weight: float, sum of all row weights in the same table

The existing `chance` field is set to null for API-sourced drops (computed
from the raw fields by the DropEntry model validator).

Also patches: base_steps, base_xp, max_efficiency, secondary_xp.
Adds new activities found in the API but missing from the wiki (e.g.,
embargoed hunting/tailoring activities).

Usage:
    python overlay_gear_api.py              # Patch activities.json
    python overlay_gear_api.py --dry-run    # Preview changes only
    python overlay_gear_api.py --rescrape   # Re-download from API (ignore cache)
"""

import json
import time
import sys
from pathlib import Path

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

import requests
from scraper_utils import get_cache_dir, get_output_file

# ============================================================================
# CONFIGURATION
# ============================================================================

API_BASE = 'https://gear.walkscape.app/api'
CACHE_DIR = get_cache_dir('gear_api')
OUTPUT_FILE = get_output_file('activities.json')
REQUEST_DELAY = 0.1
RESCRAPE = False


# ============================================================================
# API HELPERS
# ============================================================================

def _cached_request(url, cache_filename=None, json_body=None):
    """Make an API request with file-based caching."""
    if cache_filename and not RESCRAPE:
        cache_path = CACHE_DIR / cache_filename
        if cache_path.exists():
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)

    try:
        if json_body is not None:
            resp = requests.post(url, json=json_body, timeout=15)
        else:
            resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if cache_filename:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(CACHE_DIR / cache_filename, 'w', encoding='utf-8') as f:
                json.dump(data, f)

        time.sleep(REQUEST_DELAY)
        return data
    except requests.RequestException as e:
        print(f" FAILED ({e})")
        return None


def api_get(path, cache_filename=None):
    return _cached_request(f"{API_BASE}/{path}", cache_filename=cache_filename)


def api_post(path, body, cache_filename=None):
    return _cached_request(f"{API_BASE}/{path}", cache_filename=cache_filename, json_body=body)


# ============================================================================
# LOOT TABLE CONVERSION
# ============================================================================

def convert_loot_tables(activity_detail, loot_tables_data):
    """Convert API loot tables into our JSON drop format with raw API values.

    Returns (main_drops, secondary_drops, gem_drops) as lists of dicts.
    """
    main_drops, secondary_drops = [], []
    gem_drops = []
    tables_by_id = {t['id']: t for t in loot_tables_data}

    for table_group in activity_detail.get('tables', []):
        is_primary = table_group.get('isPrimary', False)
        api_types = table_group.get('type', [])  # e.g., ["chestTable"], ["gem"], ["collectible"]
        for table_id in table_group.get('tables', []):
            table_data = tables_by_id.get(table_id)
            if not table_data:
                continue

            ndc = table_data.get('noDropChance', 0)
            rows = table_data.get('tableRows', [])
            tw = sum(r.get('rowWeight', 0) for r in rows)

            if ndc >= 1.0:
                continue

            # Determine which list to add to
            if is_primary:
                target = main_drops
            elif 'gem' in api_types:
                target = gem_drops
            else:
                target = secondary_drops

            # "Nothing" entry
            if is_primary and ndc > 0:
                target.append({
                    'item_id': 'nothing', 'min_quantity': 0, 'max_quantity': 0,
                    'chance': round(ndc * 100.0, 6), 'category': None,
                })

            for row in rows:
                item_id = row.get('rowItemID') or ''
                if not item_id and row.get('isMoney'):
                    item_id = 'coins'
                if not item_id:
                    continue

                # Compute exact chance percentage from raw values
                rw = row.get('rowWeight', 0)

                target.append({
                    'item_id': item_id,
                    'min_quantity': row.get('rowMinimumAmount', 1),
                    'max_quantity': row.get('rowMaximumAmount', 1),
                    'chance': None,  # Computed by DropEntry model validator from raw fields
                    'category': None,
                    'no_drop_chance': round(ndc * 100.0, 6),  # Stored as percentage (99.6 = 99.6%)
                    'row_weight': rw,
                    'table_weight': tw,
                })

    return main_drops, secondary_drops, gem_drops


def parse_api_requirements(detail):
    """Parse requirements from API detail into the wiki scraper's format.
    
    Their format uses types: skill_level, keyword_count, achievement_points.
    Input items (arrows, fabric) use a new 'input_keyword' type since they're
    consumed per action rather than equipped.
    """
    requirements = []
    for req in detail.get('requirements', []):
        rt = req.get('type')
        rd = req.get('requirement', {})
        if rt == 'skillLevel':
            requirements.append({
                'type': 'skill_level', 'target': rd.get('skill', ''),
                'value': rd.get('level', 0),
            })
        elif rt == 'keywordEquipped':
            requirements.append({
                'type': 'keyword_count', 'target': rd.get('keyword', ''), 'value': 1,
            })
        elif rt == 'distinctKeywordItemsEquipped':
            for kw in rd.get('keywords', []):
                requirements.append({
                    'type': 'keyword_count', 'target': kw,
                    'value': rd.get('quantity', 1),
                })

    # Input items consumed per action (arrows, fabric, etc.)
    for option in detail.get('options', []):
        if option.get('type') != 'inputActivity':
            continue
        for inp in option.get('inputs', []):
            keyword = inp.get('keyword', '')
            if not keyword:
                continue
            entry = {'type': 'input_keyword', 'target': keyword, 'value': 1}
            for r in inp.get('requirements', []):
                if r.get('type') == 'inputKeywordWithLevel':
                    rd = r.get('requirement', {})
                    entry['input_skill'] = rd.get('skill')
                    entry['input_level'] = rd.get('level')
            requirements.append(entry)

    return requirements


# ============================================================================
# HELPERS
# ============================================================================

def clean_floats(obj):
    """Round floating point values to remove noise like 1.4346150000000002 → 1.434615."""
    if isinstance(obj, dict):
        return {k: clean_floats(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_floats(v) for v in obj]
    elif isinstance(obj, float):
        # Snap to the shortest clean representation
        for decimals in range(0, 7):
            candidate = round(obj, decimals)
            if abs(obj - candidate) < 1e-9:
                return candidate
        return round(obj, 10)
    return obj


# ============================================================================
# MAIN
# ============================================================================

def overlay_recipes(dry_run=False):
    """Overlay API recipe data onto recipes.json — patches existing + adds new."""
    recipes_file = get_output_file('recipes.json')
    if not recipes_file.exists():
        print(f"  Skipping recipes: {recipes_file} not found")
        return

    with open(recipes_file, 'r', encoding='utf-8') as f:
        recipes = json.load(f)
    print(f"\nLoaded {len(recipes)} recipes")

    api_list = api_get('recipes', 'api_recipes_list.json')
    if not api_list:
        print("ERROR: Could not fetch API recipe list")
        return
    print(f"API has {len(api_list)} recipes")

    wiki_by_id = {r['id']: r for r in recipes}
    wiki_by_name = {r['name'].lower(): r for r in recipes}
    patched = 0
    added = 0

    for i, api_rec in enumerate(api_list, 1):
        rid = api_rec['id']
        name = api_rec['name']

        detail = api_get(f'recipes/{rid}', f'api_recipe_{rid}.json')
        if not detail:
            print(f"  [{i}/{len(api_list)}] {name}... FAILED")
            continue

        wiki = wiki_by_id.get(rid) or wiki_by_name.get(name.lower())
        if wiki:
            # Patch existing
            changes = []

            ws = detail.get('workRequired')
            if ws is not None and wiki.get('base_steps') != ws:
                changes.append(f"steps→{ws}")
                wiki['base_steps'] = ws

            mwe = detail.get('maxWorkEfficiency')
            if mwe is not None:
                new_eff = round(mwe - 1.0, 4)
                if wiki.get('max_efficiency') != new_eff:
                    changes.append(f"eff→{new_eff}")
                    wiki['max_efficiency'] = new_eff

            skills = api_rec.get('relatedSkills', [])
            ps = skills[0] if skills else ''
            xp_map = detail.get('xpRewards', {})
            if ps in xp_map and wiki.get('base_xp') != xp_map[ps]:
                changes.append(f"xp→{xp_map[ps]}")
                wiki['base_xp'] = xp_map[ps]

            if changes:
                print(f"  [{i}/{len(api_list)}] {name}... ({', '.join(changes)})")
            patched += 1

        else:
            # New recipe from API
            skills = api_rec.get('relatedSkills', [])
            ps = skills[0] if skills else ''
            xp_map = detail.get('xpRewards', {})

            # Parse level and service from requirements
            level = 1
            service = ''
            for req in detail.get('requirements', []):
                rt = req.get('type')
                rd = req.get('requirement', {})
                if rt == 'skillLevel':
                    level = rd.get('level', 1)
                elif rt == 'service':
                    kw = rd.get('serviceKeyword', '')
                    tier = rd.get('tier', 'basic')
                    service = f"{tier}_{kw}"

            # Parse materials (API has options groups)
            material_groups = []
            for mat_group in detail.get('materials', []):
                options = []
                for opt in mat_group.get('options', []):
                    options.append({
                        'item_id': opt.get('item', ''),
                        'amount': opt.get('amount', 1),
                    })
                if options:
                    material_groups.append(options)

            # Output item
            item_rewards = detail.get('itemRewards', {})
            output_item_id = ''
            output_quantity = 1
            for item_id, qty in item_rewards.items():
                output_item_id = item_id
                output_quantity = qty
                break

            recipes.append({
                'id': rid,
                'wiki_slug': name.replace(' ', '_'),
                'name': name,
                'skill': ps,
                'level': level,
                'service': service,
                'output_item_id': output_item_id,
                'output_quantity': output_quantity,
                'materials': material_groups,
                'base_xp': xp_map.get(ps, 0),
                'base_steps': detail.get('workRequired', 0),
                'max_efficiency': round((detail.get('maxWorkEfficiency') or 1.0) - 1.0, 4),
            })
            print(f"  [{i}/{len(api_list)}] {name}... + NEW ({ps} lv{level})")
            added += 1

    print(f"Recipes: patched {patched}, added {added} new")

    if not dry_run:
        with open(recipes_file, 'w', encoding='utf-8') as f:
            json.dump(clean_floats(recipes), f, indent=2, ensure_ascii=False)
        print(f"✓ Written {len(recipes)} recipes to {recipes_file.name}")


def overlay(dry_run=False):
    print("=" * 60)
    print("Gear API → data overlay")
    print("=" * 60)

    if not OUTPUT_FILE.exists():
        print(f"ERROR: {OUTPUT_FILE} not found. Run scrape_activities.py first.")
        return

    with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
        activities = json.load(f)
    print(f"Loaded {len(activities)} activities")

    api_list = api_get('activities', 'api_activities_list.json')
    if not api_list:
        print("ERROR: Could not fetch API activity list")
        return
    print(f"API has {len(api_list)} activities")

    wiki_by_id = {a['id']: a for a in activities}
    wiki_by_name = {a['name'].lower(): a for a in activities}
    patched = 0
    added = 0

    for i, api_act in enumerate(api_list, 1):
        aid = api_act['id']
        name = api_act['name']
        if aid in ('none', 'travelling'):
            continue

        print(f"  [{i}/{len(api_list)}] {name}...", end='', flush=True)

        detail = api_get(f'activities/{aid}', f'api_activity_{aid}.json')
        if not detail:
            continue

        # Fetch loot tables
        table_ids = []
        for tg in detail.get('tables', []):
            table_ids.extend(tg.get('tables', []))
        loot = api_post(
            'lootTables/multiple', {'ids': table_ids}, f'api_loot_{aid}.json'
        ) if table_ids else []

        wiki = wiki_by_id.get(aid) or wiki_by_name.get(name.lower())
        if wiki:
            # --- Patch existing ---
            changes = []

            ws = detail.get('workRequired')
            if ws is not None and wiki.get('base_steps') != ws:
                changes.append(f"steps→{ws}")
                wiki['base_steps'] = ws

            mwe = detail.get('maxWorkEfficiency')
            if mwe is not None:
                new_eff = round(mwe - 1.0, 4)
                if wiki.get('max_efficiency') != new_eff:
                    changes.append(f"eff→{new_eff}")
                    wiki['max_efficiency'] = new_eff

            xp_map = detail.get('xpRewardsMap', {})
            ps = wiki.get('primary_skill', '')
            if ps in xp_map and wiki.get('base_xp') != xp_map[ps]:
                changes.append(f"xp→{xp_map[ps]}")
                wiki['base_xp'] = xp_map[ps]
            sec = {s: x for s, x in xp_map.items() if s != ps}
            if sec:
                wiki['secondary_xp'] = sec

            if loot:
                main_d, sec_d, gem_d = convert_loot_tables(detail, loot)
                tables = []
                if main_d:
                    tables.append({'type': 'main', 'drops': main_d})
                if sec_d:
                    tables.append({'type': 'secondary', 'drops': sec_d})
                if gem_d:
                    tables.append({'type': 'gem', 'drops': gem_d})
                old_n = sum(len(t.get('drops', [])) for t in wiki.get('loot_tables', []))
                new_n = len(main_d) + len(sec_d) + len(gem_d)
                if old_n != new_n:
                    changes.append(f"drops {old_n}→{new_n}")
                wiki['loot_tables'] = tables

            print(f" {'(' + ', '.join(changes) + ')' if changes else '✓'}")
            patched += 1

        else:
            # --- New activity ---
            skills = api_act.get('relatedSkillsList', [])
            ps = skills[0] if skills else ''
            xp_map = detail.get('xpRewardsMap', {})

            locs_data = api_get(
                f'locations/search?activityList={aid}&detailed=true',
                f'api_locations_{aid}.json',
            )
            locations = [loc['id'] for loc in (locs_data or []) if loc.get('id')]

            tables = []
            if loot:
                md, sd, gd = convert_loot_tables(detail, loot)
                if md:
                    tables.append({'type': 'main', 'drops': md})
                if sd:
                    tables.append({'type': 'secondary', 'drops': sd})
                if gd:
                    tables.append({'type': 'gem', 'drops': gd})

            activities.append({
                'id': aid,
                'wiki_slug': f"Special:MyLanguage/{name.replace(' ', '_')}",
                'name': name,
                'primary_skill': ps,
                'locations': locations,
                'base_steps': detail.get('workRequired', 0),
                'base_xp': xp_map.get(ps, 0),
                'secondary_xp': {s: x for s, x in xp_map.items() if s != ps},
                'max_efficiency': round((detail.get('maxWorkEfficiency') or 1.0) - 1.0, 4),
                'requirements': parse_api_requirements(detail),
                'faction_rewards': [],
                'loot_tables': tables,
                'modifiers': [],
                'normal_roll_worth': 0.0,
                'chest_roll_worth': 0.0,
                'fine_roll_worth': 0.0,
            })
            loc_str = f" ({', '.join(locations)})" if locations else ""
            print(f" + NEW{loc_str}")
            added += 1

    print(f"\nPatched {patched}, added {added} new")

    if not dry_run:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(clean_floats(activities), f, indent=2, ensure_ascii=False)
        print(f"✓ Written {len(activities)} activities to {OUTPUT_FILE.name}")
    else:
        print("(dry run — no file written)")

    # Also overlay recipes
    overlay_recipes(dry_run=dry_run)


if __name__ == '__main__':
    RESCRAPE = '--rescrape' in sys.argv
    overlay(dry_run='--dry-run' in sys.argv)
