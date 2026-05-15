import streamlit as st
import json
import math
import os
import re
import uuid
from collections import Counter
from typing import List, Dict, Tuple, Any, Optional

from utils.data_loader import load_game_data
from utils.constants import StatName, PERCENTAGE_STATS, EquipmentQuality, INSTANT_ACTION_PET_ABILITIES, BUFF_PET_ABILITIES, OPTIMAZATION_TARGET, SPECIAL_FIND_MAP, UTILITY_SKILLS
from calculations import calculate_steps, calculate_quality_probabilities
from models import (
    Equipment, GearSet, Collectible, Modifier, Condition, Service, Recipe, Activity, 
    Requirement, RequirementType, ConditionType, GATHERING_SKILLS, ARTISAN_SKILLS,
    Pet, Consumable, Container, CraftingNode, Loadout
)
from drop_calculator import DropCalculator

# --- GLOBAL TARGET CONFIG ---
TARGET_CATEGORIES = {
    "Main": ["Reward Rolls", "Xp", "Chests", "Materials From Input", "Fine","Collectibles"],
    "Quality": [ "Eternal Per Input","Good Per Step", "Great Per Step", "Excellent Per Step", "Perfect Per Step", "Eternal Per Step"],
    "Drops & Materials": [ 
        "Tokens Per Step", "Bird Nests Per Step","Ectoplasm Per Step", "Fine Ectoplasm Per Step",
        "Sea Shells Per Step", "Fine Sea Shells Per Step", "Crustacean Per Step", "Fine Crustacean Per Step",
        "Fibrous Plant Per Step", "Fine Fibrous Plant Per Step", "Fishing Bait Per Step", "Fine Fishing Bait Per Step",
        "Gold Nugget Per Step", "Fine Gold Nugget Per Step",
        "Find Random Gem Per Step", "Find Random Fine Gem Per Step", "Gem Finding", "Gem Finding Fine"
    ],
    "🤑": ["Coins", "Net Profit Per Step","Coins No Chests", "Coins No Fines", "Coins No Chests No Fines"],
    "Pets & Abilities":["Reward Rolls No Steps", "Exp No Steps","Chests No Steps", "Fine No Steps", "Collectibles No Steps"],
    "Misc":["Chests Per Material", "Xp Per Material"]
}

def find_category(target_name):
    for cat, targets in TARGET_CATEGORIES.items():
        if target_name in targets: return cat
    return "Base" 

# --- Helpers ---
def get_xp_for_level(level: int) -> int:
    total = 0
    for i in range(1, level):
        total += math.floor(i + 300 * (2 ** (i / 7.0)))
    return math.floor(total / 4)

def calculate_level_from_xp(current_xp: int) -> int:
    for lvl in range(1, 150):
        if get_xp_for_level(lvl + 1) > current_xp:
            return lvl
    return 150

def calculate_char_level_from_steps(current_steps: int) -> int:
    for lvl in range(1, 120):
        xp_req_standard = get_xp_for_level(lvl + 1)
        steps_req = math.floor(xp_req_standard) * 4.6
        if steps_req > current_steps:
            return lvl
    return 120

def calculate_total_level(skills_data: Dict[str, int]) -> int:
    total = 0
    for xp in skills_data.values():
        total += calculate_level_from_xp(xp)
    return total

def extract_user_reputation(user_data: Dict) -> Dict[str, float]:
    if "reputation" in user_data and isinstance(user_data["reputation"], dict):
        return {k.lower(): float(v) for k, v in user_data["reputation"].items()}
    return {}

def check_condition_details(cond: Condition, context: Dict, set_keyword_counts: Counter) -> Tuple[bool, str]:
    c_type = cond.type
    c_target = cond.target.lower() if cond.target else None
    c_val = cond.value
    
    active_skill = context.get("skill", "").lower()
    loc_id = context.get("location_id")
    loc_tags = context.get("location_tags", set())
    act_id = context.get("activity_id")
    user_ap = context.get("achievement_points", 0)
    total_lvl = context.get("total_skill_level", 0)

    if c_type == ConditionType.GLOBAL:
        return True, "Global"
    elif c_type == ConditionType.SKILL_ACTIVITY:
        if not active_skill: return False, "No active skill"
        if c_target == active_skill: return True, f"Skill is {active_skill}"
        if c_target == "gathering" and active_skill in GATHERING_SKILLS: return True, "Skill is Gathering"
        if c_target == "artisan" and active_skill in ARTISAN_SKILLS: return True, "Skill is Artisan"
        if c_target == "utility" and active_skill in UTILITY_SKILLS: return True, "Skill is Utility"
        return False, f"Requires {c_target}, current is {active_skill}"
    elif c_type == ConditionType.LOCATION:
        if not loc_id: return False, "No location set"
        if c_target == loc_id.lower(): return True, "Location Match"
        if c_target in loc_tags: return True, f"Location Tag Match ({c_target})"
        return False, f"Requires Location/Tag '{c_target}'"
    elif c_type == ConditionType.REGION:
        if c_target in loc_tags: return True, f"Region Match ({c_target})"
        return False, f"Requires Region '{c_target}'"
    elif c_type == ConditionType.SPECIFIC_ACTIVITY:
        if act_id and c_target == act_id.lower(): return True, "Activity Match"
        return False, "Wrong Activity"
    elif c_type == ConditionType.ACHIEVEMENT_POINTS:
        req = c_val or 0
        if user_ap >= req: return True, f"AP {user_ap} >= {req}"
        return False, f"Requires {req} AP (Have: {user_ap})"
    elif c_type == ConditionType.TOTAL_SKILL_LEVEL:
        req = c_val or 0
        if total_lvl >= req: return True, f"Total Lvl {total_lvl} >= {req}"
        return False, f"Requires {req} Total Lvl (Have: {total_lvl})"
    elif c_type == ConditionType.SET_EQUIPPED:
        norm_target = cond.target.replace("_", " ").strip()
        req = c_val or 1
        count = set_keyword_counts.get(norm_target, 0)
        if count >= req: return True, f"Set '{norm_target}' active ({count}/{req})"
        return False, f"Requires {req}x '{norm_target}' items (Have: {count})"
    return False, f"Unknown condition: {c_type}"

