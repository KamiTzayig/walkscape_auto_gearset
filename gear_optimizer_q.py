import itertools
from typing import Dict, List
from models import Item, Activity, GearSet
from utils import calculate_steps, calculate_quality_probabilities
from enum import Enum


RESTRICTED_TOOL_KEYWORDS = {"pickaxe", "hatchet", "fishingTool", "lure","hammer", "splitter"} # need to add more
OPTIMAZATION_TARGET = Enum("OPTIMAZATION_TARGET", ["reward_rolls", "xp", "chests", "materials", "fine", "quality"])

class GearOptimizer:
    def __init__(self, all_items: List[Item]):
        self.all_items = all_items

    def optimize(self, activity: Activity, player_level: int, player_skill_level: int, optimazation_target: OPTIMAZATION_TARGET = OPTIMAZATION_TARGET.reward_rolls):
        if player_level >= 80: tool_slots = 6
        elif player_level >= 50: tool_slots = 5
        elif player_level >= 20: tool_slots = 4
        else: tool_slots = 3

        candidates = self._get_candidates(activity)
        

        def calculate_score_for_set(current_set: GearSet) -> float:
            stats = current_set.get_stats(activity.skill)
            steps = calculate_steps(
                activity=activity,
                player_skill_level=player_skill_level, 
                player_work_efficiency=stats["work_efficiency"],
                player_minus_steps=stats["flat_step_reduction"],
                player_minus_steps_percent=stats["percent_step_reduction"]
            )
            da_mult = 1.0 + stats["double_action"]
            dr_mult = 1.0 + stats["double_rewards"]
            nmc_mult = 1.0 / (1.0 - min(0.99, stats["no_mats"]))
            
            
            if optimazation_target == OPTIMAZATION_TARGET.reward_rolls:
                return (da_mult * dr_mult) / steps
            elif optimazation_target == OPTIMAZATION_TARGET.xp:
                base_xp = activity.base_xp or 0
                xp_mult = 1.0 + stats["xp_percent"]
                flat_xp = stats["flat_xp"]
                return ((base_xp * xp_mult + flat_xp) * da_mult) / steps
            elif optimazation_target == OPTIMAZATION_TARGET.chests:
                return ((1.0 + stats["chest_finding"]) * da_mult * dr_mult) / steps
            elif optimazation_target == OPTIMAZATION_TARGET.materials:
                return  (dr_mult * nmc_mult)
            elif optimazation_target == OPTIMAZATION_TARGET.fine:
                return ((1.0 + stats["fine_material"]) * da_mult * dr_mult) / steps
            elif optimazation_target == OPTIMAZATION_TARGET.quality:
                flat_quality_bonus = stats["quality_outcome"]
                
                probs = calculate_quality_probabilities(
                    activity_min_level=activity.skill_level or 0,
                    player_skill_level=player_skill_level,
                    quality_bonus=flat_quality_bonus
                )
                
                return probs.get("Eternal", 0.0) * dr_mult * nmc_mult
            else:
                return 0.0

        candidates = self._keep_best_versions(candidates, activity, calculate_score_for_set)

        best_set = GearSet()
        base_score = calculate_score_for_set(best_set)


        single_slots = ["head", "chest", "legs", "feet", "cape", "back", "neck", "hands", "primary", "secondary", "pet", "consumable"]
        
        changed = True
        changed_iter = 0
        while changed:
            changed = False
            changed_iter += 1
            if changed_iter > 100: # Exit condition in case of flip-flopping gear
                print("Optimization loop exited after 100 loops. Potentially not optimal solution")
                break
            pre_iter_score = base_score
            
            for slot_attr in single_slots:
                best_item = getattr(best_set, slot_attr)
                max_slot_score = base_score 
                slot_key = slot_attr.capitalize()
                if slot_attr == "primary": slot_key = "Primary" 
                
                for item in candidates.get(slot_key, []):
                    setattr(best_set, slot_attr, item)
                    score = calculate_score_for_set(best_set)
                    
                    if score > max_slot_score:
                        max_slot_score = score
                        best_item = item
                    setattr(best_set, slot_attr, None) # Unequip
                
                setattr(best_set, slot_attr, best_item)
                base_score = max_slot_score

            ring_items = candidates.get("Ring", [])
            if ring_items:
                best_rings = best_set.rings
                max_r_score = base_score
                for r1, r2 in itertools.combinations_with_replacement(ring_items, 2):
                    best_set.rings = [r1, r2]
                    score = calculate_score_for_set(best_set)
                    if score > max_r_score:
                        max_r_score = score
                        best_rings = [r1, r2]
                best_set.rings = best_rings
                base_score = max_r_score

            tool_items = candidates.get("Tool", [])
            if tool_items:
                best_tools = best_set.tools
                max_t_score = base_score
                
                scored_tools = []
                for t in tool_items:
                    best_set.tools = [t]
                    scored_tools.append( (calculate_score_for_set(best_set), t) )
                scored_tools.sort(key=lambda x: x[0], reverse=True)
                top_tools = [x[1] for x in scored_tools[:30]]

                best_set.tools = []
                for r in range(1, tool_slots + 1):
                    for subset in itertools.combinations(top_tools, r):
                        if self._is_valid_tool_set(subset):
                            best_set.tools = list(subset)
                            score = calculate_score_for_set(best_set)
                            if score > max_t_score:
                                max_t_score = score
                                best_tools = list(subset)
                best_set.tools = best_tools
                self._is_valid_tool_set(best_tools)
            
            base_score = calculate_score_for_set(best_set)
            if pre_iter_score < base_score:
                changed = True
                print(f"Optimization loop {changed_iter} yielded improvement")
            pass
        
        return best_set

    def _get_candidates(self, activity: Activity) -> Dict[str, List[Item]]:
        slots = {}
        for item in self.all_items:
            item_skills = item.skill.split(',') if item.skill else []

            if item.skill is not None and activity.skill not in item_skills: continue
            if item.region and item.region != activity.region: continue
            if item.underwater_only and not activity.is_underwater: continue
            
            if item.slot not in slots: slots[item.slot] = []
            slots[item.slot].append(item)
        return slots

    def _keep_best_versions(self, candidates: Dict, activity: Activity, score_func) -> Dict:
        """
        Groups items by 'clean_item_name' and picks the one with highest score.
        Uses the exact same score_func as the main optimizer to guarantee alignment.
        """
        cleaned_candidates = {}
        temp_set = GearSet()
        
        for slot, items in candidates.items():
            best_versions = {}
            for item in items:
                key = item.clean_item_name or item.name
                
                if slot == "Tool": temp_set.tools = [item]
                elif slot == "Ring": temp_set.rings = [item]
                else: setattr(temp_set, slot.lower(), item)
                
                score = score_func(temp_set)
                
                if slot == "Tool": temp_set.tools = []
                elif slot == "Ring": temp_set.rings = []
                else: setattr(temp_set, slot.lower(), None)

                if key not in best_versions or score > best_versions[key][0]:
                    best_versions[key] = (score, item)
            
            cleaned_candidates[slot] = [v[1] for v in best_versions.values()]
        return cleaned_candidates

    def _is_valid_tool_set(self, tools: List[Item]) -> bool:
        seen_keywords = set()
        for t in tools:
            for k in t.keywords:
                if k in RESTRICTED_TOOL_KEYWORDS:
                    if k in seen_keywords: return False
                    seen_keywords.add(k)
        return True