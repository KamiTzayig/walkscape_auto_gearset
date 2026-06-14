import sys
import os
import json
import math

# Add parent directory to path so we can import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui_utils import load_data
from calculations import calculate_steps
from drop_calculator import DropCalculator
from models import Activity

def generate_costs():
    items, activities, recipes, locations, services, collectibles, pets, consumables, containers, materials = load_data()
    drop_calc = DropCalculator()
    
    costs = {}
    
    all_item_ids = set()
    for item in items: all_item_ids.add(item.id)
    for mat in materials: all_item_ids.add(mat.id)
    for cons in consumables: all_item_ids.add(cons.id)
    for k in drop_calc.item_values.keys(): all_item_ids.add(k)
    for k in drop_calc.chest_ids: all_item_ids.add(k)
    
    for item_id in all_item_ids:
        costs[item_id] = {"cost": float('inf'), "source": None}
            
    # Some items might be bought from shops, but we don't have shop data.
    # We will compute costs iteratively.
    changed = True
    iteration = 0
    while changed and iteration < 100:
        changed = False
        iteration += 1
        print(f"Iteration {iteration}...")
        
        # 1. Activities
        for act_obj in activities:
            player_lvl = 99
            we = act_obj.max_efficiency
            stats = {"work_efficiency": we}
            
            action_steps = calculate_steps(act_obj, player_lvl, we, 0, 0.0)
            
            input_cost = 0.0
            if hasattr(act_obj, 'requirements'):
                reqs = [r for r in act_obj.requirements if getattr(r.type, 'value', r.type) in ('keyword_count', 'input_keyword', 'item')]
                for req in reqs:
                    req_type_val = getattr(req.type, 'value', req.type)
                    kw_target = req.target.lower().replace("_", " ").strip() if req.target else ""
                    min_req_cost = float('inf')
                    
                    if req_type_val in ('keyword_count', 'input_keyword'):
                        for mat in list(materials) + list(consumables):
                            if hasattr(mat, 'keywords') and mat.keywords:
                                if kw_target in [k.lower().replace("_", " ").strip() for k in mat.keywords]:
                                    min_req_cost = min(min_req_cost, costs.get(mat.id, {}).get("cost", float('inf')))
                    elif req_type_val == 'item':
                        min_req_cost = min(min_req_cost, costs.get(req.target.lower(), {}).get("cost", float('inf')))
                        
                    input_cost += min_req_cost * req.value
            
            drop_table = drop_calc.get_drop_table(act_obj, stats, player_lvl)
            for drop in drop_table:
                item_id = drop["Item"]
                drop_steps = drop.get("Steps", float('inf'))
                if drop_steps == float('inf') or action_steps == 0:
                    continue
                    
                expected_yield = action_steps / drop_steps
                cost_per_item = (action_steps + input_cost) / expected_yield
                
                if cost_per_item < costs.get(item_id, {}).get("cost", float('inf')):
                    costs[item_id] = {"cost": cost_per_item, "source": f"[Activity] {act_obj.name}"}
                    changed = True
                    
        # 2. Recipes
        for rec_obj in recipes:
            player_lvl = 99
            we = rec_obj.max_efficiency
            action_steps = max(10.0, rec_obj.base_steps / (1 + we))
            
            input_cost = 0.0
            for mat_group in rec_obj.materials:
                group_cost = min([costs.get(m.item_id, {}).get("cost", float('inf')) * m.amount for m in mat_group]) if mat_group else 0.0
                input_cost += group_cost
                
            total_recipe_cost = (action_steps + input_cost) / rec_obj.output_quantity
            
            if total_recipe_cost < costs.get(rec_obj.output_item_id, {}).get("cost", float('inf')):
                costs[rec_obj.output_item_id] = {"cost": total_recipe_cost, "source": f"[Recipe] {rec_obj.name}"}
                changed = True
                
    final_costs = {k: v for k, v in costs.items() if v["cost"] != float('inf')}
    
    out_path = os.path.join(os.path.dirname(__file__), "..", "game_data", "material_costs.json")
    with open(out_path, "w") as f:
        json.dump(final_costs, f, indent=2)
    print(f"Saved {len(final_costs)} material costs to {out_path}")

if __name__ == "__main__":
    generate_costs()