def extract_user_counts(user_data: Dict) -> Dict[str, int]:
    counts = Counter()
    for container in ["bank", "inventory"]:
        data = user_data.get(container, {})
        if isinstance(data, dict):
            for k, v in data.items():
                counts[str(k).lower()] += int(v)
    gear = user_data.get("gear", {})
    if isinstance(gear, dict):
        for v in gear.values():
            if v: counts[str(v).lower()] += 1
    return counts

def get_user_collectibles(all_collectibles: List[Collectible], user_data: Dict) -> List[Collectible]:
    user_owned_ids = set()
    if "collectibles" in user_data and isinstance(user_data["collectibles"], list):
        for c_id in user_data["collectibles"]:
            user_owned_ids.add(str(c_id).lower())
    owned_objs = []
    for c in all_collectibles:
        if c.id.lower() in user_owned_ids or c.wiki_slug.lower() in user_owned_ids:
            owned_objs.append(c)
    return owned_objs

def is_material_level_valid(mat, req) -> bool:
    """Checks if a material meets the minimum skill level requirement of an activity input."""
    if not getattr(req, 'input_level', None) or not getattr(req, 'input_skill', None):
        return True 
        
    req_lvl = req.input_level
    req_skill = req.input_skill.lower()
    
    mat_lvl = 1
    if hasattr(mat, 'requirements') and mat.requirements:
        for m_req in mat.requirements:
            if getattr(m_req.type, 'value', m_req.type) == 'skill_level' and m_req.target and m_req.target.lower() == req_skill:
                mat_lvl = m_req.value
                break
                
    return mat_lvl >= req_lvl
def build_activity_context(activity, user_ap: int, user_total_level: int, loc_map: Dict, drop_calc, selected_location_id: str = None, skill_group_levels: Dict = None) -> Dict:
    """Shared helper to build exact math context for both Optimizer and Crafting Tree."""
    req_kw = {}
    if hasattr(activity, 'requirements'):
        for req in activity.requirements:
            if req.type == RequirementType.KEYWORD_COUNT and req.target:
                req_kw[req.target.lower().replace("_", " ").strip()] = req.value
                
    current_loc_id = selected_location_id
    if not current_loc_id and getattr(activity, 'locations', None):
        current_loc_id = activity.locations[0]

    current_tags = set()
    if current_loc_id and current_loc_id in loc_map:
        current_tags = {t.lower() for t in loc_map[current_loc_id].tags}
    skill = getattr(activity, 'primary_skill', getattr(activity, 'skill', ""))
    return {
        "skill": skill,
        "location_id": current_loc_id,
        "location_tags": current_tags,
        "activity_id": getattr(activity, 'id', ""),
        "required_keywords": req_kw,
        "achievement_points": user_ap,
        "total_skill_level": user_total_level,
        "skill_group_levels": skill_group_levels or {},
        "special_ev_map": drop_calc.get_special_ev_map()
    }


def filter_user_items(all_items: List[Equipment], user_data: Dict) -> List[Equipment]:
    try:
        owned_ids = set()
        counts = extract_user_counts(user_data)
        for k, v in counts.items():
            if v > 0: owned_ids.add(k)
        
        filtered_items = []
        for item in all_items:
            candidates = set()
            candidates.add(item.id.lower())
            if item.wiki_slug: candidates.add(item.wiki_slug.lower())
            if item.name: candidates.add(item.name.lower())
            base_id = item.id.lower()
            suffixes = ["_common", "_uncommon", "_rare", "_epic", "_legendary", "_ethereal", "_normal"]
            for s in suffixes:
                if base_id.endswith(s): candidates.add(base_id.replace(s, ""))
            
            if not owned_ids.isdisjoint(candidates):
                filtered_items.append(item)
        return filtered_items
    except Exception:
        return all_items

def get_compatible_services(recipe: Recipe, all_services: List[Service]) -> List[Service]:
    if recipe.service.lower() == "none":
        return []

    compatible = []
    recipe_tier_req = "basic"
    is_cursed_req = "cursed" in recipe.service.lower()
    
    if "advanced" in recipe.service.lower():
        recipe_tier_req = "advanced"
        
    for s in all_services:
        if s.skill != recipe.skill: continue
        if recipe_tier_req == "advanced" and s.tier.lower() != "advanced": continue
        s_is_cursed = "cursed" in s.id.lower() or "cursed" in s.name.lower()
        if is_cursed_req and not s_is_cursed: continue
        compatible.append(s)
    
    return sorted(compatible, key=lambda x: x.name)

