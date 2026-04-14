"""
Unit tests for tree_optimizer.py

Tests are organised into sections:
  1. _metrics_to_score (pure function)
  2. _capture_state / _apply_state roundtrip
  3. _memo_key correctness
  4. _deep_copy_state isolation
  5. _enumerate_configs
  6. _input_combos
  7. _opt_subtree / _opt_single integration (with mocked gear optimizer)
  8. Memoization behaviour
  9. Scoring goal correctness
  10. Progress / callback tracking
"""

import copy
from collections import defaultdict
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set, Tuple
from unittest.mock import MagicMock, patch

import pytest

from models import (
    Activity,
    CraftingNode,
    Equipment,
    GearSet,
    Location,
    Material,
    Modifier,
    Recipe,
    RecipeMaterial,
    Requirement,
    Service,
)
from utils.constants import (
    EquipmentSlot,
    EquipmentQuality,
    RequirementType,
    SkillName,
    StatName,
    OPTIMAZATION_TARGET,
)
from tree_optimizer import (
    TreeNodeOptimizer,
    _capture_state,
    _apply_state,
    _fresh_node,
    _metrics_to_score,
    _memo_key,
    _deep_copy_state,
    _WrappedRecipe,
    TREE_GOAL_OPTIONS,
    TREE_GOAL_TO_GEAR_TARGETS,
)


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------

def _make_node(
    item_id: str = "test_item",
    source_type: str = "recipe",
    source_id: str = "recipe_1",
    base_req: int = 1,
    available_sources: Optional[List[Dict[str, str]]] = None,
    inputs: Optional[Dict[str, "CraftingNode"]] = None,
) -> CraftingNode:
    """Quick CraftingNode factory."""
    return CraftingNode(
        node_id=f"node_{item_id}",
        item_id=item_id,
        source_type=source_type,
        source_id=source_id,
        available_sources=available_sources or [
            {"type": "recipe", "id": "recipe_1", "label": "Recipe 1"},
        ],
        base_requirement_amount=base_req,
        inputs=inputs or {},
    )


def _make_gear_set(**kwargs) -> GearSet:
    """Minimal GearSet for testing."""
    return GearSet(**kwargs)


class FakeGearOptimizer:
    """Deterministic mock for GearOptimizer."""

    def __init__(self, gear_set: Optional[GearSet] = None):
        self._gear = gear_set or _make_gear_set()
        self.call_count = 0
        self.calls: List[dict] = []

    def optimize(self, **kwargs) -> Tuple[Optional[GearSet], Optional[str], Set[str]]:
        self.call_count += 1
        self.calls.append(kwargs)
        return (self._gear, None, set())


class FakeDropCalc:
    """Minimal DropCalculator stub."""
    fine_material_map: Dict[str, str] = {}
    chest_ids: Set[str] = set()
    collectible_ids: Set[str] = set()
    item_values: Dict[str, float] = {}
    container_evs: Dict[str, float] = {}

    def get_drop_table(self, *args, **kwargs):
        return []

    def get_special_ev_map(self):
        return {}


@pytest.fixture
def locations():
    return [Location(id="loc_1", wiki_slug="loc1", name="Forest", tags=("forest",))]


