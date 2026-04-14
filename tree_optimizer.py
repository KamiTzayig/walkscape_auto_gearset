"""
tree_optimizer.py — Automated crafting tree source-selection and gear optimization.

Provides TreeNodeOptimizer which can operate in three scopes:
  - "node"    : Optimize only one specific node (source + gear), children unchanged.
  - "subtree" : Optimize a node and all nodes below it (bottom-up recursive).
  - "full"    : Alias for "subtree" applied to the root.

For every node in scope, every combination of the following is evaluated:
  - Source type (bank / recipe / activity / chest)
  - Recipe material-group alternatives
  - Compatible services (for recipes)
  - Activity input-requirement material variants

For each candidate configuration the GearOptimizer is run to find the best gear
for that specific source/context, and calculate_node_metrics() is used to score
the result.  The best configuration is applied to the node in-place.

Memoization: within one optimize() call, results are cached by
(item_id, base_requirement_amount) so repeated sub-items (e.g. pine_log used
in many branches) are solved only once.
"""

import copy
import itertools
import logging
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from models import CraftingNode, GearSet
from utils.constants import OPTIMAZATION_TARGET, GATHERING_SKILLS, ARTISAN_SKILLS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

TREE_GOAL_OPTIONS: Dict[str, str] = {
    "minimize_steps": "⚡ Minimize Steps (Fastest Production)",
    "maximize_xp": "📚 Maximize Total XP",
    "maximize_xp_per_step": "📈 Maximize XP per Step",
    "maximize_craft_xp_per_step": "🔨 Maximize Root Skill XP per Step",
}

# Map a tree goal to the GearOptimizer target(s) used while evaluating candidates.
TREE_GOAL_TO_GEAR_TARGETS: Dict[str, List[Tuple[OPTIMAZATION_TARGET, float]]] = {
    "minimize_steps":       [(OPTIMAZATION_TARGET.reward_rolls, 100.0)],
    "maximize_xp":          [(OPTIMAZATION_TARGET.xp, 100.0)],
    "maximize_xp_per_step": [(OPTIMAZATION_TARGET.xp, 50.0),
                             (OPTIMAZATION_TARGET.reward_rolls, 50.0)],
    "maximize_craft_xp_per_step": [(OPTIMAZATION_TARGET.reward_rolls, 100.0)],
}

# Map a tree goal to the auto_optimize_target name stored on the node.
_GOAL_TO_AUTO_TARGET_NAME: Dict[str, str] = {
    "minimize_steps":    "Reward Rolls",
    "maximize_xp":       "Xp",
    "maximize_xp_per_step": "Xp",
    "maximize_craft_xp_per_step": "Reward Rolls",
}

