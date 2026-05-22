import streamlit as st
import json
import time
from streamlit_js_eval import streamlit_js_eval

from ui_utils import load_data
from drop_calculator import DropCalculator
from models import Equipment, Activity, Recipe, Location, Service, Collectible, Pet, Material, Consumable

# Import our split UI components
from ui_sidebar import render_user_data_section
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

def get_relative_time(timestamp):
    if not timestamp: return "Unknown"
    diff = time.time() - timestamp
    if diff < 60: return "Just now"
    if diff < 3600: return f"{int(diff // 60)} minutes ago"
    if diff < 86400: return f"{int(diff // 3600)} hours ago"
    return f"{int(diff // 86400)} days ago"

def main():
    init_session_state()

    # --- 1. Gather all Browser Context & LocalStorage in ONE Call ---
    js_expr = """
    (() => {
        return JSON.stringify({
            width: window.innerWidth,
            user_data_v2: localStorage.getItem('WALKSCAPE_USER_DATA_V2'),
            user_data_old: localStorage.getItem('WALKSCAPE_USER_DATA'),
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
                
                if b_data.get('user_data_v2'):
                    try:
                        payload_str = b_data['user_data_v2']
                        payload = json.loads(payload_str)
                        if isinstance(payload, str): 
                            payload = json.loads(payload)
                            
                        st.session_state['user_json_text'] = payload.get('data', '')
                        st.session_state['last_sync_timestamp'] = payload.get('timestamp')
                        st.session_state['ls_loaded'] = True
                    except Exception as e:
                        print("Error parsing V2 JSON payload:", e)
                
                # Fallback to the old format if V2 isn't found
                elif b_data.get('user_data_old'):
                    st.session_state['user_json_text'] = b_data['user_data_old']
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
    all_items_raw, activities, recipes, locations, services, all_collectibles_raw, all_pets, all_consumables, all_containers, all_materials = load_data()   
    
    # --- 2. Inject Custom Entities into Base Data ---
    if st.session_state.get('custom_entities'):
        for item in st.session_state['custom_entities']:
            etype = item.get("entity_type")
            data = item.get("data")
            try:
                if etype == "Equipment": all_items_raw.append(Equipment(**data))
                elif etype == "Material": all_materials.append(Material(**data))
                elif etype == "Consumable": all_consumables.append(Consumable(**data))
                elif etype == "Activity": activities.append(Activity(**data))
                elif etype == "Recipe": recipes.append(Recipe(**data))
                elif etype == "Location": locations.append(Location(**data))
                elif etype == "Pet": all_pets.append(Pet(**data))
            except Exception as e:
                print(f"Failed to load custom {etype} ({data.get('id')}): {e}")
    all_consumables = list({c.id: c for c in all_consumables}.values())
    drop_calc = DropCalculator()
    WIKI_URL = "https://gear.walkscape.app"

    with st.container():
        user_state = render_user_data_section(is_mobile, all_collectibles_raw)

    valid_json = user_state.get("valid_json", False)
    user_data = user_state.get("user_data")

    if valid_json and user_data:
        player_name = user_data.get('name', 'Player')
        user_ap = user_state.get("user_ap", 0)
        user_total_level = user_state.get("user_total_level", 0)
        sync_timestamp = st.session_state.get('last_sync_timestamp')
        time_ago = get_relative_time(sync_timestamp)
        
        st.markdown(
            f"""
            <div style='background-color: #064e3b; border: 1px solid #047857; color: #a7f3d0; 
            padding: 10px 15px; border-radius: 8px; margin-bottom: 15px; font-size: 0.95em; 
            display: flex; justify-content: space-between; align-items: center;'>
                <div>
                    <b>🟢 Active Profile:</b> {player_name} &nbsp;|&nbsp; 
                    <b>AP:</b> {user_ap} &nbsp;|&nbsp; 
                    <b>Total Lvl:</b> {user_total_level}
                </div>
                <div style='font-size: 0.85em; color: #6ee7b7;'>
                    Last Synced: {time_ago}
                </div>
            </div>
            """, 
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            f"""
            <div style='background-color: #1e293b; border: 2px dashed #fbbf24; color: #f8fafc; 
            padding: 20px; border-radius: 12px; margin-bottom: 20px; font-size: 1.1em; 
            display: flex; align-items: center; gap: 15px;'>
                <div style='color: #fbbf24; font-size: 2.5em; font-weight: 900; animation: pulse 2s infinite;'>
                    &larr;
                </div>
                <div>
                    <h3 style='margin:0; color:#fbbf24;'>Welcome to the Optimizer!</h3>
                    <p style='margin: 5px 0 0 0; color:#cbd5e1;'>
                        Open the sidebar (top left) and paste your WalkScape profile JSON to unlock your items and stats.
                    </p>
                </div>
            </div>
            """, 
            unsafe_allow_html=True
        )

    st.title("🔥 The Giga Optimizer OF HELL 🔥")

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