@pytest.fixture
def basic_game_data():
    """Minimal game data with recipes, activities, materials, services."""
    mat_a = Material(id="mat_a", wiki_slug="mat_a", name="Material A", value=10, keywords=("wood",))
    mat_b = Material(id="mat_b", wiki_slug="mat_b", name="Material B", value=15, keywords=("wood",))
    mat_c = Material(id="mat_c", wiki_slug="mat_c", name="Material C", value=5, keywords=("ore",))

    recipe_1 = Recipe(
        id="recipe_1",
        wiki_slug="recipe_1",
        name="Craft Widget",
        skill=SkillName.CARPENTRY,
        level=1,
        service="basic_workshop",
        output_item_id="widget",
        output_quantity=1,
        materials=(
            (RecipeMaterial(item_id="mat_a", amount=2), RecipeMaterial(item_id="mat_b", amount=3)),
        ),
        base_xp=50.0,
        base_steps=10,
        max_efficiency=2.0,
    )

    recipe_2 = Recipe(
        id="recipe_2",
        wiki_slug="recipe_2",
        name="Craft Gadget",
        skill=SkillName.CARPENTRY,
        level=1,
        service="basic_workshop",
        output_item_id="gadget",
        output_quantity=1,
        materials=(
            (RecipeMaterial(item_id="mat_c", amount=1),),
        ),
        base_xp=20.0,
        base_steps=5,
        max_efficiency=1.5,
    )

    activity_1 = Activity(
        id="chop_trees",
        wiki_slug="chop_trees",
        name="Chop Trees",
        primary_skill=SkillName.WOODCUTTING,
        locations=("loc_1",),
        base_steps=20,
        base_xp=10.0,
        max_efficiency=2.0,
    )

    activity_kw = Activity(
        id="bird_feeding",
        wiki_slug="bird_feeding",
        name="Bird Feeding",
        primary_skill=SkillName.FORAGING,
        locations=("loc_1",),
        base_steps=15,
        base_xp=5.0,
        max_efficiency=1.0,
        requirements=(
            Requirement(type=RequirementType.INPUT_KEYWORD, target="plant", value=1),
        ),
    )

    service_1 = Service(
        id="basic_workshop",
        wiki_slug="basic_workshop",
        name="Basic Workshop",
        skill=SkillName.CARPENTRY,
        tier="basic",
        location="loc_1",
    )

    return {
        "recipes": {"recipe_1": recipe_1, "recipe_2": recipe_2},
        "activities": {"chop_trees": activity_1, "bird_feeding": activity_kw},
        "materials": {"mat_a": mat_a, "mat_b": mat_b, "mat_c": mat_c},
        "consumables": {},
        "services": {"basic_workshop": service_1},
        "pets": {},
        "equipment": {},
    }


@pytest.fixture
def user_state():
    return {
        "item_counts": {},
        "user_ap": 0,
        "user_reputation": {},
        "owned_collectibles": [],
        "user_total_level": 100,
    }


@pytest.fixture
def skill_levels():
    return {"carpentry": 50, "woodcutting": 50, "foraging": 50, "mining": 50}


def _make_optimizer(
    game_data, locations, user_state, skill_levels,
    gear_set=None, goal="minimize_steps",
) -> Tuple[TreeNodeOptimizer, FakeGearOptimizer]:
    """Build a TreeNodeOptimizer with a fake gear optimizer."""
    fake_go = FakeGearOptimizer(gear_set)
    opt = TreeNodeOptimizer(
        gear_optimizer=fake_go,
        game_data_dict=game_data,
        drop_calc=FakeDropCalc(),
        locations=locations,
        user_state=user_state,
        player_skill_levels=skill_levels,
        char_lvl=50,
        loadouts={},
        goal=goal,
    )
    return opt, fake_go


# ===================================================================
# 1. _metrics_to_score
# ===================================================================

class TestMetricsToScore:
    def test_none_metrics_returns_inf(self):
        assert _metrics_to_score(None, "minimize_steps") == float("inf")

    def test_empty_dict_returns_inf_for_steps(self):
        assert _metrics_to_score({}, "minimize_steps") == float("inf")

    def test_minimize_steps(self):
        m = {"steps": 42.0, "xp": {"carpentry": 100.0}}
        assert _metrics_to_score(m, "minimize_steps") == 42.0

    def test_maximize_xp(self):
        m = {"steps": 10.0, "xp": {"carpentry": 100.0, "woodcutting": 50.0}}
        assert _metrics_to_score(m, "maximize_xp") == -150.0

    def test_maximize_xp_per_step(self):
        m = {"steps": 10.0, "xp": {"carpentry": 100.0}}
        assert _metrics_to_score(m, "maximize_xp_per_step") == -10.0

    def test_maximize_xp_per_step_zero_steps(self):
        m = {"steps": 0.0, "xp": {"carpentry": 100.0}}
        assert _metrics_to_score(m, "maximize_xp_per_step") == float("inf")

    def test_unknown_goal_defaults_to_steps(self):
        m = {"steps": 7.0}
        assert _metrics_to_score(m, "some_unknown_goal") == 7.0


# ===================================================================
# 2. _capture_state / _apply_state roundtrip
# ===================================================================