def synthesize_activity_from_recipe(recipe: Recipe, service: Service) -> Activity:
    combined_reqs = list(recipe.requirements) if hasattr(recipe, 'requirements') else [] 
    has_level_req = False
    for r in combined_reqs:
        if r.type == RequirementType.SKILL_LEVEL: has_level_req = True
    
    if not has_level_req and recipe.level > 1:
        combined_reqs.append(Requirement(type=RequirementType.SKILL_LEVEL, target=recipe.skill, value=recipe.level))
        
    combined_reqs.extend(service.requirements)

    return Activity(
        id=f"{recipe.id}__@{service.id}",
        wiki_slug=recipe.wiki_slug,
        name=f"{recipe.name} (@ {service.name})",
        primary_skill=recipe.skill,
        locations=(service.location,),
        base_steps=recipe.base_steps,
        base_xp=recipe.base_xp,
        max_efficiency=recipe.max_efficiency,
        requirements=tuple(combined_reqs),
        modifiers=service.modifiers,
        output_item_id=recipe.output_item_id,
        output_quantity=recipe.output_quantity,
        materials=recipe.materials

    )

def extract_modifier_stats(modifiers: List[Modifier]) -> Dict[str, float]:
    stats = {}
    for mod in modifiers:
        val = mod.value
        if mod.stat in PERCENTAGE_STATS:
            val = val / 100.0
            
        k = mod.stat.value
        if k == StatName.BONUS_XP_ADD.value: k = "flat_xp"
        elif k == StatName.BONUS_XP_PERCENT.value: k = "xp_percent"
        elif k == StatName.XP_PERCENT.value: k = "xp_percent"
        elif k == StatName.STEPS_ADD.value: 
            k = "flat_step_reduction"
            val = -val 
        elif k == StatName.STEPS_PERCENT.value: 
            k = "percent_step_reduction"
            val = -val
            
        stats[k] = stats.get(k, 0.0) + val
    return stats

def can_tree_use_fine(node: CraftingNode, drop_calc: 'DropCalculator') -> bool:
    if node.source_type == "recipe":
        # A recipe can use fine materials if ALL of its inputs can.
        if not node.inputs:
            return False
            
        for child in node.inputs.values():
            if not can_tree_use_fine(child, drop_calc):
                return False
        return True
        
    else:
        # For leaf nodes (activity, chest, bank), check if the base item 
        # has a known fine variant in the game data.
        if node.item_id.endswith("_fine"):
            return True
            
        return node.item_id in drop_calc.fine_material_map
 
def can_use_pet_ability(ability_name: str, node: CraftingNode, game_data_dict: dict) -> bool:
    """Checks if the equipped pet's ability can actually be used on this specific node."""
    reqs = None
    if ability_name in INSTANT_ACTION_PET_ABILITIES:
        reqs = INSTANT_ACTION_PET_ABILITIES[ability_name]
    elif ability_name in BUFF_PET_ABILITIES:
        reqs = BUFF_PET_ABILITIES[ability_name]
    
    if not reqs:
        return False
        
    if node.source_type not in reqs.get("allowed_source_types", []):
        return False
        
    if node.source_type == "recipe":
        recipe = game_data_dict['recipes'].get(node.source_id)
        if not recipe: return False
        
        if reqs.get("skill") and recipe.skill.lower() != reqs["skill"].lower():
            return False
        if reqs.get("recipe_name_contains") and reqs["recipe_name_contains"].lower() not in recipe.name.lower():
            return False
            
    elif node.source_type == "activity":
        activity = game_data_dict['activities'].get(node.source_id)
        if not activity: return False
        
        if reqs.get("skill") and activity.primary_skill.lower() != reqs["skill"].lower():
            return False
            
    return True
def get_applicable_abilities(node: CraftingNode, game_data_dict: dict) -> List[Tuple[Any, Any]]:
    """Scans all pets to find any abilities that can be used on this specific node."""
    applicable = []
    for pet in game_data_dict.get('pets', {}).values():
        for lvl in pet.levels:
            for ab in lvl.abilities:
                if can_use_pet_ability(ab.name, node, game_data_dict):
                    # Only add the ability once per pet (in case it appears on multiple levels)
                    if not any(a.name == ab.name for p, a in applicable):
                        applicable.append((pet, ab))
    return applicable
def build_default_tree(
    target_item_id: str, 
    game_data: Dict[str, Any], 
    drop_calc: Any,
    global_target_quality: str = "Normal",
    global_use_fine: bool = False,
    visited: Optional[set[str]] = None
) -> CraftingNode:
    """Recursively builds the default crafting tree path for a target item."""
    if visited is None:
        visited = set()
        
    node = CraftingNode(
        node_id=str(uuid.uuid4())[:8],
        item_id=target_item_id,
        source_type="bank",
        available_sources=[{"type": "bank", "id": "bank", "label": "[Bank] From Inventory"}]
    )
    
    if target_item_id in visited:
        return node
        
    visited.add(target_item_id)
    sources = []
    base_item_id = target_item_id.replace("_fine", "") if target_item_id.endswith("_fine") else target_item_id
    
