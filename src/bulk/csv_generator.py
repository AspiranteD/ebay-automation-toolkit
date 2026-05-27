"""
eBay File Exchange CSV generator for bulk listing operations.

Generates CSV files in the exact format required by eBay Seller Hub
Reports (File Exchange / FX_LISTING feed type).

Two actions:
  - Add: Create new listings with full product data
  - End: Terminate active listings by ItemID

Production patterns:
  - Title generation with brand/model/description, truncated to 80 chars
    with condition suffix and unique identifier
  - Condition mapping: PERFECTO->3000, CON_TARA->3000, PARA_PIEZAS->7000
  - Price markup from source marketplace price
  - Image URL pipe-separation (up to 12 images)
  - Text sanitization: strip newlines/tabs that break CSV format
  - Category mapping: Amazon taxonomy -> eBay leaf categories
  - Business policies (shipping/return/payment profiles) support
"""
import csv
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .category_map import ensure_leaf_category, map_source_category, DEFAULT_LEAF_CATEGORY

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 80
MAX_ITEMS_PER_FILE = 1000
MAX_EBAY_IMAGES = 12

SITE_ID = "Spain"
FX_VERSION = "1193"

CONDITION_MAP = {
    "PERFECTO": 3000,
    "CON_TARA": 3000,
    "PARA_PIEZAS": 7000,
}

CONDITION_SUFFIX = {
    "PERFECTO": "Perfecto",
    "CON_TARA": "Con tara",
    "PARA_PIEZAS": "Para piezas",
}


@dataclass
class ListingConfig:
    """Configuration for CSV generation."""
    country: str = "ES"
    currency: str = "EUR"
    markup_pct: float = 15.0
    min_price_eur: float = 5.0
    shipping_profile: str = ""
    return_profile: str = ""
    payment_profile: str = ""
    location: str = "Madrid, ES"


