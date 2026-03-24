from typing import List, Optional, Dict, Set, Tuple, Any
from collections import defaultdict, Counter
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict, model_validator
from utils.constants import (
    ConditionType, RequirementType, EquipmentSlot, EquipmentQuality, 
    SkillName, StatName, GATHERING_SKILLS, ARTISAN_SKILLS, RESTRICTED_TOOL_KEYWORDS,
    ActivityLootTableType, ChestTableCategory
)

# ============================================================================
# MODELS
# ============================================================================

class Condition(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    type: ConditionType
    target: Optional[str] = None 
    value: Optional[int] = None

class Modifier(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    stat: StatName
    value: float
    conditions: Tuple[Condition, ...] = Field(default_factory=tuple)

class Requirement(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    type: RequirementType
    target: Optional[str] = None     
    value: int             

class DropEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    item_id: str          
    min_quantity: int
    max_quantity: int
    chance: Optional[float] = None 
    category: Optional[ChestTableCategory] = None
    # Raw API drop parameters (from gear.walkscape.app) - exact game values
    no_drop_chance: Optional[float] = None
    row_weight: Optional[float] = None
    table_weight: Optional[float] = None
    
    @model_validator(mode='before')
    @classmethod
    def compute_chance_from_raw(cls, data):
        """Auto-compute chance from raw API fields if available and chance not set."""
        if isinstance(data, dict):
            ndc = data.get('no_drop_chance')
            rw = data.get('row_weight')
            tw = data.get('table_weight')
            if ndc is not None and rw is not None and tw is not None and tw > 0:
                if data.get('chance') is None:
                    data['chance'] = (1.0 - ndc / 100.0) * (rw / tw) * 100.0
        return data

class FactionReward(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    faction_id: str       
    amount: float

class BaseEntity(BaseModel):
    model_config = ConfigDict(frozen=False)
    
    id: str
    wiki_slug: str
    name: str

class BaseItem(BaseEntity):
    model_config = ConfigDict(frozen=True)
    
    value: int          
    keywords: Tuple[str, ...] = Field(default_factory=tuple)

class SpecialShopSell(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    item_id: str 
    amount: int  

class Material(BaseItem):
    model_config = ConfigDict(frozen=True)
    modifiers: Tuple[Modifier, ...] = Field(default_factory=tuple)
    special_sell: Optional[SpecialShopSell] = None

class RecipeMaterial(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    item_id: str
    amount: int

class Container(BaseEntity):
    model_config = ConfigDict(frozen=True)
    
    type: str # "skill_chest" or "unique_openable"
    drops: Tuple[DropEntry, ...] = Field(default_factory=tuple)

    total_expected_value: float = 0.0
    materials_expected_value: float = 0.0

class Collectible(BaseEntity):
    model_config = ConfigDict(use_enum_values=True, frozen=True)
    
    modifiers: Tuple[Modifier, ...] = Field(default_factory=tuple)

class Service(BaseEntity):
    model_config = ConfigDict(use_enum_values=True, frozen=True)
    
    skill: SkillName
    tier: str
    location: str
    requirements: Tuple[Requirement, ...] = Field(default_factory=tuple)
    modifiers: Tuple[Modifier, ...] = Field(default_factory=tuple)
 
class Equipment(BaseItem):
    model_config = ConfigDict(use_enum_values=True, frozen=True)
    
    uuid: str = ""
    slot: EquipmentSlot
    quality: EquipmentQuality
    requirements: Tuple[Requirement, ...] = Field(default_factory=tuple) 
    modifiers: Tuple[Modifier, ...] = Field(default_factory=tuple)      
    
    @property
    def skill(self) -> str:
        skills = set()
        for req in self.requirements:
            if req.type == RequirementType.SKILL_LEVEL and req.target:
                skills.add(req.target.lower())
        for mod in self.modifiers:
            for cond in mod.conditions:
                if cond.type == ConditionType.SKILL_ACTIVITY and cond.target:
                    skills.add(cond.target.lower())
        return ",".join(skills) if skills else None

    @property
    def region(self) -> Optional[str]:
        for req in self.requirements:
            if req.type == RequirementType.REPUTATION: 
                return req.target 
        return None

    @property
    def is_underwater(self) -> bool:
        return "underwater" in self.keywords

    @property
    def clean_item_name(self) -> str:
        return self.name
    
    def provides_keyword(self, req_kw: str) -> bool:
        """Checks if the item provides a specific requirement keyword, handling dynamic level requirements."""
        norm_req = req_kw.lower().replace("_", " ").strip()
        
        if any(k.lower().replace("_", " ").strip() == norm_req for k in self.keywords):
            return True
            
        if norm_req.startswith("req "):
            parts = norm_req.split(" ")
            if len(parts) >= 4:
                try:
                    skill = parts[1]
                    level = int(parts[2])
                    target_kw = " ".join(parts[3:])
                    
                    has_kw = any(k.lower().replace("_", " ").strip() == target_kw for k in self.keywords)
                    if has_kw:
                        for req in self.requirements:
                            if req.type == RequirementType.SKILL_LEVEL and req.target == skill:
                                if req.value >= level:
                                    return True
                except ValueError:
                    pass
        return False

class LootTable(BaseModel):
    """Represents a specific drop table for an activity (Main, Gem, Secondary)."""
    model_config = ConfigDict(frozen=True)
    
    type: ActivityLootTableType
    drops: Tuple[DropEntry, ...] = Field(default_factory=tuple)


class Activity(BaseEntity):
    model_config = ConfigDict(use_enum_values=True, frozen=True)
    
    primary_skill: SkillName
    locations: Tuple[str, ...] = Field(default_factory=tuple) 
    base_steps: int = 0
    base_xp: float = 0.0
    secondary_xp: Dict[SkillName, float] = Field(default_factory=dict)
    max_efficiency: float = 0.0 
    requirements: Tuple[Requirement, ...] = Field(default_factory=tuple)
    faction_rewards: Tuple[FactionReward, ...] = Field(default_factory=tuple)
    materials: Tuple[Tuple[RecipeMaterial, ...], ...] = Field(default_factory=tuple)   
    loot_tables: Tuple[LootTable, ...] = Field(default_factory=tuple) # Consolidated drops
    
    modifiers: Tuple[Modifier, ...] = Field(default_factory=tuple) 
    normal_roll_worth: float = 0.0
    chest_roll_worth: float = 0.0
    fine_roll_worth: float = 0.0
    
    @property
    def level(self) -> int:
        for req in self.requirements:
            if req.type == RequirementType.SKILL_LEVEL and req.target:
                if req.target.lower() == self.primary_skill.lower():
                    return req.value
        return 1 


class Recipe(BaseEntity):
    model_config = ConfigDict(use_enum_values=True, frozen=True)
    
    skill: SkillName
    level: int
    service: str 
    output_item_id: str
    output_quantity: int
    materials: Tuple[Tuple[RecipeMaterial, ...], ...] = Field(default_factory=tuple)
    base_xp: float = 0.0
    base_steps: int = 0
    max_efficiency: float = 0.0

class Location(BaseEntity):
    model_config = ConfigDict(use_enum_values=True, frozen=True)
    
    tags: Tuple[str, ...] = Field(default_factory=tuple)

class Consumable(BaseItem):
    model_config = ConfigDict(use_enum_values=True, frozen=True)
    
    modifiers: Tuple[Modifier, ...] = Field(default_factory=tuple)
    duration: int

# --- PETS ---

class PetAbility(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    name: str
    effect: str
    requirements: Optional[str] = None
    cooldown: Optional[str] = None
    charges: Optional[int] = None

class PetLevel(BaseModel):
    model_config = ConfigDict(use_enum_values=True, frozen=True)
    
    level: int
    total_xp: int
    modifiers: Tuple[Modifier, ...] = Field(default_factory=tuple)
    abilities: Tuple[PetAbility, ...] = Field(default_factory=tuple)

class Pet(BaseEntity):
    model_config = ConfigDict(use_enum_values=True, frozen=False)
    
    egg_item_id: Optional[str] = None
    xp_requirement_desc: Optional[str] = None
    levels: Tuple[PetLevel, ...] = Field(default_factory=tuple)
    
    active_level: int = 1

    @property
    def modifiers(self) -> Tuple[Modifier, ...]:
        for lvl in self.levels:
            if lvl.level == self.active_level:
                return lvl.modifiers
        return tuple()

    @property
    def keywords(self) -> Tuple[str, ...]:
        return tuple()

# ============================================================================
# GEARSET
# ============================================================================

class GearSet(BaseModel):
    head: Optional[Equipment] = None
    chest: Optional[Equipment] = None
    legs: Optional[Equipment] = None
    feet: Optional[Equipment] = None
    back: Optional[Equipment] = None
    cape: Optional[Equipment] = None
    neck: Optional[Equipment] = None
    hands: Optional[Equipment] = None
    primary: Optional[Equipment] = None
    secondary: Optional[Equipment] = None
    
    pet: Optional[Pet] = None      
    consumable: Optional[Consumable] = None

    rings: List[Equipment] = Field(default_factory=list)
    tools: List[Equipment] = Field(default_factory=list)

    def clone(self) -> 'GearSet':
        new_set = GearSet()
        for slot in ["head", "chest", "legs", "feet", "back", "cape", "neck", "hands", "primary", "secondary"]:
            setattr(new_set, slot, getattr(self, slot))
        new_set.rings = list(self.rings)
        new_set.tools = list(self.tools)
        new_set.pet = self.pet
        new_set.consumable = self.consumable
        return new_set

    def equip(self, item: Equipment, max_tools: int = 6) -> bool:
        if self.violates_restrictions(item):
            return False

        if item.slot == EquipmentSlot.TOOLS:
            if len(self.tools) < max_tools:
                self.tools.append(item)
                return True
            return False
        elif item.slot == EquipmentSlot.RING:
            if len(self.rings) < 2:
                self.rings.append(item)
                return True
            return False
        else:
            attr = item.slot
            if hasattr(self, attr):
                if getattr(self, attr) is None:
                    setattr(self, attr, item)
                    return True
            return False

    def unequip(self, item: Equipment):
        if item in self.rings:
            self.rings.remove(item)
        elif item in self.tools:
            self.tools.remove(item)
        else:
            for slot in ["head", "chest", "legs", "feet", "back", "cape", "neck", "hands", "primary", "secondary"]:
                current = getattr(self, slot)
                if current and current.id == item.id:
                    setattr(self, slot, None)
                    break

    def violates_restrictions(self, new_item: Equipment) -> bool:
        restricted_lower = {k.lower() for k in RESTRICTED_TOOL_KEYWORDS}
        
        item_restricted_kws = set()
        for k in new_item.keywords:
            lk = k.lower()
            if lk in restricted_lower:
                item_restricted_kws.add(lk)
        
        if not item_restricted_kws: return False

        for existing in self.get_all_items():
            if isinstance(existing, Pet) or isinstance(existing, Consumable): continue 
            for k in existing.keywords:
                if k.lower() in item_restricted_kws:
                    return True
        return False

    def get_empty_slots(self, max_tools: int) -> List[str]:
        empty = []
        for slot in ["head", "chest", "legs", "feet", "back", "cape", "neck", "hands", "primary", "secondary"]:
            if getattr(self, slot) is None: empty.append(slot)
        if len(self.rings) < 2: 
             for _ in range(2 - len(self.rings)): empty.append("ring")
        if len(self.tools) < max_tools:
             for _ in range(max_tools - len(self.tools)):
                 empty.append("tools")
        return empty

    def get_all_items(self) -> List[Any]:
        items = [
            self.head, self.chest, self.legs, self.feet,
            self.back, self.cape, self.neck, self.hands,
            self.primary, self.secondary, self.pet, self.consumable
        ]
        items.extend(self.rings)
        items.extend(self.tools)
        return [i for i in items if i]

    def get_stats(self, context: Dict[str, Any] = None) -> Dict[str, float]:
        if context is None: context = {}
        active_skill = context.get("skill", "").lower() if context.get("skill") else None
        loc_id = context.get("location_id")
        loc_tags = set(t.lower() for t in context.get("location_tags", []))
        activity_id = context.get("activity_id")
        user_ap = context.get("achievement_points", 0)
        total_lvl = context.get("total_skill_level", 0)

        stats = defaultdict(float)
        active_kws = set()
        for item in self.get_all_items():
            if hasattr(item, 'keywords'):
                for kw in item.keywords:
                    active_kws.add(kw.lower().replace("_", " ").strip())

        keyword_counts = self.get_requirement_counts(list(active_kws))       
        PERCENTAGE_STATS = {
            StatName.WORK_EFFICIENCY, StatName.DOUBLE_ACTION, StatName.DOUBLE_REWARDS,
            StatName.NO_MATERIALS_CONSUMED, StatName.STEPS_PERCENT, StatName.XP_PERCENT,
            StatName.BONUS_XP_PERCENT, StatName.CHEST_FINDING, StatName.FINE_MATERIAL_FINDING,
            StatName.FIND_BIRD_NESTS, StatName.FIND_COLLECTIBLES, StatName.FIND_GEMS,
        }

        for item in self.get_all_items():
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
                        if not activity_id: applies = False
                        elif c_target and c_target != activity_id.lower(): applies = False
                    elif c_type == ConditionType.ACHIEVEMENT_POINTS:
                        if user_ap < (c_val or 0): applies = False
                    elif c_type == ConditionType.TOTAL_SKILL_LEVEL:
                        if total_lvl < (c_val or 0): applies = False
                    elif c_type == ConditionType.SET_EQUIPPED:
                        if not c_target: applies = False
                        else:
                            norm_target = c_target.replace("_", " ").strip()
                            if keyword_counts.get(norm_target, 0) < (c_val or 1): applies = False

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
    
    def get_requirement_counts(self, required_keywords: list[str]) -> Counter:
        """Counts only the requirements requested, using dynamic resolution."""
        counts = Counter()
        for item in self.get_all_items():
            if hasattr(item, 'provides_keyword'):
                for req_kw in required_keywords:
                    if item.provides_keyword(req_kw):
                        counts[req_kw] += 1
            else:
                for kw in item.keywords:
                    norm_kw = kw.lower().replace("_", " ").strip()
                    if norm_kw in required_keywords:
                        counts[norm_kw] += 1
        return counts
    
# ... (existing imports and models) ...

class Loadout(BaseModel):
    """Represents a saved gearset created by the user for the Crafting Tree."""
    id: str
    name: str
    gear_set: GearSet


class CraftingNode(BaseModel):
    """A recursive node representing a step in the production chain."""
    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    node_id: str
    item_id: str
    
    # Source Configuration
    source_type: str               
    source_id: Optional[str] = None 
    parent_activity_id: Optional[str] = None 
    
    available_sources: List[Dict[str, str]] = Field(default_factory=list)
    
    loadout_id: Optional[str] = None 
    auto_optimize_target: Optional[List[Dict[str, Any]]] = None 
    auto_gear_set: Optional[Any] = None         
    
    inputs: Dict[str, 'CraftingNode'] = Field(default_factory=dict)
    base_requirement_amount: int = 1
    metrics: Optional[Dict[str, Any]] = None

    selected_location_id: Optional[str] = None
    selected_service_id: Optional[str] = None
    selected_pet_id: Optional[str] = None
    selected_pet_level: Optional[int] = None
    selected_consumable_id: Optional[str] = None
    selected_activity_inputs: Dict[int, str] = Field(default_factory=dict)
    use_pet_ability: bool = False 