# 1. Find Recipes
    for r_id, r_obj in game_data['recipes'].items():
        # Look for the base item
        if r_obj.output_item_id == base_item_id:
            sources.append({"type": "recipe", "id": r_id, "label": f"[Recipe] {r_obj.name}"})
            
    # 2. Find Activities
    for act_id, act_obj in game_data['activities'].items():
        drop_table = drop_calc.get_drop_table(act_obj, {}, 99) 
        for drop in drop_table:
            # Check for either the exact fine drop OR the base item drop
            if drop["Item"] == base_item_id or drop["Item"] == target_item_id:
                sources.append({"type": "activity", "id": act_id, "label": f"[Activity] {act_obj.name}"})
                break
                
    # 3. Find Chests
    for chest_id, chest_obj in game_data.get('chests', {}).items():
        for drop in chest_obj.drops:
            if drop.item_id == base_item_id or drop.item_id == target_item_id:
                sources.append({"type": "chest", "id": chest_id, "label": f"[Chest] {chest_obj.name}"})
                break
    
    # 4. Check for Global Drops (Special Finds)
    is_global_drop = False
    for stat_key, reward_data in SPECIAL_FIND_MAP.items():
        if isinstance(reward_data, list):
            for item_data in reward_data:
                sub_item = item_data[0] if isinstance(item_data, tuple) else item_data
                if sub_item == base_item_id:
                    is_global_drop = True
                    break
        else:
            if reward_data == base_item_id:
                is_global_drop = True
                
        if is_global_drop: 
            break

    if is_global_drop:
        sources.append({"type": "custom", "id": "custom", "label": "🔍 Choose Custom Activity/Recipe"})
    if not sources:
        visited.remove(target_item_id)
        return node
        
    node.available_sources.extend(sources)
    
    best_source = next((s for s in sources if s["type"] == "recipe"), None)
    if not best_source:
        best_source = next((s for s in sources if s["type"] == "activity"), None)
    if not best_source:
        best_source = sources[0]
        
    source_type = best_source["type"]
    source_id = best_source["id"]
    node.source_type = source_type
    node.source_type = source_type
    node.source_id = source_id
    


# --- Recursively build inputs for Recipes ---
    if source_type == "recipe":
        recipe_obj = game_data['recipes'][source_id]
        
        # --- NEW: Check if the specific item we are crafting is the fine variant ---
        is_target_fine = target_item_id.endswith("_fine")
        
        for i, req_group in enumerate(recipe_obj.materials):
            mat_item_id = req_group[0].item_id
            
            # If global fine is checked OR we are specifically crafting a fine item, upgrade the inputs
            if global_use_fine or is_target_fine:
                if mat_item_id in drop_calc.fine_material_map:
                    mat_item_id = drop_calc.fine_material_map[mat_item_id]
                else:
                    # Safe fallback check in case the map misses it
                    fine_id = f"{mat_item_id}_fine"
                    if fine_id in game_data.get('materials', {}) or fine_id in game_data.get('consumables', {}):
                        mat_item_id = fine_id
                
            child_node = build_default_tree(
                mat_item_id, game_data, drop_calc, global_target_quality, global_use_fine, set(visited)
            )
            child_node.base_requirement_amount = req_group[0].amount
            node.inputs[f"{mat_item_id}_{i}"] = child_node
            
# --- NEW: Recursively build inputs for Activities ---
    elif source_type == "activity":
        activity_obj = game_data['activities'][source_id]
        if hasattr(activity_obj, 'requirements'):
            input_reqs = [r for r in activity_obj.requirements if getattr(r.type, 'value', r.type) in ('keyword_count', 'input_keyword', 'item')]
            for i, req in enumerate(input_reqs):
                req_type_val = getattr(req.type, 'value', req.type)
                kw_target = req.target.lower().replace("_", " ").strip() if req.target else ""
                
                first_valid_id = None
                if req_type_val in ('keyword_count', 'input_keyword'):
                    for mat in list(game_data.get('materials', {}).values()) + list(game_data.get('consumables', {}).values()):
                        if hasattr(mat, 'keywords') and mat.keywords:
                            if kw_target in [k.lower().replace("_", " ").strip() for k in mat.keywords]:
                                if is_material_level_valid(mat, req):
                                    first_valid_id = mat.id
                                    break
                elif req_type_val == 'item':
                    first_valid_id = req.target.lower()
                    
                if first_valid_id:
                    child_node = build_default_tree(
                        target_item_id=first_valid_id,
                        game_data=game_data,
                        drop_calc=drop_calc,
                        global_target_quality=global_target_quality,
                        global_use_fine=False, 
                        visited=set(visited)
                    )
                    child_node.base_requirement_amount = req.value
                    node.inputs[f"{first_valid_id}_{i}"] = child_node
    elif source_type == "chest":
        # The chest itself becomes the input material!
        child_node = build_default_tree(
            target_item_id=source_id, # The chest ID is the target for the child node
            game_data=game_data,
            drop_calc=drop_calc,
            global_target_quality=global_target_quality,
            global_use_fine=False, 
            visited=set(visited)
        )
        # We set this to 1. The fractional math (expected items per chest) 
        # will be handled inside calculate_node_metrics later.
        child_node.base_requirement_amount = 1 
        node.inputs[source_id] = child_node

    visited.remove(target_item_id)
    return node

