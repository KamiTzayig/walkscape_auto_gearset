import math
from typing import Dict, List, Optional, Any, Set, Tuple, Union
from collections import defaultdict
from models import Activity, GearSet, Collectible, ConditionType, StatName,CraftingNode, Loadout,Location 
from utils.constants import OPTIMAZATION_TARGET, PERCENTAGE_STATS, GATHERING_SKILLS, ARTISAN_SKILLS, EquipmentQuality, QUALITY_NAMES
import re

# ============================================================================
# CORE CALCULATIONS
# ============================================================================

def calculate_steps(
   activity: Activity,
   player_skill_level: int,
   player_work_efficiency: float,
   player_minus_steps: int,
   player_minus_steps_percent: float,
) -> int:
    """
    Calculates steps based on the Wiki Formula.
    """
    level_diff = max(0, player_skill_level - activity.level)
    level_eff = min(0.25, level_diff * 0.0125)

    total_added_eff = level_eff + player_work_efficiency
    efficiency_multiplier = min(1.0 + total_added_eff, activity.max_efficiency)

    step_multiplier_factor = 1.0 - player_minus_steps_percent

    base_over_eff = activity.base_steps / efficiency_multiplier
    after_percent = base_over_eff * step_multiplier_factor
    after_flat = after_percent - float(player_minus_steps)
    
    val_floored = max(10.0, after_flat)
    return int(math.ceil(val_floored))

def calculate_quality_probabilities(
    activity_min_level: int,
    player_skill_level: int,
    quality_bonus: float,
    is_fine_materials: bool = False,
    is_equipment_upgrade: bool = False
) -> dict[str, float]:
    """Calculates the probability of each quality tier, applying fine material shifts."""
    level_diff_bonus = max(0, player_skill_level - activity_min_level)
    total_outcome = level_diff_bonus + quality_bonus
    
    band_starts = [0, 100, 200, 300, 400, 500]
    start_weights = [1000.0, 200.0, 50.0, 10.0, 2.5, 0.05]
    min_weights = [4.0, 4.0, 4.0, 4.0, 2.0, 0.05]
    
    current_weights = []

    for i in range(6):
        tier_mult = i + 1
        band_start = band_starts[i]
        
        if total_outcome > band_start:
            band_end = (100 + activity_min_level) * tier_mult
            denom = band_start - band_end
            slope = 0 if denom == 0 else (start_weights[i] - min_weights[i]) / denom
            calculated_weight = start_weights[i] + (slope * (total_outcome - band_start))
            current_weights.append(max(calculated_weight, min_weights[i]))
        else:
            current_weights.append(start_weights[i])

    for i in range(4, -1, -1):
        if current_weights[i] < current_weights[i+1]:
             current_weights[i] = current_weights[i+1]

    total_weight = sum(current_weights)
    if total_weight == 0: return {k: 0.0 for k in QUALITY_NAMES}
    
    base_pct = {k: (w / total_weight) for k, w in zip(QUALITY_NAMES, current_weights)}
    
    # --- FINE MATERIAL PROBABILITY TRANSFER ---
    if is_fine_materials:
        transfer_rate = 0.3 if is_equipment_upgrade else 1.0
        new_pct = {}
        
        t_norm = base_pct["Normal"] * transfer_rate
        new_pct["Normal"] = base_pct["Normal"] - t_norm
        
        t_good = base_pct["Good"] * transfer_rate
        new_pct["Good"] = base_pct["Good"] - t_good + t_norm
        
        t_great = base_pct["Great"] * transfer_rate
        new_pct["Great"] = base_pct["Great"] - t_great + t_good
        
        t_exc = base_pct["Excellent"] * transfer_rate
        new_pct["Excellent"] = base_pct["Excellent"] - t_exc + t_great
        
        t_perf = base_pct["Perfect"] * transfer_rate
        new_pct["Perfect"] = base_pct["Perfect"] - t_perf + t_exc
        
        # Eternal just absorbs Perfect's transfer, no reduction
        new_pct["Eternal"] = base_pct["Eternal"] + t_perf
        
        return new_pct
    else:
        return base_pct
# ============================================================================
# SCORING LOGIC
# ============================================================================

