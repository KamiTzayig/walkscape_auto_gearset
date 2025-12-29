from utils import parse_csv_to_items, parse_csv_to_activities, calculate_steps
from gear_optimizer import GearOptimizer, OPTIMAZATION_TARGET
from export import export_gearset
import json

items_file_name = "items.csv"
activity_file_name = "activities.csv"
recipes_file_name = "recipes.csv"
user_data_file = "user.json"

with open(user_data_file, 'r') as f:
    user_data = json.load(f)
owned_items_names = set()

owned_items_names.update(user_data.get("bank", {}).keys())
owned_items_names.update(user_data.get("inventory", {}).keys())

if "gear" in user_data:
    equipped = {v for v in user_data["gear"].values() if v}
    owned_items_names.update(equipped)
items = parse_csv_to_items(items_file_name)
if user_data:
    items = [item for item in items if item.export_name in owned_items_names]

print(f"{len(items)} items loaded")
activities = parse_csv_to_activities(activities_file_path=activity_file_name, recipes_file_path=recipes_file_name)
print(f"{len(activities)} activities loaded")

#### EDIT THESE ###
target_name = "Create a Gold Ethernite Ring"
optimize_target = OPTIMAZATION_TARGET.materials
#######

activity = next((a for a in activities if a.activity == target_name), None)

if activity:
    print(f"Found {activity.activity} (Base Steps: {activity.base_steps}, Is Underwater: {activity.is_underwater})")
    
    optimizer = GearOptimizer(items)
    best_gear = optimizer.optimize(activity, player_level=99, player_skill_level=99, optimazation_target=optimize_target)

    print(f"\n--- Optimization Result for {target_name} ---")
    single_slots = ["head", "chest", "legs", "feet", "cape", "back", "neck", "hands", "primary", "secondary", "pet", "consumable"]
    for s in single_slots:
        val = getattr(best_gear, s)
        if val: print(f"{s.capitalize()}: {val.name}")
    
    if best_gear.rings: print(f"Rings: {', '.join([i.name for i in best_gear.rings])}")
    if best_gear.tools: print(f"Tools: {', '.join([i.name for i in best_gear.tools])}")

    stats = best_gear.get_stats(activity.skill)
    steps = calculate_steps(
        activity, 99, stats["work_efficiency"], 
        stats["flat_step_reduction"], stats["percent_step_reduction"]
    )
    
    xp_mult = 1.0 + stats["xp_percent"]
    total_xp_per_action = ((activity.base_xp * xp_mult) + stats["flat_xp"]) * (1.0 + stats["double_action"])
    
    print("\n--- Projected Stats ---")
    print(f"Steps per Action: {steps} (Base: {activity.base_steps})")
    print(f"XP per Action:    {total_xp_per_action:.2f}")
    print(f"XP per Step:      {total_xp_per_action / steps:.4f}")
    
    print("\n--- Modifiers ---")
    print(f"Work Eff: {stats['work_efficiency']*100:.1f}%")
    print(f"XP Bonus: {stats['xp_percent']*100:.1f}%")
    print(f"Dbl Act:  {stats['double_action']*100:.1f}%")
    print(stats)
    
    print("\n--- Export Code ---")
    export_string = export_gearset(best_gear)
    print(export_string)
# print the following items

# items_names = "Merfolk Dance Corslet,Flippers (Underwater),Amulet of Eel (T6-Eternal),Amulet of the Animal Kingdom (T6-Eternal),Wholly Ring,Wanderlust Walking Stick (T6-Eternal),Seth's Swamp Compass (GDTE),Hydrilium Diving Helm (T6-Eternal),Fin Gloves,Cape of Achiever,Lil Stool (GDTE),Omni-tool (200+)"

# for item in items_names.split(","):
#     item = item.strip()
#     item_obj = next((i for i in items if i.name == item), None)
#     if not item_obj:
#         print(f"Error: Could not find '{item}' in items.")
#     else:
#         print(item_obj, "\n")

# activity_names = "Guard Duty"
# for activity in activities:
#     if activity.activity in activity_names:
#         print(activity, "\n")
        