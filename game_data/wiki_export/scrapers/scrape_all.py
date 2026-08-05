#!/usr/bin/env python3
"""
Master scraper runner - Regenerates all data files from wiki.

Runs all scrapers in sequence or specific scrapers on demand.
Supports cache clearing and progress tracking.

Usage:
    python3 scrape_all.py                    # Run all scrapers
    python3 scrape_all.py --no-cache         # Delete caches first
    python3 scrape_all.py equipment materials  # Run specific scrapers only

Available scrapers:
    - equipment: Items and gear
    - materials: Crafting materials
    - consumables: Food and potions
    - pets: Pets and companions
    - collectibles: Activity rewards and collectibles
    - locations: Location data
    - containers: Chests and loot tables (requires items)
    - recipes: Crafting recipes (requires item caches)
    - services: Crafting benches and banks
    - activities: Activities and API overlay (requires containers & recipes)
    - routes: Travel routes (requires locations)
    - export_names: Export name mappings
"""

# Standard library imports
import shutil
import subprocess
import sys
from pathlib import Path

# ============================================================================
# CONFIGURATION
# ============================================================================

# Scraper definitions: (name, script_path, cache_paths (list), description)
# The order here is strictly dependent on data resolution requirements.
SCRAPERS = [
    # 1. Base Items & Data
    ('equipment', 'scrape_equipment.py', ['../cache/equipment_cache'], 'Items and gear'),
    ('materials', 'scrape_materials.py', ['../cache/materials_cache', '../cache/materials_cache.html'], 'Crafting materials'),
    ('consumables', 'scrape_consumables.py', ['../cache/consumables_cache', '../cache/consumables_cache.html'], 'Consumables'),
    ('pets', 'scrape_pets.py', ['../cache/pets_cache', '../cache/pets_cache.html'], 'Pets and companions'),
    ('collectibles', 'scrape_collectibles.py', ['../cache/collectibles_cache.html'], 'Collectibles'),
    ('locations', 'scrape_locations.py', ['../cache/locations_cache'], 'Location data'),
    
    # 2. Derived Base (Requires Items for EV calculation)
    ('containers', 'scrape_containers.py', ['../cache/containers_cache', '../cache/containers_cache.html'], 'Chests and loot tables'),
    
    # 3. Crafting Mechanics (Requires Items)
    ('recipes', 'scrape_recipes.py', ['../cache/recipes_cache', '../cache/recipes_cache.html'], 'Crafting recipes'),
    ('services', 'scrape_services.py', ['../cache/services_cache.html'], 'Crafting benches'),
    
    # 4. Activities & Overlay (Requires Containers for EV, triggers overlay on Activities AND Recipes)
    ('activities', 'scrape_activities.py', ['../cache/activities_cache', '../cache/gear_api_cache'], 'Activities and API data overlay'),
    
    # 5. Travel (Requires Locations)
    ('routes', 'scrape_routes.py', ['../cache/routes_cache.html'], 'Travel routes'),
    
    # 6. Utilities
    ('export_names', 'scrape_export_names.py', [], 'Export name mappings'),
]

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def clear_cache(cache_path_str):
    """Delete cache folder or file if it exists."""
    cache_path = Path(cache_path_str)
    if cache_path.exists():
        print(f"  Clearing cache: {cache_path_str}")
        if cache_path.is_dir():
            shutil.rmtree(cache_path)
        else:
            cache_path.unlink()

def run_scraper(script_path):
    """Run a scraper script."""
    print(f"  Running: {script_path}")
    # Run from util/scrapers directory since all scrapers are there
    scrapers_dir = Path(__file__).parent
    result = subprocess.run([sys.executable, script_path], capture_output=True, text=True, cwd=scrapers_dir)
    
    if result.returncode != 0:
        print(f"  ❌ FAILED with exit code {result.returncode}")
        if result.stderr:
            print(f"  Error output:\n{result.stderr}")
        return False
    else:
        # Show last few lines of output
        lines = result.stdout.strip().split('\n')
        for line in lines[-5:]:  # Increased to 5 to catch the API overlay print statements
            if line.strip():
                print(f"    {line}")
        print(f"  ✅ Success")
        return True

# ============================================================================
# MAIN LOGIC
# ============================================================================

def main():
    args = sys.argv[1:]
    
    # Show help
    if '--help' in args or '-h' in args:
        print(__doc__)
        return 0
    
    # Parse flags
    clear_caches = '--no-cache' in args or '--clear-cache' in args
    if clear_caches:
        args = [a for a in args if a not in ('--no-cache', '--clear-cache')]
    
    # Determine which scrapers to run
    if args:
        # Run specific scrapers
        scrapers_to_run = []
        for name in args:
            scraper = next((s for s in SCRAPERS if s[0] == name), None)
            if scraper:
                scrapers_to_run.append(scraper)
            else:
                print(f"Unknown scraper: {name}")
                print(f"Available: {', '.join(s[0] for s in SCRAPERS)}")
                return 1
    else:
        # Run all scrapers
        scrapers_to_run = SCRAPERS
    
    print("=" * 60)
    print("Walkscape Data Scraper")
    print("=" * 60)
    print()
    
    # Clear caches if requested
    if clear_caches:
        print("Clearing caches...")
        scrapers_dir = Path(__file__).parent
        for name, script, cache_paths, desc in scrapers_to_run:
            for cache in cache_paths:
                clear_cache(scrapers_dir / cache)
        print()
    
    # Run scrapers
    print(f"Running {len(scrapers_to_run)} scraper(s)...")
    print()
    
    results = []
    for name, script, cache_paths, desc in scrapers_to_run:
        print(f"[{name.upper()}] {desc}")
        success = run_scraper(script)
        results.append((name, success))
        print()
    
    # Summary
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    
    success_count = sum(1 for _, success in results if success)
    total_count = len(results)
    
    for name, success in results:
        status = "✅" if success else "❌"
        print(f"{status} {name}")
    
    print()
    print(f"Completed: {success_count}/{total_count} successful")
    
    return 0 if success_count == total_count else 1

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    sys.exit(main())