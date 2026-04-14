import streamlit as st
import streamlit.components.v1 as components
import json
import copy
import pandas as pd
from datetime import datetime
from models import CraftingNode, GearSet
from utils.constants import EquipmentQuality, OPTIMAZATION_TARGET, GATHERING_SKILLS, ARTISAN_SKILLS
from ui_utils import build_default_tree, can_tree_use_fine, calculate_level_from_xp, TARGET_CATEGORIES, get_compatible_services, synthesize_activity_from_recipe, build_activity_context, extract_modifier_stats, get_applicable_abilities, get_best_auto_pet, get_pet_charges_gained
from calculations import calculate_node_metrics
from gear_optimizer import GearOptimizer
from utils.export import export_gearset
from tree_optimizer import TreeNodeOptimizer, TREE_GOAL_OPTIONS
from utils.data_loader import load_blocklist

MAX_SNAPSHOTS = 5

# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def _extract_node_metrics(node: CraftingNode, result: dict = None) -> dict:
    """Walk the tree and collect per-node metrics into a flat dict keyed by (item_id, source_id)."""
    if result is None:
        result = {}
    if node.metrics:
        key = f"{node.item_id}|{node.source_type}|{node.source_id or ''}"
        result[key] = {
            "item_id": node.item_id,
            "item_name": node.item_id.replace('_', ' ').title(),
            "source_type": node.source_type,
            "source_id": node.source_id,
            "steps": node.metrics.get("steps", float('inf')),
            "xp": dict(node.metrics.get("xp", {})),
            "stats_used": dict(node.metrics.get("stats_used", {})),
        }
    for child in node.inputs.values():
        _extract_node_metrics(child, result)
    return result


def _take_snapshot(root: CraftingNode, gear_mode: str, goal: str) -> dict:
    """Create a snapshot dict from the current tree state."""
    mode_labels = {"inventory": "My Inventory", "all_gear": "All Gear", "all_minus_blocklist": "All minus Blocklist"}
    return {
        "name": f"{mode_labels.get(gear_mode, gear_mode)} — {datetime.now().strftime('%H:%M:%S')}",
        "gear_mode": gear_mode,
        "goal": goal,
        "root_steps": root.metrics.get("steps", float('inf')) if root.metrics else float('inf'),
        "root_xp": dict(root.metrics.get("xp", {})) if root.metrics else {},
        "root_raw_materials": dict(root.metrics.get("raw_materials", {})) if root.metrics else {},
        "node_metrics": _extract_node_metrics(root),
    }


# ---------------------------------------------------------------------------
# Upgrade suggestion helpers
# ---------------------------------------------------------------------------

def _get_node_activity_and_context(node: CraftingNode, game_data_dict: dict, drop_calc, locations, user_state: dict):
    """Build the activity object, gear targets, and context needed to run the optimizer for a node."""
    extra_passives = {}
    activity_obj = None
    skill_name = ""

    if node.source_type == "recipe":
        recipe_obj = game_data_dict['recipes'].get(node.source_id)
        if not recipe_obj:
            return None, None, None, None, {}
        skill_name = recipe_obj.skill
        svc_id = getattr(node, 'selected_service_id', None)
        if svc_id:
            srv = game_data_dict.get('services', {}).get(svc_id)
            if srv:
                activity_obj = synthesize_activity_from_recipe(recipe_obj, srv)
                extra_passives = extract_modifier_stats(srv.modifiers)
        if not activity_obj:
            from tree_optimizer import _WrappedRecipe
            activity_obj = _WrappedRecipe(recipe_obj)

    elif node.source_type in ("activity", "chest"):
        act_id = node.source_id if node.source_type == "activity" else node.parent_activity_id
        activity_obj = game_data_dict['activities'].get(act_id)
        if activity_obj:
            skill_name = activity_obj.primary_skill

    if not activity_obj:
        return None, None, None, None, {}

    # Build gear targets from node's auto_optimize_target
    gear_targets = []
    if getattr(node, 'auto_optimize_target', None):
        for t_dict in node.auto_optimize_target:
            target_enum_name = t_dict["target"].replace(" ", "_").lower()
            try:
                enum_target = OPTIMAZATION_TARGET[target_enum_name]
                gear_targets.append((enum_target, float(t_dict["weight"])))
            except KeyError:
                continue
    if not gear_targets:
        gear_targets = [(OPTIMAZATION_TARGET.reward_rolls, 100.0)]

    loc_map = {loc.id: loc for loc in locations}
    node_context = build_activity_context(
        activity_obj,
        user_state.get("user_ap", 0),
        user_state.get("user_total_level", 0),
        loc_map, drop_calc,
        getattr(node, 'selected_location_id', None),
    )

    # Extract passives from selected activity inputs
    if node.source_type == "activity" and hasattr(activity_obj, 'requirements'):
        input_reqs = [r for r in activity_obj.requirements if getattr(r.type, 'value', r.type) in ('keyword_count', 'input_keyword', 'item')]
        for i, req in enumerate(input_reqs):
            mat_id = node.selected_activity_inputs.get(i)
            if mat_id:
                mat_obj = game_data_dict.get('materials', {}).get(mat_id) or game_data_dict.get('consumables', {}).get(mat_id)
                if mat_obj:
                    if getattr(mat_obj, 'modifiers', None):
                        for k, v in extract_modifier_stats(mat_obj.modifiers).items():
                            extra_passives[k] = extra_passives.get(k, 0.0) + v
                    if getattr(mat_obj, 'keywords', None):
                        for kw in mat_obj.keywords:
                            norm_kw = kw.lower().replace("_", " ").strip()
                            node_context["required_keywords"].pop(norm_kw, None)

    node_context["is_fine_materials"] = st.session_state.get('global_fine', False)
    return activity_obj, skill_name, gear_targets, node_context, extra_passives


def find_upgrade_suggestions(node: CraftingNode, all_items_raw, game_data_dict: dict, drop_calc,
                             locations, user_state: dict, player_skill_levels: dict, char_lvl: int):
    """
    Compare the user's current gear for a node against the 'all minus blocklist' ceiling
    and return a list of per-slot upgrade suggestions sorted by local step impact.
    
    Returns list of dicts: [{slot, current_item, upgrade_item, baseline_steps, upgraded_steps, steps_saved, pct_improvement}, ...]
    """
    baseline_gear = getattr(node, 'auto_gear_set', None)
    if not baseline_gear:
        return [], "No optimized gear set on this node. Run the optimizer first."

    activity_obj, skill_name, gear_targets, node_context, extra_passives = \
        _get_node_activity_and_context(node, game_data_dict, drop_calc, locations, user_state)
    if not activity_obj:
        return [], "Could not resolve activity for this node."

    player_lvl = player_skill_levels.get(skill_name.lower(), 99) if skill_name else 99
    blocklist_ids = load_blocklist()

    pet_obj = game_data_dict.get('pets', {}).get(getattr(node, 'selected_pet_id', None))
    if pet_obj:
        pet_obj = pet_obj.copy(update={"active_level": getattr(node, 'selected_pet_level', 1)})
    cons_obj = game_data_dict.get('consumables', {}).get(getattr(node, 'selected_consumable_id', None))

    ap = user_state.get("user_ap", 0)
    reputation = user_state.get("user_reputation", {})
    collectibles = user_state.get("owned_collectibles", [])

    # 1. Run ceiling optimizer (all gear minus blocklist)
    optimizer = GearOptimizer(all_items_raw, locations)
    ceiling_result = optimizer.optimize(
        activity=activity_obj,
        player_level=char_lvl,
        player_skill_level=player_lvl,
        optimazation_target=gear_targets,
        owned_item_counts=None,  # unlimited — all gear
        achievement_points=ap,
        user_reputation=reputation,
        owned_collectibles=collectibles,
        context_override=node_context,
        pet=pet_obj,
        consumable=cons_obj,
        extra_passive_stats=extra_passives,
        blacklisted_ids=blocklist_ids,
    )
    ceiling_gear = ceiling_result[0]
    if not ceiling_gear:
        return [], "Ceiling optimizer returned no result."

    # 2. Calculate baseline steps (local node only)
    from calculations import calculate_node_metrics as _calc
    baseline_metrics = node.metrics
    baseline_steps = baseline_metrics.get("steps", float('inf')) if baseline_metrics else float('inf')

    # 3. Compare slot-by-slot and evaluate marginal impact of each swap
    NAMED_SLOTS = ["head", "chest", "legs", "feet", "back", "cape", "neck", "hands", "primary", "secondary"]
    suggestions = []

    def _item_id(item):
        return item.id if item else None

    def _item_name(item):
        return item.name if item else "Empty"

    def _evaluate_swap(test_gear: GearSet) -> float:
        """Calculate local node steps with a modified gear set."""
        original_gear = node.auto_gear_set
        node.auto_gear_set = test_gear
        try:
            test_metrics = _calc(
                node, st.session_state.get('saved_loadouts', {}),
                game_data_dict, drop_calc, player_skill_levels,
                user_state, locations,
                global_target_quality="Normal",
                global_use_fine=st.session_state.get('global_fine', False),
            )
            return test_metrics.get("steps", float('inf'))
        except Exception:
            return float('inf')
        finally:
            node.auto_gear_set = original_gear

    # Named slots
    for slot in NAMED_SLOTS:
        current = getattr(baseline_gear, slot, None)
        ceiling_item = getattr(ceiling_gear, slot, None)
        if _item_id(current) == _item_id(ceiling_item):
            continue
        test_gear = baseline_gear.clone()
        setattr(test_gear, slot, ceiling_item)
        swapped_steps = _evaluate_swap(test_gear)
        if swapped_steps < baseline_steps:
            suggestions.append({
                "slot": slot.title(),
                "current_item": _item_name(current),
                "upgrade_item": _item_name(ceiling_item),
                "baseline_steps": baseline_steps,
                "upgraded_steps": swapped_steps,
                "steps_saved": baseline_steps - swapped_steps,
                "pct_improvement": ((baseline_steps - swapped_steps) / baseline_steps * 100) if baseline_steps > 0 else 0,
            })

    # Rings (positional)
    for i in range(2):
        current = baseline_gear.rings[i] if i < len(baseline_gear.rings) else None
        ceiling_item = ceiling_gear.rings[i] if i < len(ceiling_gear.rings) else None
        if _item_id(current) == _item_id(ceiling_item):
            continue
        test_gear = baseline_gear.clone()
        test_rings = list(test_gear.rings)
        while len(test_rings) <= i:
            test_rings.append(None)
        test_rings[i] = ceiling_item
        test_gear.rings = [r for r in test_rings if r is not None]
        swapped_steps = _evaluate_swap(test_gear)
        if swapped_steps < baseline_steps:
            suggestions.append({
                "slot": f"Ring {i+1}",
                "current_item": _item_name(current),
                "upgrade_item": _item_name(ceiling_item),
                "baseline_steps": baseline_steps,
                "upgraded_steps": swapped_steps,
                "steps_saved": baseline_steps - swapped_steps,
                "pct_improvement": ((baseline_steps - swapped_steps) / baseline_steps * 100) if baseline_steps > 0 else 0,
            })

    # Tools (positional)
    for i in range(max(len(baseline_gear.tools), len(ceiling_gear.tools))):
        current = baseline_gear.tools[i] if i < len(baseline_gear.tools) else None
        ceiling_item = ceiling_gear.tools[i] if i < len(ceiling_gear.tools) else None
        if _item_id(current) == _item_id(ceiling_item):
            continue
        test_gear = baseline_gear.clone()
        test_tools = list(test_gear.tools)
        while len(test_tools) <= i:
            test_tools.append(None)
        test_tools[i] = ceiling_item
        test_gear.tools = [t for t in test_tools if t is not None]
        swapped_steps = _evaluate_swap(test_gear)
        if swapped_steps < baseline_steps:
            suggestions.append({
                "slot": f"Tool {i+1}",
                "current_item": _item_name(current),
                "upgrade_item": _item_name(ceiling_item),
                "baseline_steps": baseline_steps,
                "upgraded_steps": swapped_steps,
                "steps_saved": baseline_steps - swapped_steps,
                "pct_improvement": ((baseline_steps - swapped_steps) / baseline_steps * 100) if baseline_steps > 0 else 0,
            })

    suggestions.sort(key=lambda s: s["steps_saved"], reverse=True)
    return suggestions, None