def calculate_score(
    gear_set: GearSet, 
    activity: Activity, 
    player_skill_level: int, 
    target: Union[OPTIMAZATION_TARGET, List[Tuple[OPTIMAZATION_TARGET, float]]], 
    context: Dict, 
    ignore_requirements: bool = False, 
    passive_stats: Dict[str, float] = None,
    normalization_context: Dict[OPTIMAZATION_TARGET, Tuple[float, float]] = None
) -> float:
    """
    The Master Score Function.
    Calculates the 'Utility Value' of a gear set based on the optimization target.
    Supports single OPTIMAZATION_TARGET or a list of weighted targets for composite scoring.
    """
    # 1. Check Requirements
    if not ignore_requirements:
        required_keywords = context.get("required_keywords", {})
        if required_keywords:
            set_keywords = gear_set.get_requirement_counts(list(required_keywords.keys()))
            deficit = 0
            for req_kw, req_count in required_keywords.items():
                curr_count = set_keywords.get(req_kw, 0)
                if curr_count < req_count: 
                    deficit += (req_count - curr_count)
            if deficit > 0: 
                return -10000.0 * deficit

    # 2. Get Stats
    stats = gear_set.get_stats(context)
    if passive_stats:
        for k, v in passive_stats.items():
            stats[k] = stats.get(k, 0.0) + v

    # 3. Handle Composite Scoring
    if isinstance(target, list):
        if not normalization_context:
            # Fallback: Treat as sum of raw scores (not recommended due to magnitude mismatch)
            total_score = 0.0
            for sub_target, weight in target:
                raw_score = _calculate_single_target_score(sub_target, activity, player_skill_level, stats,context)
                total_score += raw_score * weight
            return total_score
        
        composite_score = 0.0
        for sub_target, weight in target:
            raw_score = _calculate_single_target_score(sub_target, activity, player_skill_level, stats, context)
            baseline, range_val = normalization_context.get(sub_target, (0.0, 1.0))
            
            # Normalize: (Score - Baseline) / (Max - Baseline)
            # 0% = Baseline, 100% = Max
            if range_val == 0: normalized = 0.0
            else: normalized = (raw_score - baseline) / range_val
            
            composite_score += normalized * weight
            
        return composite_score

    # 4. Handle Single Target Scoring
    else:
        return _calculate_single_target_score(target, activity, player_skill_level, stats, context)