def calculate_node_cost(
    node: CraftingNode, 
    loadouts: Dict[str, Loadout], 
    game_data: Dict[str, Any], 
    drop_calc: DropCalculator, 
    player_skill_levels: Dict[str, int]
) -> float:
    
    if node.source_type == "bank":
        return 0.0

    loadout = loadouts.get(node.loadout_id)
    gear_set = loadout.gear_set if loadout else GearSet()
    
    target_item_id = node.item_id
    if getattr(node, 'use_fine_materials', False) and not target_item_id.endswith("_fine"):
        base_id = target_item_id
        if (base_id in drop_calc.fine_material_map or 
            f"{base_id}_fine" in game_data.get('materials', {}) or 
            f"{base_id}_fine" in game_data.get('consumables', {})):
            target_item_id = f"{target_item_id}_fine"

    recipe_obj, activity_obj = None, None
    skill_name, min_level = "", 1
    node_context = {"achievement_points": 0, "total_skill_level": 0} 
    
    if node.source_type == "recipe":
        recipe_obj = game_data['recipes'].get(node.source_id)
        if not recipe_obj: return float('inf')
        skill_name, min_level = recipe_obj.skill, recipe_obj.level
        node_context["skill"] = skill_name
    elif node.source_type == "activity":
        act_id = node.source_id
        activity_obj = game_data['activities'].get(act_id)
        if not activity_obj: return float('inf')
        skill_name, min_level = activity_obj.primary_skill, activity_obj.level
        node_context["skill"] = skill_name
        if activity_obj.locations: node_context["location_id"] = activity_obj.locations[0]

    player_lvl = player_skill_levels.get(skill_name.lower(), 99) if skill_name else 99
    stats = gear_set.get_stats(node_context)

    if node.source_type == "activity" and node.inputs:
        for input_id, child_node in node.inputs.items():
            mat_item_id = child_node.item_id
            mat_obj = game_data.get('materials', {}).get(mat_item_id) or game_data.get('consumables', {}).get(mat_item_id)
            if mat_obj and hasattr(mat_obj, 'modifiers') and mat_obj.modifiers:
                mat_stats = extract_modifier_stats(mat_obj.modifiers)
                for k, v in mat_stats.items():
                    stats[k] = stats.get(k, 0.0) + v
    
    DA = stats.get("double_action", 0.0)
    DR = stats.get("double_rewards", 0.0)
    NMC = stats.get("no_materials_consumed", 0.0)
    
    p_valid_quality = 1.0
    if getattr(node, 'target_quality', EquipmentQuality.NORMAL) not in [EquipmentQuality.NORMAL, EquipmentQuality.NONE]:
        from utils.constants import QUALITY_RANK
        target_rank = QUALITY_RANK.get(node.target_quality, 0)
        
        is_equipment_upgrade = False
        if recipe_obj and hasattr(recipe_obj, 'materials'):
            for mat_group in recipe_obj.materials:
                for mat in mat_group:
                    base_id = mat.item_id.replace("_fine", "")
                    has_fine = (base_id in drop_calc.fine_material_map or 
                                f"{base_id}_fine" in game_data.get('materials', {}) or 
                                f"{base_id}_fine" in game_data.get('consumables', {}))
                    if not has_fine:
                        is_equipment_upgrade = True
                        break
                if is_equipment_upgrade: break
        
        probs = calculate_quality_probabilities(
            min_level, player_lvl, stats.get("quality_outcome", 0),
            is_fine_materials=getattr(node, 'use_fine_materials', False),
            is_equipment_upgrade=is_equipment_upgrade
        )
        valid_tiers = [q.value for q, r in QUALITY_RANK.items() if r >= target_rank and q != EquipmentQuality.NONE]
        p_valid_quality = sum(probs.get(q, 0.0) for q in valid_tiers)
        if p_valid_quality <= 0.00001: return float('inf')

    if node.source_type == "recipe":
        steps_per_action = calculate_steps(recipe_obj, player_lvl, stats.get("work_efficiency", 0.0), int(stats.get("flat_step_reduction", 0)), stats.get("percent_step_reduction", 0.0))
        q_out = recipe_obj.output_quantity
        isolated_steps = steps_per_action / ((1.0 + DA) * (1.0 + DR) * q_out * p_valid_quality)
        
        children_cost = 0.0
        for input_id, child_node in node.inputs.items():
            req_amount = child_node.base_requirement_amount
            input_ratio = ((1.0 - NMC) * req_amount) / ((1.0 + DR) * q_out * p_valid_quality)
            child_cost = calculate_node_cost(child_node, loadouts, game_data, drop_calc, player_skill_levels)
            children_cost += (input_ratio * child_cost)
            
        return isolated_steps + children_cost

    elif node.source_type == "activity":
        drop_table = drop_calc.get_drop_table(activity_obj, stats, player_lvl)
        target_drop_steps = float('inf')
        for drop in drop_table:
            if drop["Item"] == target_item_id: 
                target_drop_steps = drop["Steps"]
                break
                
        if target_drop_steps == float('inf'): return float('inf')
        base_act_steps = target_drop_steps / p_valid_quality
        
        children_cost = 0.0
        if node.inputs:
            steps_per_action = calculate_steps(activity_obj, player_lvl, stats.get("work_efficiency", 0.0), int(stats.get("flat_step_reduction", 0)), stats.get("percent_step_reduction", 0.0))
            for input_id, child_node in node.inputs.items():
                req_amount = child_node.base_requirement_amount
                input_ratio = (req_amount * target_drop_steps * (1.0 + DA)) / (steps_per_action * p_valid_quality)
                child_cost = calculate_node_cost(child_node, loadouts, game_data, drop_calc, player_skill_levels)
                children_cost += (input_ratio * child_cost)
                
        return base_act_steps + children_cost

    elif node.source_type == "chest":
        chest_obj = game_data['chests'].get(node.source_id)
        if not chest_obj: return float('inf')
        
        is_target_fine = target_item_id.endswith("_fine")
        base_target_id = target_item_id.replace("_fine", "")
        
        expected_items_per_chest = 0.0
        for drop in chest_obj.drops:
            if drop.item_id == base_target_id:
                chance = (drop.chance or 0.0) / 100.0
                avg_q = (drop.min_quantity + drop.max_quantity) / 2.0
                raw_expected = chance * 4.0 * avg_q
                
                has_fine = (base_target_id in drop_calc.fine_material_map or 
                            f"{base_target_id}_fine" in game_data.get('materials', {}) or 
                            f"{base_target_id}_fine" in game_data.get('consumables', {}))
                
                if has_fine:
                    if is_target_fine:
                        expected_items_per_chest += raw_expected * 0.01
                    else:
                        expected_items_per_chest += raw_expected * 0.99
                elif not is_target_fine:
                    expected_items_per_chest += raw_expected
                
        if expected_items_per_chest <= 0: return float('inf')
        
        chests_needed = 1.0 / expected_items_per_chest
        child_cost = 0.0
        
        if node.source_id in node.inputs:
            child_cost = calculate_node_cost(node.inputs[node.source_id], loadouts, game_data, drop_calc, player_skill_levels)
            
        return (chests_needed * child_cost) / p_valid_quality

    return 0.0

