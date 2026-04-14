import streamlit as st
import json
from streamlit_js_eval import streamlit_js_eval

from ui_utils import load_data, _data_files_hash
from drop_calculator import DropCalculator
from models import Equipment, Activity, Recipe, Location, Service, Collectible, Pet, Material, Consumable

# Import our split UI components
from ui_sidebar import render_sidebar, render_user_data_section
from tab_crafting_tree import render_crafting_tree_tab
from tab_optimizer import render_optimizer_tab
from tab_data_entry import render_data_entry_tab

# --- Page Config ---
st.set_page_config(
    page_title="WalkScape Gear Optimizer",
    layout="wide",
    initial_sidebar_state="expanded"
)

try:
    with open("style.css", "r") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
except FileNotFoundError:
    pass

def init_session_state():
    if 'locked_items_state' not in st.session_state:
        st.session_state['locked_items_state'] = {}
    if 'blacklist_state' not in st.session_state:
        st.session_state['blacklist_state'] = []
    if 'user_json_text' not in st.session_state:
        st.session_state['user_json_text'] = ""
    if 'ls_loaded' not in st.session_state:
        st.session_state['ls_loaded'] = False
    if 'custom_entities' not in st.session_state:
        st.session_state['custom_entities'] = []

    if 'opt_targets_list' not in st.session_state:
        st.session_state['opt_targets_list'] = [{"id": 0, "target": "Reward Rolls", "weight": 100}]
        st.session_state['next_target_id'] = 1

    if 'saved_loadouts' not in st.session_state: st.session_state['saved_loadouts'] = {}
    if 'crafting_tree_root' not in st.session_state: st.session_state['crafting_tree_root'] = None
    if 'tree_snapshots' not in st.session_state: st.session_state['tree_snapshots'] = []

def main():
    init_session_state()

    # --- 1. Gather all Browser Context & LocalStorage in ONE Call ---
    # Multiple streamlit_js_eval components can cause race conditions and Duplicate Element Key errors.
    # This grabs the screen width and all local storage keys in one shot.
    js_expr = """
    (() => {
        return JSON.stringify({
            width: window.innerWidth,
            user_data: localStorage.getItem('WALKSCAPE_USER_DATA'),
            custom_data: localStorage.getItem('WALKSCAPE_CUSTOM_DATA')
        });
    })()
    """
    
    browser_data_raw = streamlit_js_eval(js_expressions=js_expr, key='browser_init_data')
    
    is_mobile = False
    if browser_data_raw:
        try:
            b_data = json.loads(browser_data_raw)
            
            # Check Screen Width
            width = b_data.get('width')
            if width:
                is_mobile = width < 768
                
            # Process Local Storage ONLY if we haven't loaded it yet for this session
            if not st.session_state.get('browser_data_loaded'):
                
                if b_data.get('user_data'):
                    st.session_state['user_json_text'] = b_data['user_data']
                    st.session_state['ls_loaded'] = True
                    
                if b_data.get('custom_data'):
                    try:
                        st.session_state['custom_entities'] = json.loads(b_data['custom_data'])
                    except json.JSONDecodeError:
                        st.session_state['custom_entities'] = []
                        
                st.session_state['browser_data_loaded'] = True
                st.rerun() # Force an immediate rerun so the UI populates with the loaded data
                
        except Exception as e:
            print("Error parsing browser data:", e)

    # Load Base Data globally
    _items, _activities, _recipes, _locations, _services, _collectibles, _pets, _consumables, _containers, _materials = load_data(file_hash=_data_files_hash())
    # IMPORTANT: copy lists before mutating so we don't corrupt the @st.cache_data cache
    all_items_raw = list(_items)
    activities = list(_activities)
    recipes = list(_recipes)
    locations = list(_locations)
    services = list(_services)
    all_collectibles_raw = list(_collectibles)
    all_pets = list(_pets)
    all_consumables = list(_consumables)
    all_containers = list(_containers)
    all_materials = list(_materials)

    # --- 2. Inject Custom Entities into Base Data ---
    # Build lookup sets for conflict detection.
    _loaded_activity_ids = {a.id for a in activities}
    _loaded_recipe_ids   = {r.id for r in recipes}

    if st.session_state.get('custom_entities'):
        for item in st.session_state['custom_entities']:
            etype = item.get("entity_type")
            data = item.get("data")
            try:
                if etype == "Equipment":
                    all_items_raw.append(Equipment(**data))
                elif etype == "Material":
                    all_materials.append(Material(**data))
                elif etype == "Consumable":
                    all_consumables.append(Consumable(**data))
                elif etype == "Activity":
                    cid = data.get("id", "")
                    if cid in _loaded_activity_ids:
                        # Merge: apply custom data on top of the loaded activity so that
                        # unset fields (e.g. base_steps=0 default) don't wipe valid loaded values.
                        loaded_act = next(a for a in activities if a.id == cid)
                        base = loaded_act.model_dump(mode="json")
                        # Only override with non-default custom values to avoid silent zeroing.
                        for k, v in data.items():
                            if k == "base_steps" and v == 0:
                                continue  # 0 is the Pydantic default, not an intentional override
                            base[k] = v
                        merged = Activity(**base)
                        idx = next(i for i, a in enumerate(activities) if a.id == cid)
                        activities[idx] = merged
                        if merged.base_steps != loaded_act.base_steps:
                            st.warning(
                                f"⚠️ Custom activity **{cid}** overrides the loaded entry "
                                f"(base_steps: {loaded_act.base_steps} → {merged.base_steps}). "
                                "Check the Data Entry tab if this is unintentional."
                            )
                    else:
                        activities.append(Activity(**data))
                elif etype == "Recipe":
                    cid = data.get("id", "")
                    if cid in _loaded_recipe_ids:
                        loaded_rec = next(r for r in recipes if r.id == cid)
                        base = loaded_rec.model_dump(mode="json")
                        base.update(data)
                        idx = next(i for i, r in enumerate(recipes) if r.id == cid)
                        recipes[idx] = Recipe(**base)
                    else:
                        recipes.append(Recipe(**data))
                elif etype == "Location":
                    locations.append(Location(**data))
                elif etype == "Pet":
                    all_pets.append(Pet(**data))
            except Exception as e:
                print(f"Failed to load custom {etype} ({data.get('id')}): {e}")

    drop_calc = DropCalculator()
    WIKI_URL = "https://gear.walkscape.app"

    render_sidebar()

    st.title("🛡️ WalkScape Gear Optimizer")

    with st.container():
        user_state = render_user_data_section(is_mobile, all_collectibles_raw)

    tab_opt, tab_tree, tab_entry = st.tabs(["🎯 Single Optimizer", "🌳 Crafting Tree Calculator", "📝 Data Entry"])
    
    with tab_tree:
        render_crafting_tree_tab(
            recipes, all_items_raw, activities, all_containers, 
            user_state, drop_calc, locations, services, all_pets, all_consumables, all_materials
        )
    with tab_opt:
        render_optimizer_tab(
            is_mobile, user_state, all_items_raw, activities, recipes, 
            locations, services, all_pets, all_consumables, all_materials, drop_calc, WIKI_URL
        )
    with tab_entry:
        render_data_entry_tab(
            all_items_raw, activities, locations, services, 
            all_pets, all_consumables, all_materials
        )

if __name__ == "__main__":
    main()