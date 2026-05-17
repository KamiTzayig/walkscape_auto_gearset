import streamlit as st
import json
import uuid
import time
from streamlit_js_eval import streamlit_js_eval
from models import Loadout
from utils.constants import GATHERING_SKILLS, ARTISAN_SKILLS, UTILITY_SKILLS
from ui_utils import (
    calculate_char_level_from_steps, calculate_total_level, extract_user_counts, 
    extract_user_reputation, get_user_collectibles, calculate_level_from_xp
)

def clear_json():
    """Callback to instantly clear the text area."""
    st.session_state['user_json_text'] = ""

def handle_json_change():
    """Callback that runs the moment the user clicks away after pasting."""
    val = st.session_state.get('user_json_text', '')
    if val.strip():
        try:
            # Test if it's valid JSON
            json.loads(val)
            # Trigger the JS save and update timestamp (using Unix time for relative calculation)
            st.session_state['trigger_ls_save'] = val
            st.session_state['last_sync_timestamp'] = time.time()
            # Force the use_owned checkbox to True on a fresh load
            st.session_state['use_owned_checkbox'] = True
            st.toast("🟢 Profile successfully synced!", icon="✅")
        except json.JSONDecodeError:
            st.toast("🔴 Invalid JSON provided. Check your copy-paste.", icon="❌")

def render_user_data_section(is_mobile, all_collectibles_raw):
    # Initialize default state
    user_state = {
        "user_data": None,
        "calculated_char_lvl": 99,
        "user_skills_map": {},
        "valid_json": False,
        "item_counts": {},
        "user_ap": 0,
        "user_total_level": 0,
        "skill_group_levels": {},
        "owned_collectibles": [],
        "user_reputation": {},
        "owned_pets": {},
        "use_owned": False
    }

    # Ensure the text area key exists
    if "user_json_text" not in st.session_state:
        st.session_state["user_json_text"] = ""

    with st.sidebar:
        st.header("📂 Player Profile")
        
        # The Auto-Saving Text Area
        val = st.text_area(
            "Paste User JSON", 
            height=120, 
            placeholder='Paste JSON here...',
            key="user_json_text",
            on_change=handle_json_change,
            label_visibility="collapsed"
        )
        c1, c2 = st.columns([3, 1.5])
        with c1:
            st.markdown("<div style='font-weight: 900;'>tap ANYWHERE to load the data!</div>", unsafe_allow_html=True)
        with c2:
            st.button("🧹 Clear", on_click=clear_json, use_container_width=True, help="Clear text area for easy pasting")


        # Handle the invisible LocalStorage save triggered by the callback
        if st.session_state.get('trigger_ls_save'):
            # Wrap the data in an envelope with the timestamp
            payload = {
                "timestamp": st.session_state['last_sync_timestamp'],
                "data": st.session_state['trigger_ls_save']
            }
            payload_str = json.dumps(payload)
            safe_js_string = json.dumps(payload_str) # JS-safe serialization
            
            streamlit_js_eval(
                js_expressions=f"localStorage.setItem('WALKSCAPE_USER_DATA_V2', {safe_js_string})",
                key="ls_saver"
            )
            # Reset trigger so it doesn't run constantly
            st.session_state['trigger_ls_save'] = None

        # Parse and populate the user state
        if val.strip():
            try:
                user_data = json.loads(val)
                user_state["valid_json"] = True
                user_state["user_data"] = user_data
                
                steps = user_data.get("steps", 0)
                user_state["calculated_char_lvl"] = calculate_char_level_from_steps(steps)
                user_state["user_skills_map"] = user_data.get("skills", {})
                user_state["user_ap"] = user_data.get("achievement_points", 0)
                user_state["item_counts"] = extract_user_counts(user_data)
                user_state["user_reputation"] = extract_user_reputation(user_data)
                user_state["owned_pets"] = extract_user_pets(user_data)
                
                if user_state["user_skills_map"]:
                    user_state["user_total_level"] = calculate_total_level(user_state["user_skills_map"])
                    user_state["skill_group_levels"] = {
                        "gathering": sum(calculate_level_from_xp(user_state["user_skills_map"].get(s, 0)) for s in GATHERING_SKILLS),
                        "artisan": sum(calculate_level_from_xp(user_state["user_skills_map"].get(s, 0)) for s in ARTISAN_SKILLS),
                        "utility": sum(calculate_level_from_xp(user_state["user_skills_map"].get(s, 0)) for s in UTILITY_SKILLS)
                    }
                if all_collectibles_raw:
                    user_state["owned_collectibles"] = get_user_collectibles(all_collectibles_raw, user_data)
                
                # Persistent Checkbox tied to session state
                if "use_owned_checkbox" not in st.session_state:
                    st.session_state["use_owned_checkbox"] = True
                    
                user_state["use_owned"] = st.checkbox(
                    "Only use owned items", 
                    key="use_owned_checkbox"
                )
                
            except json.JSONDecodeError:
                pass # Silently wait, the toast already handled the error message
                
        st.divider()

    return user_state


def extract_user_pets(user_data: dict) -> dict:
    owned_pets = {}
    pets_list = []
    
    if "pets" in user_data and isinstance(user_data["pets"], dict):
        equipped = user_data["pets"].get("pet")
        if equipped:
            pets_list.append(equipped)
            
    available = user_data.get("available_pets", [])
    if isinstance(available, list):
        pets_list.extend(available)
        
    for p in pets_list:
        species = p.get("species", "").lower()
        lvl = p.get("level", 1)
        name = p.get("name", species.title())
        
        if species not in owned_pets or lvl > owned_pets[species]["level"]:
            owned_pets[species] = {"name": name, "level": lvl}
            
    return owned_pets