def _calculate_single_target_score(target: OPTIMAZATION_TARGET, activity: Activity, player_skill_level: int, stats: Dict[str, float], context: Dict = None) -> float:
    """Helper to calculate raw score for a single target from stats."""
    if context is None: context = {}
    # Calculate Steps
    steps = calculate_steps(
        activity=activity,
        player_skill_level=player_skill_level, 
        player_work_efficiency=stats.get("work_efficiency", 0),
        player_minus_steps=stats.get("flat_step_reduction", 0),
        player_minus_steps_percent=stats.get("percent_step_reduction", 0)
    )
    steps = max(1, steps)

    # Multipliers
    da_val = min(1.0, stats.get("double_action", 0))
    dr_val = min(1.0,stats.get("double_rewards", 0) )
    nmc_val = min(0.99, stats.get("no_materials_consumed", 0)) 
        
    da_mult = 1.0 + da_val
    dr_mult = 1.0 + dr_val
    nmc_mult = 1.0 / (1.0 - nmc_val)
    
    is_fine = context.get("is_fine_materials", False) if context else False
    is_upg = context.get("is_equipment_upgrade", False) if context else False
    
    fine_conversion_rate = min(1.0, 0.01 * (1.0 + stats.get("fine_material_finding", 0.0)))
    
    val = 0.0
    if target == OPTIMAZATION_TARGET.reward_rolls:
        val = (da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.reward_rolls_no_steps:
        val = (da_mult * dr_mult)
    elif target == OPTIMAZATION_TARGET.xp:
        base_xp = activity.base_xp or 0
        xp_mult = 1.0 + stats.get("xp_percent", 0)
        flat_xp = stats.get("flat_xp", 0)
        val = (((base_xp + flat_xp) * xp_mult) * da_mult) / steps
    elif target == OPTIMAZATION_TARGET.exp_no_steps:
        base_xp = activity.base_xp or 0
        xp_mult = 1.0 + stats.get("xp_percent", 0)
        flat_xp = stats.get("flat_xp", 0)
        val = (((base_xp + flat_xp) * xp_mult) * da_mult)
    elif target == OPTIMAZATION_TARGET.xp_per_material:
        mats = activity.materials[0] if activity.materials else ()
        total_mats = max(1, sum(m.amount for m in mats))
        base_xp = activity.base_xp or 0
        xp_mult = 1.0 + stats.get("xp_percent", 0)
        flat_xp = stats.get("flat_xp", 0)
        val = ((base_xp + flat_xp) * xp_mult * nmc_mult) / total_mats
    elif target == OPTIMAZATION_TARGET.chests:
        val = ((1.0 + stats.get("chest_finding", 0)) * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.chests_no_steps:
        val = ((1.0 + stats.get("chest_finding", 0)) * da_mult * dr_mult)
    elif target == OPTIMAZATION_TARGET.chests_per_material:
        val = (1.0 + stats.get("chest_finding", 0)) * dr_mult * nmc_mult
    elif target == OPTIMAZATION_TARGET.materials_from_input:
        val = (dr_mult * nmc_mult)
    elif target == OPTIMAZATION_TARGET.fine:
        val = ((1.0 + stats.get("fine_material_finding", 0)) * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.fine_no_steps:
        val = ((1.0 + stats.get("fine_material_finding", 0)) * da_mult * dr_mult)
    elif target == OPTIMAZATION_TARGET.eternal_per_input:
        flat_quality_bonus = stats.get("quality_outcome", 0)
        probs = calculate_quality_probabilities(
            activity_min_level=activity.level, 
            player_skill_level=player_skill_level,
            quality_bonus=flat_quality_bonus,
            is_fine_materials=is_fine,
            is_equipment_upgrade=is_upg
        )
        score_q = probs.get("Eternal", 0.0)
        val = score_q * dr_mult * nmc_mult
        
    elif target in [OPTIMAZATION_TARGET.good_per_step, OPTIMAZATION_TARGET.great_per_step, 
                    OPTIMAZATION_TARGET.excellent_per_step, OPTIMAZATION_TARGET.perfect_per_step, 
                    OPTIMAZATION_TARGET.eternal_per_step]:
        flat_quality_bonus = stats.get("quality_outcome", 0)
        probs = calculate_quality_probabilities(
            activity_min_level=activity.level, 
            player_skill_level=player_skill_level,
            quality_bonus=flat_quality_bonus,
            is_fine_materials=is_fine,
            is_equipment_upgrade=is_upg
        )
        
        if target == OPTIMAZATION_TARGET.good_per_step:
            score_q = probs.get("Good", 0.0)
        elif target == OPTIMAZATION_TARGET.great_per_step:
            score_q = probs.get("Great", 0.0)
        elif target == OPTIMAZATION_TARGET.excellent_per_step:
            score_q = probs.get("Excellent", 0.0)
        elif target == OPTIMAZATION_TARGET.perfect_per_step:
            score_q = probs.get("Perfect", 0.0)
        elif target == OPTIMAZATION_TARGET.eternal_per_step:
            score_q = probs.get("Eternal", 0.0)
            
        val = (score_q * da_mult * dr_mult) / steps

    elif target == OPTIMAZATION_TARGET.tokens_per_step:
        chance = stats.get("find_adventurers_guild_token", 0)
        val = (chance * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.ectoplasm_per_step:
        chance = stats.get("find_ectoplasm", 0)
        val = (chance * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.fine_ectoplasm_per_step:
        chance = stats.get("find_ectoplasm", 0)
        val = (chance * fine_conversion_rate * da_mult * dr_mult) / steps

    elif target == OPTIMAZATION_TARGET.sea_shells_per_step:
        chance = stats.get("find_sea_shells", 0)
        val = (chance * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.fine_sea_shells_per_step:
        chance = stats.get("find_sea_shells", 0)
        val = (chance * fine_conversion_rate * da_mult * dr_mult) / steps

    elif target == OPTIMAZATION_TARGET.crustacean_per_step:
        chance = stats.get("find_crustacean", 0)
        val = (chance * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.fine_crustacean_per_step:
        chance = stats.get("find_crustacean", 0)
        val = (chance * fine_conversion_rate * da_mult * dr_mult) / steps

    elif target == OPTIMAZATION_TARGET.fibrous_plant_per_step:
        chance = stats.get("find_fibrous_plant", 0)
        val = (chance * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.fine_fibrous_plant_per_step:
        chance = stats.get("find_fibrous_plant", 0)
        val = (chance * fine_conversion_rate * da_mult * dr_mult) / steps

    elif target == OPTIMAZATION_TARGET.fishing_bait_per_step:
        chance = stats.get("find_fishing_bait", 0)
        val = (chance * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.fine_fishing_bait_per_step:
        chance = stats.get("find_fishing_bait", 0)
        val = (chance * fine_conversion_rate * da_mult * dr_mult) / steps

    elif target == OPTIMAZATION_TARGET.gold_nugget_per_step:
        chance = stats.get("find_gold_nugget", 0)
        val = (chance * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.fine_gold_nugget_per_step:
        chance = stats.get("find_gold_nugget", 0)
        val = (chance * fine_conversion_rate * da_mult * dr_mult) / steps

    elif target == OPTIMAZATION_TARGET.find_random_gem_per_step:
        chance = stats.get("find_random_gem", 0)
        val = (chance * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.find_random_fine_gem_per_step:
        chance = stats.get("find_random_gem", 0)
        val = (chance * fine_conversion_rate * da_mult * dr_mult) / steps

    elif target == OPTIMAZATION_TARGET.gem_finding:
        gem_mult = 1.0 + stats.get("find_gems", 0)
        val = (gem_mult * da_mult * dr_mult) / steps
    elif target == OPTIMAZATION_TARGET.gem_finding_fine:
        gem_mult = 1.0 + stats.get("find_gems", 0)
        val = (gem_mult * fine_conversion_rate * da_mult * dr_mult) / steps

    elif target == OPTIMAZATION_TARGET.collectibles:
        val = ((1.0 + stats.get("find_collectibles", 0)) * da_mult * dr_mult) / steps
 
    elif target == OPTIMAZATION_TARGET.collectibles_no_steps:
        val = ((1.0 + stats.get("find_collectibles", 0)) * da_mult * dr_mult)

    elif target in [OPTIMAZATION_TARGET.coins, OPTIMAZATION_TARGET.coins_no_chests, 
                    OPTIMAZATION_TARGET.coins_no_fines, OPTIMAZATION_TARGET.coins_no_chests_no_fines]:
        
        base_normal = getattr(activity, 'normal_roll_worth', 0.0)
        base_chest = getattr(activity, 'chest_roll_worth', 0.0)
        base_fine = getattr(activity, 'fine_roll_worth', 0.0)
        
        allow_chests = target in [OPTIMAZATION_TARGET.coins, OPTIMAZATION_TARGET.coins_no_fines]
        allow_fines = target in [OPTIMAZATION_TARGET.coins, OPTIMAZATION_TARGET.coins_no_chests]
        
        chest_bonus = stats.get("chest_finding", 0.0)

        if allow_fines:
            ev_normal = base_normal * (1.0 - fine_conversion_rate)
            ev_fine = base_fine * fine_conversion_rate
        else:
            ev_normal = base_normal
            ev_fine = 0.0
            
        ev_chest = (base_chest * (1.0 + chest_bonus)) if allow_chests else 0.0
            
        ev_special = 0.0
        special_ev_map = context.get("special_ev_map", {})
        
        for stat_key, ev_data in special_ev_map.items():
            chance = stats.get(stat_key, 0.0)
            if chance <= 0: continue
            
            is_chest = stat_key in ["chance_to_find_bird_nest", "find_coin_pouch", "find_skill_chest"]
            if is_chest and not allow_chests:
                continue
                
            if allow_fines and not is_chest:
                ev_special += chance * (ev_data["normal"] * (1.0 - fine_conversion_rate) + ev_data["fine"] * fine_conversion_rate)
            else:
                ev_special += chance * ev_data["normal"]
        
        # Total EV per activity roll
        total_ev_per_roll = ev_normal + ev_fine + ev_chest + ev_special        
        
        val = (total_ev_per_roll * da_mult * dr_mult) / steps
    
    return val

def analyze_score(gear_set: GearSet, activity, player_skill_level, target, context, passive_stats: Dict[str, float] = None, normalization_context=None):
    """Debug helper to explain the score."""
    val = calculate_score(gear_set, activity, player_skill_level, target, context, passive_stats=passive_stats, normalization_context=normalization_context)
    stats = gear_set.get_stats(context)
    if passive_stats:
        for k,v in passive_stats.items(): stats[k] = stats.get(k, 0.0) + v
    
    steps = calculate_steps(
        activity=activity,
        player_skill_level=player_skill_level, 
        player_work_efficiency=stats.get("work_efficiency", 0),
        player_minus_steps=stats.get("flat_step_reduction", 0),
        player_minus_steps_percent=stats.get("percent_step_reduction", 0)
    )
    
    target_breakdown = []
    
    if isinstance(target, list) and normalization_context:
        for sub_target, weight in target:
            raw_score = _calculate_single_target_score(sub_target, activity, player_skill_level, stats, context)
            baseline, range_val = normalization_context.get(sub_target, (0.0, 1.0))
            
            # Normalize
            if range_val == 0: normalized = 0.0
            else: normalized = (raw_score - baseline) / range_val
            
            contribution = normalized * weight
            
            target_breakdown.append({
                "target": sub_target.name.replace("_", " ").title(),
                "weight": weight,
                "raw_value": raw_score,
                "baseline": baseline,
                "max_val": baseline + range_val,
                "normalized": normalized,
                "contribution": contribution
            })
    
    return {
        "score": val,
        "steps": steps,
        "denominator": steps,
        "stats": stats,
        "target_breakdown": target_breakdown
    }

def calculate_passive_stats(collectibles: List[Collectible], context: Dict) -> Dict[str, float]:
    """
    Calculates stats from collected items (Passive).
    """
    stats = defaultdict(float)
    active_skill = context.get("skill", "").lower() if context.get("skill") else None
    loc_id = context.get("location_id")
    loc_tags = context.get("location_tags", set())
    act_id = context.get("activity_id")
    user_ap = context.get("achievement_points", 0)
    total_lvl = context.get("total_skill_level", 0)

    for item in collectibles:
        for mod in item.modifiers:
            applies = True
            for condition in mod.conditions:
                c_type = condition.type
                c_target = condition.target.lower() if condition.target else None
                c_val = condition.value
                if c_type == ConditionType.GLOBAL: continue 
                elif c_type == ConditionType.SKILL_ACTIVITY:
                    if not active_skill: applies = False 
                    elif c_target:
                        if c_target == active_skill: pass
                        elif c_target == "gathering" and active_skill in GATHERING_SKILLS: pass
                        elif c_target == "artisan" and active_skill in ARTISAN_SKILLS: pass
                        else: applies = False
                elif c_type == ConditionType.LOCATION:
                    if not loc_id: applies = False
                    else:
                        if not (c_target == loc_id.lower() or c_target in loc_tags): applies = False
                elif c_type == ConditionType.REGION:
                    if not loc_tags: applies = False
                    elif c_target and c_target not in loc_tags: applies = False
                elif c_type == ConditionType.SPECIFIC_ACTIVITY:
                    if not act_id: applies = False
                    elif c_target and c_target != act_id.lower(): applies = False
                elif c_type == ConditionType.ACHIEVEMENT_POINTS:
                    if user_ap < (c_val or 0): applies = False
                elif c_type == ConditionType.TOTAL_SKILL_LEVEL:
                    if total_lvl < (c_val or 0): applies = False
            
            if applies:
                stat_key = mod.stat.value
                value = mod.value
                if mod.stat in PERCENTAGE_STATS: value = value / 100.0
                if stat_key == StatName.BONUS_XP_ADD.value: stat_key = "flat_xp"
                elif stat_key == StatName.BONUS_XP_PERCENT.value: stat_key = "xp_percent"
                elif stat_key == StatName.XP_PERCENT.value: stat_key = "xp_percent"
                elif stat_key == StatName.STEPS_ADD.value: 
                    stat_key = "flat_step_reduction"
                    value = -value 
                elif stat_key == StatName.STEPS_PERCENT.value: 
                    stat_key = "percent_step_reduction"
                    value = -value
                stats[stat_key] += value
    return dict(stats)

class MockActivity:
    """A lightweight wrapper to pass Recipe objects into calculate_steps."""
    def __init__(self, level, base_steps, max_efficiency):
        self.level = level
        self.base_steps = base_steps
        self.max_efficiency = max_efficiency

def get_actions_per_charge(effect: str) -> int:
    """Extracts the number of actions a pet ability completes instantly."""
    m = re.search(r'[Cc]ompletes (\d+) ', effect)
    if m: return int(m.group(1))
    return 0

def calculate_node_metrics(
    node: 'CraftingNode', 
    loadouts: Dict[str, 'Loadout'], 
    game_data: Dict[str, Any], 
    drop_calc: Any, 
    player_skill_levels: Dict[str, int],
    user_state: Dict[str, Any],
    locations: List['Location'],
    global_target_quality: str = "Normal",
    global_use_fine: bool = False
) -> Dict[str, Any]:
    
    from ui_utils import build_activity_context, synthesize_activity_from_recipe, extract_modifier_stats
    
    res = {
        "steps": float('inf'),
        "xp": defaultdict(float),
        "local_steps": 0.0,
        "local_xp": defaultdict(float),
        "shopping_list": defaultdict(float),
        "raw_materials": defaultdict(float),
        "stats_used": {},
        "steps_breakdown": defaultdict(float),
        "steps_by_skill": defaultdict(float),
        "pet_steps_gained": defaultdict(float),
        "ability_charges_used": defaultdict(float),
        "consumable_steps_needed": defaultdict(float)
    }

    if node.source_type == "bank":
        res["steps"] = 0.0
        res["shopping_list"][node.item_id] += 1.0
        res["raw_materials"][node.item_id] += 1.0
        return res

    # 1. Resolve Base GearSet
    if getattr(node, "loadout_id", None) == "AUTO" and getattr(node, "auto_gear_set", None):
        base_gear = node.auto_gear_set
    else:
        loadout = loadouts.get(node.loadout_id) if getattr(node, "loadout_id", None) else None
        base_gear = loadout.gear_set if loadout else None
    
    gear_set_eval = base_gear.clone() if base_gear else GearSet()

    # 2. Inject Node-Specific Pets & Consumables
    pet_obj = None
    active_ability = None
    if getattr(node, 'selected_pet_id', None):
        pet_obj = game_data.get('pets', {}).get(node.selected_pet_id)
        if pet_obj: 
            pet_lvl = getattr(node, 'selected_pet_level', 1) or 1
            pet_obj = pet_obj.model_copy(update={"active_level": pet_lvl})
            gear_set_eval.pet = pet_obj
            
            # Find the active ability to calculate charge cost
            for lvl in pet_obj.levels:
                if lvl.level == pet_lvl and lvl.abilities:
                    active_ability = lvl.abilities[0]
        
    cons = None
    if getattr(node, 'selected_consumable_id', None):
        cons = game_data.get('consumables', {}).get(node.selected_consumable_id)
        if cons: gear_set_eval.consumable = cons

    target_item_id = node.item_id
    if global_use_fine and not target_item_id.endswith("_fine"):
        target_item_id = f"{target_item_id}_fine"

    # 3. Resolve Activity & Service Synthesizing
    recipe_obj, activity_obj = None, None
    skill_name, min_level, base_xp = "", 1, 0.0
    
    if node.source_type == "recipe":
        recipe_obj = game_data['recipes'].get(node.source_id)
        activity_obj = recipe_obj 
        if recipe_obj and getattr(node, 'selected_service_id', None):
            srv = game_data.get('services', {}).get(node.selected_service_id)
            if srv: activity_obj = synthesize_activity_from_recipe(recipe_obj, srv)
        
        if recipe_obj: skill_name, min_level, base_xp = recipe_obj.skill, recipe_obj.level, recipe_obj.base_xp

    elif node.source_type in ["activity", "chest"]:
        act_id = node.source_id if node.source_type == "activity" else node.parent_activity_id
        activity_obj = game_data['activities'].get(act_id)
        if activity_obj: skill_name, min_level, base_xp = activity_obj.primary_skill, activity_obj.level, activity_obj.base_xp

    if not activity_obj: return res

    player_lvl = player_skill_levels.get(skill_name.lower(), 99) if skill_name else 99

    # 4. Context & Passive Stats Injection
    loc_map = {loc.id: loc for loc in locations}
    
    context = build_activity_context(
        activity=activity_obj, 
        user_ap=user_state.get('user_ap', 0), 
        user_total_level=user_state.get('user_total_level', 0), 
        loc_map=loc_map, 
        drop_calc=drop_calc, 
        selected_location_id=getattr(node, 'selected_location_id', None)
    )

    stats = gear_set_eval.get_stats(context)
    passive_stats = calculate_passive_stats(user_state.get('owned_collectibles', []), context)
    
    # Inject service modifiers into passive stats if present
    if hasattr(activity_obj, 'modifiers') and activity_obj.modifiers:
        act_mods = extract_modifier_stats(activity_obj.modifiers)
        for k, v in act_mods.items():
            passive_stats[k] = passive_stats.get(k, 0.0) + v
    if node.source_type == "activity" and node.inputs:
        for input_id, child_node in node.inputs.items():
            mat_item_id = child_node.item_id
            mat_obj = game_data.get('materials', {}).get(mat_item_id) or game_data.get('consumables', {}).get(mat_item_id)
            if mat_obj and hasattr(mat_obj, 'modifiers') and mat_obj.modifiers:
                from ui_utils import extract_modifier_stats
                mat_stats = extract_modifier_stats(mat_obj.modifiers)
                for k, v in mat_stats.items():
                    passive_stats[k] = passive_stats.get(k, 0.0) + v       
    # Combine everything
    for k, v in passive_stats.items():
        stats[k] = stats.get(k, 0.0) + v

    # --- Math Extraction ---
    DA = min(1.0, stats.get("double_action", 0.0))
    DR = min(1.0, stats.get("double_rewards", 0.0))
    NMC = min(0.99, stats.get("no_materials_consumed", 0.0))
    XP_BONUS = stats.get("xp_percent", 0.0)
    FLAT_XP = stats.get("flat_xp", 0.0)
    WE = stats.get("work_efficiency", 0.0)
    
    # --- Quality Probability ---
    p_valid_quality = 1.0
    if global_target_quality not in ["Normal", "None"]:
        from utils.constants import QUALITY_RANK
        target_rank = QUALITY_RANK.get(global_target_quality, 0)
        
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
            is_fine_materials=global_use_fine,
            is_equipment_upgrade=is_equipment_upgrade
        )
        valid_tiers = [q.value for q, r in QUALITY_RANK.items() if r >= target_rank and q != "None"]
        p_valid_quality = sum(probs.get(q, 0.0) for q in valid_tiers)
        if p_valid_quality <= 0.00001: return res

    res["stats_used"] = {
        "DA": DA, "DR": DR, "NMC": NMC, "WE": WE, "XP_BONUS": XP_BONUS,
        "p_valid_quality": p_valid_quality,
        "base_steps": activity_obj.base_steps if activity_obj else 0
    }
    res["debug"] = {
        "node_item_id": node.item_id,
        "target_item_id": target_item_id,
        "global_use_fine": global_use_fine,
        "source_type": node.source_type,
        "source_id": node.source_id,
        "activity_id": activity_obj.id if activity_obj else None,
        "activity_name": activity_obj.name if activity_obj else None,
        "gear_target": node.auto_optimize_target[0]["target"] if getattr(node, "auto_optimize_target", None) else None,
        "tree_opt_score": getattr(node, "_tree_opt_score", None),
        "DA": DA,
        "DR": DR,
    }
 
    is_using_ability = False
    instant_pet = None
    instant_ability = None

    if getattr(node, 'use_pet_ability', False):
        from ui_utils import get_applicable_abilities
        applicable = get_applicable_abilities(node, game_data)
        if applicable:
            instant_pet, instant_ability = applicable[0] # Grab the first valid ability
            if get_actions_per_charge(instant_ability.effect) > 0:
                is_using_ability = True
    # ==========================================
    # RECIPE
    # ==========================================
    if node.source_type == "recipe":
        steps_per_action = calculate_steps(recipe_obj, player_lvl, WE, int(stats.get("flat_step_reduction", 0)), stats.get("percent_step_reduction", 0.0))
        res["debug"]["activity_base_steps"] = activity_obj.base_steps
        res["debug"]["activity_obj_type"] = type(activity_obj).__name__
        res["debug"]["activity_obj_dict_base_steps"] = activity_obj.__dict__.get("base_steps", "NOT_IN_DICT") if hasattr(activity_obj, "__dict__") else "NO_DICT"
        res["debug"]["all_matching_activities"] = None
        res["debug"]["same_object"] = None
        res["debug"]["activity_max_efficiency"] = activity_obj.max_efficiency
        res["debug"]["activity_level"] = min_level
        res["debug"]["player_lvl"] = player_lvl
        res["debug"]["WE"] = WE
        res["debug"]["flat_step_reduction"] = int(stats.get("flat_step_reduction", 0))
        res["debug"]["percent_step_reduction"] = stats.get("percent_step_reduction", 0.0)
        res["debug"]["steps_per_action"] = steps_per_action
        res["debug"]["NMC"] = NMC
        res["debug"]["quality_outcome"] = stats.get("quality_outcome", 0)
        q_out = recipe_obj.output_quantity
        
        actions_needed = 1.0 / ((1.0 + DA) * (1.0 + DR) * q_out * p_valid_quality)
        normal_steps = actions_needed * steps_per_action
        res["debug"]["output_quantity"] = q_out
        res["debug"]["actions_needed"] = round(actions_needed, 6)
        
        if is_using_ability:
            res["steps"] = 0.0
            charges = actions_needed / get_actions_per_charge(instant_ability.effect)
            res["ability_charges_used"][f"{instant_pet.name}: {instant_ability.name}"] += charges
            res["steps_breakdown"][f"⚡ Recipe: {recipe_obj.name} (Instant)"] += 0.0
        else:
            res["steps"] = normal_steps
            res["steps_breakdown"][f"Recipe: {recipe_obj.name}"] += normal_steps
            if skill_name: res["steps_by_skill"][skill_name.lower()] += normal_steps
            if pet_obj: res["pet_steps_gained"][pet_obj.name] += normal_steps
            if cons: res["consumable_steps_needed"][node.selected_consumable_id] += normal_steps * (1 + DA)

        isolated_xp = (((base_xp + FLAT_XP) * (1.0 + XP_BONUS))) / ((1.0 + DR) * q_out * p_valid_quality)
        if skill_name: res["xp"][skill_name.lower()] += isolated_xp
        res["local_steps"] = normal_steps if not is_using_ability else 0.0
        if skill_name: res["local_xp"][skill_name.lower()] += isolated_xp
        for sk in GATHERING_SKILLS | ARTISAN_SKILLS:
            gain_xp = stats.get(f"gain_{sk}_xp", 0.0)
            if gain_xp > 0:
                res["xp"][sk] += gain_xp * actions_needed
        for input_id, child_node in node.inputs.items():
            req_amount = child_node.base_requirement_amount
            input_ratio = ((1.0 - NMC) * req_amount) / ((1.0 + DR) * q_out * p_valid_quality)

            # Pass user_state and locations down into the recursion
            child_metrics = calculate_node_metrics(
                child_node, loadouts, game_data, drop_calc, player_skill_levels,
                user_state, locations,
                global_use_fine=global_use_fine
            )

            res["steps"] += (input_ratio * child_metrics["steps"])
            for sk, xpv in child_metrics["xp"].items(): res["xp"][sk] += (input_ratio * xpv)
            for item_k, amt in child_metrics["shopping_list"].items(): res["shopping_list"][item_k] += (input_ratio * amt)
            for item_k, amt in child_metrics["raw_materials"].items(): res["raw_materials"][item_k] += (input_ratio * amt)
            for src, stp in child_metrics["steps_breakdown"].items(): res["steps_breakdown"][src] += (input_ratio * stp)
            for sk, stp in child_metrics["steps_by_skill"].items(): res["steps_by_skill"][sk] += (input_ratio * stp)
            for p_name, stp in child_metrics["pet_steps_gained"].items(): res["pet_steps_gained"][p_name] += (input_ratio * stp)
            for a_name, chg in child_metrics["ability_charges_used"].items(): res["ability_charges_used"][a_name] += (input_ratio * chg)
            for c_id, stp in child_metrics["consumable_steps_needed"].items(): res["consumable_steps_needed"][c_id] += (input_ratio * stp)

    # ==========================================
    # ACTIVITY
    # ==========================================
    elif node.source_type == "activity":
        steps_per_action = calculate_steps(activity_obj, player_lvl, WE, int(stats.get("flat_step_reduction", 0)), stats.get("percent_step_reduction", 0.0))
        drop_table = drop_calc.get_drop_table(activity_obj, stats, player_lvl)
        res["debug"]["steps_per_action"] = steps_per_action
        res["debug"]["player_lvl"] = player_lvl
        res["debug"]["activity_level"] = activity_obj.level
        res["debug"]["activity_base_steps"] = activity_obj.base_steps
        res["debug"]["activity_obj_type"] = type(activity_obj).__name__
        res["debug"]["activity_obj_dict_base_steps"] = activity_obj.__dict__.get("base_steps", "NOT_IN_DICT") if hasattr(activity_obj, '__dict__') else "NO_DICT"
        # Check all activities in game_data with this ID
        all_matches = [(a_id, a.base_steps) for a_id, a in game_data.get('activities', {}).items() if a_id == activity_obj.id]
        res["debug"]["all_matching_activities"] = all_matches
        res["debug"]["activity_obj_id_python"] = id(activity_obj)
        res["debug"]["game_data_obj_id_python"] = id(game_data.get('activities', {}).get(activity_obj.id))
        res["debug"]["same_object"] = activity_obj is game_data.get('activities', {}).get(activity_obj.id)
        res["debug"]["activity_max_efficiency"] = activity_obj.max_efficiency
        res["debug"]["WE"] = WE
        res["debug"]["flat_step_reduction"] = int(stats.get("flat_step_reduction", 0))
        res["debug"]["percent_step_reduction"] = stats.get("percent_step_reduction", 0.0)
        res["debug"]["quality_outcome"] = stats.get("quality_outcome", 0)
        res["debug"]["fine_material_finding"] = stats.get("fine_material_finding", 0.0)
        res["debug"]["drop_table"] = [
            {k: round(v, 6) if isinstance(v, float) else v for k, v in d.items()}
            for d in drop_table
        ]
        res["debug"]["drop_table_items"] = [d["Item"] for d in drop_table]
        matched_drop = None
        for drop in drop_table:
            if drop["Item"] == target_item_id:
                matched_drop = drop
                normal_steps = drop["Steps"] / p_valid_quality
                actions_needed = normal_steps / steps_per_action
                
                if is_using_ability:
                    res["steps"] = 0.0
                    charges = actions_needed / get_actions_per_charge(instant_ability.effect)
                    res["ability_charges_used"][f"{instant_pet.name}: {instant_ability.name}"] += charges
                    res["steps_breakdown"][f"⚡ Activity: {activity_obj.name} (Instant)"] += 0.0
                else:
                    res["steps"] = normal_steps
                    res["steps_breakdown"][f"Activity: {activity_obj.name}"] += normal_steps
                    if skill_name: res["steps_by_skill"][skill_name.lower()] += normal_steps
                    if pet_obj: res["pet_steps_gained"][pet_obj.name] += normal_steps
                    if cons: res["consumable_steps_needed"][node.selected_consumable_id] += normal_steps * (1 + DA)

                p_drop_q_drop = steps_per_action / (drop["Steps"] * (1.0 + DA) * (1.0 + DR))
                isolated_xp = (((base_xp + FLAT_XP) * (1.0 + XP_BONUS))) / ((1.0 + DR) * p_drop_q_drop * p_valid_quality)
                if skill_name: res["xp"][skill_name.lower()] += isolated_xp
                res["local_steps"] = normal_steps if not is_using_ability else 0.0
                if skill_name: res["local_xp"][skill_name.lower()] += isolated_xp
                res["raw_materials"][target_item_id] += 1.0

                for sk in GATHERING_SKILLS | ARTISAN_SKILLS:
                    gain_xp = stats.get(f"gain_{sk}_xp", 0.0)
                    if gain_xp > 0:
                        res["xp"][sk] += gain_xp * actions_needed

                for input_id, child_node in node.inputs.items():
                    req_amount = child_node.base_requirement_amount
                    # For activities, NMC does not apply! DA consumes extra materials, so it factors back in.
                    input_ratio = (req_amount * drop["Steps"] * (1.0 + DA)) / (steps_per_action * p_valid_quality)

                    child_metrics = calculate_node_metrics(
                        child_node, loadouts, game_data, drop_calc, player_skill_levels,
                        user_state, locations,
                        global_use_fine=False
                    )

                    res["steps"] += (input_ratio * child_metrics["steps"])
                    for sk, xpv in child_metrics["xp"].items(): res["xp"][sk] += (input_ratio * xpv)
                    for item_k, amt in child_metrics["shopping_list"].items(): res["shopping_list"][item_k] += (input_ratio * amt)
                    for item_k, amt in child_metrics["raw_materials"].items(): res["raw_materials"][item_k] += (input_ratio * amt)
                    for src, stp in child_metrics["steps_breakdown"].items(): res["steps_breakdown"][src] += (input_ratio * stp)
                    for sk, stp in child_metrics["steps_by_skill"].items(): res["steps_by_skill"][sk] += (input_ratio * stp)
                    for p_name, stp in child_metrics["pet_steps_gained"].items(): res["pet_steps_gained"][p_name] += (input_ratio * stp)
                    for a_name, chg in child_metrics["ability_charges_used"].items(): res["ability_charges_used"][a_name] += (input_ratio * chg)
                    for c_id, stp in child_metrics["consumable_steps_needed"].items(): res["consumable_steps_needed"][c_id] += (input_ratio * stp)

                break
        res["debug"]["matched_drop"] = matched_drop
        res["debug"]["matched_target"] = target_item_id

    # ==========================================
    # CHEST
    # ==========================================
    elif node.source_type == "chest":
        chest_obj = game_data['chests'].get(node.source_id)
        drop_table = drop_calc.get_drop_table(activity_obj, stats, player_lvl)
        steps_per_action = calculate_steps(activity_obj, player_lvl, WE, int(stats.get("flat_step_reduction", 0)), stats.get("percent_step_reduction", 0.0))
        steps_per_chest = float('inf')
        
        for drop in drop_table:
            if drop["Item"] == node.source_id:
                steps_per_chest = drop["Steps"]
                break
                
        if steps_per_chest != float('inf') and chest_obj:
            expected_items_per_chest = sum((d.chance / 100.0) * ((d.min_quantity + d.max_quantity) / 2.0) for d in chest_obj.drops if d.item_id == target_item_id)
            if expected_items_per_chest > 0:
                normal_steps = (steps_per_chest / expected_items_per_chest) / p_valid_quality
                actions_needed = normal_steps / steps_per_action


                if is_using_ability:
                    res["steps"] = 0.0
                    charges = actions_needed / get_actions_per_charge(instant_ability.effect)
                    res["ability_charges_used"][f"{instant_pet.name}: {instant_ability.name}"] += charges
                    res["steps_breakdown"][f"⚡ Chest: {chest_obj.name} (Instant)"] += 0.0
                else:
                    res["steps"] = normal_steps
                    res["steps_breakdown"][f"Chest: {chest_obj.name}"] += normal_steps
                    if skill_name: res["steps_by_skill"][skill_name.lower()] += normal_steps
                    if pet_obj: res["pet_steps_gained"][pet_obj.name] += normal_steps
                    if cons: res["consumable_steps_needed"][node.selected_consumable_id] += normal_steps * (1 + DA)

                p_chest_eff = steps_per_action / (steps_per_chest * (1.0 + DA) * (1.0 + DR))
                xp_per_chest = (((base_xp + FLAT_XP) * (1.0 + XP_BONUS))) / ((1.0 + DR) * p_chest_eff)
                isolated_xp = (xp_per_chest / expected_items_per_chest) / p_valid_quality
                if skill_name: res["xp"][skill_name.lower()] += isolated_xp
                res["local_steps"] = normal_steps if not is_using_ability else 0.0
                if skill_name: res["local_xp"][skill_name.lower()] += isolated_xp
                res["raw_materials"][target_item_id] += 1.0

    return res