class EbayCSVGenerator:
    """Generates eBay File Exchange CSV files for bulk listing operations."""

    def __init__(self, config: Optional[ListingConfig] = None):
        self._config = config or ListingConfig()

    def calculate_price(self, source_price: Optional[float]) -> Optional[float]:
        """Apply markup percentage and enforce minimum price."""
        if source_price is None:
            return None
        ebay_price = round(source_price * (1 + self._config.markup_pct / 100), 2)
        if ebay_price < self._config.min_price_eur:
            return None
        return ebay_price

    @staticmethod
    def sanitize_text(text: str) -> str:
        """Strip newlines/tabs/control chars that break CSV format."""
        return re.sub(r"[\r\n\t\x0b\x0c\x85\u2028\u2029]+", " ", text).strip()

    @staticmethod
    def sanitize_row(row: list) -> list:
        """Sanitize all string fields in a CSV row."""
        return [
            re.sub(r"[\r\n\t]+", " ", str(v)).strip() if isinstance(v, str) else v
            for v in row
        ]

    @staticmethod
    def get_images_piped(image_urls_raw: Optional[str]) -> str:
        """Extract up to 12 valid HTTP image URLs, pipe-separated."""
        if not image_urls_raw:
            return ""
        raw = str(image_urls_raw).strip()
        tokens = [t.strip() for t in re.split(r"[,\s]+", raw) if t.strip()]
        valid = [t for t in tokens if t.startswith("http")][:MAX_EBAY_IMAGES]
        return "|".join(valid)

    def generate_title(
        self,
        lpn: str,
        brand: str = "",
        model: str = "",
        description: str = "",
        condition_code: str = "",
    ) -> str:
        """
        Build eBay listing title with truncation and unique suffix.

        Format: "Brand - Model - Description [Condition #XXXX]"
        Truncated to 80 chars total, preserving word boundaries.
        """
        uid = lpn[-4:] if len(lpn) >= 4 else lpn
        cond_label = CONDITION_SUFFIX.get(condition_code.upper(), "Usado")

        parts = []
        if brand and brand.lower() not in ("unknown", "generic", "nan", "none", ""):
            parts.append(brand)
        if model and model.lower() not in ("unknown", "nan", "none", ""):
            parts.append(model)
        if description and description.lower() not in ("nan", "none"):
            desc_clean = re.sub(r"\s+", " ", description)
            desc_clean = re.sub(
                r"[^\w\s\-/.,()áéíóúñüÁÉÍÓÚÑÜ]", "", desc_clean
            )
            parts.append(desc_clean)

        title = " - ".join(parts) if len(parts) > 1 else " ".join(parts)
        if not title:
            title = "Articulo de liquidacion"

        suffix = f" [{cond_label} #{uid}]"
        max_len = MAX_TITLE_LENGTH - len(suffix)
        if len(title) > max_len:
            title = title[: max_len - 3].rsplit(" ", 1)[0] + "..."
        return title.strip() + suffix

    def build_description(
        self,
        description: str = "",
        features: str = "",
        condition_description: str = "",
        asin: str = "",
    ) -> str:
        """Build HTML description (single line, no newlines to break CSV)."""
        lines = []
        if description and description.lower() not in ("nan", "none"):
            lines.append(self.sanitize_text(description))
        if features and features.lower() not in ("nan", "none"):
            lines.append(self.sanitize_text(features))
        if condition_description and condition_description.lower() not in ("nan", "none"):
            lines.append(f"Estado: {self.sanitize_text(condition_description)}")
        if asin and asin.lower() not in ("nan", "none"):
            lines.append(f"ASIN Amazon: {asin}")
        lines.append("Articulo de segunda mano / liquidacion. NO es nuevo.")
        return self.sanitize_text("<br>".join(lines))

    def build_listing_row(
        self,
        item: dict,
        category_map: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Build a single listing row from item data.

        Returns None if item doesn't pass price filter.
        """
        source_price = item.get("source_price")
        ebay_price = self.calculate_price(source_price)
        if ebay_price is None:
            return None

        condition_code = str(item.get("condition_code", "") or "").upper()
        condition_id = CONDITION_MAP.get(condition_code, 3000)

        category = DEFAULT_LEAF_CATEGORY
        if category_map:
            category = map_source_category(
                item.get("department", ""),
                item.get("category", ""),
                item.get("subcategory", ""),
                category_map,
            )

        title = self.generate_title(
            lpn=item.get("lpn", ""),
            brand=item.get("brand", ""),
            model=item.get("model", ""),
            description=item.get("description", ""),
            condition_code=condition_code,
        )
        images = self.get_images_piped(item.get("image_urls"))
        desc_html = self.build_description(
            description=item.get("description", ""),
            features=item.get("features", ""),
            condition_description=item.get("condition_description", ""),
            asin=item.get("asin", ""),
        )

        brand = str(item.get("brand", "") or "").strip()
        if brand.lower() in ("unknown", "generic", "nan", "none", ""):
            brand = "Unbranded"

        mpn = str(item.get("model", "") or "").strip()
        if mpn.lower() in ("unknown", "nan", "none", ""):
            mpn = "Does Not Apply"

        color = str(item.get("color", "") or "").strip()
        if color.lower() in ("unknown", "nan", "none", ""):
            color = ""

        cond_desc = str(item.get("condition_description", "") or "").strip()
        if cond_desc.lower() in ("nan", "none"):
            cond_desc = ""
        cond_desc = self.sanitize_text(cond_desc)[:65]

        return {
            "lpn": item["lpn"],
            "category": category,
            "title": title,
            "condition_id": condition_id,
            "condition_description": cond_desc,
            "brand": brand,
            "mpn": mpn,
            "color": color,
            "price": ebay_price,
            "images": images,
            "description": desc_html,
        }

    def write_add_csv(
        self,
        items: list[dict],
        output_dir: str,
        file_index: int = 1,
        total_files: int = 1,
    ) -> str:
        """Write a File Exchange CSV with Action=Add."""
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"ebay_fileexchange_{timestamp}_part{file_index}_of_{total_files}.csv"
        filepath = os.path.join(output_dir, filename)

        cfg = self._config
        action_header = (
            f"*Action(SiteID={SITE_ID}|Country={cfg.country}|"
            f"Currency={cfg.currency}|Version={FX_VERSION}|CC=UTF-8)"
        )

        headers = [
            action_header, "Custom label (SKU)", "Category ID", "Title",
            "ConditionID", "C:Condition Description", "C:Brand", "C:MPN",
            "C:Color", "C:Type", "Start price", "Quantity",
            "Item photo URL", "Description", "Format", "Duration", "Location",
        ]

        use_policies = bool(
            cfg.shipping_profile or cfg.return_profile or cfg.payment_profile
        )
        if use_policies:
            headers.extend([
                "Shipping profile name", "Return profile name", "Payment profile name",
            ])
        else:
            headers.extend([
                "Shipping type", "Shipping service 1 option",
                "Shipping service 1 cost", "Shipping service 1 priority",
                "Returns accepted option", "Returns within option",
                "Refund option", "Return shipping cost paid by", "Max dispatch time",
            ])

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)

            for item in items:
                base_row = [
                    "Add", item["lpn"], item["category"], item["title"],
                    item["condition_id"], item["condition_description"],
                    item["brand"], item["mpn"], item["color"], "Second Hand",
                    item["price"], 1, item["images"], item["description"],
                    "FixedPrice", "GTC", cfg.location,
                ]
                if use_policies:
                    base_row.extend([
                        cfg.shipping_profile, cfg.return_profile, cfg.payment_profile,
                    ])
                else:
                    base_row.extend([
                        "Flat", "ES_Otros", "4.99", "1",
                        "ReturnsAccepted", "Days_30", "MoneyBack", "Buyer", "3",
                    ])
                writer.writerow(self.sanitize_row(base_row))

        logger.info("CSV Add generated: %s (%d items)", filepath, len(items))
        return filepath

    def write_end_csv(
        self,
        items: list[dict],
        output_dir: str,
        file_index: int = 1,
        total_files: int = 1,
    ) -> str:
        """Write a File Exchange CSV with Action=End."""
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"ebay_end_{timestamp}_part{file_index}_of_{total_files}.csv"
        filepath = os.path.join(output_dir, filename)

        cfg = self._config
        action_header = (
            f"*Action(SiteID={SITE_ID}|Country={cfg.country}|"
            f"Currency={cfg.currency}|Version={FX_VERSION}|CC=UTF-8)"
        )

        headers = [action_header, "ItemID", "EndCode", "Custom label (SKU)"]

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for item in items:
                writer.writerow([
                    "End", item["ebay_item_id"], "NotAvailable", item["lpn"],
                ])

        logger.info("CSV End generated: %s (%d items)", filepath, len(items))
        return filepath

    def generate_add_csvs(
        self,
        items: list[dict],
        output_dir: str,
        category_map: Optional[dict] = None,
    ) -> list[str]:
        """Build listing rows and write chunked CSVs (max 1000 items each)."""
        rows = []
        stats = {"included": 0, "skipped_price": 0}

        for item in items:
            row = self.build_listing_row(item, category_map)
            if row:
                rows.append(row)
                stats["included"] += 1
            else:
                stats["skipped_price"] += 1

        if not rows:
            logger.warning("No items passed price filter")
            return []

        import math
        total_files = math.ceil(len(rows) / MAX_ITEMS_PER_FILE)
        paths = []

        for i in range(total_files):
            start = i * MAX_ITEMS_PER_FILE
            chunk = rows[start : start + MAX_ITEMS_PER_FILE]
            path = self.write_add_csv(chunk, output_dir, i + 1, total_files)
            paths.append(path)

        logger.info(
            "Generated %d Add files with %d items (skipped %d on price)",
            len(paths), stats["included"], stats["skipped_price"],
        )
        return paths
