"""Tests for eBay sync orchestrator."""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from src.sync.sync_service import (
    EbaySyncService,
    MIN_DAYS_BACK, MAX_DAYS_BACK, DEFAULT_DAYS_BACK, SAFETY_MARGIN_DAYS,
)


@pytest.fixture
def mock_deps():
    return {
        "import_orders": MagicMock(return_value={"new": 3, "updated": 2}),
        "get_sold_items": MagicMock(return_value=[]),
        "get_available_items": MagicMock(return_value=[]),
        "generate_end_csvs": MagicMock(return_value=["/tmp/end.csv"]),
        "generate_add_csvs": MagicMock(return_value=["/tmp/add.csv"]),
        "upload_csvs": MagicMock(return_value=["/tmp/result.csv"]),
        "import_responses": MagicMock(),
    }


@pytest.fixture
def sync(mock_deps):
    return EbaySyncService(**mock_deps)


class TestDaysBackCalculation:
    def test_default_when_no_callback(self, mock_deps):
        svc = EbaySyncService(**mock_deps)
        assert svc.calculate_days_back() == DEFAULT_DAYS_BACK

    def test_default_when_no_previous_orders(self, mock_deps):
        mock_deps["get_last_import_date"] = MagicMock(return_value=None)
        svc = EbaySyncService(**mock_deps)
        assert svc.calculate_days_back() == DEFAULT_DAYS_BACK

    def test_recent_import_uses_minimum(self, mock_deps):
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        mock_deps["get_last_import_date"] = MagicMock(return_value=yesterday)
        svc = EbaySyncService(**mock_deps)
        result = svc.calculate_days_back()
        assert result == MIN_DAYS_BACK

    def test_old_import_increases_days(self, mock_deps):
        old = datetime.now(timezone.utc) - timedelta(days=20)
        mock_deps["get_last_import_date"] = MagicMock(return_value=old)
        svc = EbaySyncService(**mock_deps)
        result = svc.calculate_days_back()
        assert result == 20 + SAFETY_MARGIN_DAYS

    def test_very_old_capped_at_max(self, mock_deps):
        ancient = datetime.now(timezone.utc) - timedelta(days=200)
        mock_deps["get_last_import_date"] = MagicMock(return_value=ancient)
        svc = EbaySyncService(**mock_deps)
        result = svc.calculate_days_back()
        assert result == MAX_DAYS_BACK

    def test_error_in_callback_uses_minimum(self, mock_deps):
        mock_deps["get_last_import_date"] = MagicMock(side_effect=Exception("DB error"))
        svc = EbaySyncService(**mock_deps)
        result = svc.calculate_days_back()
        assert result == MIN_DAYS_BACK

    def test_safety_margin_added(self, mock_deps):
        ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)
        mock_deps["get_last_import_date"] = MagicMock(return_value=ten_days_ago)
        svc = EbaySyncService(**mock_deps)
        result = svc.calculate_days_back()
        assert result == 10 + SAFETY_MARGIN_DAYS


class TestSyncAfterExtraction:
    def test_imports_orders(self, sync, mock_deps):
        stats = sync.sync_after_extraction("/tmp")
        mock_deps["import_orders"].assert_called_once()
        assert stats["ebay_orders"] == {"new": 3, "updated": 2}

    def test_ends_sold_items(self, mock_deps):
        mock_deps["get_sold_items"].return_value = [
            {"lpn": "LPN1", "ebay_item_id": "111"},
            {"lpn": "LPN2", "ebay_item_id": "222"},
        ]
        svc = EbaySyncService(**mock_deps)
        stats = svc.sync_after_extraction("/tmp")
        assert stats["ended_sold"] == 2
        mock_deps["generate_end_csvs"].assert_called_once()
        mock_deps["upload_csvs"].assert_called()

    def test_uploads_new_listings(self, mock_deps):
        mock_deps["get_available_items"].return_value = [
            {"lpn": "LPN3", "source_price": 10.0},
        ]
        svc = EbaySyncService(**mock_deps)
        stats = svc.sync_after_extraction("/tmp")
        assert stats["new_listings"] == 1

    def test_continues_on_order_import_error(self, mock_deps):
        mock_deps["import_orders"].side_effect = Exception("API down")
        svc = EbaySyncService(**mock_deps)
        stats = svc.sync_after_extraction("/tmp")
        assert stats["ebay_orders"] == {}
        # Should still try to end sold and upload new

    def test_continues_on_end_error(self, mock_deps):
        mock_deps["get_sold_items"].side_effect = Exception("DB error")
        svc = EbaySyncService(**mock_deps)
        stats = svc.sync_after_extraction("/tmp")
        assert stats["ended_sold"] == 0

    def test_on_end_items_callback(self, mock_deps):
        on_end = MagicMock()
        mock_deps["on_end_items"] = on_end
        mock_deps["get_sold_items"].return_value = [{"lpn": "LPN1", "ebay_item_id": "1"}]
        svc = EbaySyncService(**mock_deps)
        svc.sync_after_extraction("/tmp")
        on_end.assert_called_once()


class TestFullRelist:
    def test_imports_orders_first(self, sync, mock_deps):
        sync.full_relist("/tmp")
        mock_deps["import_orders"].assert_called_with(3)

    def test_ends_then_adds(self, mock_deps):
        mock_deps["get_sold_items"].return_value = [
            {"lpn": "L1", "ebay_item_id": "E1"},
        ]
        mock_deps["get_available_items"].return_value = [
            {"lpn": "L2", "source_price": 10.0},
        ]
        svc = EbaySyncService(**mock_deps)
        stats = svc.full_relist("/tmp")
        assert stats["ended"] == 1
        assert stats["added"] == 1

    def test_continues_on_end_error(self, mock_deps):
        mock_deps["get_sold_items"].side_effect = Exception("fail")
        mock_deps["get_available_items"].return_value = [{"lpn": "L1"}]
        svc = EbaySyncService(**mock_deps)
        stats = svc.full_relist("/tmp")
        assert stats["ended"] == 0
        # add should still proceed

    def test_empty_listings(self, sync, mock_deps):
        stats = sync.full_relist("/tmp")
        assert stats["ended"] == 0
        assert stats["added"] == 0