# Activities where keyword inputs are interchangeable and should be collapsed
# to a single representative item to avoid combinatorial explosion.
# Maps activity_id -> {slot_index: keyword}
COLLAPSED_INPUT_ACTIVITIES: Dict[str, Dict[int, str]] = {
    "bird_feeding": {0: "plant"},
}


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class TreeNodeOptimizer:
    """
    Optimizes source selection and gear for crafting tree nodes.

    Parameters
    ----------
    gear_optimizer   : GearOptimizer instance (already constructed with all_items + locations).
    game_data_dict   : The full game data dictionary used throughout the app.
    drop_calc        : DropCalculator instance.
    locations        : List of Location objects.
    user_state       : The user_state dict from Streamlit session state.
    player_skill_levels : {skill_name: level} mapping.
    char_lvl         : Overall character level (used by GearOptimizer for tool slots).
    loadouts         : {loadout_id: Loadout} mapping from session state.
    goal             : One of the keys in TREE_GOAL_OPTIONS.
    global_quality   : Target quality string for the root node ("Normal", "Good", …).
    global_use_fine  : Whether fine materials are globally enabled.
    gear_mode        : "inventory" (default), "all_gear", or "all_minus_blocklist".
    blocklist_ids    : Set of item IDs to exclude when gear_mode is "all_minus_blocklist".
    """

    def __init__(
        self,
        gear_optimizer: Any,
        game_data_dict: dict,
        drop_calc: Any,
        locations: list,
        user_state: dict,
        player_skill_levels: dict,
        char_lvl: int,
        loadouts: dict,
        goal: str = "minimize_steps",
        global_quality: str = "Normal",
        global_use_fine: bool = False,
        gear_mode: str = "inventory",
        blocklist_ids: Optional[Set[str]] = None,
    ) -> None:
        self._optimizer = gear_optimizer
        self._gd = game_data_dict
        self._drop_calc = drop_calc
        self._loc_map = {loc.id: loc for loc in locations}
        self._locations = locations
        self._user_state = user_state
        self._player_skill_levels = player_skill_levels
        self._char_lvl = char_lvl
        self._loadouts = loadouts
        self.goal = goal
        self._global_quality = global_quality
        self._global_use_fine = global_use_fine
        self._gear_mode = gear_mode
        self._blocklist_ids: Set[str] = blocklist_ids or set()

        # Derived from user_state for gear optimizer calls
        # When gear_mode is not "inventory", ownership is bypassed (None = unlimited).
        if gear_mode == "inventory":
            self._owned_item_counts: Optional[dict] = user_state.get("item_counts", {})
        else:
            self._owned_item_counts = None
        self._ap: int = user_state.get("user_ap", 0)
        self._reputation: dict = user_state.get("user_reputation", {})
        self._collectibles: list = user_state.get("owned_collectibles", [])

        # Memo cache cleared at the start of each optimize() call.
        # key: (item_id, base_requirement_amount, frozenset_of_source_types)
        # value: (node_state_dict, score)
        self._memo: Dict[Tuple, Tuple] = {}

        # Progress tracking
        self._ops_done: int = 0
        self._ops_total: int = 1
        self._progress_cb: Optional[Callable[[int, int], None]] = None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def optimize(
        self,
        node: CraftingNode,
        scope: str = "subtree",
        progress_callback: Optional[Callable[[int, int], None]] = None,
        node_start_callback: Optional[Callable[[str, int], None]] = None,
        node_done_callback: Optional[Callable[[str, str, str, float], None]] = None,
    ) -> None:
        """
        Optimize *node* in-place according to *scope*.

        scope:
            "node"    — this node only (source + gear), children stay unchanged.
            "subtree" — this node and all descendants (recursive, bottom-up).
            "full"    — identical to "subtree" (alias for clarity when called from root).

        progress_callback(done, total) is called after each gear-optimizer run.
        node_start_callback(item_id, num_configs) is called when a node begins evaluation.
        node_done_callback(item_id, source_type, source_id, score) is called when a node's best config is chosen.
        """
        self._memo.clear()
        self._ops_done = 0
        self._ops_total = 0
        self._progress_cb = progress_callback
        self._node_start_cb = node_start_callback
        self._node_done_cb = node_done_callback

        # Detect root skill for craft-xp-per-step goal.
        self._root_skill: Optional[str] = None
        if self.goal == "maximize_craft_xp_per_step":
            if node.source_type == "recipe":
                recipe = self._gd.get("recipes", {}).get(node.source_id)
                if recipe:
                    self._root_skill = recipe.skill.lower()
            elif node.source_type == "activity":
                activity = self._gd.get("activities", {}).get(node.source_id)
                if activity:
                    self._root_skill = activity.primary_skill.lower()

        if scope == "node":
            self._opt_single(node, is_root=True)
        else:
            self._opt_subtree(node, is_root=True)

    def update_metrics(self, node: CraftingNode, is_root: bool = False) -> None:
        """
        Recompute and persist metrics for *node* and all its descendants.
        Call this after optimize() to refresh the displayed step/XP numbers.
        """
        from calculations import calculate_node_metrics
        from utils.export import export_gearset

        # Bottom-up so children are always evaluated before parents (matches
        # the existing run_and_save_metrics pattern).
        for child in node.inputs.values():
            self.update_metrics(child, is_root=False)

        quality = self._global_quality if is_root else "Normal"
        node.metrics = calculate_node_metrics(
            node, self._loadouts, self._gd, self._drop_calc,
            self._player_skill_levels, self._user_state, self._locations,
            global_target_quality=quality,
            global_use_fine=self._global_use_fine,
        )

        if getattr(node, "loadout_id", None) == "AUTO" and getattr(node, "auto_gear_set", None):
            try:
                node.metrics["gear_set_base64"] = export_gearset(node.auto_gear_set)
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Core optimization helpers
    # -----------------------------------------------------------------------

    def _opt_subtree(self, node: CraftingNode, is_root: bool = False) -> float:
        """
        Recursively optimize this node and all children.
        Returns the best achievable score for the subtree.
        """
        # Respect user-set bank override — don't re-optimize bank nodes.
        if node.source_type == "bank":
            return 0.0

        cache_key = _memo_key(node)
        if cache_key in self._memo:
            cached_state, cached_score = self._memo[cache_key]
            _apply_state(node, _deep_copy_state(cached_state))
            node._tree_opt_done = True
            return cached_score

        configs = self._enumerate_configs(node)
        if not configs:
            return float("inf")

        # Dynamic progress: add this node's configs to the total now.
        self._ops_total += len(configs)

        if self._node_start_cb:
            self._node_start_cb(node.item_id, len(configs))

        best_score: Optional[float] = None
        best_state: Optional[dict] = None

        for config in configs:
            # Create a fresh working copy of the node with this config applied.
            work = _fresh_node(node)
            self._apply_config(work, config)

            # Recursively optimize all children of this config.
            for child in work.inputs.values():
                self._opt_subtree(child, is_root=False)

            # Run gear optimizer for this node with its (now-optimised) children.
            gear = self._run_gear_opt(work)
            work.auto_gear_set = gear
            work.loadout_id = "AUTO"
            work.auto_optimize_target = self._make_auto_target(work)
            work._tree_opt_done = True

            self._tick()

            score = self._score(work, is_root=is_root)

            if best_score is None or score < best_score:
                best_score = score
                best_state = _capture_state(work)

        if best_state is not None:
            _apply_state(node, best_state)
            node._tree_opt_done = True
            node._tree_opt_score = best_score
            self._memo[cache_key] = (_deep_copy_state(best_state), best_score)

        if self._node_done_cb and best_state is not None:
            self._node_done_cb(
                node.item_id,
                best_state["source_type"],
                best_state["source_id"],
                best_score if best_score is not None else float("inf"),
            )

        return best_score if best_score is not None else float("inf")

    def _opt_single(self, node: CraftingNode, is_root: bool = False) -> None:
        """
        Optimize only this node's source and gear.
        The existing children (inputs) are reused unchanged for scoring.
        """
        # Respect user-set bank override.
        if node.source_type == "bank":
            return

        configs = self._enumerate_configs(node)
        if not configs:
            return

        # Dynamic progress: add this node's configs to the total now.
        self._ops_total += len(configs)

        if self._node_start_cb:
            self._node_start_cb(node.item_id, len(configs))

        # Preserve existing children so each candidate can reference them.
        saved_inputs = dict(node.inputs)
        saved_act_inputs = dict(node.selected_activity_inputs)
        original_source_type = node.source_type

        best_score: Optional[float] = None
        best_state: Optional[dict] = None

        for config in configs:
            work = _fresh_node(node)
            # Rebuild children from scratch for this config so they match its source type.
            self._apply_config(work, config)
            # When source type matches the original, overlay the user's existing
            # (possibly already-optimized) children back onto the fresh defaults.
            if config["source_type"] == original_source_type:
                for key in saved_inputs:
                    if key in work.inputs:
                        work.inputs[key] = saved_inputs[key]
                work.selected_activity_inputs = dict(saved_act_inputs)

            gear = self._run_gear_opt(work)
            work.auto_gear_set = gear
            work.loadout_id = "AUTO"
            work.auto_optimize_target = self._make_auto_target(work)
            work._tree_opt_done = True

            self._tick()

            score = self._score(work, is_root=is_root)

            if best_score is None or score < best_score:
                best_score = score
                best_state = _capture_state(work)

        if best_state is not None:
            _apply_state(node, best_state)
            node._tree_opt_done = True
            node._tree_opt_score = best_score

        if self._node_done_cb and best_state is not None:
            self._node_done_cb(
                node.item_id,
                best_state["source_type"],
                best_state["source_id"],
                best_score if best_score is not None else float("inf"),
            )

    # -----------------------------------------------------------------------
    # Config enumeration
    # -----------------------------------------------------------------------

    def _enumerate_configs(self, node: CraftingNode) -> List[Dict]:
        """Return every valid (source × inputs × service) configuration for node."""
        configs: List[Dict] = []

        for source in node.available_sources:
            src_type: str = source["type"]
            src_id_raw: str = source["id"]

            if src_type == "bank":
                # Bank is a user-only choice — skip it in automatic optimization.
                continue

            if src_type == "chest":
                parts = src_id_raw.split("::")
                parent_act_id = parts[1] if len(parts) > 1 else None
                loc_ids = self._get_location_ids("activity", parent_act_id) if parent_act_id else [None]
                for loc_id in loc_ids:
                    configs.append({
                        "source_type": "chest",
                        "source_id": parts[0],
                        "parent_activity_id": parent_act_id,
                        "material_choices": {},
                        "activity_inputs": {},
                        "service_id": None,
                        "location_id": loc_id,
                    })
                continue

            for combo in self._input_combos(src_type, src_id_raw):
                for loc_id in self._get_location_ids(src_type, src_id_raw):
                    configs.append({
                        "source_type": src_type,
                        "source_id": src_id_raw,
                        "parent_activity_id": None,
                        **combo,
                        "location_id": loc_id,
                    })

        return configs

    def _input_combos(self, src_type: str, src_id: str) -> List[Dict]:
        """
        Return all (material_choices, activity_inputs, service_id) combinations
        for a given recipe or activity source.
        """
        gd = self._gd

        if src_type == "recipe":
            recipe = gd["recipes"].get(src_id)
            if not recipe:
                return [{"material_choices": {}, "activity_inputs": {}, "service_id": None}]

            # Each material group may have multiple alternatives.
            group_opts: List[List[Tuple[int, str]]] = []
            for i, mat_group in enumerate(recipe.materials):
                group_opts.append([(i, m.item_id) for m in mat_group])

            # Try every compatible service, plus no service.
            from ui_utils import get_compatible_services
            services = get_compatible_services(recipe, list(gd.get("services", {}).values()))
            svc_opts: List[Optional[str]] = [None] + [s.id for s in services]

            combos = []
            for mat_prod in itertools.product(*group_opts):
                choices = {slot_i: item_id for slot_i, item_id in mat_prod}
                for svc_id in svc_opts:
                    combos.append({
                        "material_choices": choices,
                        "activity_inputs": {},
                        "service_id": svc_id,
                    })
            return combos

        if src_type == "activity":
            activity = gd["activities"].get(src_id)
            if not activity:
                return [{"material_choices": {}, "activity_inputs": {}, "service_id": None}]

            input_reqs = [
                r for r in activity.requirements
                if getattr(r.type, "value", r.type) in ("keyword_count", "input_keyword", "item")
            ]
            if not input_reqs:
                return [{"material_choices": {}, "activity_inputs": {}, "service_id": None}]

            collapsed_slots = COLLAPSED_INPUT_ACTIVITIES.get(src_id, {})

            req_opts: List[List[Tuple[int, Optional[str]]]] = []
            for i, req in enumerate(input_reqs):
                rtype = getattr(req.type, "value", req.type)
                valid_ids: List[Optional[str]] = []

                if rtype in ("keyword_count", "input_keyword") and req.target:
                    kw = req.target.lower().replace("_", " ").strip()

                    if i in collapsed_slots:
                        # Collapse: pick just the first normal material as representative.
                        for mat in (
                            list(gd.get("materials", {}).values())
                            + list(gd.get("consumables", {}).values())
                        ):
                            if hasattr(mat, "keywords") and mat.keywords:
                                if kw in [k.lower().replace("_", " ").strip() for k in mat.keywords]:
                                    valid_ids.append(mat.id)
                                    break
                    else:
                        for mat in (
                            list(gd.get("materials", {}).values())
                            + list(gd.get("consumables", {}).values())
                        ):
                            if hasattr(mat, "keywords") and mat.keywords:
                                if kw in [k.lower().replace("_", " ").strip() for k in mat.keywords]:
                                    valid_ids.append(mat.id)
                elif rtype == "item" and req.target:
                    valid_ids.append(req.target.lower())

                req_opts.append([(i, mid) for mid in (valid_ids or [None])])

            combos = []
            for prod in itertools.product(*req_opts):
                inputs = {slot_i: mid for slot_i, mid in prod if mid is not None}
                combos.append({
                    "material_choices": {},
                    "activity_inputs": inputs,
                    "service_id": None,
                })
            return combos

        return [{"material_choices": {}, "activity_inputs": {}, "service_id": None}]

    def _get_location_ids(self, src_type: str, src_id: Optional[str]) -> List[Optional[str]]:
        """Return location IDs to try for a source. Falls back to [None]."""
        if src_type == "activity" and src_id:
            activity = self._gd.get("activities", {}).get(src_id)
            if activity and activity.locations:
                return list(activity.locations)
        return [None]

    # -----------------------------------------------------------------------
    # Config application
    # -----------------------------------------------------------------------

    def _apply_config(self, node: CraftingNode, config: Dict) -> None:
        """Apply a full config to *node*, rebuilding its child inputs from scratch."""
        from ui_utils import build_default_tree

        node.source_type = config["source_type"]
        node.source_id = config["source_id"]
        node.parent_activity_id = config["parent_activity_id"]
        node.selected_service_id = config.get("service_id")
        node.selected_location_id = config.get("location_id")
        node.inputs = {}
        node.selected_activity_inputs = {}

        gd = self._gd
        src_type = node.source_type

        if src_type == "recipe":
            recipe = gd["recipes"].get(node.source_id)
            if recipe and recipe.materials:
                mat_choices: Dict[int, str] = config.get("material_choices", {})
                for i, mat_group in enumerate(recipe.materials):
                    mat_id = mat_choices.get(i, mat_group[0].item_id)
                    mat_amount = next(
                        (m.amount for m in mat_group if m.item_id == mat_id),
                        mat_group[0].amount,
                    )
                    child = build_default_tree(mat_id, gd, self._drop_calc)
                    child.base_requirement_amount = mat_amount
                    node.inputs[f"{mat_id}_{i}"] = child

        elif src_type == "activity":
            activity = gd["activities"].get(node.source_id)
            if activity:
                act_inputs: Dict[int, str] = config.get("activity_inputs", {})
                node.selected_activity_inputs = dict(act_inputs)
                collapsed_slots = COLLAPSED_INPUT_ACTIVITIES.get(node.source_id, {})
                input_reqs = [
                    r for r in activity.requirements
                    if getattr(r.type, "value", r.type) in ("keyword_count", "input_keyword", "item")
                ]
                for i, req in enumerate(input_reqs):
                    mat_id = act_inputs.get(i)
                    if mat_id:
                        child = build_default_tree(mat_id, gd, self._drop_calc)
                        child.base_requirement_amount = req.value
                        if i in collapsed_slots:
                            child.source_type = "bank"
                            child.source_id = "bank"
                            child.inputs = {}
                            child.available_sources = [{"type": "bank", "label": "From Bank", "id": "bank"}]
                            child._collapsed_input = True
                        node.inputs[f"{mat_id}_{i}"] = child

    # -----------------------------------------------------------------------
    # Gear optimizer call
    # -----------------------------------------------------------------------

    def _run_gear_opt(self, node: CraftingNode) -> Optional[GearSet]:
        """Run GearOptimizer for node's current configuration. Returns best GearSet or None."""
        from ui_utils import synthesize_activity_from_recipe, build_activity_context, extract_modifier_stats

        gd = self._gd
        activity_obj = None
        skill_name = ""
        extra_passives: Dict[str, float] = {}

        if node.source_type == "recipe":
            recipe_obj = gd["recipes"].get(node.source_id)
            if not recipe_obj:
                return None
            skill_name = recipe_obj.skill
            svc_id = getattr(node, "selected_service_id", None)
            if svc_id:
                srv = gd.get("services", {}).get(svc_id)
                if srv:
                    activity_obj = synthesize_activity_from_recipe(recipe_obj, srv)
                    extra_passives = extract_modifier_stats(srv.modifiers)
            if not activity_obj:
                activity_obj = _WrappedRecipe(recipe_obj)

        elif node.source_type in ("activity", "chest"):
            act_id = (
                node.source_id if node.source_type == "activity" else node.parent_activity_id
            )
            activity_obj = gd["activities"].get(act_id)
            if activity_obj:
                skill_name = activity_obj.primary_skill

        if not activity_obj:
            return None

        gear_targets = TREE_GOAL_TO_GEAR_TARGETS.get(self.goal, [(OPTIMAZATION_TARGET.reward_rolls, 100.0)])
        if self._global_use_fine and self.goal == "minimize_steps":
            if skill_name.lower() in GATHERING_SKILLS:
                gear_targets = [(OPTIMAZATION_TARGET.fine, 100.0)]
            elif skill_name.lower() in ARTISAN_SKILLS:
                gear_targets = [(OPTIMAZATION_TARGET.materials_from_input, 100.0)]
        elif self.goal == "maximize_craft_xp_per_step" and self._root_skill and skill_name.lower() == self._root_skill:
            gear_targets = [(OPTIMAZATION_TARGET.xp, 100.0)]

        node_context = build_activity_context(
            activity_obj,
            self._ap,
            self._user_state.get("user_total_level", 0),
            self._loc_map,
            self._drop_calc,
            getattr(node, "selected_location_id", None),
        )

        # Extract passives from selected activity inputs and remove satisfied keyword reqs.
        if node.source_type == "activity" and hasattr(activity_obj, "requirements"):
            input_reqs = [
                r for r in activity_obj.requirements
                if getattr(r.type, "value", r.type) in ("keyword_count", "input_keyword", "item")
            ]
            for i, req in enumerate(input_reqs):
                mat_id = node.selected_activity_inputs.get(i)
                if mat_id:
                    mat_obj = gd.get("materials", {}).get(mat_id) or gd.get("consumables", {}).get(mat_id)
                    if mat_obj:
                        if getattr(mat_obj, "modifiers", None):
                            for k, v in extract_modifier_stats(mat_obj.modifiers).items():
                                extra_passives[k] = extra_passives.get(k, 0.0) + v
                        if getattr(mat_obj, "keywords", None):
                            for kw in mat_obj.keywords:
                                norm_kw = kw.lower().replace("_", " ").strip()
                                node_context["required_keywords"].pop(norm_kw, None)

        player_lvl = self._player_skill_levels.get(skill_name.lower(), 99) if skill_name else 99

        pet_obj = gd.get("pets", {}).get(getattr(node, "selected_pet_id", None))
        if pet_obj:
            pet_obj = pet_obj.copy(update={"active_level": getattr(node, "selected_pet_level", 1)})
        cons_obj = gd.get("consumables", {}).get(getattr(node, "selected_consumable_id", None))

        node_context["is_fine_materials"] = self._global_use_fine

        try:
            result = self._optimizer.optimize(
                activity=activity_obj,
                player_level=self._char_lvl,
                player_skill_level=player_lvl,
                optimazation_target=gear_targets,
                owned_item_counts=self._owned_item_counts,
                achievement_points=self._ap,
                user_reputation=self._reputation,
                owned_collectibles=self._collectibles,
                context_override=node_context,
                pet=pet_obj,
                consumable=cons_obj,
                extra_passive_stats=extra_passives,
                blacklisted_ids=self._blocklist_ids if self._gear_mode == "all_minus_blocklist" else None,
            )
            return result[0]
        except Exception:
            logger.warning("Gear optimizer failed for node %s (source=%s)",
                           node.item_id, node.source_id, exc_info=True)
            return None

    # -----------------------------------------------------------------------
    # Scoring
    # -----------------------------------------------------------------------

    def _score(self, node: CraftingNode, is_root: bool = False) -> float:
        """Score node's current config via calculate_node_metrics (recursive)."""
        from calculations import calculate_node_metrics

        quality = self._global_quality if is_root else "Normal"
        try:
            metrics = calculate_node_metrics(
                node, self._loadouts, self._gd, self._drop_calc,
                self._player_skill_levels, self._user_state, self._locations,
                global_target_quality=quality,
                global_use_fine=self._global_use_fine,
            )
            return _metrics_to_score(metrics, self.goal, self._root_skill)
        except Exception:
            logger.warning("Scoring failed for node %s (source=%s)",
                           node.item_id, node.source_id, exc_info=True)
            return float("inf")

    # -----------------------------------------------------------------------
    # Progress tracking
    # -----------------------------------------------------------------------

    def _tick(self) -> None:
        self._ops_done += 1
        if self._progress_cb:
            self._progress_cb(self._ops_done, self._ops_total)

    # -----------------------------------------------------------------------
    # Misc
    # -----------------------------------------------------------------------

    def _make_auto_target(self, node: CraftingNode) -> List[Dict]:
        skill_name = ""
        if node.source_type == "recipe":
            recipe = self._gd["recipes"].get(node.source_id)
            if recipe:
                skill_name = recipe.skill
        elif node.source_type in ("activity", "chest"):
            act_id = node.source_id if node.source_type == "activity" else node.parent_activity_id
            act = self._gd.get("activities", {}).get(act_id)
            if act:
                skill_name = act.primary_skill
        if self._global_use_fine and self.goal == "minimize_steps":
            if skill_name.lower() in GATHERING_SKILLS:
                name = "Fine"
            elif skill_name.lower() in ARTISAN_SKILLS:
                name = "Materials From Input"
            else:
                name = _GOAL_TO_AUTO_TARGET_NAME.get(self.goal, "Reward Rolls")
        elif self.goal == "maximize_craft_xp_per_step" and self._root_skill and skill_name.lower() == self._root_skill:
            name = "Xp"
        else:
            name = _GOAL_TO_AUTO_TARGET_NAME.get(self.goal, "Reward Rolls")
        return [{"id": 0, "target": name, "weight": 100}]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

