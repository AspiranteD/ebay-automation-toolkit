"""
eBay sync orchestrator for automated listing lifecycle management.

Two main flows:

1. sync_after_extraction()
   Runs after each Wallapop order extraction:
     - Import eBay orders (Fulfillment API)
     - End listings for items sold on other channels
     - Upload new listings for newly available items
     - All via Feed API (fully automated)

2. full_relist()
   Runs every N days (configurable):
     - End all active listings
     - Re-upload all to improve eBay search positioning
     - 100% automated via Feed API

Dynamic days_back:
  If the server was down for days/weeks, the service calculates how
  many days of orders to request based on the last import timestamp.
  Min 7 days, max 90 days, with a 2-day safety margin.
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)

MIN_DAYS_BACK = 7
MAX_DAYS_BACK = 90
DEFAULT_DAYS_BACK = 30
SAFETY_MARGIN_DAYS = 2


class EbaySyncService:
    """
    Orchestrates eBay listing lifecycle: import, end, add, relist.

    Database-agnostic: uses callbacks for persistence queries.
    """

    def __init__(
        self,
        import_orders: Callable[[int], dict],
        get_sold_items: Callable[[], list[dict]],
        get_available_items: Callable[[], list[dict]],
        generate_end_csvs: Callable[[list[dict], str], list[str]],
        generate_add_csvs: Callable[[list[dict], str], list[str]],
        upload_csvs: Callable[[list[str]], list[str]],
        import_responses: Callable[[list[str]], None],
        get_last_import_date: Optional[Callable[[], Optional[datetime]]] = None,
        on_end_items: Optional[Callable[[list[dict]], None]] = None,
    ):
        self._import_orders = import_orders
        self._get_sold_items = get_sold_items
        self._get_available_items = get_available_items
        self._generate_end_csvs = generate_end_csvs
        self._generate_add_csvs = generate_add_csvs
        self._upload_csvs = upload_csvs
        self._import_responses = import_responses
        self._get_last_import_date = get_last_import_date
        self._on_end_items = on_end_items

    def calculate_days_back(self) -> int:
        """
        Calculate dynamic days_back based on last import timestamp.

        If the server was down for days, requests more history from
        the API to avoid missing orders. Bounded [7, 90].
        """
        if not self._get_last_import_date:
            return DEFAULT_DAYS_BACK

        try:
            last_import = self._get_last_import_date()
            if not last_import:
                logger.info("No previous eBay orders, using %d days", DEFAULT_DAYS_BACK)
                return DEFAULT_DAYS_BACK

            now = datetime.now(timezone.utc)
            days_since = (now - last_import).days + SAFETY_MARGIN_DAYS
            result = max(MIN_DAYS_BACK, min(days_since, MAX_DAYS_BACK))

            if result > MIN_DAYS_BACK:
                logger.warning(
                    "Last eBay import %d days ago, using days_back=%d",
                    days_since - SAFETY_MARGIN_DAYS, result,
                )
            return result
        except Exception as e:
            logger.error("Error calculating days_back: %s, using %d", e, MIN_DAYS_BACK)
            return MIN_DAYS_BACK

    def sync_after_extraction(self, output_dir: str) -> dict:
        """
        Full sync after marketplace extraction.

        Steps:
          1. Import eBay orders with dynamic days_back
          2. End listings for items sold on other channels
          3. Upload new listings for available items
        """
        stats = {
            "ebay_orders": {},
            "ended_sold": 0,
            "new_listings": 0,
            "uploaded_files": 0,
        }

        try:
            days_back = self.calculate_days_back()
            logger.info("[Sync] Importing eBay orders (days_back=%d)...", days_back)
            stats["ebay_orders"] = self._import_orders(days_back)
        except Exception as e:
            logger.error("[Sync] Error importing orders: %s", e)

        try:
            ended = self._end_sold_items(output_dir)
            stats["ended_sold"] = ended
            if ended:
                logger.info("[Sync] %d listings ended (sold on other channels)", ended)
        except Exception as e:
            logger.error("[Sync] Error ending sold items: %s", e)

        try:
            uploaded = self._upload_new_listings(output_dir)
            stats["new_listings"] = uploaded.get("items", 0)
            stats["uploaded_files"] = uploaded.get("files", 0)
        except Exception as e:
            logger.error("[Sync] Error uploading new listings: %s", e)

        logger.info("[Sync] Complete: %s", stats)
        return stats

    def full_relist(self, output_dir: str) -> dict:
        """
        Full relist: end all active listings, then re-upload all.

        Used to improve eBay search positioning by cycling listings.
        """
        stats = {"ended": 0, "added": 0, "end_files": 0, "add_files": 0}

        try:
            logger.info("[Relist] Importing orders before relist...")
            self._import_orders(3)
        except Exception as e:
            logger.error("[Relist] Error importing orders: %s", e)

        try:
            logger.info("[Relist] Ending all active listings...")
            sold_items = self._get_sold_items()
            if sold_items:
                end_paths = self._generate_end_csvs(sold_items, output_dir)
                if end_paths:
                    result_files = self._upload_csvs(end_paths)
                    stats["end_files"] = len(result_files)
                    stats["ended"] = len(sold_items)
                    self._import_responses(result_files)
        except Exception as e:
            logger.error("[Relist] Error ending listings: %s", e)

        time.sleep(10)

        try:
            logger.info("[Relist] Uploading all listings...")
            available = self._get_available_items()
            if available:
                add_paths = self._generate_add_csvs(available, output_dir)
                if add_paths:
                    result_files = self._upload_csvs(add_paths)
                    stats["add_files"] = len(result_files)
                    stats["added"] = len(available)
                    self._import_responses(result_files)
        except Exception as e:
            logger.error("[Relist] Error uploading listings: %s", e)

        logger.info("[Relist] Complete: %s", stats)
        return stats

    def _end_sold_items(self, output_dir: str) -> int:
        """End eBay listings for items sold on other channels."""
        sold_items = self._get_sold_items()
        if not sold_items:
            return 0

        logger.info("[Sync] %d items sold outside eBay, generating End...", len(sold_items))

        end_paths = self._generate_end_csvs(sold_items, output_dir)
        if self._on_end_items:
            self._on_end_items(sold_items)

        if end_paths:
            result_files = self._upload_csvs(end_paths)
            self._import_responses(result_files)

        return len(sold_items)

    def _upload_new_listings(self, output_dir: str) -> dict:
        """Generate and upload CSVs for newly available items."""
        available = self._get_available_items()
        if not available:
            return {"items": 0, "files": 0}

        add_paths = self._generate_add_csvs(available, output_dir)
        if not add_paths:
            return {"items": 0, "files": 0}

        result_files = self._upload_csvs(add_paths)
        self._import_responses(result_files)

        return {"items": len(available), "files": len(result_files)}
