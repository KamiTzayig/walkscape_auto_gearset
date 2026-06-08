import streamlit as st
import streamlit.components.v1 as components
import json
import pandas as pd
from models import CraftingNode
from utils.constants import EquipmentQuality, OPTIMAZATION_TARGET, INSTANT_ACTION_PET_ABILITIES, BUFF_PET_ABILITIES
from ui_utils import build_default_tree, can_tree_use_fine, calculate_level_from_xp, TARGET_CATEGORIES, get_compatible_services, synthesize_activity_from_recipe, build_activity_context, extract_modifier_stats, get_applicable_abilities, get_best_auto_pet, get_pet_charges_gained, is_material_level_valid, get_filtered_consumables
from calculations import calculate_node_metrics, solve_crafting_tree_lp
from gear_optimizer import GearOptimizer
from utils.export import export_gearset

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
            if recipe.service.lower() != "none":
                compat_services = get_compatible_services(recipe, list(game_data_dict['services'].values()))
                if compat_services:
                    opts = ["None"] + [s.id for s in compat_services]
                    def format_srv(x):
                        if x == "None": return "None"
                        return next((f"{s.name} ({s.location})" for s in compat_services if s.id == x), x)
                    idx = opts.index(node.selected_service_id) if getattr(node, 'selected_service_id', None) in opts else 0
                    new_srv = st.selectbox("Service", opts, index=idx, format_func=format_srv)
                    node.selected_service_id = new_srv if new_srv != "None" else None
            else:
                # Location selection for recipes with NO service
                opts = [loc.id for loc in locations]
                def format_loc(x):
                    return next((loc.name for loc in locations if loc.id == x), x)
                # Default to the first location if none is set
                if not getattr(node, 'selected_location_id', None) or node.selected_location_id not in opts:
                    node.selected_location_id = opts[0]
                idx = opts.index(node.selected_location_id)
                node.selected_location_id = st.selectbox("Location", opts, index=idx, format_func=format_loc)

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
        
    activity_obj = None
    if node.source_type == "recipe":
        activity_obj = game_data_dict['recipes'].get(node.source_id)
    elif node.source_type == "activity":
        activity_obj = game_data_dict['activities'].get(node.source_id)
        
    filtered_cons = get_filtered_consumables(list(game_data_dict['consumables'].values()), activity_obj)
    cons_opts = ["None"] + [c.id for c in filtered_cons]
    
    def format_cons(x):
        if x == "None": return "None"
        return next((c.name for c in filtered_cons if c.id == x), x)
        
    c_idx = cons_opts.index(node.selected_consumable_id) if getattr(node, 'selected_consumable_id', None) in cons_opts else 0
    new_cons_id = st.selectbox("Consumable", cons_opts, index=c_idx, format_func=format_cons)
    node.selected_consumable_id = new_cons_id if new_cons_id != "None" else None
    
    if st.button("Save & Close", type="primary"):
        st.rerun()