class _WrappedRecipe:
    """Minimal Activity-like wrapper around a Recipe for GearOptimizer compatibility."""

    def __init__(self, r: Any) -> None:
        self.id = r.id
        self.name = r.name
        self.primary_skill = r.skill
        self.level = r.level
        self.base_xp = r.base_xp
        self.base_steps = r.base_steps
        self.max_efficiency = r.max_efficiency
        self.locations: list = []
        self.requirements: list = []
        self.modifiers: list = getattr(r, "modifiers", [])
        self.keywords: tuple = getattr(r, "keywords", ())
        self.reward_type: Optional[str] = getattr(r, "reward_type", None)


def _fresh_node(src: CraftingNode) -> CraftingNode:
    """Return a minimal new CraftingNode sharing identity fields with *src*."""
    return CraftingNode(
        node_id=src.node_id,
        item_id=src.item_id,
        source_type=src.source_type,
        source_id=src.source_id,
        parent_activity_id=src.parent_activity_id,
        available_sources=list(src.available_sources),
        base_requirement_amount=src.base_requirement_amount,
        selected_pet_id=src.selected_pet_id,
        selected_pet_level=src.selected_pet_level,
        selected_consumable_id=src.selected_consumable_id,
    )


def _capture_state(node: CraftingNode) -> Dict[str, Any]:
    """
    Capture all mutable configuration from *node* into a plain dict.
    Child input nodes are stored as live references (already optimised).
    """
    return {
        "source_type": node.source_type,
        "source_id": node.source_id,
        "parent_activity_id": node.parent_activity_id,
        "selected_service_id": node.selected_service_id,
        "selected_location_id": node.selected_location_id,
        "selected_activity_inputs": dict(node.selected_activity_inputs),
        "auto_gear_set": node.auto_gear_set,
        "auto_optimize_target": node.auto_optimize_target,
        "loadout_id": node.loadout_id,
        "inputs": dict(node.inputs),
    }