@st.cache_data
def load_data():
    base_path = "game_data/wiki_export/autogenerated"
    equipment_path = f"{base_path}/equipment.json"
    act_path = f"{base_path}/activities.json"
    rec_path = f"{base_path}/recipes.json"
    loc_path = f"{base_path}/locations.json"
    services_path = f"{base_path}/services.json"
    collectibles_path = f"{base_path}/collectibles.json"
    materials_path = f"{base_path}/materials.json"
    
    items, activities, recipes, locations, services, collectibles, materials = load_game_data(
        equipment_path, act_path, rec_path, loc_path, services_path, collectibles_path, materials_path
    )
    
    pets = []
    pets_path = f"{base_path}/pets.json"
    if os.path.exists(pets_path):
        try:
            with open(pets_path, "r", encoding="utf-8") as f:
                pets_data = json.load(f)
                for p_data in pets_data:
                    pets.append(Pet(**p_data))
        except Exception as e:
            st.error(f"Error loading pets.json: {e}")

    consumables = []
    cons_path = f"{base_path}/consumables.json"
    if os.path.exists(cons_path):
        try:
            with open(cons_path, "r", encoding="utf-8") as f:
                cons_data = json.load(f)
                for c_data in cons_data:
                    consumables.append(Consumable(**c_data))
        except Exception as e:
            st.error(f"Error loading consumables.json: {e}")

    containers = []
    cont_path = f"{base_path}/containers.json"
    if os.path.exists(cont_path):
        try:
            with open(cont_path, "r", encoding="utf-8") as f:
                cont_data = json.load(f)
                for c_data in cont_data: containers.append(Container(**c_data))
        except Exception as e: st.error(f"Error loading containers.json: {e}")
            
    return items, activities, recipes, locations, services, collectibles, pets, consumables, containers, materials


def get_pet_ability_map(game_data_dict: dict) -> Dict[str, List[str]]:
    """Returns a map of {ability_name: [pet_id, ...]} for all pets and their abilities."""
    ability_map: Dict[str, List[str]] = {}
    for pet in game_data_dict.get('pets', {}).values():
        for lvl in pet.levels:
            for ab in lvl.abilities:
                ability_map.setdefault(ab.name, [])
                if pet.id not in ability_map[ab.name]:
                    ability_map[ab.name].append(pet.id)
    return ability_map