@st.dialog("⚙️ Choose Optimization Targets")
def node_target_dialog(node: CraftingNode):
    targets = node.auto_optimize_target
    
    all_targets = []
    for cat_targets in TARGET_CATEGORIES.values():
        all_targets.extend(cat_targets)
        
    for i, t in enumerate(targets):
        c1, c2, c3 = st.columns([3, 2, 1])
        with c1:
            t_idx = all_targets.index(t["target"]) if t["target"] in all_targets else 0
            new_t = st.selectbox("Target", options=all_targets, index=t_idx, key=f"dlg_t_{node.node_id}_{i}")
            targets[i]["target"] = new_t
        with c2:
            new_w = st.number_input("Weight %", min_value=1, max_value=100, value=int(t["weight"]), key=f"dlg_w_{node.node_id}_{i}")
            targets[i]["weight"] = new_w
        with c3:
            st.write("")
            st.write("")
            if st.button("🗑️", key=f"dlg_del_{node.node_id}_{i}"):
                if len(targets) > 1:
                    targets.pop(i)
                    st.rerun()
                
    if st.button("➕ Add Target"):
        targets.append({"id": len(targets), "target": all_targets[0], "weight": 50})
        st.rerun()
        
    if st.button("Save & Close", type="primary"):
        node.auto_optimize_target = targets
        st.rerun()

@st.dialog("⚙️ Node Settings")
def node_settings_dialog(node: CraftingNode, game_data_dict: dict, locations, user_state: dict):
    owned_pets = user_state.get("owned_pets", {})

    # 1. Service (Recipes only)
    if node.source_type == "recipe":
        recipe = game_data_dict['recipes'].get(node.source_id)
        if recipe:
            compat_services = get_compatible_services(recipe, list(game_data_dict['services'].values()))
            if compat_services:
                opts = ["None"] + [s.id for s in compat_services]
                def format_srv(x):
                    if x == "None": return "None"
                    return next((f"{s.name} ({s.location})" for s in compat_services if s.id == x), x)
                idx = opts.index(node.selected_service_id) if getattr(node, 'selected_service_id', None) in opts else 0
                new_srv = st.selectbox("Service", opts, index=idx, format_func=format_srv)
                node.selected_service_id = new_srv if new_srv != "None" else None

    # 2. Location (Activities only)
    elif node.source_type == "activity":
        act = game_data_dict['activities'].get(node.source_id)
        if act and act.locations:
            opts = ["None"] + list(act.locations)
            def format_loc(x):
                if x == "None": return "None"
                return next((loc.name for loc in locations if loc.id == x), x)
            idx = opts.index(node.selected_location_id) if getattr(node, 'selected_location_id', None) in opts else 0
            new_loc = st.selectbox("Location", opts, index=idx, format_func=format_loc)
            node.selected_location_id = new_loc if new_loc != "None" else None

    # 3. Pet
    pets = list(game_data_dict['pets'].values())
    pet_opts = ["None"] + [p.id for p in pets]
    
    def format_pet(x):
        if x == "None": return "None"
        p = next((p for p in pets if p.id == x), None)
        if not p: return x
        if x in owned_pets: return f"{owned_pets[x]['name']} ({p.name})"
        return p.name
        
    p_idx = pet_opts.index(node.selected_pet_id) if getattr(node, 'selected_pet_id', None) in pet_opts else 0
    new_pet_id = st.selectbox("Pet", pet_opts, index=p_idx, format_func=format_pet)
    node.selected_pet_id = new_pet_id if new_pet_id != "None" else None
    
    if node.selected_pet_id:
        pet_obj = game_data_dict['pets'][node.selected_pet_id]
        max_lvl = max([l.level for l in pet_obj.levels]) if pet_obj.levels else 1
        lvls = list(range(1, max_lvl + 1))
        
        default_lvl = max_lvl
        if node.selected_pet_id in owned_pets:
            default_lvl = min(owned_pets[node.selected_pet_id]["level"], max_lvl)
            
        l_idx = lvls.index(node.selected_pet_level) if getattr(node, 'selected_pet_level', None) in lvls else lvls.index(default_lvl) if default_lvl in lvls else len(lvls)-1
        node.selected_pet_level = st.selectbox("Pet Level", lvls, index=l_idx)
        
    # 4. Consumable
    cons = list(game_data_dict['consumables'].values())
    cons_opts = ["None"] + [c.id for c in cons]
    def format_cons(x):
        if x == "None": return "None"
        return next((c.name for c in cons if c.id == x), x)
    c_idx = cons_opts.index(node.selected_consumable_id) if getattr(node, 'selected_consumable_id', None) in cons_opts else 0
    new_cons_id = st.selectbox("Consumable", cons_opts, index=c_idx, format_func=format_cons)
    node.selected_consumable_id = new_cons_id if new_cons_id != "None" else None
    
    if st.button("Save & Close", type="primary"):
        st.rerun()

def _clear_widget_keys(node):
    """Recursively clear cached Streamlit widget keys so dropdowns reflect optimizer results."""
    for key_prefix in ("src_", "ld_"):
        key = f"{key_prefix}{node.node_id}"
        if key in st.session_state:
            del st.session_state[key]
    for child in node.inputs.values():
        _clear_widget_keys(child)


def _run_tree_opt(node, scope, optimizer_context, game_data_dict, drop_calc, locations, user_state, is_root=False):
    """Build a TreeNodeOptimizer, run it for the given scope, then refresh metrics."""
    valid_json = user_state.get("valid_json", False)
    user_skills_map = user_state.get("user_skills_map", {})
    player_skill_levels = {k: calculate_level_from_xp(v) for k, v in user_skills_map.items()} if valid_json else {}
    char_lvl = user_state.get("calculated_char_lvl", 99) if valid_json else 99

    gear_opt = GearOptimizer(optimizer_context["all_items_raw"], locations)
    tree_opt = TreeNodeOptimizer(
        gear_optimizer=gear_opt,
        game_data_dict=game_data_dict,
        drop_calc=drop_calc,
        locations=locations,
        user_state=user_state,
        player_skill_levels=player_skill_levels,
        char_lvl=char_lvl,
        loadouts=optimizer_context["loadouts"],
        goal=optimizer_context["tree_opt_goal"],
        global_quality=optimizer_context["global_quality"],
        global_use_fine=optimizer_context["global_use_fine"],
        gear_mode=optimizer_context.get("gear_mode", "inventory"),
        blocklist_ids=optimizer_context.get("blocklist_ids"),
    )

    progress_bar = st.progress(0, text="Running tree optimizer…")
    log_container = st.container()
    log_lines = []

    def on_progress(done, total):
        pct = min(done / total, 1.0) if total > 0 else 1.0
        progress_bar.progress(pct, text=f"Evaluating candidates… ({done}/{total})")

    def on_node_start(item_id, num_configs):
        item_name = item_id.replace('_', ' ').title()
        line = f"**Optimizing** {item_name} — {num_configs} options…"
        log_lines.append(line)
        log_container.markdown(line)

    def on_node_done(item_id, source_type, source_id, score):
        item_name = item_id.replace('_', ' ').title()
        source_icon = {"recipe": "🔨", "activity": "🪓", "chest": "🧰", "bank": "🏦"}.get(source_type, "📦")
        source_label = source_id.replace('_', ' ').title()
        goal = optimizer_context.get("tree_opt_goal", "minimize_steps")
        if score == float('inf'):
            line = f"&nbsp;&nbsp;→ {item_name}: {source_icon} {source_label} (no valid config)"
        elif goal == "minimize_steps":
            line = f"&nbsp;&nbsp;→ {item_name}: {source_icon} {source_label} ({score:,.1f} steps)"
        elif goal == "maximize_xp":
            line = f"&nbsp;&nbsp;→ {item_name}: {source_icon} {source_label} ({-score:,.1f} XP)"
        elif goal == "maximize_xp_per_step":
            line = f"&nbsp;&nbsp;→ {item_name}: {source_icon} {source_label} ({-score:,.2f} XP/step)"
        else:
            line = f"&nbsp;&nbsp;→ {item_name}: {source_icon} {source_label} (score: {score:,.1f})"
        log_lines.append(line)
        log_container.markdown(line)

    tree_opt.optimize(
        node, scope=scope,
        progress_callback=on_progress,
        node_start_callback=on_node_start,
        node_done_callback=on_node_done,
    )
    tree_opt.update_metrics(node, is_root=is_root)
    _clear_widget_keys(node)
    progress_bar.empty()

    # Persist log lines so they survive st.rerun()
    st.session_state['tree_opt_log'] = log_lines