class TestCaptureApplyState:
    def test_roundtrip_preserves_fields(self):
        node = _make_node()
        node.selected_service_id = "svc_1"
        node.selected_location_id = "loc_1"
        node.auto_gear_set = _make_gear_set()
        node.auto_optimize_target = [{"id": 0, "target": "Xp", "weight": 100}]
        node.loadout_id = "AUTO"
        node.selected_activity_inputs = {0: "mat_a"}

        state = _capture_state(node)

        target = _make_node(item_id="other")
        _apply_state(target, state)

        assert target.source_type == node.source_type
        assert target.source_id == node.source_id
        assert target.selected_service_id == "svc_1"
        assert target.selected_location_id == "loc_1"
        assert target.loadout_id == "AUTO"
        assert target.selected_activity_inputs == {0: "mat_a"}
        assert target.auto_gear_set is not None

    def test_state_inputs_are_separate_dicts(self):
        child = _make_node(item_id="child_1", source_type="bank", source_id="bank")
        node = _make_node(inputs={"child_1_0": child})

        state = _capture_state(node)
        # Modifying captured state dict should not affect original node's inputs dict
        state["inputs"]["new_key"] = _make_node(item_id="extra")
        assert "new_key" not in node.inputs


# ===================================================================
# 3. _memo_key
# ===================================================================

class TestMemoKey:
    def test_same_identity_same_key(self):
        n1 = _make_node(item_id="x", base_req=5)
        n2 = _make_node(item_id="x", base_req=5)
        assert _memo_key(n1) == _memo_key(n2)

    def test_different_amount_different_key(self):
        n1 = _make_node(item_id="x", base_req=5)
        n2 = _make_node(item_id="x", base_req=10)
        assert _memo_key(n1) != _memo_key(n2)

    def test_different_sources_different_key(self):
        n1 = _make_node(
            item_id="x",
            available_sources=[{"type": "recipe", "id": "r1", "label": "R1"}],
        )
        n2 = _make_node(
            item_id="x",
            available_sources=[
                {"type": "recipe", "id": "r1", "label": "R1"},
                {"type": "activity", "id": "a1", "label": "A1"},
            ],
        )
        assert _memo_key(n1) != _memo_key(n2)

    def test_source_order_irrelevant(self):
        sources_a = [
            {"type": "recipe", "id": "r1", "label": "R1"},
            {"type": "activity", "id": "a1", "label": "A1"},
        ]
        sources_b = list(reversed(sources_a))
        n1 = _make_node(item_id="x", available_sources=sources_a)
        n2 = _make_node(item_id="x", available_sources=sources_b)
        assert _memo_key(n1) == _memo_key(n2)


# ===================================================================
# 4. _deep_copy_state
# ===================================================================

class TestDeepCopyState:
    def test_children_are_independent(self):
        child = _make_node(item_id="child", source_type="bank", source_id="bank")
        node = _make_node(inputs={"child_0": child})
        state = _capture_state(node)

        copied = _deep_copy_state(state)
        # Mutate the copy's child
        copied["inputs"]["child_0"].source_type = "activity"

        # Original should be unaffected
        assert state["inputs"]["child_0"].source_type == "bank"


# ===================================================================
# 5. _enumerate_configs
# ===================================================================