def get_best_auto_pet(node: CraftingNode, game_data_dict: dict, loc_map: dict, drop_calc, user_ap: int = 0, total_lvl: int = 0, use_owned: bool = False, owned_pets: dict = None) -> Tuple[Optional[str], Optional[int]]:
    """Finds the best pet for a node based on stats, falling back to ability charging."""
    if not game_data_dict.get('pets'): return None, None
    if owned_pets is None: owned_pets = {}

    # 1. Build a dummy context for the node to test conditions
    activity_obj = None
    if node.source_type == "recipe":
        activity_obj = game_data_dict['recipes'].get(node.source_id)
    elif node.source_type in ["activity", "chest"]:
        act_id = node.source_id if node.source_type == "activity" else node.parent_activity_id
        activity_obj = game_data_dict['activities'].get(act_id)

    if not activity_obj: return None, None

    context = build_activity_context(activity_obj, user_ap, total_lvl, loc_map, drop_calc, getattr(node, 'selected_location_id', None))
    act_skill = (context.get("skill") or "").lower()
    loc_tags = context.get("location_tags", set())
    loc_id = (context.get("location_id") or "").lower()

    # 2. If the activity requires a specific pet ability, that pet is mandatory
    required_abilities = [
        r.target for r in getattr(activity_obj, 'requirements', [])
        if r.type == RequirementType.PET_ABILITY and r.target
    ]
    if required_abilities:
        ability_map = get_pet_ability_map(game_data_dict)
        for ability_name in required_abilities:
            provider_ids = ability_map.get(ability_name, [])
            for pid in provider_ids:
                pet = game_data_dict['pets'].get(pid)
                if not pet: continue
                if use_owned and pid not in owned_pets: continue
                if use_owned and pid in owned_pets:
                    target_lvl = owned_pets[pid]["level"]
                    eval_lvl_obj = next((l for l in pet.levels if l.level == target_lvl), None)
                    if not eval_lvl_obj:
                        valid = [l for l in pet.levels if l.level <= target_lvl]
                        eval_lvl_obj = valid[-1] if valid else None
                else:
                    eval_lvl_obj = pet.levels[-1] if pet.levels else None
                if eval_lvl_obj:
                    return pet.id, eval_lvl_obj.level
        # Required pet not available (not owned or not in data) — still return first provider if any
        for ability_name in required_abilities:
            provider_ids = ability_map.get(ability_name, [])
            for pid in provider_ids:
                pet = game_data_dict['pets'].get(pid)
                if not pet: continue
                eval_lvl_obj = pet.levels[-1] if pet.levels else None
                if eval_lvl_obj:
                    return pet.id, eval_lvl_obj.level

    # 3. Phase 1: Find a pet that gives active stats
    for pet in game_data_dict['pets'].values():
        # Skip if we are strictly using owned items and we don't own this pet
        if use_owned and pet.id not in owned_pets:
            continue
            
        eval_lvl_obj = None
        if use_owned and pet.id in owned_pets:
            target_lvl = owned_pets[pet.id]["level"]
            eval_lvl_obj = next((l for l in pet.levels if l.level == target_lvl), None)
            # Fallback if the exact level is missing from the pet's definition
            if not eval_lvl_obj:
                valid = [l for l in pet.levels if l.level <= target_lvl]
                if valid: eval_lvl_obj = valid[-1]
        else:
            eval_lvl_obj = pet.levels[-1] if pet.levels else None

        if not eval_lvl_obj: continue
        
        helps = False
        for mod in eval_lvl_obj.modifiers:
            applies = True
            for cond in mod.conditions:
                applies_cond, _ = check_condition_details(cond, context, Counter())
                if not applies_cond:
                    applies = False
                    break
            
            if applies:
                stat_name = mod.stat.value if hasattr(mod.stat, 'value') else mod.stat
                stat_val = mod.value
                
                if stat_name in ["steps_add", "steps_percent", "percent_step_reduction", "flat_step_reduction"]:
                    if stat_val < 0: helps = True # Negative steps are good
                elif stat_name == "inventory_space":
                    pass # Ignore inventory space for optimization
                elif stat_val > 0:
                    helps = True # Positive stats are good
                    
            if helps:
                break
                
        if helps:
            return pet.id, eval_lvl_obj.level

    # 4. Phase 2: Find a pet that can charge an active ability (Fallback)
    for pet in game_data_dict['pets'].values():
        # Skip if we are strictly using owned items and we don't own this pet
        if use_owned and pet.id not in owned_pets:
            continue
            
        eval_lvl_obj = None
        if use_owned and pet.id in owned_pets:
            target_lvl = owned_pets[pet.id]["level"]
            eval_lvl_obj = next((l for l in pet.levels if l.level == target_lvl), None)
            # Fallback if the exact level is missing from the pet's definition
            if not eval_lvl_obj:
                valid = [l for l in pet.levels if l.level <= target_lvl]
                if valid: eval_lvl_obj = valid[-1]
        else:
            eval_lvl_obj = pet.levels[-1] if pet.levels else None

        if not eval_lvl_obj: continue
        
        for ab in eval_lvl_obj.abilities:
            # Ensure the ability is explicitly supported in our constants
            if ab.name not in INSTANT_ACTION_PET_ABILITIES and ab.name not in BUFF_PET_ABILITIES:
                continue
                
            # Ignore time-based cooldowns or abilities with no step requirement
            if not ab.cooldown or "steps" not in ab.cooldown.lower():
                continue
                
            cd_lower = ab.cooldown.lower()
            charges_here = False
            
            # Parse: "4,000 stepsNot doing Agility."
            if "not doing" in cd_lower:
                forbidden = cd_lower.split("not doing")[1].replace(".", "").strip()
                if act_skill and act_skill != forbidden:
                    charges_here = True
                    
            # Parse: "4,000 stepsWhile doing Foraging."
            elif "while doing" in cd_lower:
                required = cd_lower.split("while doing")[1].replace(" recipes", "").replace(".", "").strip()
                if act_skill == required:
                    charges_here = True
                    
            # Parse: "5,000 stepsWhile in Underwater location."
            elif "while in" in cd_lower:
                required = cd_lower.split("while in")[1].replace(" location", "").replace(".", "").strip()
                if required in loc_tags or required == loc_id:
                    charges_here = True
                    
            if charges_here:
                return pet.id, eval_lvl_obj.level

    return None, None


