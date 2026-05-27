"""Tests for eBay response file importer."""
import csv
import json
import os
import pytest

from src.bulk.response_importer import EbayResponseImporter, COLUMN_PATTERNS


@pytest.fixture
def importer(tmp_path):
    return EbayResponseImporter(downloads_dir=str(tmp_path))


class TestColumnDetection:
    def test_standard_columns(self, importer):
        cols = ["ItemID", "Custom Label", "Status", "Error Message"]
        col_map = importer._detect_columns(cols)
        assert col_map["item_id"] == "ItemID"
        assert col_map["custom_label"] == "Custom Label"
        assert col_map["status"] == "Status"
        assert col_map["error"] == "Error Message"

    def test_lowercase_columns(self, importer):
        cols = ["itemid", "customlabel", "status", "errormessage"]
        col_map = importer._detect_columns(cols)
        assert "item_id" in col_map
        assert "custom_label" in col_map

    def test_spanish_columns(self, importer):
        cols = ["ItemID", "SKU", "Resultado", "*Action"]
        col_map = importer._detect_columns(cols)
        assert col_map.get("custom_label") == "SKU"
        assert col_map.get("status") == "Resultado"
        assert col_map.get("action") == "*Action"

    def test_partial_match(self, importer):
        cols = ["eBay Item ID", "Custom Label (SKU)", "Upload Status", "Error Details"]
        col_map = importer._detect_columns(cols)
        assert "item_id" in col_map
        assert "status" in col_map

    def test_missing_columns_graceful(self, importer):
        cols = ["Random1", "Random2"]
        col_map = importer._detect_columns(cols)
        assert "custom_label" not in col_map


class TestParseResponseRows:
    def test_success_add(self, importer):
        cols = ["ItemID", "CustomLabel", "Status", "Error Message"]
        rows = [
            {"ItemID": "123456789", "CustomLabel": "LPN001", "Status": "Success", "Error Message": ""},
        ]
        results = importer.parse_response_rows(rows, cols)
        assert len(results) == 1
        assert results[0]["lpn"] == "LPN001"
        assert results[0]["item_id"] == "123456789"
        assert results[0]["status"] == "active"
        assert results[0]["outcome"] == "success"

    def test_success_end(self, importer):
        cols = ["ItemID", "CustomLabel", "Status", "*Action"]
        rows = [
            {"ItemID": "123", "CustomLabel": "LPN001", "Status": "Success", "*Action": "End"},
        ]
        results = importer.parse_response_rows(rows, cols)
        assert results[0]["status"] == "ended"
        assert results[0]["is_end_action"] is True

    def test_warning_treated_as_success(self, importer):
        cols = ["ItemID", "CustomLabel", "Status"]
        rows = [{"ItemID": "123", "CustomLabel": "LPN001", "Status": "Warning"}]
        results = importer.parse_response_rows(rows, cols)
        assert results[0]["outcome"] == "warning"
        assert results[0]["status"] == "active"

    def test_failure(self, importer):
        cols = ["ItemID", "CustomLabel", "Status", "Error Message"]
        rows = [
            {"ItemID": "", "CustomLabel": "LPN002", "Status": "Failure",
             "Error Message": "Invalid category"},
        ]
        results = importer.parse_response_rows(rows, cols)
        assert results[0]["status"] == "error"
        assert results[0]["outcome"] == "error"
        assert results[0]["error"] == "Invalid category"

    def test_empty_lpn_skipped(self, importer):
        cols = ["ItemID", "CustomLabel", "Status"]
        rows = [{"ItemID": "123", "CustomLabel": "", "Status": "Success"}]
        results = importer.parse_response_rows(rows, cols)
        assert len(results) == 0

    def test_nan_item_id_cleaned(self, importer):
        cols = ["ItemID", "CustomLabel", "Status"]
        rows = [{"ItemID": "nan", "CustomLabel": "LPN001", "Status": "Success"}]
        results = importer.parse_response_rows(rows, cols)
        assert results[0]["item_id"] == ""

    def test_nan_error_cleaned(self, importer):
        cols = ["ItemID", "CustomLabel", "Status", "Error Message"]
        rows = [
            {"ItemID": "123", "CustomLabel": "LPN001", "Status": "Success",
             "Error Message": "nan"},
        ]
        results = importer.parse_response_rows(rows, cols)
        assert results[0]["error"] is None

    def test_missing_custom_label_raises(self, importer):
        cols = ["ItemID", "Status"]
        with pytest.raises(ValueError, match="No SKU"):
            importer.parse_response_rows([], cols)

    def test_unknown_status_with_item_id(self, importer):
        cols = ["ItemID", "CustomLabel", "Status"]
        rows = [{"ItemID": "999", "CustomLabel": "LPN1", "Status": "Complete"}]
        results = importer.parse_response_rows(rows, cols)
        assert results[0]["status"] == "active"
        assert results[0]["outcome"] == "success"

    def test_unknown_status_without_item_id(self, importer):
        cols = ["ItemID", "CustomLabel", "Status"]
        rows = [{"ItemID": "", "CustomLabel": "LPN1", "Status": ""}]
        results = importer.parse_response_rows(rows, cols)
        assert results[0]["status"] == "error"


class TestSummarize:
    def test_summary(self, importer):
        results = [
            {"outcome": "success"},
            {"outcome": "success"},
            {"outcome": "warning"},
            {"outcome": "error"},
        ]
        stats = importer.summarize(results)
        assert stats["success"] == 2
        assert stats["warnings"] == 1
        assert stats["errors"] == 1
        assert stats["total"] == 4


class TestImportTracking:
    def test_mark_imported(self, importer, tmp_path):
        importer.mark_imported(str(tmp_path / "file1.csv"))
        imported = importer._load_imported_set()
        assert "file1.csv" in imported

    def test_dedup(self, importer, tmp_path):
        importer.mark_imported(str(tmp_path / "file1.csv"))
        importer.mark_imported(str(tmp_path / "file1.csv"))
        imported = importer._load_imported_set()
        assert len(imported) == 1


class TestIsResponseFile:
    def test_detects_response_file(self, importer, tmp_path):
        filepath = tmp_path / "ebay_fileexchange_response.csv"
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ItemID", "CustomLabel", "Status", "Error Message"])
            writer.writerow(["123", "LPN1", "Success", ""])
        assert importer.is_response_file(str(filepath)) is True

    def test_rejects_source_file(self, importer, tmp_path):
        filepath = tmp_path / "ebay_source.csv"
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["*Action", "Custom label (SKU)", "Category ID"])
            writer.writerow(["Add", "LPN1", "175837"])
        assert importer.is_response_file(str(filepath)) is False

    def test_nonexistent_file(self, importer):
        assert importer.is_response_file("/nonexistent/file.csv") is False


class TestFindPendingFiles:
    def test_finds_new_files(self, importer, tmp_path):
        filepath = tmp_path / "ebay_fileexchange_2024-06-15_10-30-00_part1.csv"
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ItemID", "CustomLabel", "Status"])
            writer.writerow(["123", "LPN1", "Success"])
        pending = importer.find_pending_files()
        assert len(pending) == 1

    def test_skips_already_imported(self, importer, tmp_path):
        filepath = tmp_path / "ebay_fileexchange_2024-06-15_10-30-00_part1.csv"
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ItemID", "CustomLabel", "Status"])
            writer.writerow(["123", "LPN1", "Success"])
        importer.mark_imported(str(filepath))
        pending = importer.find_pending_files()
        assert len(pending) == 0