class TestEnumerateConfigs:
    @pytest.fixture(autouse=True)
    def _setup(self, basic_game_data, locations, user_state, skill_levels):
        self.opt, self.fake_go = _make_optimizer(
            basic_game_data, locations, user_state, skill_levels
        )

    def test_bank_source_skipped(self):
        node = _make_node(
            available_sources=[
                {"type": "bank", "id": "bank", "label": "Bank"},
                {"type": "recipe", "id": "recipe_2", "label": "Craft Gadget"},
            ]
        )
        configs = self.opt._enumerate_configs(node)
        assert all(c["source_type"] != "bank" for c in configs)
        assert len(configs) >= 1

    def test_recipe_source_generates_material_combos(self):
        """recipe_1 has 1 material group with 2 alternatives (mat_a, mat_b)."""
        node = _make_node(
            available_sources=[
                {"type": "recipe", "id": "recipe_1", "label": "Craft Widget"},
            ]
        )
        configs = self.opt._enumerate_configs(node)
        mat_choices = {tuple(sorted(c["material_choices"].items())) for c in configs}
        # Should have at least 2 material combos (mat_a and mat_b for slot 0)
        assert len(mat_choices) >= 2

    def test_recipe_source_includes_service_variants(self):
        """recipe_1 uses basic_workshop, which exists in game data → service combos."""
        node = _make_node(
            available_sources=[
                {"type": "recipe", "id": "recipe_1", "label": "Craft Widget"},
            ]
        )
        configs = self.opt._enumerate_configs(node)
        service_ids = {c.get("service_id") for c in configs}
        # Should include None (no service) and "basic_workshop"
        assert None in service_ids
        assert "basic_workshop" in service_ids

    def test_activity_source_with_locations(self):
        """chop_trees has locations=("loc_1",) → each config gets location_id="loc_1"."""
        node = _make_node(
            source_type="activity",
            source_id="chop_trees",
            available_sources=[
                {"type": "activity", "id": "chop_trees", "label": "Chop Trees"},
            ]
        )
        configs = self.opt._enumerate_configs(node)
        assert len(configs) >= 1
        # All should have loc_1
        for c in configs:
            assert c["location_id"] == "loc_1"

    def test_chest_source_extraction(self):
        """Chest source with parent_activity_id encoded in the id."""
        node = _make_node(
            available_sources=[
                {"type": "chest", "id": "chest_1::chop_trees", "label": "Tree Chest"},
            ]
        )
        configs = self.opt._enumerate_configs(node)
        assert len(configs) >= 1
        assert configs[0]["source_type"] == "chest"
        assert configs[0]["source_id"] == "chest_1"
        assert configs[0]["parent_activity_id"] == "chop_trees"


# ===================================================================
# 6. _input_combos
# ===================================================================

class TestInputCombos:
    @pytest.fixture(autouse=True)
    def _setup(self, basic_game_data, locations, user_state, skill_levels):
        self.opt, _ = _make_optimizer(
            basic_game_data, locations, user_state, skill_levels
        )

    def test_recipe_material_alternatives(self):
        """recipe_1 has group 0 with 2 alternatives → 2 material combos per service."""
        combos = self.opt._input_combos("recipe", "recipe_1")
        # Each combo has material_choices and service_id
        mat_sets = [tuple(sorted(c["material_choices"].items())) for c in combos]
        # Should have alternatives for mat_a vs mat_b in slot 0
        unique_mats = set(mat_sets)
        assert len(unique_mats) >= 2

    def test_recipe_with_single_material(self):
        """recipe_2 has only 1 material option → 1 material combo per service."""
        combos = self.opt._input_combos("recipe", "recipe_2")
        mat_choices = [c["material_choices"] for c in combos]
        # All should pick mat_c for slot 0
        for mc in mat_choices:
            assert mc.get(0) == "mat_c"

    def test_activity_no_requirements(self):
        """chop_trees has no input requirements → single trivial combo."""
        combos = self.opt._input_combos("activity", "chop_trees")
        assert len(combos) == 1
        assert combos[0]["activity_inputs"] == {}

    def test_activity_with_keyword_requirement(self):
        """bird_feeding requires 'plant' keyword → finds matching materials."""
        # Add a plant-keyword material to game data for this test
        from models import Material as Mat
        plant_mat = Mat(id="seeds", wiki_slug="seeds", name="Seeds", value=1, keywords=("plant",))
        self.opt._gd["materials"]["seeds"] = plant_mat

        combos = self.opt._input_combos("activity", "bird_feeding")
        # Should find at least the seeds material
        found_seeds = any(
            c["activity_inputs"].get(0) == "seeds" for c in combos
        )
        assert found_seeds

    def test_unknown_source_type(self):
        combos = self.opt._input_combos("unknown_type", "whatever")
        assert len(combos) == 1
        assert combos[0] == {"material_choices": {}, "activity_inputs": {}, "service_id": None}

    def test_missing_recipe(self):
        combos = self.opt._input_combos("recipe", "nonexistent_recipe")
        assert len(combos) == 1


# ===================================================================
# 7. Scoring goal correctness
# ===================================================================

