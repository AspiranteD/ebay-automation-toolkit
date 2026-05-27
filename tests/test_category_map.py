"""Tests for eBay category mapping."""
import pytest

from src.bulk.category_map import (
    PARENT_TO_LEAF, DEFAULT_LEAF_CATEGORY,
    ensure_leaf_category, map_source_category,
)


class TestEnsureLeafCategory:
    def test_parent_consumer_electronics(self):
        assert ensure_leaf_category("293") == "175837"

    def test_parent_computers(self):
        assert ensure_leaf_category("58058") == "162"

    def test_parent_home_garden(self):
        assert ensure_leaf_category("11700") == "181076"

    def test_parent_kitchen(self):
        assert ensure_leaf_category("20625") == "181076"

    def test_parent_tools(self):
        assert ensure_leaf_category("631") == "181076"

    def test_parent_sporting_goods(self):
        assert ensure_leaf_category("888") == "310"

    def test_leaf_category_passthrough(self):
        assert ensure_leaf_category("175837") == "175837"

    def test_unknown_category_passthrough(self):
        assert ensure_leaf_category("999999") == "999999"

    def test_whitespace_handling(self):
        assert ensure_leaf_category("  293  ") == "175837"

    def test_all_parents_have_valid_leaves(self):
        for parent, leaf in PARENT_TO_LEAF.items():
            assert leaf  # not empty
            assert leaf != parent or parent == "175837"  # leaf != parent except self-ref


class TestMapSourceCategory:
    def test_department_match(self):
        cat_map = {("electronics", "amazon_department"): "293"}
        result = map_source_category("Electronics", "", "", cat_map)
        assert result == "175837"  # 293 -> leaf

    def test_category_match(self):
        cat_map = {("laptops", "amazon_category"): "58058"}
        result = map_source_category("", "Laptops", "", cat_map)
        assert result == "162"  # 58058 -> leaf

    def test_subcategory_match(self):
        cat_map = {("wireless headphones", "amazon_subcategory"): "112529"}
        result = map_source_category("", "", "Wireless Headphones", cat_map)
        assert result == "112529"

    def test_priority_department_over_category(self):
        cat_map = {
            ("electronics", "amazon_department"): "293",
            ("headphones", "amazon_category"): "112529",
        }
        result = map_source_category("Electronics", "Headphones", "", cat_map)
        assert result == "175837"  # department wins

    def test_no_match_returns_default(self):
        result = map_source_category("Unknown", "Unknown", "Unknown", {})
        assert result == DEFAULT_LEAF_CATEGORY

    def test_empty_inputs(self):
        result = map_source_category("", "", "", {})
        assert result == DEFAULT_LEAF_CATEGORY

    def test_case_insensitive(self):
        cat_map = {("electronics", "amazon_department"): "293"}
        result = map_source_category("ELECTRONICS", "", "", cat_map)
        assert result == "175837"