def get_pet_charges_gained(pet_name: str, steps: float, game_data_dict: dict) -> Optional[float]:
    """Return ability charges gained by walking `steps` with the named pet.

    Looks up the pet's first step-based ability cooldown and divides steps by
    that cooldown value. Returns None if the pet has no step-based cooldown.
    """
    for pet in game_data_dict['pets'].values():
        if pet.name == pet_name:
            for lvl in pet.levels:
                for ab in lvl.abilities:
                    if ab.cooldown and "steps" in ab.cooldown.lower():
                        m = re.search(r'([\d,]+)\s*steps', ab.cooldown, re.IGNORECASE)
                        if m:
                            cd_steps = int(m.group(1).replace(',', ''))
                            return steps / cd_steps
    return None


def format_target_metric(t_name, raw_val, base_steps):
    t_name_lower = t_name.lower()
    if "no steps" in t_name_lower:
        return f"{raw_val:.2f} Output per Action"
    elif "reward rolls" in t_name_lower:
        human_val = 1.0 / raw_val if raw_val > 0 else 0
        return f"{human_val:.2f} Steps/Roll" if raw_val > 0 else "∞ Steps/Roll"
    elif "xp per material" in t_name_lower:
        return f"{raw_val:.2f} XP/Mat"
    elif "chests per material" in t_name_lower:
        materials_per_chest = 250.0 / raw_val if raw_val > 0 else 0
        return f"{materials_per_chest:.1f} Materials per Chest"
    elif "xp" in t_name_lower:
        return f"{raw_val:.2f} XP/Step"
    elif "chests" in t_name_lower:
        human_val = 250.0 / raw_val if raw_val > 0 else 0
        return f"{human_val:.1f} Steps/Chest" if raw_val > 0 else "∞ Steps/Chest"
    elif "materials from input" in t_name_lower:
        return f"{raw_val:.3f} Output Ratio"
    elif "fine" in t_name_lower:
        human_val = 100.0 / raw_val if raw_val > 0 else 0
        return f"{human_val:.1f} Steps/Fine Roll" if raw_val > 0 else "∞ Steps/Fine Roll"
    elif "collectibles" in t_name_lower:
        relative_mult = raw_val * base_steps
        return f"{relative_mult:.2f}x Collectibles Base Rate"
    elif "gems" in t_name_lower:
        relative_mult = raw_val * base_steps
        return f"{relative_mult:.2f}x Gems Base Rate"
    elif "coins" in t_name_lower:
        human_val = raw_val * 1000.0
        return f"{human_val:.2f} Coins/1k Steps"
    elif "net profit" in t_name_lower:
        human_val = raw_val * 1000.0
        return f"{human_val:.4f} Coins/1k Steps"
    elif "eternal per input" in t_name_lower:
        human_val = 1.0 / raw_val if raw_val > 0 else float('inf')
        return f"{human_val:.2f} Inputs/Eternal" if raw_val > 0 else "∞ Inputs/Eternal"
    elif any(q in t_name_lower for q in ["good per step", "great per step", "excellent per step", "perfect per step", "eternal per step"]):
        human_val = 1.0 / raw_val if raw_val > 0 else float('inf')
        display_tier_name = t_name.split()[0].title()
        return f"{human_val:.2f} Steps/{display_tier_name}" if raw_val > 0 else f"∞ Steps/{display_tier_name}"
    
    elif "per step" in t_name_lower and not ("xp" in t_name_lower or "quality" in t_name_lower or "eternal" in t_name_lower):
        # A catch-all for all our new specific item trigger drops
        human_val = 1.0 / raw_val if raw_val > 0 else float('inf')
        item_label = t_name.split(" Per Step")[0]
        return f"{human_val:.2f} Steps / {item_label}" if raw_val > 0 else f"∞ Steps / {item_label}"
    else:
        return f"{raw_val:.4f}"



def get_filtered_consumables(all_consumables: List[Consumable], selected_obj: Any) -> List[Consumable]:
    if not selected_obj:
        return all_consumables
        
    skill = getattr(selected_obj, 'primary_skill', getattr(selected_obj, 'skill', "")).lower()
    is_activity = isinstance(selected_obj, Activity)
    
    filtered_with_priority = []
    
    for cons in all_consumables:
        is_global = False
        is_skill_match = False
        
        for mod in cons.modifiers:
            for cond in mod.conditions:
                c_type = getattr(cond.type, 'value', cond.type)
                
                if c_type == "global":
                    is_global = True
                elif c_type == "skill_activity":
                    c_target = cond.target.lower() if cond.target else ""
                    if (c_target == skill or 
                        (c_target == "gathering" and skill in GATHERING_SKILLS) or
                        (c_target == "artisan" and skill in ARTISAN_SKILLS) or
                        (c_target == "utility" and skill in UTILITY_SKILLS)):
                        is_skill_match = True

        if is_skill_match or is_global:
            if is_activity:
                clean_modifiers = [
                    mod for mod in cons.modifiers 
                    if mod.stat != StatName.QUALITY_OUTCOME
                ]
                if clean_modifiers:
                    cons_copy = cons.model_copy(update={"modifiers": tuple(clean_modifiers)})
                    priority = 1 if (is_global and not is_skill_match) else 0
                    filtered_with_priority.append((priority, cons_copy))
            else:
                priority = 1 if (is_global and not is_skill_match) else 0
                filtered_with_priority.append((priority, cons))

    filtered_with_priority.sort(key=lambda x: (x[0], x[1].name))
    
    return [item[1] for item in filtered_with_priority]