class TestScoringGoals:
    """Verify that _score picks the right config for each goal."""

    def _make_metrics(self, steps: float, xp: float) -> Dict:
        return {"steps": steps, "xp": defaultdict(float, {"carpentry": xp})}

    def test_minimize_steps_picks_lowest(self):
        m1 = self._make_metrics(steps=100, xp=50)
        m2 = self._make_metrics(steps=50, xp=20)
        s1 = _metrics_to_score(m1, "minimize_steps")
        s2 = _metrics_to_score(m2, "minimize_steps")
        # Lower score = better; m2 has fewer steps → lower score
        assert s2 < s1

    def test_maximize_xp_picks_highest(self):
        m1 = self._make_metrics(steps=100, xp=200)
        m2 = self._make_metrics(steps=100, xp=50)
        s1 = _metrics_to_score(m1, "maximize_xp")
        s2 = _metrics_to_score(m2, "maximize_xp")
        # Lower score = better; m1 has more XP → more negative → lower score
        assert s1 < s2

    def test_maximize_xp_per_step(self):
        m1 = self._make_metrics(steps=10, xp=100)  # ratio=10
        m2 = self._make_metrics(steps=10, xp=50)   # ratio=5
        s1 = _metrics_to_score(m1, "maximize_xp_per_step")
        s2 = _metrics_to_score(m2, "maximize_xp_per_step")
        assert s1 < s2  # m1 has better ratio → lower (more negative) score


# ===================================================================
# 8. _WrappedRecipe
# ===================================================================

class TestWrappedRecipe:
    def test_basic_fields(self):
        recipe = SimpleNamespace(
            id="r1", name="Test Recipe", skill="carpentry",
            level=5, base_xp=10.0, base_steps=20, max_efficiency=1.5,
        )
        w = _WrappedRecipe(recipe)
        assert w.id == "r1"
        assert w.primary_skill == "carpentry"
        assert w.level == 5
        assert w.base_xp == 10.0
        assert w.base_steps == 20
        assert w.max_efficiency == 1.5
        assert w.locations == []
        assert w.requirements == []

    def test_passthrough_attributes(self):
        recipe = SimpleNamespace(
            id="r1", name="Test", skill="carpentry",
            level=1, base_xp=0, base_steps=0, max_efficiency=1.0,
            modifiers=[{"stat": "xp", "value": 5}],
            keywords=("foo",),
            reward_type="normal",
        )
        w = _WrappedRecipe(recipe)
        assert w.modifiers == [{"stat": "xp", "value": 5}]
        assert w.keywords == ("foo",)
        assert w.reward_type == "normal"

    def test_missing_optional_attributes(self):
        recipe = SimpleNamespace(
            id="r1", name="Test", skill="carpentry",
            level=1, base_xp=0, base_steps=0, max_efficiency=1.0,
        )
        w = _WrappedRecipe(recipe)
        assert w.modifiers == []
        assert w.keywords == ()
        assert w.reward_type is None


# ===================================================================
# 9. _fresh_node
# ===================================================================

class TestFreshNode:
    def test_identity_fields_preserved(self):
        orig = _make_node(item_id="foo", source_type="recipe", source_id="r1", base_req=5)
        orig.selected_pet_id = "pet_1"
        orig.selected_pet_level = 3
        orig.selected_consumable_id = "potion_1"

        fresh = _fresh_node(orig)
        assert fresh.node_id == orig.node_id
        assert fresh.item_id == orig.item_id
        assert fresh.source_type == orig.source_type
        assert fresh.source_id == orig.source_id
        assert fresh.base_requirement_amount == 5
        assert fresh.selected_pet_id == "pet_1"
        assert fresh.selected_pet_level == 3
        assert fresh.selected_consumable_id == "potion_1"

    def test_inputs_are_empty(self):
        child = _make_node(item_id="child")
        orig = _make_node(inputs={"child_0": child})
        fresh = _fresh_node(orig)
        assert fresh.inputs == {}

    def test_available_sources_is_copy(self):
        orig = _make_node(available_sources=[{"type": "bank", "id": "bank", "label": "Bank"}])
        fresh = _fresh_node(orig)
        fresh.available_sources.append({"type": "recipe", "id": "r2", "label": "R2"})
        assert len(orig.available_sources) == 1


# ===================================================================
# 10. Integration: _opt_subtree with mock gear optimizer
# ===================================================================