def render_tree_node(node: CraftingNode, game_data_dict: dict, drop_calc, locations, user_state: dict, level: int = 0):
    icon = {"recipe": "🔨", "activity": "🪓", "chest": "🧰", "bank": "🏦"}.get(node.source_type, "📦")
    item_name = node.item_id.replace('_', ' ').title()
    title = f"{icon} {item_name} (x{node.base_requirement_amount})"
    
    with st.expander(title, expanded=(level < 2)):
        c1, c2, c3 = st.columns([3, 3, 2])
        
        with c1:
            opts = [s["label"] for s in node.available_sources]
            current_label = opts[0]
            if node.source_type != "bank":
                for s in node.available_sources:
                    if s["type"] == node.source_type and s["id"] == node.source_id:
                        current_label = s["label"]
                        break
            
            idx = opts.index(current_label) if current_label in opts else 0
            new_label = st.selectbox("Source", options=opts, index=idx, key=f"src_{node.node_id}", label_visibility="collapsed")
            
            if new_label != current_label:
                selected_src = next(s for s in node.available_sources if s["label"] == new_label)
                node.source_type = selected_src["type"]
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
                                            if is_material_level_valid(mat, req):
                                                first_valid_id = mat.id
                                                break
                            elif req_type_val == 'item':
                                first_valid_id = req.target.lower()

                            if first_valid_id:
                                node.selected_activity_inputs[i] = first_valid_id
                                child_node = build_default_tree(first_valid_id, game_data_dict, drop_calc)
                                child_node.base_requirement_amount = req.value
                                node.inputs[f"{first_valid_id}_{i}"] = child_node
                elif node.source_type == "chest":
                    child_node = build_default_tree(node.source_id, game_data_dict, drop_calc)
                    child_node.base_requirement_amount = 1
                    node.inputs[node.source_id] = child_node
                            
                st.rerun()
            if node.source_type == "custom":
                st.markdown("###### 🔍 Select Global Source")
                
                # Combine all activities and recipes into a single list
                act_map = {f"[Activity] {a.name}": ("activity", a.id) for a in game_data_dict['activities'].values()}
                rec_map = {f"[Recipe] {r.name}": ("recipe", r.id) for r in game_data_dict['recipes'].values()}
                combined_map = {**act_map, **rec_map}
                combined_names = sorted(list(combined_map.keys()))
                
                custom_choice = st.selectbox(
                    "Search Activity or Recipe", 
                    options=["-- Select --"] + combined_names, 
                    index=0, 
                    key=f"custom_sel_{node.node_id}"
                )
                
                if custom_choice != "-- Select --":
                    chosen_type, chosen_id = combined_map[custom_choice]
                    new_custom_label = f"{custom_choice} (Custom)"
                    
                    # 1. Inject this new choice into the main dropdown so the user can see/keep it
                    if not any(s["label"] == new_custom_label for s in node.available_sources):
                        node.available_sources.append({
                            "type": chosen_type,
                            "id": chosen_id,
                            "label": new_custom_label
                        })
                    
                    # 2. Morph the node into the chosen type
                    node.source_type = chosen_type
                    node.source_id = chosen_id
                    
                    # 3. Clear existing inputs & states
                    node.inputs.clear()
                    if hasattr(node, 'selected_activity_inputs'):
                        node.selected_activity_inputs.clear()
                    if hasattr(node, '_pet_auto_checked'):
                        delattr(node, '_pet_auto_checked')
                    node.selected_pet_id = None
                    node.selected_pet_level = None
                    
                    # 4. Rebuild the child inputs for the newly morphed node
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
                                                if is_material_level_valid(mat, req):
                                                    first_valid_id = mat.id
                                                    break
                                elif req_type_val == 'item':
                                    first_valid_id = req.target.lower()

                                if first_valid_id:
                                    node.selected_activity_inputs[i] = first_valid_id
                                    child_node = build_default_tree(first_valid_id, game_data_dict, drop_calc)
                                    child_node.base_requirement_amount = req.value
                                    node.inputs[f"{first_valid_id}_{i}"] = child_node

                    # 5. Rerun the UI so the location/service overrides pop into existence!
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
                                            if is_material_level_valid(mat, req):
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

        # Show Local Node Math Breakdown
        if node.source_type != "bank":
            applicable_abs = get_applicable_abilities(node, game_data_dict)
            
            if applicable_abs:
                st.write("") 
                for pet_obj, ab in applicable_abs:
                    label = f"⚡ Use {ab.name} ({pet_obj.name}) - 0 Steps" if ab.name in INSTANT_ACTION_PET_ABILITIES else f"⚡ Use {ab.name} ({pet_obj.name}) - Buff"
                    new_val = st.checkbox(
                        label, 
                        value=getattr(node, 'use_pet_ability', False), 
                        key=f"ab_{node.node_id}_{ab.name}"
                    )
                    if new_val != getattr(node, 'use_pet_ability', False):
                        node.use_pet_ability = new_val
                        st.rerun()

        # --- EXPOSE HUMAN-INTUITIVE LP SOLVER STATS ---
        if node.metrics and "lp_data" in node.metrics:
            lp_data = node.metrics["lp_data"]
            lp_actions = lp_data.get("actions", 0.0)
            lp_steps = lp_data.get("steps", 0.0)
            contribs = lp_data.get("contributions", [])
            consumptions = lp_data.get("consumptions", [])
            act_name = lp_data.get("source_name", "this activity").replace("Activity: ", "").replace("Recipe: ", "").replace("Chest: ", "")
            
            if lp_actions > 0.001 and node.source_type != "bank":
                msg_html = f"Ran <strong>{lp_actions:,.1f}</strong> actions of {act_name} (Total: <strong>{lp_steps:,.0f}</strong> steps)."
                
                if contribs:
                    msg_html += "<div style='margin-top: 5px;'><strong>Produced (Tree Essentials):</strong></div><ul style='margin-top: 2px; margin-bottom: 5px;'>"
                    for c in contribs:
                        display_name = c['item_id'].replace('_', ' ').title()
                        msg_html += f"<li><strong>{c['amount']:,.2f} {display_name}</strong> <em>({c['percent']:,.1f}% of total needed)</em></li>"
                    msg_html += "</ul>"
                    
                if consumptions:
                    msg_html += "<div style='margin-top: 5px;'><strong>Consumed:</strong></div><ul style='margin-top: 2px; margin-bottom: 0px;'>"
                    for c in consumptions:
                        display_name = c['item_id'].replace('_', ' ').title()
                        msg_html += f"<li><strong>{c['amount']:,.2f} {display_name}</strong></li>"
                    msg_html += "</ul>"
                
                st.markdown(
                    f"""
                    <div style='background-color: rgba(46, 204, 113, 0.1); border-left: 3px solid #2ecc71; padding: 10px; margin-top: 10px; font-size: 0.9em; color: #a0aec0;'>
                        {msg_html}
                    </div>
                    """, 
                    unsafe_allow_html=True
                )
            elif lp_actions <= 0.001:
                st.markdown(
                    """
                    <div style='background-color: rgba(255, 255, 255, 0.05); border-left: 3px solid #718096; padding: 10px; margin-top: 10px; font-size: 0.9em; color: #718096;'>
                        <strong>Skipped by Solver:</strong><br>
                        This item requirement was fulfilled 100% by free byproducts from other activities in your tree!
                    </div>
                    """, 
                    unsafe_allow_html=True
                )

        st.write("") 
        if node.source_type == "recipe":
            recipe = game_data_dict['recipes'].get(node.source_id)
            if recipe:
                if recipe.service.lower() != "none":
                    compat_services = get_compatible_services(recipe, list(game_data_dict['services'].values()))
                    if compat_services:
                        opts = [s.id for s in compat_services]
                        def format_srv(x):
                            return next((f"{s.name} ({s.location})" for s in compat_services if s.id == x), x)
                        idx = opts.index(node.selected_service_id) if getattr(node, 'selected_service_id', None) in opts else 0
                        
                        new_srv = st.selectbox("📌 Service Override", opts, index=idx, format_func=format_srv, key=f"inl_srv_{node.node_id}")
                        if new_srv != (getattr(node, 'selected_service_id', None) or "None"):
                            node.selected_service_id = new_srv if new_srv != "None" else None
                            st.rerun()
                else:
                    # Inline Location Override for recipes with NO service
                    opts = [loc.id for loc in locations]
                    def format_loc(x):
                        return next((loc.name for loc in locations if loc.id == x), x)
                    # Default to the first location if none is set
                    if not getattr(node, 'selected_location_id', None) or node.selected_location_id not in opts:
                        node.selected_location_id = opts[0]
                    idx = opts.index(node.selected_location_id)
                    
                    new_loc = st.selectbox("📌 Location Override", opts, index=idx, format_func=format_loc, key=f"inl_loc_{node.node_id}")
                    if new_loc != node.selected_location_id:
                        node.selected_location_id = new_loc
                        st.rerun()
                        
        elif node.source_type == "activity":
            act = game_data_dict['activities'].get(node.source_id)
            if act and act.locations:
                opts = list(act.locations)
                def format_loc(x):
                    return next((loc.name for loc in locations if loc.id == x), x)
                idx = opts.index(node.selected_location_id) if getattr(node, 'selected_location_id', None) in opts else 0
                
                new_loc = st.selectbox("📌 Location Override", opts, index=idx, format_func=format_loc, key=f"inl_loc_{node.node_id}")
                if new_loc != (getattr(node, 'selected_location_id', None) or "None"):
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

            
        if node.inputs:
            st.markdown("###### ⬇️ Requires:")
            with st.container(border=False):
                for child_id, child_node in node.inputs.items():
                    render_tree_node(child_node, game_data_dict, drop_calc, locations, user_state, level + 1)

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
                qualities = [q.capitalize() for q in EquipmentQuality]
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
                        if n.source_type == "activity" and getattr(n, 'loadout_id', None) == "AUTO":
                            target_name = "Fine" if new_fine_val else "Reward Rolls"
                            n.auto_optimize_target = [{"id": 0, "target": target_name, "weight": 100}]
                        for child in n.inputs.values():
                            force_fine_targets(child)
                            
                    force_fine_targets(root)
                    st.rerun()
            else:
                st.session_state['global_fine'] = False

        st.divider()
        
        render_tree_node(root, game_data_dict, drop_calc, locations, user_state)       
        st.divider()
        if st.button("🧮 Calculate True Cost & Run Optimizers", type="primary"):
            
            valid_json = user_state.get("valid_json", False)
            user_skills_map = user_state.get("user_skills_map", {})
            player_skill_levels = {k: calculate_level_from_xp(v) for k, v in user_skills_map.items()} if valid_json else {}
            
            optimizer = GearOptimizer(all_items_raw, locations)
            owned_item_counts = user_state.get("item_counts", {}) if valid_json else {}
            ap = user_state.get("user_ap", 0) if valid_json else 0
            reputation = user_state.get("user_reputation", {}) if valid_json else {}
            collectibles = user_state.get("owned_collectibles", []) if valid_json else []
            char_lvl = user_state.get("calculated_char_lvl", 99) if valid_json else 99

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
                                            self.materials = r.materials
                                            self.output_item_id = r.output_item_id
                                            self.output_quantity = r.output_quantity
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
                                loc_map, drop_calc, getattr(node, 'selected_location_id', None),user_state.get("skill_group_levels", {}),
                            )
                            
                            base_input_cost = 0.0
                            if node.source_type == "recipe" and hasattr(activity_obj, 'materials'):
                                for mat_group in activity_obj.materials:
                                    mat = mat_group[0]
                                    base_id = mat.item_id.replace("_fine", "")
                                    mat_id = base_id
                                    if st.session_state.get('global_fine', False):
                                        fine_id = drop_calc.fine_material_map.get(base_id, f"{base_id}_fine")
                                        if fine_id in drop_calc.item_values:
                                            mat_id = fine_id
                                    base_input_cost += mat.amount * drop_calc.item_values.get(mat_id, 0.0)
                            elif node.source_type == "activity" and hasattr(activity_obj, 'requirements'):
                                input_reqs = [r for r in activity_obj.requirements if getattr(r.type, 'value', r.type) in ('keyword_count', 'input_keyword', 'item')]
                                for i, req in enumerate(input_reqs):
                                    mat_id = getattr(node, 'selected_activity_inputs', {}).get(i)
                                    if mat_id:
                                        base_input_cost += req.value * drop_calc.item_values.get(mat_id, 0.0)

                            node_context["base_input_cost"] = base_input_cost
                            
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
                            if pet_obj: 
                                pet_obj = pet_obj.copy(update={"active_level": getattr(node, 'selected_pet_level', 1)})
                                pet_obj.use_pet_ability = getattr(node, 'use_pet_ability', False)
                            
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
                                extra_passive_stats=extra_passives
                            )
                            node.auto_gear_set = opt_result[0] 

                    # -----------------------------------------------------
                    # PASS 1: Local Recursive Math (Preserves UI & Gearsets)
                    # -----------------------------------------------------
                    target_qual = st.session_state.get('global_quality', "Normal") if is_root else "Normal"
                    
                    node.metrics = calculate_node_metrics(
                        node, st.session_state['saved_loadouts'], game_data_dict, drop_calc, 
                        player_skill_levels, user_state, locations,  
                        global_target_quality=target_qual, global_use_fine=st.session_state.get('global_fine', False)
                    )
                    
                    # --- PREPARE EXPORT BASE64 ---
                    gs = None
                    if getattr(node, 'loadout_id', None) == "AUTO" and getattr(node, 'auto_gear_set', None):
                        gs = node.auto_gear_set
                    elif getattr(node, 'loadout_id', None) and node.loadout_id in st.session_state['saved_loadouts']:
                        gs = st.session_state['saved_loadouts'][node.loadout_id].gear_set.clone()

                    if gs:
                        # Grab the items directly via list comprehension
                        gs.inputs = [game_data_dict['materials'].get(i) or game_data_dict['consumables'].get(i) 
                                     for i in getattr(node, 'selected_activity_inputs', {}).values() if i]
                        
                        node.metrics["gear_set_base64"] = export_gearset(gs)

                # 1. Run the Gear Optimizer across the whole tree and calculate local node math
                run_and_save_metrics(root, is_root=True)
                
                # -----------------------------------------------------
                # PASS 2: RUN THE LINEAR PROGRAMMING SOLVER! (Global Byproduct Sharing)
                # -----------------------------------------------------
                target_qual = st.session_state.get('global_quality', "Normal")
                
                success, msg, master_metrics = solve_crafting_tree_lp(
                    root_node=root,
                    loadouts=st.session_state['saved_loadouts'],
                    game_data=game_data_dict,
                    drop_calc=drop_calc,
                    player_skill_levels=player_skill_levels,
                    user_state=user_state,
                    locations=locations,
                    global_target_quality=target_qual,
                    global_use_fine=st.session_state.get('global_fine', False)
                )
                
                if success:
                    # Safely inject the master summary numbers, but keep the root gearset AND LP Data!
                    master_metrics["gear_set_base64"] = root.metrics.get("gear_set_base64")
                    if "lp_data" in root.metrics:
                        master_metrics["lp_data"] = root.metrics["lp_data"]
                    root.metrics = master_metrics
                else:
                    st.error(f"**Mathematical Infeasibility Detected:**\n\n{msg}")
                    root.metrics = {"steps": float('inf'), "stats_used": {}}
                    
            st.rerun()

        # ==========================================
        # SUMMARY SECTION
        # ==========================================
        if root.metrics and root.metrics.get("steps", float('inf')) != float('inf'):
            st.markdown("### 📊 Grand Totals Summary")
            st.caption(f"Calculated for **{target_amount}x {target_item.replace('_', ' ').title()}**")
            
            total_steps = root.metrics["steps"] * target_amount
            days_est = total_steps / daily_steps
            
            # --- PRE-CALCULATE FINANCIALS & DROPS ---
            raw_material_cost = 0.0
            for item_id, amt in root.metrics["raw_materials"].items():
                raw_material_cost += (amt * target_amount) * drop_calc.item_values.get(item_id, 0.0)

            target_value = 0.0
            side_value = 0.0
            
            base_target_id = target_item_id.replace("_fine", "")
            qualities_data = []
            side_drops_data = []

            for drop_id, amt in root.metrics.get("drops_gained", {}).items():
                final_amt = amt * target_amount
                
                # Ignore mathematically insignificant fractions
                if final_amt < 0.001: 
                    continue
                
                display_name = drop_id.replace("_", " ").title()
                val_per_unit = drop_calc.container_evs.get(drop_id, drop_calc.item_values.get(drop_id, 0.0))
                total_val = final_amt * val_per_unit
                
                if drop_id.startswith(base_target_id):
                    qualities_data.append({"Item": display_name, "Yield": final_amt, "Value": total_val})
                    target_value += total_val
                else:
                    side_drops_data.append({"Item": display_name, "Yield": final_amt, "Value": total_val})
                    side_value += total_val

            net_profit = (target_value + side_value) - raw_material_cost
            
            # --- Key Metrics Cards ---
            c_sum1, c_sum2, c_sum3 = st.columns(3)
            with c_sum1:
                st.markdown(f"<div style='background-color:#0f172a; padding:15px; border-radius:8px; border: 1px solid #1e293b;'>"
                            f"<h4 style='margin:0; color:#e2e8f0;'>Total Steps</h4>"
                            f"<h2 style='margin:0; color:#4ade80;'>{total_steps:,.0f}</h2>"
                            f"<span style='color:#94a3b8;'>Estimated Time: <b>{days_est:,.1f} days</b></span>"
                            f"</div>", unsafe_allow_html=True)
                
            with c_sum2:
                st.markdown(f"<div style='background-color:#0f172a; padding:15px; border-radius:8px; border: 1px solid #1e293b;'>"
                            f"<h4 style='margin:0; color:#e2e8f0;'>Total Material Cost</h4>"
                            f"<h2 style='margin:0; color:#f87171;'>{raw_material_cost:,.0f} 🪙</h2>"
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
            c_fin1, c_fin2 = st.columns(2)
            with c_fin1:
                st.markdown(f"<div style='background-color:#0f172a; padding:15px; border-radius:8px; border: 1px solid #1e293b;'>"
                            f"<h4 style='margin:0; color:#e2e8f0;'>Side Drops Value</h4>"
                            f"<h2 style='margin:0; color:#fbbf24;'>{side_value:,.0f} 🪙</h2>"
                            f"<span style='color:#94a3b8;'>Sell value of all byproducts and chests</span>"
                            f"</div>", unsafe_allow_html=True)
            with c_fin2:
                color = "#4ade80" if net_profit >= 0 else "#f87171"
                st.markdown(f"<div style='background-color:#0f172a; padding:15px; border-radius:8px; border: 1px solid #1e293b;'>"
                            f"<h4 style='margin:0; color:#e2e8f0;'>Net Profit</h4>"
                            f"<h2 style='margin:0; color:{color};'>{net_profit:,.0f} 🪙</h2>"
                            f"<span style='color:#94a3b8;'>Total Yield ({target_value + side_value:,.0f} 🪙) - Material Cost</span>"
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
                    
            # --- NEW STEP 4: DROPS & BYPRODUCTS UI ---
            st.write("")
            st.markdown("---")
            st.markdown("### 🎁 Crafting Outcomes & Side Drops")
            st.caption("Displays the actual yield of the target item (including qualities) and all accumulated byproducts across the chain.")

            c_drop1, c_drop2 = st.columns(2)
            with c_drop1:
                st.markdown("##### 🎯 Target Item Yield")
                st.caption("Expected distribution of qualities.")
                if qualities_data:
                    df_qual = pd.DataFrame(qualities_data).sort_values(by="Yield", ascending=False)
                    st.dataframe(
                        df_qual,
                        column_config={
                            "Item": st.column_config.TextColumn("Item"),
                            "Yield": st.column_config.NumberColumn("Quantity Yielded", format="%.2f"),
                            "Value": st.column_config.NumberColumn("Total Value", format="%.1f 🪙")
                        },
                        hide_index=True,
                        width="stretch"
                    )
                else:
                    st.info("No target items generated (this usually means the target couldn't be crafted).")

            with c_drop2:
                st.markdown("##### 📦 Side Drops & Byproducts")
                st.caption("Chests, tokens, gems, and gathered materials.")
                if side_drops_data:
                    df_side = pd.DataFrame(side_drops_data).sort_values(by="Yield", ascending=False)
                    st.dataframe(
                        df_side,
                        column_config={
                            "Item": st.column_config.TextColumn("Item"),
                            "Yield": st.column_config.NumberColumn("Quantity Yielded", format="%.2f"),
                            "Value": st.column_config.NumberColumn("Total Value", format="%.1f 🪙")
                        },
                        hide_index=True,
                        width="stretch"
                    )
                else:
                    st.info("No side drops accumulated.")