def render_tree_node(node: CraftingNode, game_data_dict: dict, drop_calc, locations, user_state: dict, level: int = 0, optimizer_context: dict = None):
    icon = {"recipe": "🔨", "activity": "🪓", "chest": "🧰", "bank": "🏦"}.get(node.source_type, "📦")
    item_name = node.item_id.replace('_', ' ').title()
    auto_badge = " 🤖" if getattr(node, '_tree_opt_done', False) else ""
    title = f"{icon} {item_name} (x{node.base_requirement_amount}){auto_badge}"
    
    with st.expander(title, expanded=(level < 2)):
        c1, c2, c3 = st.columns([3, 3, 2])
        
        with c1:
            opts = [s["label"] for s in node.available_sources]
            current_label = opts[0]
            if node.source_type != "bank":
                for s in node.available_sources:
                    if s["type"] == node.source_type:
                        if node.source_type == "chest" and s["id"] == f"{node.source_id}::{node.parent_activity_id}":
                            current_label = s["label"]
                            break
                        elif s["id"] == node.source_id:
                            current_label = s["label"]
                            break
            
            idx = opts.index(current_label) if current_label in opts else 0
            new_label = st.selectbox("Source", options=opts, index=idx, key=f"src_{node.node_id}", label_visibility="collapsed")
            
            if new_label != current_label:
                selected_src = next(s for s in node.available_sources if s["label"] == new_label)
                node._tree_opt_done = False
                node.source_type = selected_src["type"]
                if selected_src["type"] == "chest":
                    node.source_id, node.parent_activity_id = selected_src["id"].split("::")
                else:
                    node.source_id = selected_src["id"]
                    
                node.inputs.clear()
                if hasattr(node, 'selected_activity_inputs'):
                    node.selected_activity_inputs.clear()
                if hasattr(node, '_pet_auto_checked'):
                    delattr(node, '_pet_auto_checked')
                node.selected_pet_id = None
                node.selected_pet_level = None
                if node.source_type == "recipe":
                    recipe = game_data_dict['recipes'].get(node.source_id)
                    if recipe and recipe.materials:
                        for i, material_group in enumerate(recipe.materials):
                            if not material_group: continue
                            mat = material_group[0] 
                            child_node = build_default_tree(mat.item_id, game_data_dict, drop_calc)
                            child_node.base_requirement_amount = mat.amount
                            node.inputs[f"{mat.item_id}_{i}"] = child_node

                elif node.source_type == "activity":
                    activity_obj = game_data_dict['activities'].get(node.source_id)
                    if activity_obj and hasattr(activity_obj, 'requirements'):
                        input_reqs = [r for r in activity_obj.requirements if getattr(r.type, 'value', r.type) in ('keyword_count', 'input_keyword', 'item')]
                        for i, req in enumerate(input_reqs):
                            req_type_val = getattr(req.type, 'value', req.type)
                            kw_target = req.target.lower().replace("_", " ").strip() if req.target else ""

                            first_valid_id = None
                            if req_type_val in ('keyword_count', 'input_keyword'):
                                for mat in list(game_data_dict['materials'].values()) + list(game_data_dict['consumables'].values()):
                                    if hasattr(mat, 'keywords') and mat.keywords:
                                        if kw_target in [k.lower().replace("_", " ").strip() for k in mat.keywords]:
                                            first_valid_id = mat.id
                                            break
                            elif req_type_val == 'item':
                                first_valid_id = req.target.lower()

                            if first_valid_id:
                                node.selected_activity_inputs[i] = first_valid_id
                                child_node = build_default_tree(first_valid_id, game_data_dict, drop_calc)
                                child_node.base_requirement_amount = req.value
                                node.inputs[f"{first_valid_id}_{i}"] = child_node
                            
                st.rerun()

            # --- RENDER ACTIVITY INPUT SELECTION ---
            if node.source_type == "activity":
                activity_obj = game_data_dict['activities'].get(node.source_id)
                
                if activity_obj and hasattr(activity_obj, 'requirements'):
                    input_reqs = [r for r in activity_obj.requirements if getattr(r.type, 'value', r.type) in ('keyword_count', 'input_keyword', 'item')]
                    
                    if input_reqs:
                        st.markdown("###### 📦 Required Inputs")
                        for i, req in enumerate(input_reqs):
                            req_type_val = getattr(req.type, 'value', req.type)
                            valid_mats = []
                            
                            if req_type_val in ('keyword_count', 'input_keyword') and req.target:
                                kw_target = req.target.lower().replace("_", " ").strip()
                                for mat in list(game_data_dict['materials'].values()) + list(game_data_dict['consumables'].values()):
                                    if hasattr(mat, 'keywords') and mat.keywords:
                                        mat_kws = [k.lower().replace("_", " ").strip() for k in mat.keywords]
                                        if kw_target in mat_kws:
                                            valid_mats.append(mat)
                            elif req_type_val == 'item' and req.target:
                                item_target = req.target.lower()
                                for mat in list(game_data_dict['materials'].values()) + list(game_data_dict['consumables'].values()):
                                    if mat.id == item_target or mat.id == f"{item_target}_fine":
                                        valid_mats.append(mat)
                                        
                            if valid_mats:
                                seen = set()
                                unique_valid_mats = []
                                for m in valid_mats:
                                    if m.id not in seen:
                                        seen.add(m.id)
                                        unique_valid_mats.append(m)
                                        
                                mat_names = [m.name for m in unique_valid_mats]
                                
                                current_mat_id = node.selected_activity_inputs.get(i)
                                if not current_mat_id and unique_valid_mats:
                                    current_mat_id = unique_valid_mats[0].id
                                    node.selected_activity_inputs[i] = current_mat_id
                                    
                                    # Ensure child node exists
                                    input_key = f"{current_mat_id}_{i}"
                                    if input_key not in node.inputs:
                                        c_node = build_default_tree(
                                            target_item_id=current_mat_id, 
                                            game_data=game_data_dict, 
                                            drop_calc=drop_calc, 
                                            global_target_quality="Normal", 
                                            global_use_fine=False
                                        )
                                        c_node.base_requirement_amount = req.value
                                        node.inputs[input_key] = c_node
                                
                                current_mat_name = next((m.name for m in unique_valid_mats if m.id == current_mat_id), mat_names[0] if mat_names else "")
                                
                                try: idx = mat_names.index(current_mat_name)
                                except ValueError: idx = 0
                                    
                                sel_name = st.selectbox(f"{req.target.replace('_', ' ').title()} ({req.value}x)", options=mat_names, index=idx, key=f"act_in_{node.node_id}_{i}")
                                
                                if sel_name != current_mat_name:
                                    sel_obj = next(m for m in unique_valid_mats if m.name == sel_name)
                                    node.selected_activity_inputs[i] = sel_obj.id
                                    
                                    # Rebuild inputs to sync the tree
                                    node.inputs.clear()
                                    for j, j_req in enumerate(input_reqs):
                                        mat_id_to_use = node.selected_activity_inputs.get(j)
                                        if mat_id_to_use:
                                            input_key = f"{mat_id_to_use}_{j}"
                                            c_node = build_default_tree(
                                                target_item_id=mat_id_to_use, 
                                                game_data=game_data_dict, 
                                                drop_calc=drop_calc, 
                                                global_target_quality="Normal", 
                                                global_use_fine=False
                                            )
                                            c_node.base_requirement_amount = j_req.value
                                            node.inputs[input_key] = c_node
                                                                    
                                    st.rerun()

        with c2:
            if node.source_type != "bank":
                if getattr(node, 'loadout_id', None) is None:
                    node.loadout_id = "AUTO"

                loadout_opts = ["✨ Choose Optimization Target", "Default Gear"] + [l.name for l in st.session_state['saved_loadouts'].values()]
                current_idx = 0
                
                if getattr(node, 'loadout_id', None) == "AUTO":
                    current_l_name = "✨ Choose Optimization Target"
                elif node.loadout_id in st.session_state['saved_loadouts']:
                    current_l_name = st.session_state['saved_loadouts'][node.loadout_id].name
                else:
                    current_l_name = "Default Gear"
                    
                if current_l_name in loadout_opts: 
                    current_idx = loadout_opts.index(current_l_name)

                selected_l_name = st.selectbox("Loadout", options=loadout_opts, index=current_idx, key=f"ld_{node.node_id}", label_visibility="collapsed")
                
                if selected_l_name == "Default Gear": 
                    node.loadout_id = "DEFAULT" 
                    node.auto_optimize_target = None
                elif selected_l_name == "✨ Choose Optimization Target":
                    node.loadout_id = "AUTO"
                    
                    if not getattr(node, 'auto_optimize_target', None):
                        default_t = "Reward Rolls"
                        if level == 0: 
                            items_list = game_data_dict.get('items', [])
                            is_equipment = any(item.id == node.item_id or item.id.startswith(f"{node.item_id}_") for item in items_list)
                            default_t = "Eternal Per Input" if is_equipment else "Materials From Input"
                        else:
                            if node.source_type == "recipe":
                                default_t = "Materials From Input"
                            elif node.source_type == "activity":
                                default_t = "Fine" if st.session_state.get('global_fine', False) else "Reward Rolls"
                                
                        node.auto_optimize_target = [{"id": 0, "target": default_t, "weight": 100}]
                    
                    if not hasattr(node, '_pet_auto_checked'):
                        loc_map = {loc.id: loc for loc in locations}
                        use_owned = user_state.get("use_owned", False)
                        owned_pets = user_state.get("owned_pets", {})
                        pet_id, pet_lvl = get_best_auto_pet(node, game_data_dict, loc_map, drop_calc, 0, 0, use_owned, owned_pets)
                        if pet_id:
                            node.selected_pet_id = pet_id
                            node.selected_pet_level = pet_lvl
                        node._pet_auto_checked = True
                    
                    c2_a, c2_b, c2_c = st.columns([3, 1, 1])
                    with c2_a:
                        summary = " | ".join([f"{t['weight']}% {t['target']}" for t in node.auto_optimize_target])
                        st.caption(f"🎯 **Target:** {summary}")
                    with c2_b:
                        if st.button("🎯", key=f"cfg_btn_{node.node_id}", help="Configure Target Weighting"):
                            node_target_dialog(node)
                    with c2_c:
                        if st.button("⚙️", key=f"set_btn_{node.node_id}", help="Node Settings (Pets, Location, Service)"):
                            node_settings_dialog(node, game_data_dict, locations, user_state)
                else:
                    node.loadout_id = next(l_id for l_id, l in st.session_state['saved_loadouts'].items() if l.name == selected_l_name)
                    node.auto_optimize_target = None
                    
                    c2_a, c2_b = st.columns([4, 1])
                    with c2_a: st.write("")
                    with c2_b:
                        if st.button("⚙️", key=f"set_btn_ld_{node.node_id}", help="Node Settings (Pets, Location, Service)"):
                            node_settings_dialog(node, game_data_dict, locations, user_state)
        with c3:
            if node.metrics:
                steps = node.metrics.get("steps", 0)
                if steps != float('inf') and steps > 0:
                    st.markdown(f"<div style='text-align:right; color:#4ade80; font-weight:bold;'>{steps:,.1f} Steps/ea</div>", unsafe_allow_html=True)
                    local_skill = None
                    if node.source_type == "recipe":
                        r = game_data_dict['recipes'].get(node.source_id)
                        if r: local_skill = r.skill.lower()
                    elif node.source_type in ["activity", "chest"]:
                        act_id = node.source_id if node.source_type == "activity" else node.parent_activity_id
                        a = game_data_dict['activities'].get(act_id)
                        if a: local_skill = a.primary_skill.lower()
                    if local_skill:
                        local_steps_val = node.metrics.get("local_steps", 0)
                        local_skill_xp = node.metrics.get("local_xp", {}).get(local_skill, 0)
                        tree_skill_xp = node.metrics.get("xp", {}).get(local_skill, 0)
                        has_local = local_steps_val > 0 and local_skill_xp > 0
                        has_tree = steps > 0 and tree_skill_xp > 0
                        if has_local and has_tree:
                            local_xps = local_skill_xp / local_steps_val
                            tree_xps = tree_skill_xp / steps
                            same = abs(local_xps - tree_xps) / max(abs(local_xps), 1e-9) < 0.001
                            if same:
                                st.markdown(f"<div style='text-align:right; color:#60a5fa; font-size:0.85em;'>{local_xps:,.2f} XP/step</div>", unsafe_allow_html=True)
                            else:
                                st.markdown(
                                    f"<div style='text-align:right; color:#60a5fa; font-size:0.85em;' title='XP per step for this node only'>{local_xps:,.2f} XP/step <span style='color:#94a3b8;'>(local)</span></div>"
                                    f"<div style='text-align:right; color:#818cf8; font-size:0.8em;' title='XP per step across this entire subtree'>{tree_xps:,.2f} XP/step <span style='color:#94a3b8;'>(tree)</span></div>",
                                    unsafe_allow_html=True
                                )
                        elif has_tree:
                            xp_per_step = tree_skill_xp / steps
                            st.markdown(f"<div style='text-align:right; color:#60a5fa; font-size:0.85em;'>{xp_per_step:,.2f} XP/step</div>", unsafe_allow_html=True)
                elif steps == 0:
                    st.markdown(f"<div style='text-align:right; color:#94a3b8; font-weight:bold;'>From Bank</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div style='text-align:right; color:#f87171;'>Impossible</div>", unsafe_allow_html=True)
                
                if node.metrics.get("gear_set_base64"):
                    export_str = node.metrics["gear_set_base64"]
                    safe_id = node.node_id.replace('-', '_')
                    btn_id = f"copyBtn_{safe_id}"
                    
                    js_code = f"""
                    <script>
                    function copyToClipboard_{safe_id}() {{
                        var content = {json.dumps(export_str)};
                        navigator.clipboard.writeText(content).then(function() {{
                            document.getElementById("{btn_id}").innerHTML = "✅ Copied!";
                            setTimeout(function() {{
                                document.getElementById("{btn_id}").innerHTML = "📋 Copy Gearset";
                            }}, 2000);
                        }}, function(err) {{
                            console.error('Async: Could not copy text: ', err);
                        }});
                    }}
                    </script>
                    <div style="text-align: right; margin-top: 5px;">
                        <button id="{btn_id}" onclick="copyToClipboard_{safe_id}()" style="
                            background-color: #ff4b4b; 
                            color: white; 
                            border: none; 
                            padding: 8px 15px; 
                            border-radius: 4px; 
                            cursor: pointer; 
                            font-family: 'Source Sans Pro', sans-serif;
                            font-weight: 600;
                            font-size: 14px;
                            width: 100%;
                        ">📋 Copy Gearset</button>
                    </div>
                    """
                    components.html(js_code, height=50)

        if getattr(node, '_collapsed_input', False):
            st.info("This input has been simplified for optimization — any item with the matching keyword is equivalent.")

        # Show Local Node Math Breakdown
        if node.metrics and node.metrics.get("stats_used") and node.source_type != "bank":
            stats = node.metrics["stats_used"]
            st.caption("🔍 **Local Node Math Breakdown**")
            cols = st.columns(4)
            cols[0].markdown(f"<span style='font-size:0.85em'>Double Action: **{stats.get('DA', 0)*100:.1f}%**</span>", unsafe_allow_html=True)
            cols[1].markdown(f"<span style='font-size:0.85em'>Double Rewards: **{stats.get('DR', 0)*100:.1f}%**</span>", unsafe_allow_html=True)
            cols[2].markdown(f"<span style='font-size:0.85em'>No Mats Consumed: **{stats.get('NMC', 0)*100:.1f}%**</span>", unsafe_allow_html=True)
            cols[3].markdown(f"<span style='font-size:0.85em'>Target Quality Prob: **{stats.get('p_valid_quality', 1)*100:.2f}%**</span>", unsafe_allow_html=True)
            
            applicable_abs = get_applicable_abilities(node, game_data_dict)
            
            if applicable_abs:
                st.write("") 
                for pet_obj, ab in applicable_abs:
                    new_val = st.checkbox(
                        f"⚡ Use {ab.name} ({pet_obj.name}) - 0 Steps", 
                        value=getattr(node, 'use_pet_ability', False), 
                        key=f"ab_{node.node_id}_{ab.name}"
                    )
                    if new_val != getattr(node, 'use_pet_ability', False):
                        node.use_pet_ability = new_val
                        st.rerun()

        # --- Gear Set Overview (dropdown) ---
        gear_set = None
        if node.source_type != "bank":
            if getattr(node, 'loadout_id', None) == "AUTO" and getattr(node, 'auto_gear_set', None):
                gear_set = node.auto_gear_set
            elif getattr(node, 'loadout_id', None) and node.loadout_id in st.session_state.get('saved_loadouts', {}):
                gear_set = st.session_state['saved_loadouts'][node.loadout_id].gear_set

        if gear_set:
            with st.expander("🛡️ Gear Set Overview", expanded=False):
                # Left two columns: worn gear, Right two columns: tools
                gear_col1, gear_col2, tool_col1, tool_col2 = st.columns(4)

                worn_items = []
                pet_str = "None"
                if gear_set.pet:
                    pet_lvl = getattr(gear_set.pet, 'active_level', None)
                    pet_str = f"{gear_set.pet.name} (Lvl {pet_lvl})" if pet_lvl else gear_set.pet.name
                worn_items.append(("🐾 Pet", pet_str))

                cons_str = "None"
                if gear_set.consumable:
                    cons_str = gear_set.consumable.name
                worn_items.append(("🧪 Consumable", cons_str))

                slot_emojis = {
                    "Head": "🪖", "Chest": "👕", "Legs": "👖", "Feet": "👢",
                    "Back": "🎒", "Cape": "🦸", "Neck": "📿", "Hands": "🧤",
                    "Primary": "⚔️", "Secondary": "🛡️",
                }
                for slot in ["Head", "Chest", "Legs", "Feet", "Back", "Cape", "Neck", "Hands", "Primary", "Secondary"]:
                    item = getattr(gear_set, slot.lower())
                    worn_items.append((f"{slot_emojis[slot]} {slot}", item.name if item else "None"))

                for i in range(2):
                    r_name = "None"
                    if i < len(gear_set.rings) and gear_set.rings[i]:
                        r_name = gear_set.rings[i].name
                    worn_items.append((f"💍 Ring {i+1}", r_name))

                mid = (len(worn_items) + 1) // 2
                with gear_col1:
                    for slot, name in worn_items[:mid]:
                        st.markdown(f"**{slot}**  \n{name}")
                with gear_col2:
                    for slot, name in worn_items[mid:]:
                        st.markdown(f"**{slot}**  \n{name}")

                tool_items = []
                for i in range(6):
                    t_name = "-"
                    if i < len(gear_set.tools) and gear_set.tools[i]:
                        t_name = gear_set.tools[i].name
                    tool_items.append((f"🔧 Tool {i+1}", t_name))

                tool_mid = (len(tool_items) + 1) // 2
                with tool_col1:
                    for slot, name in tool_items[:tool_mid]:
                        st.markdown(f"**{slot}**  \n{name}")
                with tool_col2:
                    for slot, name in tool_items[tool_mid:]:
                        st.markdown(f"**{slot}**  \n{name}")

        # --- Debug Info ---
        if node.metrics and node.metrics.get("debug") and node.source_type != "bank":
            with st.expander("🐛 Debug Info", expanded=False):
                dbg = node.metrics["debug"]
                stats_used = node.metrics.get('stats_used', {})
                src_type = dbg.get('source_type')
                st.code(
                    f"node.item_id:          {dbg.get('node_item_id')}\n"
                    f"target_item_id:        {dbg.get('target_item_id')}\n"
                    f"global_use_fine:       {dbg.get('global_use_fine')}\n"
                    f"source_type:           {src_type}\n"
                    f"source_id:             {dbg.get('source_id')}\n"
                    f"activity_id:           {dbg.get('activity_id')}\n"
                    f"activity_name:         {dbg.get('activity_name')}\n"
                    f"\n--- Optimizer ---\n"
                    f"gear_target:           {dbg.get('gear_target')}\n"
                    f"tree_opt_score:        {dbg.get('tree_opt_score')}\n"
                    f"\n--- Stats ---\n"
                    f"DA (double_action):    {dbg.get('DA')}\n"
                    f"DR (double_rewards):   {dbg.get('DR')}\n"
                    + (f"NMC (no_mat_consumed): {dbg.get('NMC')}\n" if src_type == "recipe" else "")
                    + f"quality_outcome:       {dbg.get('quality_outcome')}\n"
                    f"\n--- Step Calculation ---\n"
                    f"activity_base_steps:   {dbg.get('activity_base_steps')}\n"
                    f"activity_obj_type:     {dbg.get('activity_obj_type')}\n"
                    f"activity_dict_base_steps:{dbg.get('activity_obj_dict_base_steps')}\n"
                    f"all_matching_activities:{dbg.get('all_matching_activities')}\n"
                    f"same_object_as_gamedata:{dbg.get('same_object')}\n"
                    f"activity_max_efficiency:{dbg.get('activity_max_efficiency')}\n"
                    f"activity_level:        {dbg.get('activity_level')}\n"
                    f"player_lvl:            {dbg.get('player_lvl')}\n"
                    f"WE (work_efficiency):  {dbg.get('WE')}\n"
                    f"flat_step_reduction:   {dbg.get('flat_step_reduction')}\n"
                    f"percent_step_reduction:{dbg.get('percent_step_reduction')}\n"
                    f"steps_per_action:      {dbg.get('steps_per_action')}\n"
                    + (
                        f"\n--- Recipe Output ---\n"
                        f"output_quantity:       {dbg.get('output_quantity')}\n"
                        f"actions_needed:        {dbg.get('actions_needed')}\n"
                        if src_type == "recipe" else ""
                    )
                    + f"\n--- Drop Calculation ---\n"
                    f"fine_material_finding: {dbg.get('fine_material_finding')}\n"
                    f"matched_target:        {dbg.get('matched_target')}\n"
                    f"matched_drop:          {dbg.get('matched_drop')}\n"
                    f"p_valid_quality:       {stats_used.get('p_valid_quality')}\n"
                    f"steps (result):        {node.metrics.get('steps')}\n",
                    language=None
                )
                if dbg.get("drop_table"):
                    st.caption("Full drop table from `get_drop_table()`:")
                    st.dataframe(pd.DataFrame(dbg["drop_table"]), hide_index=True)

        st.write("")
        if node.source_type == "recipe":
            recipe = game_data_dict['recipes'].get(node.source_id)
            if recipe:
                compat_services = get_compatible_services(recipe, list(game_data_dict['services'].values()))
                if compat_services:
                    opts = [s.id for s in compat_services]
                    def format_srv(x):
                        return next((f"{s.name} ({s.location})" for s in compat_services if s.id == x), x)
                    srv_key = f"inl_srv_{node.node_id}"
                    current_srv = getattr(node, 'selected_service_id', None)
                    if current_srv in opts:
                        st.session_state[srv_key] = current_srv
                    idx = opts.index(current_srv) if current_srv in opts else 0
                    
                    new_srv = st.selectbox("📌 Service Override", opts, index=idx, format_func=format_srv, key=srv_key)
                    if new_srv != (current_srv or "None"):
                        node.selected_service_id = new_srv if new_srv != "None" else None
                        st.rerun()
                        
        elif node.source_type == "activity":
            act = game_data_dict['activities'].get(node.source_id)
            if act and act.locations:
                opts = list(act.locations)
                def format_loc(x):
                    return next((loc.name for loc in locations if loc.id == x), x)
                loc_key = f"inl_loc_{node.node_id}"
                current_loc = getattr(node, 'selected_location_id', None)
                if current_loc in opts:
                    st.session_state[loc_key] = current_loc
                idx = opts.index(current_loc) if current_loc in opts else 0
                
                new_loc = st.selectbox("📌 Location Override", opts, index=idx, format_func=format_loc, key=loc_key)
                if new_loc != (current_loc or "None"):
                    node.selected_location_id = new_loc if new_loc != "None" else None
                    st.rerun()
                    
        # Alternative material swap (Recipes)
        if node.source_type == "recipe" and node.inputs:
            recipe = game_data_dict['recipes'].get(node.source_id)
            if recipe and recipe.materials:
                has_options = any(len(g) > 1 for g in recipe.materials)
                if has_options:
                    st.caption("🔀 **Alternative Materials Available:**")
                    for i, material_group in enumerate(recipe.materials):
                        if len(material_group) > 1:
                            current_mat_id = None
                            for m in material_group:
                                if f"{m.item_id}_{i}" in node.inputs:
                                    current_mat_id = m.item_id
                                    break
                            
                            if not current_mat_id:
                                current_mat_id = material_group[0].item_id
                                
                            opts = [m.item_id for m in material_group]
                            
                            def make_format_func(mg):
                                def format_func(x):
                                    for m in mg:
                                        if m.item_id == x:
                                            return f"{m.amount}x {m.item_id.replace('_', ' ').title()}"
                                    return x
                                return format_func
                            
                            idx = opts.index(current_mat_id) if current_mat_id in opts else 0
                            
                            new_mat_id = st.selectbox(
                                f"Variant for Input {i+1}", 
                                options=opts, 
                                index=idx, 
                                format_func=make_format_func(material_group),
                                key=f"mat_opt_{node.node_id}_{i}"
                            )
                            
                            if new_mat_id != current_mat_id:
                                old_key = f"{current_mat_id}_{i}"
                                new_mat = next(m for m in material_group if m.item_id == new_mat_id)
                                
                                new_inputs = {}
                                for k, v in node.inputs.items():
                                    if k == old_key:
                                        c_node = build_default_tree(new_mat.item_id, game_data_dict, drop_calc)
                                        c_node.base_requirement_amount = new_mat.amount
                                        new_inputs[f"{new_mat_id}_{i}"] = c_node
                                    else:
                                        new_inputs[k] = v
                                        
                                if f"{new_mat_id}_{i}" not in new_inputs:
                                    c_node = build_default_tree(new_mat.item_id, game_data_dict, drop_calc)
                                    c_node.base_requirement_amount = new_mat.amount
                                    new_inputs[f"{new_mat_id}_{i}"] = c_node
                                    
                                node.inputs = new_inputs
                                st.rerun()

            
        # --- Tree Optimizer per-node buttons ---
        if optimizer_context and node.source_type != "bank" and len(node.available_sources) > 1:
            st.divider()
            opt_c1, opt_c2, opt_c3, opt_c4 = st.columns([2, 2, 2, 3])
            with opt_c1:
                if st.button("⚡ This Node", key=f"opt_node_{node.node_id}", help="Optimize source & gear for this node only"):
                    _run_tree_opt(node, "node", optimizer_context, game_data_dict, drop_calc, locations, user_state, is_root=(level == 0))
                    st.rerun()
            with opt_c2:
                if st.button("⚡ Node + Children", key=f"opt_sub_{node.node_id}", help="Optimize this node and all nodes below it"):
                    _run_tree_opt(node, "subtree", optimizer_context, game_data_dict, drop_calc, locations, user_state, is_root=(level == 0))
                    st.rerun()
            with opt_c3:
                has_gear = getattr(node, 'auto_gear_set', None) is not None and node.metrics
                if st.button("🔍 Find Upgrades", key=f"upg_btn_{node.node_id}",
                             disabled=not has_gear,
                             help="Compare your gear to the best available and show upgrade suggestions. Requires optimization first."):
                    st.session_state[f"upg_show_{node.node_id}"] = True
            with opt_c4:
                if getattr(node, '_tree_opt_done', False):
                    if st.button("↩ Reset to Manual", key=f"opt_reset_{node.node_id}", help="Clear auto-optimization for this node"):
                        node._tree_opt_done = False
                        st.rerun()

            # --- Upgrade suggestions display ---
            if st.session_state.get(f"upg_show_{node.node_id}", False):
                with st.expander("🔍 Upgrade Suggestions", expanded=True):
                    valid_json = user_state.get("valid_json", False)
                    user_skills_map = user_state.get("user_skills_map", {})
                    p_skill_levels = {k: calculate_level_from_xp(v) for k, v in user_skills_map.items()} if valid_json else {}
                    p_char_lvl = user_state.get("calculated_char_lvl", 99) if valid_json else 99

                    with st.spinner("Analyzing upgrades…"):
                        suggestions, err = find_upgrade_suggestions(
                            node, optimizer_context["all_items_raw"],
                            game_data_dict, drop_calc, locations, user_state,
                            p_skill_levels, p_char_lvl,
                        )

                    if err:
                        st.warning(err)
                    elif not suggestions:
                        st.success("Your gear is already optimal for this node! No upgrades found.")
                    else:
                        total_saved = sum(s["steps_saved"] for s in suggestions)
                        baseline = suggestions[0]["baseline_steps"]
                        total_pct = (total_saved / baseline * 100) if baseline > 0 else 0
                        st.markdown(f"**Total potential improvement:** {total_saved:,.2f} steps/ea ({total_pct:,.1f}%) if all upgrades are applied individually.")
                        st.caption("Each row shows the impact of swapping *just that slot* into your current gear.")

                        upgrade_rows = []
                        for s in suggestions:
                            upgrade_rows.append({
                                "Slot": s["slot"],
                                "Current": s["current_item"],
                                "Upgrade To": s["upgrade_item"],
                                "Steps Saved": s["steps_saved"],
                                "Improvement": f"{s['pct_improvement']:.1f}%",
                            })
                        st.dataframe(
                            pd.DataFrame(upgrade_rows),
                            column_config={
                                "Slot": st.column_config.TextColumn("Slot"),
                                "Current": st.column_config.TextColumn("Current Item"),
                                "Upgrade To": st.column_config.TextColumn("Upgrade To"),
                                "Steps Saved": st.column_config.NumberColumn("Steps Saved", format="%.2f"),
                                "Improvement": st.column_config.TextColumn("Improvement"),
                            },
                            hide_index=True,
                            use_container_width=True,
                        )

                    if st.button("Close", key=f"upg_close_{node.node_id}"):
                        st.session_state[f"upg_show_{node.node_id}"] = False
                        st.rerun()

        if node.inputs:
            st.markdown("###### ⬇️ Requires:")
            with st.container(border=False):
                for child_id, child_node in node.inputs.items():
                    render_tree_node(child_node, game_data_dict, drop_calc, locations, user_state, level + 1, optimizer_context=optimizer_context)

