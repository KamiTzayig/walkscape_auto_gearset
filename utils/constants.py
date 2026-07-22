from enum import Enum

class EquipmentQuality(str, Enum):
    NORMAL = "Normal"
    GOOD = "Good"
    GREAT = "Great"
    EXCELLENT = "Excellent"
    PERFECT = "Perfect"
    ETERNAL = "Eternal"
    NONE = "None" 

class EquipmentSlot(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    HEAD = "head"
    CHEST = "chest"
    LEGS = "legs"
    FEET = "feet"
    TOOLS = "tools"
    RING = "ring"
    NECK = "neck"
    CAPE = "cape"
    BACK = "back"
    HANDS = "hands"
    UNKNOWN = "unknown"

class SkillName(str, Enum):
    AGILITY = "agility"
    CARPENTRY = "carpentry"
    COOKING = "cooking"
    CRAFTING = "crafting"
    FISHING = "fishing"
    FORAGING = "foraging"
    MINING = "mining"
    SMITHING = "smithing"
    TRINKETRY = "trinketry"
    WOODCUTTING = "woodcutting"
    TRAVELING = "traveling"
    HUNTING = "hunting"        
    TAILORING = "tailoring"   
    NONE = "none"

# --- SKILL CATEGORIES ---
GATHERING_SKILLS = {
    "foraging", "mining", "woodcutting", "fishing" , "hunting"
}

ARTISAN_SKILLS = {
    "smithing", "carpentry", "trinketry", "crafting", "cooking", "tailoring"
}

UTILITY_SKILLS = {
    "agility", "traveling"
}
class ConditionType(str, Enum):
    GLOBAL = "global"
    LOCATION = "location"           
    REGION = "region"               
    SKILL_ACTIVITY = "skill_activity"    
    SPECIFIC_ACTIVITY = "specific_activity" 
    ACHIEVEMENT_POINTS = "achievement_points" 
    ITEM_OWNERSHIP = "item_ownership"
    SET_EQUIPPED = "set_equipped"
    TOTAL_SKILL_LEVEL = "total_skill_level"
    ACTIVITY_COMPLETION = "activity_completion"
    REPUTATION = "reputation"


class RequirementType(str, Enum):
    SKILL_LEVEL = "skill_level"
    QUEST_COMPLETED = "quest_completed"
    REPUTATION = "reputation"
    CHARACTER_LEVEL = "character_level"
    KEYWORD_COUNT = "keyword_count"       
    ACTIVITY_COMPLETION = "activity_completion" 
    ACHIEVEMENT_POINTS = "achievement_points" 
    TOOL_EQUIPPED = "tool_equipped"
    UNIQUE_TOOLS = "unique_tools"
    SPECIFIC_ACTIVITY = "specific_activity"
    INPUT_KEYWORD = "input_keyword"
    PET_ABILITY = "pet_ability"
    TOTAL_SKILL_LEVEL = "total_skill_level"
    SKILL_GROUP_LEVEL = "skill_group_level"

class StatName(str, Enum):
    # Core
    WORK_EFFICIENCY = "work_efficiency"
    DOUBLE_ACTION = "double_action"
    DOUBLE_REWARDS = "double_rewards"
    NO_MATERIALS_CONSUMED = "no_materials_consumed"
    QUALITY_OUTCOME = "quality_outcome"
    
    # Steps
    STEPS_ADD = "steps_add"
    STEPS_PERCENT = "steps_percent"
    XP_PERCENT = "xp_percent" 
    
    # XP (Legacy/Variations)
    BONUS_XP_ADD = "bonus_xp_add"
    BONUS_XP_PERCENT = "bonus_xp_percent"
    
    # Finding
    CHEST_FINDING = "chest_finding"
    FIND_BIRD_NESTS = "find_bird_nests"
    FIND_COLLECTIBLES = "find_collectibles"
    FIND_GEMS = "find_gems"
    FINE_MATERIAL_FINDING = "fine_material_finding"
    
    # Finding Items
    FIND_ADVENTURERS_GUILD_TOKEN = "find_adventurers_guild_token"
    FIND_FISHING_BAIT = "find_fishing_bait"
    FIND_CRUSTACEAN = "find_crustacean"
    FIND_ECTOPLASM = "find_ectoplasm"
    FIND_GOLD = "find_gold"
    FIND_COIN_POUCH = "find_coin_pouch"
    FIND_JUNK = "find_junk"
    FIND_SEA_SHELLS = "find_sea_shells"
    FIND_GOLD_NUGGET = "find_gold_nugget"
    FIND_SKILL_CHEST = "find_skill_chest"
    CHANCE_TO_FIND_BIRD_NEST = "chance_to_find_bird_nest"
    FIND_RANDOM_GEM = "find_random_gem"
    FIND_FIBROUS_PLANT = "find_fibrous_plant"
    FIND_LINENS = "find_linens"
    INVENTORY_SPACE = "inventory_space"

    # "Any Action" Global XP
    GAIN_AGILITY_XP = "gain_agility_xp"
    GAIN_CARPENTRY_XP = "gain_carpentry_xp"
    GAIN_COOKING_XP = "gain_cooking_xp"
    GAIN_CRAFTING_XP = "gain_crafting_xp"
    GAIN_FISHING_XP = "gain_fishing_xp"
    GAIN_FORAGING_XP = "gain_foraging_xp"
    GAIN_MINING_XP = "gain_mining_xp"
    GAIN_SMITHING_XP = "gain_smithing_xp"
    GAIN_TRINKETRY_XP = "gain_trinketry_xp"
    GAIN_WOODCUTTING_XP = "gain_woodcutting_xp"
    GAIN_TRAVELING_XP = "gain_traveling_xp"
    GAIN_HUNTING_XP = "gain_hunting_xp"
    GAIN_TAILORING_XP = "gain_tailoring_xp"

# --- Constants & Config ---
RESTRICTED_TOOL_KEYWORDS = {
    "Pickaxe", "Hatchet", "Fishing tool", "Fishing lure", "Foraging tool", "Basket", "Bellows",
    "Bug catching net", "Chisel", "Climbing gear", "Cooking knife", "Cooking pan",
    "Fishing cage", "Fishing net", "Fishing spear", "Gold pan", "Knife",
    "Life vest", "Local map", "Log Splitter", "Magnetic", "Magnifying lens",
    "Ruler", "Sander", "Saw", "Sickle", "Wrench", "Smithing hammer",
    "Cutting mat", 
    "Fishing rod", 
    "Fishing rod rest", 
    "Hunting bow", 
    "Needle", 
    "Pliers", 
    "Pins", 
    "Scissors", 
    "Toolbox"
}



QUALITY_RANK = {
    EquipmentQuality.NORMAL: 0, EquipmentQuality.GOOD: 1, EquipmentQuality.GREAT: 2,
    EquipmentQuality.EXCELLENT: 3, EquipmentQuality.PERFECT: 4, EquipmentQuality.ETERNAL: 5,
    EquipmentQuality.NONE: -1
}

QUALITY_NAMES = [
    EquipmentQuality.NORMAL.value, EquipmentQuality.GOOD.value, EquipmentQuality.GREAT.value,
    EquipmentQuality.EXCELLENT.value, EquipmentQuality.PERFECT.value, EquipmentQuality.ETERNAL.value,
]

QUALITY_SUFFIX_MAP = {
    "Normal": "_common",
    "Good": "_uncommon",
    "Great": "_rare",
    "Excellent": "_epic",
    "Perfect": "_legendary",
    "Eternal": "_ethereal"
}

class OPTIMAZATION_TARGET(str, Enum):
    reward_rolls = "reward_rolls"
    xp = "xp"
    chests = "chests"
    materials_from_input = "materials_from_input"
    fine = "fine"
    collectibles = "collectibles"
    #
    eternal_per_input = "eternal_per_input"
    good_per_step = "good_per_step"
    great_per_step = "great_per_step"
    excellent_per_step = "excellent_per_step"
    perfect_per_step = "perfect_per_step"
    eternal_per_step = "eternal_per_step"
   
    tokens_per_step = "tokens_per_step"
    ectoplasm_per_step = "ectoplasm_per_step"
    fine_ectoplasm_per_step = "fine_ectoplasm_per_step"
    sea_shells_per_step = "sea_shells_per_step"
    fine_sea_shells_per_step = "fine_sea_shells_per_step"
    crustacean_per_step = "crustacean_per_step"
    fine_crustacean_per_step = "fine_crustacean_per_step"
    fibrous_plant_per_step = "fibrous_plant_per_step"
    fine_fibrous_plant_per_step = "fine_fibrous_plant_per_step"
    fishing_bait_per_step = "fishing_bait_per_step"
    fine_fishing_bait_per_step = "fine_fishing_bait_per_step"
    gold_nugget_per_step = "gold_nugget_per_step"
    fine_gold_nugget_per_step = "fine_gold_nugget_per_step"
    find_random_gem_per_step = "find_random_gem_per_step"
    find_random_fine_gem_per_step = "find_random_fine_gem_per_step"
    gem_finding = "gem_finding"
    gem_finding_fine = "gem_finding_fine"
    bird_nests_per_step = "bird_nests_per_step"


    coins = "coins"
    coins_no_chests = "coins_no_chests"
    coins_no_fines = "coins_no_fines"
    coins_no_chests_no_fines = "coins_no_chests_no_fines"
    net_profit_per_step = "net_profit_per_step"
   
    xp_per_material = "xp_per_material"
    chests_per_material = "chests_per_material"
    
    exp_no_steps = "exp_no_steps"
    chests_no_steps = "chests_no_steps"
    reward_rolls_no_steps = "reward_rolls_no_steps"
    fine_no_steps = "fine_no_steps"
    collectibles_no_steps = "collectibles_no_steps"

# Correct Set Union Syntax using |
REWARD_ROLL_STATS = {StatName.DOUBLE_ACTION, StatName.DOUBLE_REWARDS, StatName.WORK_EFFICIENCY, StatName.STEPS_ADD, StatName.STEPS_PERCENT}
COIN_BASE_STATS = REWARD_ROLL_STATS | {StatName.FIND_GOLD, StatName.FIND_COIN_POUCH}
TARGET_TO_STATS = {
    OPTIMAZATION_TARGET.reward_rolls: REWARD_ROLL_STATS,
    OPTIMAZATION_TARGET.xp: {StatName.BONUS_XP_ADD, StatName.BONUS_XP_PERCENT, StatName.DOUBLE_ACTION, StatName.WORK_EFFICIENCY, StatName.STEPS_ADD, StatName.STEPS_PERCENT},
    OPTIMAZATION_TARGET.chests: REWARD_ROLL_STATS | {StatName.CHEST_FINDING},
    OPTIMAZATION_TARGET.materials_from_input: {StatName.DOUBLE_REWARDS, StatName.NO_MATERIALS_CONSUMED},
    OPTIMAZATION_TARGET.fine: REWARD_ROLL_STATS | {StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.collectibles: REWARD_ROLL_STATS | {StatName.FIND_COLLECTIBLES},
    OPTIMAZATION_TARGET.eternal_per_input: {StatName.QUALITY_OUTCOME, StatName.DOUBLE_REWARDS, StatName.NO_MATERIALS_CONSUMED},
    OPTIMAZATION_TARGET.good_per_step: REWARD_ROLL_STATS | {StatName.QUALITY_OUTCOME},
    OPTIMAZATION_TARGET.great_per_step: REWARD_ROLL_STATS | {StatName.QUALITY_OUTCOME},
    OPTIMAZATION_TARGET.excellent_per_step: REWARD_ROLL_STATS | {StatName.QUALITY_OUTCOME},
    OPTIMAZATION_TARGET.perfect_per_step: REWARD_ROLL_STATS | {StatName.QUALITY_OUTCOME},
    OPTIMAZATION_TARGET.eternal_per_step: REWARD_ROLL_STATS | {StatName.QUALITY_OUTCOME},
    
    OPTIMAZATION_TARGET.tokens_per_step: REWARD_ROLL_STATS | {StatName.FIND_ADVENTURERS_GUILD_TOKEN},
    OPTIMAZATION_TARGET.ectoplasm_per_step: REWARD_ROLL_STATS | {StatName.FIND_ECTOPLASM},
    OPTIMAZATION_TARGET.fine_ectoplasm_per_step: REWARD_ROLL_STATS | {StatName.FIND_ECTOPLASM, StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.sea_shells_per_step: REWARD_ROLL_STATS | {StatName.FIND_SEA_SHELLS},
    OPTIMAZATION_TARGET.fine_sea_shells_per_step: REWARD_ROLL_STATS | {StatName.FIND_SEA_SHELLS, StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.crustacean_per_step: REWARD_ROLL_STATS | {StatName.FIND_CRUSTACEAN},
    OPTIMAZATION_TARGET.fine_crustacean_per_step: REWARD_ROLL_STATS | {StatName.FIND_CRUSTACEAN, StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.fibrous_plant_per_step: REWARD_ROLL_STATS | {StatName.FIND_FIBROUS_PLANT},
    OPTIMAZATION_TARGET.fine_fibrous_plant_per_step: REWARD_ROLL_STATS | {StatName.FIND_FIBROUS_PLANT, StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.fishing_bait_per_step: REWARD_ROLL_STATS | {StatName.FIND_FISHING_BAIT},
    OPTIMAZATION_TARGET.fine_fishing_bait_per_step: REWARD_ROLL_STATS | {StatName.FIND_FISHING_BAIT, StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.gold_nugget_per_step: REWARD_ROLL_STATS | {StatName.FIND_GOLD_NUGGET},
    OPTIMAZATION_TARGET.fine_gold_nugget_per_step: REWARD_ROLL_STATS | {StatName.FIND_GOLD_NUGGET, StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.find_random_gem_per_step: REWARD_ROLL_STATS | {StatName.FIND_RANDOM_GEM},
    OPTIMAZATION_TARGET.find_random_fine_gem_per_step: REWARD_ROLL_STATS | {StatName.FIND_RANDOM_GEM, StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.gem_finding: REWARD_ROLL_STATS | {StatName.FIND_GEMS},
    OPTIMAZATION_TARGET.gem_finding_fine: REWARD_ROLL_STATS | {StatName.FIND_GEMS, StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.bird_nests_per_step: REWARD_ROLL_STATS | {StatName.FIND_BIRD_NESTS, StatName.CHANCE_TO_FIND_BIRD_NEST},
    
    
    OPTIMAZATION_TARGET.coins: COIN_BASE_STATS | {StatName.CHEST_FINDING, StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.coins_no_chests: COIN_BASE_STATS | {StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.coins_no_fines: COIN_BASE_STATS | {StatName.CHEST_FINDING},
    OPTIMAZATION_TARGET.coins_no_chests_no_fines: COIN_BASE_STATS,
    OPTIMAZATION_TARGET.net_profit_per_step: COIN_BASE_STATS | {
        StatName.CHEST_FINDING, StatName.FINE_MATERIAL_FINDING, StatName.NO_MATERIALS_CONSUMED,
        StatName.QUALITY_OUTCOME, StatName.FIND_BIRD_NESTS, StatName.CHANCE_TO_FIND_BIRD_NEST,
        StatName.FIND_GEMS, StatName.FIND_ADVENTURERS_GUILD_TOKEN,
        StatName.FIND_SEA_SHELLS, StatName.FIND_CRUSTACEAN,
        StatName.FIND_FIBROUS_PLANT, StatName.FIND_FISHING_BAIT, StatName.FIND_GOLD_NUGGET,
        StatName.FIND_RANDOM_GEM, StatName.FIND_SKILL_CHEST, StatName.FIND_LINENS
    },

    OPTIMAZATION_TARGET.xp_per_material: {StatName.BONUS_XP_ADD, StatName.BONUS_XP_PERCENT, StatName.NO_MATERIALS_CONSUMED},
    OPTIMAZATION_TARGET.chests_per_material: {StatName.CHEST_FINDING, StatName.DOUBLE_REWARDS, StatName.NO_MATERIALS_CONSUMED}, 
    OPTIMAZATION_TARGET.exp_no_steps: {StatName.BONUS_XP_ADD, StatName.BONUS_XP_PERCENT, StatName.DOUBLE_ACTION},
    OPTIMAZATION_TARGET.chests_no_steps: {StatName.DOUBLE_ACTION, StatName.DOUBLE_REWARDS, StatName.CHEST_FINDING},
    OPTIMAZATION_TARGET.reward_rolls_no_steps: {StatName.DOUBLE_ACTION, StatName.DOUBLE_REWARDS},
    OPTIMAZATION_TARGET.fine_no_steps: {StatName.DOUBLE_ACTION, StatName.DOUBLE_REWARDS, StatName.FINE_MATERIAL_FINDING},
    OPTIMAZATION_TARGET.collectibles_no_steps: {StatName.DOUBLE_ACTION, StatName.DOUBLE_REWARDS, StatName.FIND_COLLECTIBLES}
}

# Mapping StatName Enums to the keys output by GearSet.get_stats()
STAT_ENUM_TO_KEY = {
    StatName.STEPS_ADD: "flat_step_reduction",
    StatName.STEPS_PERCENT: "percent_step_reduction",
    StatName.BONUS_XP_ADD: "flat_xp",
    StatName.BONUS_XP_PERCENT: "xp_percent",
    StatName.XP_PERCENT: "xp_percent"
}

PERCENTAGE_STATS = {
    StatName.WORK_EFFICIENCY, StatName.DOUBLE_ACTION, StatName.DOUBLE_REWARDS,
    StatName.NO_MATERIALS_CONSUMED, StatName.STEPS_PERCENT, StatName.XP_PERCENT,
    StatName.BONUS_XP_PERCENT, StatName.CHEST_FINDING, StatName.FINE_MATERIAL_FINDING,
    StatName.FIND_BIRD_NESTS, StatName.FIND_COLLECTIBLES, StatName.FIND_GEMS,
    StatName.FIND_RANDOM_GEM, StatName.FIND_FIBROUS_PLANT, StatName.FIND_CRUSTACEAN,
    StatName.FIND_SKILL_CHEST, StatName.FIND_SEA_SHELLS, StatName.FIND_GOLD,

    StatName.FIND_RANDOM_GEM, StatName.FIND_FIBROUS_PLANT, StatName.FIND_CRUSTACEAN,
    StatName.FIND_SKILL_CHEST, StatName.FIND_SEA_SHELLS, StatName.FIND_GOLD,
    StatName.FIND_ECTOPLASM, StatName.FIND_FISHING_BAIT, StatName.FIND_GOLD_NUGGET,
    StatName.FIND_ADVENTURERS_GUILD_TOKEN, StatName.FIND_COIN_POUCH, 
    StatName.FIND_JUNK, StatName.CHANCE_TO_FIND_BIRD_NEST, StatName.FIND_LINENS
}
class ActivityLootTableType(str, Enum):
    MAIN = "main"
    SECONDARY = "secondary"
    GEM = "gem"
    # Fallback for unknown tables
    OTHER = "other"




FISHING_BAIT_TABLE = [("bug_bait", 0.50, 1.0), ("frozen_bait", 0.50, 1.0)]

GEM_TABLE = [
    ("rough_opal", 0.29851, 1.0), ("rough_star_pearl", 0.29851, 1.0),
    ("rough_topaz", 0.14925, 1.0), ("rough_wrentmarine", 0.14925, 1.0),
    ("rough_jade", 0.04975, 1.0), ("rough_ruby", 0.02985, 1.0),
    ("rough_sun_stone", 0.01990, 1.0), ("rough_ethernite", 0.00498, 1.0)
]

JUNK_TABLE = [
    ("trash", 0.48454, 1.0), ("fishbone", 0.08247, 1.0),
    ("grass", 0.07732, 1.0), ("mud", 0.07732, 1.0),
    ("copper_arrows", 0.05155, 2.0), ("milkweed", 0.05155, 1.0), 
    ("moondaisy", 0.05155, 1.0), ("sea_shell", 0.05155, 1.0),
    ("birch_skis", 0.01546, 1.0), ("clay_skydisc", 0.01546, 1.0),
    ("rough_opal", 0.01546, 1.0), ("simple_torch", 0.01546, 1.0),
    ("rusty_chest", 0.00515, 1.0), ("sunken_chest", 0.00515, 1.0)
]

SKILL_CHEST_TABLE = [
    ("agility_chest", 0.08333, 1.0), ("carpentry_chest", 0.08333, 1.0),
    ("cooking_chest", 0.08333, 1.0), ("crafting_chest", 0.08333, 1.0),
    ("fishing_chest", 0.08333, 1.0), ("foraging_chest", 0.08333, 1.0),
    ("hunting_chest", 0.08333, 1.0), ("mining_chest", 0.08333, 1.0),
    ("smithing_chest", 0.08333, 1.0), ("tailoring_chest", 0.08333, 1.0),
    ("trinketry_chest", 0.08333, 1.0), ("woodcutting_chest", 0.08333, 1.0),
]

CRUSTACEAN_TABLE = [
    ("raw_crab", 0.54545, 1.5), ("raw_lobster", 0.36364, 1.0), ("raw_shrimp", 0.09091, 4.5)
]

FIBROUS_PLANT_TABLE = [("hemp", 0.70, 1.0), ("flax", 0.30, 1.0)]

LINENS_TABLE = [("linen_cloth", 0.67, 2.5), ("tough_linen_cloth", 0.33, 1.0)]

SPECIAL_FIND_MAP = {
    "chance_to_find_bird_nest": "bird_nest",
    "find_coin_pouch": "coin_pouch",
    "find_gold_nugget": "gold_nugget",
    "find_adventurers_guild_token": "adventurers_guild_token",
    "find_ectoplasm": "ectoplasm",
    "find_sea_shells": [("sea_shell", 1.0, 5.5)], # 1-10 quantity
    "find_gold": [("coins", 1.0, 5.5)],           # 1-10 quantity
    "find_fishing_bait": FISHING_BAIT_TABLE,
    "find_junk": JUNK_TABLE,
    "find_random_gem": GEM_TABLE,
    "find_skill_chest": SKILL_CHEST_TABLE,
    "find_crustacean": CRUSTACEAN_TABLE,
    "find_fibrous_plant": FIBROUS_PLANT_TABLE,
    "find_linens": LINENS_TABLE
}


class ChestTableCategory(str, Enum):
    """Categories for Container/Chest drops."""
    MAIN = "Main"
    VALUABLES = "Valuables"
    COMMON = "Common"
    UNCOMMON = "Uncommon"
    RARE = "Rare"
    EPIC = "Epic"
    LEGENDARY = "Legendary"
    ETHEREAL = "Ethereal"
    OTHER = "Other"

DOMINANCE_EXEMPT_ITEMS = {
    "spectral_saw_common",
    "spectral_saw_uncommon",
    "spectral_saw_rare",
    "spectral_saw_epic",
    "spectral_saw_legendary",
    "spectral_saw_ethereal",

}

INSTANT_ACTION_PET_ABILITIES = {
    "Pecking Order": { # Chicken
        "allowed_source_types": ["activity"],
        "skill": "foraging"
    },
    "Shell Forge": { # Tortoise
        "allowed_source_types": ["recipe"],
        "skill": "smithing",
        "recipe_name_contains": "smelt" 
    },
}

BUFF_PET_ABILITIES = {
    "The Hunt Is On": { # Tiger
        "allowed_source_types": ["activity"],
        "skill": "hunting",
        "modifiers": {
            "work_efficiency": 0.06,
            "double_action": 0.03,
            "flat_step_reduction": 2
        }
    },
    "Party Mode": { # Pixie
        "allowed_source_types": ["activity", "recipe"],
        "skill": None,
        "modifiers": {
            "find_collectibles": 0.20
        }
    },
}