def _apply_state(node: CraftingNode, state: Dict[str, Any]) -> None:
    """Restore a node's configuration from a state dict captured by _capture_state."""
    node.source_type = state["source_type"]
    node.source_id = state["source_id"]
    node.parent_activity_id = state["parent_activity_id"]
    node.selected_service_id = state["selected_service_id"]
    node.selected_location_id = state["selected_location_id"]
    node.selected_activity_inputs = dict(state["selected_activity_inputs"])
    node.auto_gear_set = state["auto_gear_set"]
    node.auto_optimize_target = state["auto_optimize_target"]
    node.loadout_id = state["loadout_id"]
    node.inputs = dict(state["inputs"])


def _memo_key(node: CraftingNode) -> Tuple:
    """Build a cache key that includes item identity and available source types."""
    source_sig = frozenset(
        (s["type"], s["id"]) for s in node.available_sources
    )
    return (node.item_id, node.base_requirement_amount, source_sig)


def _reassign_node_ids(node: CraftingNode) -> None:
    """Recursively assign fresh node_ids so memoized copies don't collide in the UI."""
    import uuid
    node.node_id = str(uuid.uuid4())[:8]
    for child in node.inputs.values():
        _reassign_node_ids(child)


def _deep_copy_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-copy a captured state so memoized children are not shared."""
    new_state = copy.deepcopy(state)
    for child in new_state.get("inputs", {}).values():
        _reassign_node_ids(child)
    return new_state


def _metrics_to_score(metrics: Optional[Dict], goal: str, root_skill: Optional[str] = None) -> float:
    """Convert a metrics dict to a comparable score (lower is always better)."""
    if not metrics:
        return float("inf")
    if goal == "minimize_steps":
        return metrics.get("steps", float("inf"))
    if goal == "maximize_xp":
        return -sum(metrics.get("xp", {}).values())
    if goal == "maximize_xp_per_step":
        steps = metrics.get("steps", 0.0)
        xp = sum(metrics.get("xp", {}).values())
        return -(xp / steps) if steps > 0 else float("inf")
    if goal == "maximize_craft_xp_per_step":
        steps = metrics.get("steps", 0.0)
        xp_by_skill = metrics.get("xp", {})
        root_xp = sum(v for k, v in xp_by_skill.items() if k.lower() == root_skill) if root_skill else 0.0
        if root_xp > 0 and steps > 0:
            return -(root_xp / steps)
        return steps if steps > 0 else float("inf")
    return metrics.get("steps", float("inf"))
