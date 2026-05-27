"""
eBay response file parser for File Exchange / Seller Hub Reports.

When a CSV is uploaded to eBay Seller Hub, eBay generates a response file
containing the result of each row:
  - ItemID assigned (for new listings via Add)
  - Status (Success / Warning / Failure)
  - Error messages

This module parses those response files with:
  - Multi-language column detection (English/Spanish, varying names)
  - Add vs End action detection
  - Batch import of all pending files in a directory
  - Dedup tracking via JSON log file
  - Watch mode for auto-importing new files
"""
import glob
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


COLUMN_PATTERNS = {
    "item_id": ["itemid", "item id", "item_id", "ebay item id"],
    "custom_label": ["customlabel", "custom label", "custom_label", "sku"],
    "status": ["status", "upload status", "resultado"],
    "error": ["error message", "errormessage", "error", "errors", "message"],
    "action": ["action", "*action", "accion"],
    "fees": ["fee", "fees", "listing fee"],
}


class EbayResponseImporter:
    """Parses eBay response CSV files and extracts results per listing."""

    def __init__(self, downloads_dir: Optional[str] = None):
        self._downloads_dir = downloads_dir or os.path.join(
            os.path.expanduser("~"), "Downloads"
        )
        self._imported_log = os.path.join(self._downloads_dir, ".ebay_imported_files.json")

    def _detect_columns(self, columns: list[str]) -> dict[str, str]:
        """
        Detect relevant columns via pattern matching.

        eBay response files vary column names by version and language.
        This maps semantic keys to actual column names.
        """
        col_map: dict[str, str] = {}
        cols_lower = {c.lower().strip(): c for c in columns}

        for key, candidates in COLUMN_PATTERNS.items():
            for candidate in candidates:
                match = cols_lower.get(candidate)
                if match:
                    col_map[key] = match
                    break
            if key not in col_map:
                for col_name, original in cols_lower.items():
                    if any(c in col_name for c in candidates):
                        col_map[key] = original
                        break

        return col_map

    def parse_response_rows(
        self, rows: list[dict], columns: list[str],
    ) -> list[dict]:
        """
        Parse response rows into structured results.

        Returns list of dicts with: lpn, item_id, status, error, action.
        """
        col_map = self._detect_columns(columns)
        if "custom_label" not in col_map:
            raise ValueError(
                f"No SKU/CustomLabel column found. Available: {columns}"
            )

        results = []
        for row in rows:
            lpn = str(row.get(col_map["custom_label"], "")).strip()
            if not lpn:
                continue

            item_id = ""
            if "item_id" in col_map:
                item_id = str(row.get(col_map["item_id"], "")).strip()
                if item_id.lower() in ("nan", "none", ""):
                    item_id = ""

            status_raw = ""
            if "status" in col_map:
                status_raw = str(row.get(col_map["status"], "")).strip().lower()

            error_msg = None
            if "error" in col_map:
                err = str(row.get(col_map["error"], "")).strip()
                if err.lower() not in ("nan", "none", ""):
                    error_msg = err

            action_raw = ""
            if "action" in col_map:
                action_raw = str(row.get(col_map["action"], "")).strip().lower()
            is_end = "end" in action_raw

            if "success" in status_raw or "complete" in status_raw:
                new_status = "ended" if is_end else "active"
                outcome = "success"
            elif "warning" in status_raw:
                new_status = "ended" if is_end else "active"
                outcome = "warning"
            elif "fail" in status_raw or "error" in status_raw:
                new_status = "error"
                outcome = "error"
            else:
                if item_id:
                    new_status = "ended" if is_end else "active"
                    outcome = "success"
                else:
                    new_status = "error"
                    outcome = "error"

            results.append({
                "lpn": lpn,
                "item_id": item_id,
                "status": new_status,
                "outcome": outcome,
                "error": error_msg,
                "is_end_action": is_end,
            })

        return results

    def summarize(self, results: list[dict]) -> dict:
        """Aggregate results into stats."""
        stats = {"success": 0, "warnings": 0, "errors": 0, "total": len(results)}
        for r in results:
            if r["outcome"] == "success":
                stats["success"] += 1
            elif r["outcome"] == "warning":
                stats["warnings"] += 1
            else:
                stats["errors"] += 1
        return stats

    def _load_imported_set(self) -> set[str]:
        if os.path.exists(self._imported_log):
            with open(self._imported_log, "r") as f:
                return set(json.load(f))
        return set()

    def _save_imported_set(self, imported: set[str]):
        with open(self._imported_log, "w") as f:
            json.dump(sorted(imported), f, indent=2)

    def is_response_file(self, filepath: str) -> bool:
        """Detect if a CSV is an eBay response file (not a source file)."""
        try:
            import csv as csv_mod
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv_mod.reader(f)
                header = next(reader, None)
                if not header:
                    return False
            cols_lower = {c.lower().strip() for c in header}
            has_status = any("status" in c for c in cols_lower)
            has_itemid = any("itemid" in c.replace(" ", "") for c in cols_lower)
            has_error = any("error" in c for c in cols_lower)
            return has_status and (has_itemid or has_error)
        except Exception:
            return False

    def find_pending_files(self) -> list[str]:
        """Find eBay response files in downloads not yet imported."""
        imported = self._load_imported_set()
        pattern = os.path.join(self._downloads_dir, "ebay_fileexchange_*-*.csv")
        candidates = sorted(glob.glob(pattern))

        return [
            f for f in candidates
            if os.path.basename(f) not in imported and self.is_response_file(f)
        ]

    def mark_imported(self, filepath: str):
        """Mark a file as imported in the tracking log."""
        imported = self._load_imported_set()
        imported.add(os.path.basename(filepath))
        self._save_imported_set(imported)