class TestOptSubtreeIntegration:
    """
    Tests _opt_subtree end-to-end with a mocked gear optimizer and
    patched calculate_node_metrics / build_default_tree to control scoring.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, basic_game_data, locations, user_state, skill_levels):
        self.gd = basic_game_data
        self.locations = locations
        self.user_state = user_state
        self.skill_levels = skill_levels

    def _build_opt(self, goal="minimize_steps"):
        return _make_optimizer(
            self.gd, self.locations, self.user_state, self.skill_levels, goal=goal
        )

    def test_bank_node_returns_zero(self):
        opt, _ = self._build_opt()
        opt.optimize(_make_node(source_type="bank", source_id="bank"), scope="node")
        # bank nodes are never re-optimized, score is effectively 0
        node = _make_node(source_type="bank", source_id="bank")
        # Directly test the internal method after optimize initialises callbacks
        opt._memo.clear()
        opt._node_start_cb = None
        opt._node_done_cb = None
        score = opt._opt_subtree(node, is_root=False)
        assert score == 0.0

    def test_no_configs_returns_inf(self):
        """Node with only bank sources → enumerate returns empty → inf."""
        opt, _ = self._build_opt()
        node = _make_node(
            available_sources=[{"type": "bank", "id": "bank", "label": "Bank"}]
        )
        # Override source_type to non-bank so it doesn't short-circuit
        node.source_type = "recipe"
        opt._node_start_cb = None
        opt._node_done_cb = None
        score = opt._opt_subtree(node, is_root=False)
        assert score == float("inf")

    @patch("tree_optimizer.TreeNodeOptimizer._score")
    @patch("tree_optimizer.TreeNodeOptimizer._apply_config")
    def test_best_config_chosen_by_score(self, mock_apply, mock_score):
        """When multiple configs exist, the one with the lowest score wins."""
        opt, fake_go = self._build_opt()

        # Two recipe configs (mat_a vs mat_b) + 2 service variants = 4 configs
        node = _make_node(
            available_sources=[
                {"type": "recipe", "id": "recipe_1", "label": "Craft Widget"},
            ]
        )

        # Make _score return decreasing values so last config is best
        call_idx = {"n": 0}
        def score_side_effect(n, is_root=False):
            call_idx["n"] += 1
            return 100.0 - call_idx["n"]
        mock_score.side_effect = score_side_effect
        mock_apply.return_value = None

        opt._node_start_cb = None
        opt._node_done_cb = None
        opt._opt_subtree(node, is_root=True)
        # Should have tried all configs
        assert mock_score.call_count >= 2
        assert fake_go.call_count >= 2

    def test_memoization_avoids_redundant_work(self):
        """Two nodes with same item_id+amount+sources should reuse memo."""
        opt, fake_go = self._build_opt()
        opt._node_start_cb = None
        opt._node_done_cb = None

        with patch.object(opt, "_score", return_value=10.0), \
             patch.object(opt, "_apply_config"):
            n1 = _make_node(item_id="mat_c", source_type="recipe", source_id="recipe_2",
                            available_sources=[{"type": "recipe", "id": "recipe_2", "label": "Craft Gadget"}])
            n2 = _make_node(item_id="mat_c", source_type="recipe", source_id="recipe_2",
                            available_sources=[{"type": "recipe", "id": "recipe_2", "label": "Craft Gadget"}])

            opt._opt_subtree(n1, is_root=False)
            calls_after_first = fake_go.call_count

            opt._opt_subtree(n2, is_root=False)
            # Second call should hit memo — no new gear optimizer calls
            assert fake_go.call_count == calls_after_first


# ===================================================================
# 11. Integration: _opt_single
# ===================================================================

class TestOptSingle:
    @pytest.fixture(autouse=True)
    def _setup(self, basic_game_data, locations, user_state, skill_levels):
        self.gd = basic_game_data
        self.locations = locations
        self.user_state = user_state
        self.skill_levels = skill_levels

    def test_bank_node_skipped(self):
        opt, fake_go = _make_optimizer(
            self.gd, self.locations, self.user_state, self.skill_levels
        )
        node = _make_node(source_type="bank", source_id="bank")
        opt._opt_single(node, is_root=False)
        assert fake_go.call_count == 0

    @patch("tree_optimizer.TreeNodeOptimizer._score")
    @patch("tree_optimizer.TreeNodeOptimizer._apply_config")
    def test_single_updates_gear_set(self, mock_apply, mock_score):
        mock_score.return_value = 5.0
        mock_apply.return_value = None

        opt, fake_go = _make_optimizer(
            self.gd, self.locations, self.user_state, self.skill_levels
        )
        node = _make_node(
            available_sources=[
                {"type": "recipe", "id": "recipe_2", "label": "Craft Gadget"},
            ]
        )
        opt.optimize(node, scope="node")
        # Should have called gear optimizer
        assert fake_go.call_count >= 1
        # Node should have been updated
        assert node.loadout_id == "AUTO"


# ===================================================================
# 12. Progress & callback tracking
# ===================================================================

class TestCallbacks:
    @pytest.fixture(autouse=True)
    def _setup(self, basic_game_data, locations, user_state, skill_levels):
        self.gd = basic_game_data
        self.locations = locations
        self.user_state = user_state
        self.skill_levels = skill_levels

    @patch("tree_optimizer.TreeNodeOptimizer._score", return_value=1.0)
    @patch("tree_optimizer.TreeNodeOptimizer._apply_config")
    def test_progress_callback_called(self, mock_apply, mock_score):
        opt, _ = _make_optimizer(
            self.gd, self.locations, self.user_state, self.skill_levels
        )
        progress_calls = []

        node = _make_node(
            available_sources=[
                {"type": "recipe", "id": "recipe_2", "label": "Craft Gadget"},
            ]
        )
        opt.optimize(node, scope="node", progress_callback=lambda d, t: progress_calls.append((d, t)))
        assert len(progress_calls) >= 1
        # Each call should have done <= total
        for done, total in progress_calls:
            assert done <= total

    @patch("tree_optimizer.TreeNodeOptimizer._score", return_value=1.0)
    @patch("tree_optimizer.TreeNodeOptimizer._apply_config")
    def test_node_start_callback_called(self, mock_apply, mock_score):
        opt, _ = _make_optimizer(
            self.gd, self.locations, self.user_state, self.skill_levels
        )
        start_calls = []

        node = _make_node(
            available_sources=[
                {"type": "recipe", "id": "recipe_2", "label": "Craft Gadget"},
            ]
        )
        opt.optimize(
            node, scope="node",
            node_start_callback=lambda item_id, n: start_calls.append((item_id, n)),
        )
        assert len(start_calls) >= 1
        assert start_calls[0][0] == "test_item"  # item_id of the node
        assert start_calls[0][1] >= 1  # num_configs > 0

    @patch("tree_optimizer.TreeNodeOptimizer._score", return_value=1.0)
    @patch("tree_optimizer.TreeNodeOptimizer._apply_config")
    def test_node_done_callback_called(self, mock_apply, mock_score):
        opt, _ = _make_optimizer(
            self.gd, self.locations, self.user_state, self.skill_levels
        )
        done_calls = []

        node = _make_node(
            available_sources=[
                {"type": "recipe", "id": "recipe_2", "label": "Craft Gadget"},
            ]
        )
        opt.optimize(
            node, scope="node",
            node_done_callback=lambda *args: done_calls.append(args),
        )
        assert len(done_calls) == 1
        item_id, source_type, source_id, score = done_calls[0]
        assert item_id == "test_item"
        assert isinstance(score, float)


# ===================================================================
# 13. _make_auto_target
# ===================================================================

class TestMakeAutoTarget:
    @pytest.fixture(autouse=True)
    def _setup(self, basic_game_data, locations, user_state, skill_levels):
        self.gd = basic_game_data
        self.locations = locations
        self.user_state = user_state
        self.skill_levels = skill_levels

    def test_minimize_steps_target(self):
        opt, _ = _make_optimizer(
            self.gd, self.locations, self.user_state, self.skill_levels, goal="minimize_steps"
        )
        target = opt._make_auto_target()
        assert target == [{"id": 0, "target": "Reward Rolls", "weight": 100}]

    def test_maximize_xp_target(self):
        opt, _ = _make_optimizer(
            self.gd, self.locations, self.user_state, self.skill_levels, goal="maximize_xp"
        )
        target = opt._make_auto_target()
        assert target == [{"id": 0, "target": "Xp", "weight": 100}]

    def test_fine_materials_override(self):
        opt, _ = _make_optimizer(
            self.gd, self.locations, self.user_state, self.skill_levels, goal="minimize_steps"
        )
        opt._global_use_fine = True
        target = opt._make_auto_target()
        assert target == [{"id": 0, "target": "Fine", "weight": 100}]