def render_crafting_tree_tab(recipes, all_items_raw, activities, all_containers, user_state, drop_calc, locations, services, all_pets, all_consumables, all_materials):
    st.subheader("Crafting Tree Calculator")
    st.caption("Calculate the true step cost, raw material requirements, and profitability of complex items.")
    
    with st.expander("📖 **How this math works & Flow of Calculations**", expanded=False):
        st.markdown("""
        **The Logic of the Chain:**
        When calculating the true cost of a crafted item, this tool traverses the tree from bottom to top. It applies your selected gear stats at each node to calculate the true fractional cost of exactly 1 item.

       """)
        
 

        st.markdown("""
            ### 🧮 Formula Breakdown
            
            **1. Action Steps (The base cost of an action):**
            $$ \\text{Action Steps} = \\max\\left(10, \\left[ \\frac{\\text{Base Steps}}{1 + \\text{Work Efficiency}} \\times (1 - \\text{Step Reduction \\%}) \\right] - \\text{Flat Step Reduction} \\right) $$

            **2. Quality Probability (The Target Rule):**
            *Note: When calculating a tree, Quality Probability is strictly calculated for the **Final Crafted Output** (Root Node). All sub-components and materials are calculated at Normal quality (Probability = 1.0) because recipes accept normal inputs.*

            **3. Steps per Item (Recipes):**
            $$ \\text{Steps/Item} = \\frac{\\text{Action Steps}}{\\text{Base Output Qty} \\times (1 + \\text{Double Action}) \\times (1 + \\text{Double Rewards}) \\times \\text{Quality Prob}} $$

            **4. Input Material Ratio (Recipes):**
            *How much of a raw material is consumed to make ONE final output item:*
            $$ \\text{Input Ratio} = \\frac{\\text{Required Amount} \\times (1 - \\text{No Materials Consumed})}{\\text{Base Output Qty} \\times (1 + \\text{Double Rewards}) \\times \\text{Quality Prob}} $$

            **5. Steps per Item (Gathering Activities):**
            $$ \\text{Steps/Item} = \\frac{\\text{Action Steps}}{(\\text{Drop Chance} \\times \\text{Avg Qty}) \\times (1 + \\text{Double Action}) \\times (1 + \\text{Double Rewards})} $$
        
            **6. Steps per Item (Chests 🧰):**
            *First, calculates how many steps it takes to find the chest, then divides by the expected loot:*
            $$ \\text{Steps/Chest} = \\frac{\\text{Action Steps}}{\\text{Chest Drop Chance} \\times (1 + \\text{DA}) \\times (1 + \\text{DR})} $$
            $$ \\text{Steps/Item} = \\frac{\\text{Steps/Chest}}{\\text{Expected Items from Chest Loot Table}} $$

            **7. XP per Item:**
            $$ \\text{XP/Item} = \\frac{(\\text{Base XP} \\times (1 + \\text{Bonus XP \\%})) + \\text{Flat XP}}{\\text{Base Output Qty} \\times (1 + \\text{Double Rewards}) \\times \\text{Quality Prob}} $$
            *(Note: Double Action speeds up your action frequency but does not grant "free" XP per item, meaning it mathematically cancels out of the per-item XP ratio! Double Rewards, however, dilutes the XP by giving you items without extra effort).*
            """)

    all_item_names = sorted(list({r.output_item_id for r in recipes}))
    target_item = st.selectbox("Select Target Item", options=all_item_names, format_func=lambda x: x.replace('_', ' ').title())
    
    game_data_dict = {
        'recipes': {r.id: r for r in recipes},
        'activities': {a.id: a for a in activities},
        'chests': {c.id: c for c in all_containers},
        'services': {s.id: s for s in services},
        'pets': {p.id: p for p in all_pets},
        'consumables': {c.id: c for c in all_consumables},
        'materials': {m.id: m for m in all_materials},
        'items': all_items_raw
    }
        
    if st.button("Generate Tree", type="primary"):
        st.session_state['crafting_tree_root'] = build_default_tree(target_item, game_data_dict,drop_calc=drop_calc)
        st.session_state['tree_target_item'] = target_item 
        st.rerun()

    st.divider()

    if st.session_state.get('crafting_tree_root'):
        root = st.session_state['crafting_tree_root']
        target_item_id = st.session_state.get('tree_target_item', root.item_id)
        
        # Compute player levels once — shared by both manual Calculate and tree optimizer.
        valid_json = user_state.get("valid_json", False)
        user_skills_map = user_state.get("user_skills_map", {})
        player_skill_levels = {k: calculate_level_from_xp(v) for k, v in user_skills_map.items()} if valid_json else {}
        char_lvl = user_state.get("calculated_char_lvl", 99) if valid_json else 99

        st.markdown("### ⚙️ Global Settings")
        c_g1, c_g2, c_g3, c_g4 = st.columns(4)

        with c_g1:
            target_amount = st.number_input("Target Quantity", min_value=1, value=1, step=1)

        with c_g2:
            daily_steps = st.number_input("Est. Daily Steps", min_value=1000, value=10000, step=1000, help="Used to estimate real-world time.")

        with c_g3:
            is_equipment = any(
                item.id == target_item_id or item.id.startswith(f"{target_item_id}_")
                for item in all_items_raw
            )

            if is_equipment:
                qualities = [q for q in EquipmentQuality]
                st.session_state['global_quality'] = st.selectbox("Target Quality", options=qualities, index=0)
            else:
                st.session_state['global_quality'] = "Normal"
                st.caption("*(Target Quality N/A)*")

        with c_g4:
            st.write("")
            st.write("")
            can_fine = True #can_tree_use_fine(root, drop_calc)
            if can_fine:
                new_fine_val = st.checkbox("💎 Fine Materials", value=st.session_state.get('global_fine', False))
                if new_fine_val != st.session_state.get('global_fine', False):
                    st.session_state['global_fine'] = new_fine_val

                    def force_fine_targets(n):
                        if getattr(n, 'loadout_id', None) == "AUTO":
                            if n.source_type == "activity":
                                act = game_data_dict.get('activities', {}).get(n.source_id)
                                skill = act.primary_skill.lower() if act else ""
                            elif n.source_type == "recipe":
                                recipe = game_data_dict.get('recipes', {}).get(n.source_id)
                                skill = recipe.skill.lower() if recipe else ""
                            else:
                                skill = ""
                            if not new_fine_val:
                                target_name = "Reward Rolls"
                            elif skill in GATHERING_SKILLS:
                                target_name = "Fine"
                            elif skill in ARTISAN_SKILLS:
                                target_name = "Materials From Input"
                            else:
                                target_name = "Reward Rolls"
                            n.auto_optimize_target = [{"id": 0, "target": target_name, "weight": 100}]
                        for child in n.inputs.values():
                            force_fine_targets(child)

                    force_fine_targets(root)
                    st.rerun()
            else:
                st.session_state['global_fine'] = False

        # --- Tree Optimizer Controls ---
        st.markdown("### 🤖 Auto-Optimize Tree *(optional)*")
        opt_col1, opt_col2, opt_col3 = st.columns([3, 2, 2])
        with opt_col1:
            goal_keys = list(TREE_GOAL_OPTIONS.keys())
            goal_labels = list(TREE_GOAL_OPTIONS.values())
            current_goal = st.session_state.get('tree_opt_goal', 'minimize_steps')
            goal_idx = goal_keys.index(current_goal) if current_goal in goal_keys else 0
            selected_goal_label = st.selectbox(
                "Optimization Goal",
                options=goal_labels,
                index=goal_idx,
                key="tree_opt_goal_select",
                help="The objective used when comparing source options across all nodes.",
            )
            selected_goal = goal_keys[goal_labels.index(selected_goal_label)]
            st.session_state['tree_opt_goal'] = selected_goal

        # --- Gear Mode Selector ---
        gear_mode_options = {
            "inventory": "📦 My Inventory",
            "all_gear": "🌟 All Gear (Best Quality)",
            "all_minus_blocklist": "🚫 All Gear minus Blocklist",
        }
        gear_mode_keys = list(gear_mode_options.keys())
        gear_mode_labels = list(gear_mode_options.values())
        current_gear_mode = st.session_state.get('tree_gear_mode', 'inventory')
        gear_mode_idx = gear_mode_keys.index(current_gear_mode) if current_gear_mode in gear_mode_keys else 0
        with opt_col2:
            selected_gear_mode_label = st.selectbox(
                "Gear Pool",
                options=gear_mode_labels,
                index=gear_mode_idx,
                key="tree_gear_mode_select",
                help=(
                    "**My Inventory** — Only gear you own.\n\n"
                    "**All Gear (Best Quality)** — Every item in the game at highest quality. True theoretical best.\n\n"
                    "**All Gear minus Blocklist** — Same as above but excluding items in `game_data/blocklist.txt` (ethereal items by default)."
                ),
            )
            selected_gear_mode = gear_mode_keys[gear_mode_labels.index(selected_gear_mode_label)]
            st.session_state['tree_gear_mode'] = selected_gear_mode

        # Load blocklist when needed
        blocklist_ids = set()
        if selected_gear_mode == "all_minus_blocklist":
            blocklist_ids = load_blocklist()

        with opt_col3:
            st.write("")
            st.write("")
            if st.button("⚡ Optimize Full Tree", type="secondary", help="Try every source/gear combination on all nodes and apply the best configuration. This may take a while."):
                optimizer_context_full = {
                    "all_items_raw": all_items_raw,
                    "loadouts": st.session_state.get('saved_loadouts', {}),
                    "tree_opt_goal": selected_goal,
                    "global_quality": st.session_state.get('global_quality', 'Normal'),
                    "global_use_fine": st.session_state.get('global_fine', False),
                    "gear_mode": selected_gear_mode,
                    "blocklist_ids": blocklist_ids,
                }
                with st.spinner("Running full tree optimization…"):
                    _run_tree_opt(root, "full", optimizer_context_full, game_data_dict, drop_calc, locations, user_state, is_root=True)
                st.rerun()

        # --- Blocklist display ---
        if selected_gear_mode == "all_minus_blocklist" and blocklist_ids:
            with st.expander(f"🚫 Blocklist ({len(blocklist_ids)} items excluded) — edit `game_data/blocklist.txt` to change", expanded=False):
                # Build a name lookup from all_items_raw for display
                id_to_name = {item.id.lower(): item.name for item in all_items_raw}
                blocklist_display = sorted(
                    [{"ID": bid, "Name": id_to_name.get(bid, bid.replace("_", " ").title())} for bid in blocklist_ids],
                    key=lambda x: x["Name"],
                )
                st.dataframe(blocklist_display, use_container_width=True, hide_index=True)
        elif selected_gear_mode == "all_gear":
            st.caption("Using **all gear** in the game at highest available quality — no ownership restrictions.")

        # --- Snapshot Controls ---
        snap_col1, snap_col2 = st.columns([2, 5])
        with snap_col1:
            has_metrics = root.metrics and root.metrics.get("steps", float('inf')) != float('inf')
            if st.button("📸 Save Snapshot", disabled=not has_metrics,
                         help="Store current tree metrics for comparison. Run 'Calculate True Cost' first."):
                snapshots = st.session_state.get('tree_snapshots', [])
                new_snap = _take_snapshot(root, selected_gear_mode, selected_goal)
                if len(snapshots) >= MAX_SNAPSHOTS:
                    snapshots.pop(0)
                snapshots.append(new_snap)
                st.session_state['tree_snapshots'] = snapshots
                st.toast(f"Snapshot saved: **{new_snap['name']}**")
                st.rerun()
        with snap_col2:
            snapshots = st.session_state.get('tree_snapshots', [])
            if snapshots:
                snap_names = [s['name'] for s in snapshots]
                st.caption(f"📸 **Saved:** {' · '.join(snap_names)}")
                if st.button("🗑️ Clear All Snapshots", key="clear_snapshots"):
                    st.session_state['tree_snapshots'] = []
                    st.rerun()

        # Show persisted optimizer log from the last run
        if st.session_state.get('tree_opt_log'):
            with st.expander("🤖 Last Optimization Results", expanded=True):
                for line in st.session_state['tree_opt_log']:
                    st.markdown(line, unsafe_allow_html=True)

        optimizer_context = {
            "all_items_raw": all_items_raw,
            "loadouts": st.session_state.get('saved_loadouts', {}),
            "tree_opt_goal": selected_goal,
            "global_quality": st.session_state.get('global_quality', 'Normal'),
            "global_use_fine": st.session_state.get('global_fine', False),
            "gear_mode": selected_gear_mode,
            "blocklist_ids": blocklist_ids,
        }

        st.divider()

        render_tree_node(root, game_data_dict, drop_calc, locations, user_state, optimizer_context=optimizer_context)
        st.divider()
        if st.button("🧮 Calculate True Cost & Run Optimizers", type="primary"):

            optimizer = GearOptimizer(all_items_raw, locations)
            if selected_gear_mode == "inventory":
                owned_item_counts = user_state.get("item_counts", {}) if valid_json else {}
            else:
                owned_item_counts = None
            calc_blocklist = blocklist_ids if selected_gear_mode == "all_minus_blocklist" else set()
            ap = user_state.get("user_ap", 0) if valid_json else 0
            reputation = user_state.get("user_reputation", {}) if valid_json else {}
            collectibles = user_state.get("owned_collectibles", []) if valid_json else []

            with st.spinner("Optimizing gear and calculating cascading steps..."):
                def run_and_save_metrics(node, is_root=False):
                    for child in node.inputs.values():
                        run_and_save_metrics(child, is_root=False)
                    
                    # --- AUTO OPTIMIZE LOGIC ---
                    if getattr(node, 'loadout_id', None) == "AUTO" and getattr(node, 'auto_optimize_target', None):
                        activity_obj = None
                        skill_name = ""
                        extra_passives = {}
                        
                        if node.source_type == "recipe":
                            recipe_obj = game_data_dict['recipes'].get(node.source_id)
                            if recipe_obj:
                                skill_name = recipe_obj.skill
                                activity_obj = recipe_obj
                                if getattr(node, 'selected_service_id', None):
                                    srv = game_data_dict['services'].get(node.selected_service_id)
                                    if srv:
                                        activity_obj = synthesize_activity_from_recipe(recipe_obj, srv)
                                        extra_passives = extract_modifier_stats(srv.modifiers)
                                else:
                                    # Fallback simple wrapper if no service selected
                                    class WrappedRecipe:
                                        def __init__(self, r):
                                            self.id = r.id
                                            self.name = r.name
                                            self.primary_skill = r.skill
                                            self.level = r.level
                                            self.base_xp = r.base_xp
                                            self.base_steps = r.base_steps
                                            self.max_efficiency = r.max_efficiency
                                            self.locations = []
                                            self.requirements = []
                                    activity_obj = WrappedRecipe(recipe_obj)
                                    
                        elif node.source_type in ["activity", "chest"]:
                            act_id = node.source_id if node.source_type == "activity" else node.parent_activity_id
                            activity_obj = game_data_dict['activities'].get(act_id)
                            skill_name = activity_obj.primary_skill if activity_obj else ""
                        
                        if activity_obj:
                            formatted_targets = []
                            for t_dict in node.auto_optimize_target:
                                target_enum_name = t_dict["target"].replace(" ", "_").lower()
                                try:
                                    enum_target = OPTIMAZATION_TARGET[target_enum_name]
                                    formatted_targets.append((enum_target, float(t_dict["weight"])))
                                except KeyError:
                                    continue
                                    
                            if not formatted_targets:
                                formatted_targets = [(OPTIMAZATION_TARGET.reward_rolls, 100.0)]
                            
                            player_lvl_opt = player_skill_levels.get(skill_name.lower(), 99) if skill_name else 99
                            
                            loc_map = {loc.id: loc for loc in locations}
                            node_context = build_activity_context(
                                activity_obj, 
                                user_state.get("user_ap", 0), 
                                user_state.get("user_total_level", 0), 
                                loc_map, drop_calc, getattr(node, 'selected_location_id', None)
                            )
                            
                            # --- NEW: Extract Extra Passives & Forgive Requirements from Selected Inputs ---
                            if node.source_type == "activity" and hasattr(activity_obj, 'requirements'):
                                input_reqs = [r for r in activity_obj.requirements if getattr(r.type, 'value', r.type) in ('keyword_count', 'input_keyword', 'item')]
                                for i, req in enumerate(input_reqs):
                                    mat_id = getattr(node, 'selected_activity_inputs', {}).get(i)
                                    if mat_id:
                                        mat_obj = game_data_dict['materials'].get(mat_id) or game_data_dict['consumables'].get(mat_id)
                                        if mat_obj:
                                            if getattr(mat_obj, 'modifiers', None):
                                                mat_stats = extract_modifier_stats(mat_obj.modifiers)
                                                for k, v in mat_stats.items():
                                                    extra_passives[k] = extra_passives.get(k, 0.0) + v
                                            if getattr(mat_obj, 'keywords', None):
                                                for kw in mat_obj.keywords:
                                                    norm_kw = kw.lower().replace("_", " ").strip()
                                                    node_context["required_keywords"].pop(norm_kw, None)
                            
                            pet_obj = game_data_dict['pets'].get(getattr(node, 'selected_pet_id', None))
                            if pet_obj: pet_obj = pet_obj.copy(update={"active_level": getattr(node, 'selected_pet_level', 1)})
                            
                            cons_obj = game_data_dict['consumables'].get(getattr(node, 'selected_consumable_id', None))
                            is_equipment_upgrade = False
                            if node.source_type == "recipe" and hasattr(activity_obj, 'materials'):
                                for mat_group in activity_obj.materials:
                                    for mat in mat_group:
                                        base_id = mat.item_id.replace("_fine", "")
                                        has_fine = (base_id in drop_calc.fine_material_map or 
                                                    f"{base_id}_fine" in game_data_dict.get('materials', {}) or 
                                                    f"{base_id}_fine" in game_data_dict.get('consumables', {}))
                                        if not has_fine:
                                            is_equipment_upgrade = True
                                            break
                                    if is_equipment_upgrade: break
                            
                            node_context["is_fine_materials"] = st.session_state.get('global_fine', False)
                            node_context["is_equipment_upgrade"] = is_equipment_upgrade 
                            opt_result = optimizer.optimize(
                                activity=activity_obj,
                                player_level=char_lvl,
                                player_skill_level=player_lvl_opt,
                                optimazation_target=formatted_targets,
                                owned_item_counts=owned_item_counts,
                                achievement_points=ap,
                                user_reputation=reputation,
                                owned_collectibles=collectibles,
                                context_override=node_context,
                                pet=pet_obj,
                                consumable=cons_obj,
                                extra_passive_stats=extra_passives,
                                blacklisted_ids=calc_blocklist,
                            )
                            node.auto_gear_set = opt_result[0] 
                    
                    # --- CALCULATE COST ---
                    target_qual = st.session_state.get('global_quality', "Normal") if is_root else "Normal"
                    
                    node.metrics = calculate_node_metrics(
                        node, st.session_state['saved_loadouts'], 
                        game_data_dict, drop_calc, player_skill_levels,
                        user_state, locations,  
                        global_target_quality=target_qual,
                        global_use_fine=st.session_state.get('global_fine', False)
                    )
                    
                    # --- EXPORT BASE64 ---
                    if getattr(node, 'loadout_id', None) == "AUTO" and getattr(node, 'auto_gear_set', None):
                        node.metrics["gear_set_base64"] = export_gearset(node.auto_gear_set)
                    elif getattr(node, 'loadout_id', None) and node.loadout_id in st.session_state['saved_loadouts']:
                        node.metrics["gear_set_base64"] = export_gearset(st.session_state['saved_loadouts'][node.loadout_id].gear_set)
                        
                    return node.metrics
                
                run_and_save_metrics(root, is_root=True)
                
            st.rerun()

        # ==========================================
        # SUMMARY SECTION
        # ==========================================
        if root.metrics and root.metrics.get("steps", float('inf')) != float('inf'):
            st.markdown("### 📊 Grand Totals Summary")
            st.caption(f"Calculated for **{target_amount}x {target_item.replace('_', ' ').title()}**")
            
            total_steps = root.metrics["steps"] * target_amount
            days_est = total_steps / daily_steps
            
            # --- Key Metrics Cards ---
            c_sum1, c_sum2, c_sum3 = st.columns(3)
            with c_sum1:
                st.markdown(f"<div style='background-color:#0f172a; padding:15px; border-radius:8px; border: 1px solid #1e293b;'>"
                            f"<h4 style='margin:0; color:#e2e8f0;'>Total Steps</h4>"
                            f"<h2 style='margin:0; color:#4ade80;'>{total_steps:,.0f}</h2>"
                            f"<span style='color:#94a3b8;'>Estimated Time: <b>{days_est:,.1f} days</b></span>"
                            f"</div>", unsafe_allow_html=True)
                
            with c_sum2:
                cost = 0.0
                for item_id, amt in root.metrics["raw_materials"].items():
                    cost += (amt * target_amount) * drop_calc.item_values.get(item_id, 0.0)
                
                st.markdown(f"<div style='background-color:#0f172a; padding:15px; border-radius:8px; border: 1px solid #1e293b;'>"
                            f"<h4 style='margin:0; color:#e2e8f0;'>Total Material Cost</h4>"
                            f"<h2 style='margin:0; color:#f87171;'>{cost:,.0f} 🪙</h2>"
                            f"<span style='color:#94a3b8;'>Value of all gathered & banked inputs</span>"
                            f"</div>", unsafe_allow_html=True)
                
            with c_sum3:
                total_xp = sum(root.metrics["xp"].values()) * target_amount
                avg_xp = total_xp / total_steps if (total_steps > 0 and total_steps != float('inf')) else 0
                
                st.markdown(f"<div style='background-color:#0f172a; padding:15px; border-radius:8px; border: 1px solid #1e293b;'>"
                            f"<h4 style='margin:0; color:#e2e8f0;'>Total XP Yield</h4>"
                            f"<h2 style='margin:0; color:#60a5fa;'>{total_xp:,.0f}</h2>"
                            f"<span style='color:#94a3b8;'>Avg: <b>{avg_xp:,.2f}</b> XP/Step</span>"
                            f"</div>", unsafe_allow_html=True)

            st.write("")
            
            # --- Details Tables ---
            c_det1, c_det2 = st.columns(2)
            c_det3, c_det4 = st.columns(2)
            
            with c_det1:
                st.markdown("##### 🛒 Raw Materials Shopping List")
                st.caption("Materials required from Bank (factors in NMC and DR).")
                shopping_data = []
                for item_id, amt in root.metrics["shopping_list"].items():
                    final_amt = amt * target_amount
                    shopping_data.append({
                        "Item": item_id.replace('_', ' ').title(),
                        "Amount Needed": f"{final_amt:,.2f}"
                    })
                if shopping_data:
                    st.dataframe(pd.DataFrame(shopping_data), hide_index=True, width="stretch")
                else:
                    st.info("No raw materials required from the bank! (Everything gathered via activities).")
                    

            with c_det2:
                st.markdown("##### 🪙 Material Cost Breakdown")
                st.caption("Coin value of all base materials.")
                cost_data = []
                for item_id, amt in root.metrics["raw_materials"].items():
                    final_amt = amt * target_amount
                    unit_val = drop_calc.item_values.get(item_id, 0.0)
                    total_cost = final_amt * unit_val
                    cost_data.append({
                        "Item": item_id.replace('_', ' ').title(),
                        "Quantity": final_amt,
                        "Unit Value": unit_val,
                        "Total Cost": total_cost
                    })
                
                if cost_data:
                    df_cost = pd.DataFrame(cost_data).sort_values(by="Total Cost", ascending=False)
                    st.dataframe(
                        df_cost,
                        column_config={
                            "Item": st.column_config.TextColumn("Item"),
                            "Quantity": st.column_config.NumberColumn("Quantity", format="%.2f"),
                            "Unit Value": st.column_config.NumberColumn("Unit Value", format="%.1f"),
                            "Total Cost": st.column_config.NumberColumn("Total Cost", format="%.1f 🪙")
                        },
                        hide_index=True, 
                        width="stretch"
                    )
                else:
                    st.info("No materials required.")
            
            st.write("")
            
            with c_det3:
                st.markdown("##### 📈 XP Breakdown")
                st.caption("Experience gained by skill.")
                xp_data = []
                steps_by_skill = root.metrics.get("steps_by_skill", {})
                for skill, amt in root.metrics["xp"].items():
                    if amt > 0:
                        skill_steps = steps_by_skill.get(skill, 0) * target_amount
                        xp_total = amt * target_amount
                        xp_per_step_total = xp_total / total_steps if total_steps > 0 else 0
                        xp_per_step_skill = xp_total / skill_steps if skill_steps > 0 else 0
                        xp_data.append({
                            "Skill": skill.title(),
                            "XP": f"{xp_total:,.1f}",
                            "XP/Step (skill)": f"{xp_per_step_skill:,.3f}",
                            "XP/Step (total)": f"{xp_per_step_total:,.3f}",
                        })
                if xp_data:
                    st.dataframe(pd.DataFrame(xp_data).sort_values(by="Skill"), hide_index=True, width="stretch")
                else:
                    st.info("No XP generated by this chain.")

            with c_det4:
                st.markdown("##### 🥾 Steps Breakdown")
                st.caption("Total steps spent on each specific activity.")
                steps_data = []
                for source, steps in root.metrics["steps_breakdown"].items():
                    final_steps = steps * target_amount
                    if final_steps > 0 and final_steps != float('inf'):
                        steps_data.append({
                            "Source": source,
                            "Steps": final_steps,
                        })
                if steps_data:
                    df_steps = pd.DataFrame(steps_data).sort_values(by="Steps", ascending=False)
                    st.dataframe(
                        df_steps,
                         column_config={
                            "Source": st.column_config.TextColumn("Activity / Source"),
                            "Steps": st.column_config.NumberColumn("Total Steps", format="%.0f"),
                        },
                        hide_index=True, 
                        width="stretch"
                    )
                else:
                    st.info("No steps required.")

            st.write("") 
            c_pet1, c_pet2 = st.columns(2)
            
            with c_pet1:
                st.markdown("##### 🐾 Pet Steps Walked")
                st.caption("Steps taken doing tasks *without* instant completion, while having a pet equipped.")
                pet_data = []
                for pet_name, steps in root.metrics["pet_steps_gained"].items():
                    final_steps = steps * target_amount
                    if final_steps > 0:
                        charges = get_pet_charges_gained(pet_name, final_steps, game_data_dict)
                        pet_data.append({"Pet": pet_name, "Steps Walked": final_steps, "Charges Gained": charges})
                if pet_data:
                    df_pets = pd.DataFrame(pet_data).sort_values(by="Steps Walked", ascending=False)
                    st.dataframe(
                        df_pets,
                        column_config={
                            "Steps Walked": st.column_config.NumberColumn("Steps Walked", format="%.0f"),
                            "Charges Gained": st.column_config.NumberColumn("Charges Gained", format="%.1f"),
                        },
                        hide_index=True,
                        width="stretch"
                    )
                else:
                    st.info("No steps walked with pets.")
                    
            with c_pet2:
                st.markdown("##### ⚡ Ability Charges Used")
                st.caption("Estimated charges required to instantly complete the selected tasks.")
                ab_data = []
                for ab_name, charges in root.metrics["ability_charges_used"].items():
                    final_charges = charges * target_amount
                    if final_charges > 0:
                        ab_data.append({"Ability": ab_name, "Charges": final_charges})
                if ab_data:
                    st.dataframe(
                        pd.DataFrame(ab_data).sort_values(by="Charges", ascending=False),
                        column_config={"Charges": st.column_config.NumberColumn("Charges Used", format="%.2f")},
                        hide_index=True, 
                        width="stretch"
                    )
                else:
                    st.info("No ability charges used.")

            st.write("") 
            c_consumables1, c_unused = st.columns(2)
            
            with c_consumables1:
                st.markdown("##### 🧪 Consumables Needed")
                st.caption("Total units needed based on steps active and consumable duration.")
                import math
                cons_data = []
                for cons_id, steps in root.metrics["consumable_steps_needed"].items():
                    final_steps = steps * target_amount
                    cons_obj = game_data_dict['consumables'].get(cons_id)
                    if cons_obj and final_steps > 0:
                        qty_needed = math.ceil(final_steps / cons_obj.duration)
                        cons_data.append({
                            "Consumable": cons_obj.name,
                            "Steps Active": final_steps,
                            "Duration (steps)": cons_obj.duration,
                            "Qty Needed": qty_needed
                        })
                if cons_data:
                    st.dataframe(
                        pd.DataFrame(cons_data).sort_values(by="Qty Needed", ascending=False),
                        column_config={
                            "Consumable": st.column_config.TextColumn("Consumable"),
                            "Steps Active": st.column_config.NumberColumn("Steps Active", format="%.0f"),
                            "Duration (steps)": st.column_config.NumberColumn("Duration (steps)", format="%d"),
                            "Qty Needed": st.column_config.NumberColumn("Qty Needed", format="%d"),
                        },
                        hide_index=True,
                        width="stretch"
                    )
                else:
                    st.info("No consumables used in this crafting chain.")

            # ==========================================
            # SNAPSHOT COMPARISON SECTION
            # ==========================================
            snapshots = st.session_state.get('tree_snapshots', [])
            if len(snapshots) >= 2:
                st.divider()
                st.markdown("### 📊 Snapshot Comparison")
                st.caption("Compare saved optimization results across different gear pools to identify where your gear is weakest.")

                # --- Summary comparison cards ---
                snap_cols = st.columns(len(snapshots))
                for i, snap in enumerate(snapshots):
                    with snap_cols[i]:
                        snap_steps = snap.get("root_steps", float('inf'))
                        snap_xp = sum(snap.get("root_xp", {}).values())
                        st.markdown(
                            f"<div style='background-color:#0f172a; padding:12px; border-radius:8px; border: 1px solid #334155;'>"
                            f"<h5 style='margin:0; color:#e2e8f0;'>{snap['name']}</h5>"
                            f"<span style='color:#4ade80; font-size:1.1em; font-weight:bold;'>{snap_steps:,.1f} steps/ea</span><br>"
                            f"<span style='color:#60a5fa; font-size:0.9em;'>{snap_xp:,.0f} total XP</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                st.write("")

                # --- Pick two snapshots to compare ---
                cmp_col1, cmp_col2 = st.columns(2)
                snap_names = [s['name'] for s in snapshots]
                with cmp_col1:
                    base_idx = st.selectbox("Baseline (your gear)", options=range(len(snap_names)), format_func=lambda i: snap_names[i], index=0, key="snap_cmp_base")
                with cmp_col2:
                    ceil_idx = st.selectbox("Ceiling (target)", options=range(len(snap_names)), format_func=lambda i: snap_names[i], index=min(1, len(snap_names)-1), key="snap_cmp_ceil")

                base_snap = snapshots[base_idx]
                ceil_snap = snapshots[ceil_idx]

                # --- Per-node delta table ---
                base_nodes = base_snap.get("node_metrics", {})
                ceil_nodes = ceil_snap.get("node_metrics", {})
                all_keys = set(base_nodes.keys()) | set(ceil_nodes.keys())

                rows = []
                for key in all_keys:
                    b = base_nodes.get(key)
                    c = ceil_nodes.get(key)
                    if not b or not c:
                        continue
                    b_steps = b.get("steps", float('inf'))
                    c_steps = c.get("steps", float('inf'))
                    if b_steps == float('inf') or c_steps == float('inf'):
                        continue
                    if b_steps == 0 and c_steps == 0:
                        continue
                    delta = b_steps - c_steps
                    pct = (delta / b_steps * 100) if b_steps > 0 else 0
                    rows.append({
                        "Node": b.get("item_name", key),
                        "Source": (b.get("source_type", "") or "").title(),
                        f"Steps ({base_snap['name']})": b_steps,
                        f"Steps ({ceil_snap['name']})": c_steps,
                        "Δ Steps": delta,
                        "Improvement %": pct,
                    })

                if rows:
                    rows.sort(key=lambda r: r["Δ Steps"], reverse=True)
                    df_cmp = pd.DataFrame(rows)
                    total_base = base_snap.get("root_steps", 0)
                    total_ceil = ceil_snap.get("root_steps", 0)
                    total_delta = total_base - total_ceil
                    total_pct = (total_delta / total_base * 100) if total_base > 0 else 0

                    if total_delta > 0:
                        st.markdown(
                            f"**Overall gap:** {total_delta:,.1f} steps/ea ({total_pct:,.1f}% improvement potential) "
                            f"— {total_base:,.1f} → {total_ceil:,.1f}"
                        )
                    elif total_delta < 0:
                        st.markdown(
                            f"**Baseline is faster** by {-total_delta:,.1f} steps/ea — no improvement needed!"
                        )
                    else:
                        st.markdown("**Both snapshots are equal** — no differences found.")

                    st.dataframe(
                        df_cmp,
                        column_config={
                            "Node": st.column_config.TextColumn("Node"),
                            "Source": st.column_config.TextColumn("Source"),
                            f"Steps ({base_snap['name']})": st.column_config.NumberColumn(f"Steps ({base_snap['name']})", format="%.1f"),
                            f"Steps ({ceil_snap['name']})": st.column_config.NumberColumn(f"Steps ({ceil_snap['name']})", format="%.1f"),
                            "Δ Steps": st.column_config.NumberColumn("Δ Steps", format="%.1f"),
                            "Improvement %": st.column_config.NumberColumn("Improvement %", format="%.1f%%"),
                        },
                        hide_index=True,
                        use_container_width=True,
                    )
                else:
                    st.info("No comparable nodes found between the selected snapshots. Make sure both were run on the same tree.")
