"""
eBay Fulfillment API orders service.

Fetches orders from eBay's Fulfillment API with:
- Offset-based pagination (PAGE_SIZE=50)
- Date range filtering via creationdate
- Complex buyer extraction from nested fulfillmentStartInstructions
- Tracking code extraction with multiple fallback paths
- LPN resolution with legacy suffix handling
- Fee/shipping splitting across multi-item orders
- Auto-complete of delivered orders after N days
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

import requests

from .status import (
    map_ebay_status,
    ST_POR_ENVIAR, ST_ENVIADO, ST_ENTREGADO, ST_COMPLETADO,
    ST_CANCELADO, ST_REEMBOLSADO,
    CANCEL_STATES, CANCEL_STATUSES, REFUND_SALE_STATUSES,
    ALL_SALE_STATUSES, PAYMENT_STATUS_PAGADO, PAYMENT_STATUS_REEMBOLSADO,
)

logger = logging.getLogger(__name__)

FULFILLMENT_API = "https://api.ebay.com/sell/fulfillment/v1"
MARKETPLACE_ID = "EBAY_ES"
PAGE_SIZE = 50


@dataclass
class BuyerInfo:
    """Parsed buyer data from eBay order."""
    name: str = ""
    username: str = ""
    country: str = "ES"
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state_or_province: str = ""
    postal_code: str = ""
    phone: str = ""
    email: str = ""
    company_name: str = ""

    def to_dict(self) -> dict:
        return {
            "fullName": self.name,
            "addressLine1": self.address_line1,
            "addressLine2": self.address_line2,
            "city": self.city,
            "stateOrProvince": self.state_or_province,
            "postalCode": self.postal_code,
            "countryCode": self.country,
            "phone": self.phone,
            "email": self.email,
            "companyName": self.company_name,
        }

    def format_for_label(self) -> str:
        """Format address for copy-paste onto a shipping label."""
        lines = []
        if self.name:
            lines.append(self.name)
        if self.company_name:
            lines.append(self.company_name)
        if self.address_line1:
            lines.append(self.address_line1)
        if self.address_line2:
            lines.append(self.address_line2)
        city_line = ""
        if self.postal_code:
            city_line += self.postal_code + " "
        if self.city:
            city_line += self.city
        if self.state_or_province:
            city_line += f" ({self.state_or_province})"
        if city_line:
            lines.append(city_line.strip())
        if self.country and self.country != "ES":
            lines.append(self.country)
        if self.phone:
            lines.append(f"Tel: {self.phone}")
        return "\n".join(lines)


@dataclass
class TrackingInfo:
    """Tracking and carrier data from eBay order."""
    tracking_number: str = ""
    carrier_code: str = ""
    fulfillment_hrefs: list[str] = field(default_factory=list)


@dataclass
class OrderData:
    """Fully parsed eBay order with all enrichment data."""
    order_id: str
    request_id: str
    status_id: int
    buyer: BuyerInfo
    tracking: TrackingInfo
    line_items: list[dict] = field(default_factory=list)
    order_date: Optional[datetime] = None
    ship_by: Optional[datetime] = None
    order_name: str = ""
    total_amount: float = 0.0
    total_fee: float = 0.0
    delivery_cost: float = 0.0
    fee_per_item: float = 0.0
    shipping_per_item: float = 0.0
    shipping_carrier_code: str = ""
    shipping_service_code: str = ""
    image_url: str = ""


class EbayOrdersService:
    """
    Fetches and parses orders from eBay Fulfillment API.

    Database-agnostic: returns OrderData objects. The caller controls
    persistence via callbacks.
    """

    def __init__(
        self,
        get_token: Callable[[], str],
        refresh_token: Callable[[], None],
        marketplace_id: str = MARKETPLACE_ID,
    ):
        self._get_token = get_token
        self._refresh_token = refresh_token
        self._marketplace_id = marketplace_id

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self._marketplace_id,
            "Content-Type": "application/json",
        }

    def _refresh_and_retry(self, url: str, params: dict) -> requests.Response:
        self._refresh_token()
        return requests.get(url, headers=self._headers(), params=params, timeout=30)

    def fetch_orders(self, days_back: int = 3) -> list[dict]:
        """Fetch orders from eBay created in the last N days."""
        since = (
            datetime.now(timezone.utc) - timedelta(days=days_back)
        ).strftime("%Y-%m-%dT00:00:00.000Z")
        date_filter = f"creationdate:[{since}..]"

        all_orders: list[dict] = []
        offset = 0

        while True:
            url = f"{FULFILLMENT_API}/order"
            params = {
                "filter": date_filter,
                "limit": PAGE_SIZE,
                "offset": offset,
                "fieldGroups": "TAX_BREAKDOWN",
            }

            resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
            if resp.status_code == 401:
                resp = self._refresh_and_retry(url, params)
            resp.raise_for_status()

            data = resp.json()
            orders = data.get("orders", [])
            all_orders.extend(orders)

            total = data.get("total", 0)
            logger.info(
                "Fetched %d/%d orders (offset=%d)", len(all_orders), total, offset
            )

            if len(all_orders) >= total or not orders:
                break
            offset += PAGE_SIZE

        return all_orders

    def parse_order(self, order: dict) -> Optional[OrderData]:
        """Parse a raw eBay order dict into an OrderData object."""
        order_id = order.get("orderId", "")
        if not order_id:
            return None

        request_id = f"EBAY-{order_id}"
        status_id = map_ebay_status(order)
        buyer = self._extract_buyer(order)
        tracking = self._extract_tracking(order)
        line_items = order.get("lineItems", [])

        order_date_str = order.get("creationDate")
        order_date = (
            datetime.fromisoformat(order_date_str.replace("Z", "+00:00"))
            if order_date_str
            else datetime.now(timezone.utc)
        )

        ship_by = None
        if line_items:
            instructions = line_items[0].get("lineItemFulfillmentInstructions", {})
            ship_by_str = instructions.get("shipByDate")
            if ship_by_str:
                ship_by = datetime.fromisoformat(ship_by_str.replace("Z", "+00:00"))

        order_name = (
            line_items[0].get("title", "eBay Order") if line_items else "eBay Order"
        )

        pricing = order.get("pricingSummary", {})
        total_amount = float(pricing.get("total", {}).get("value", "0") or 0)
        total_fee_val = order.get("totalMarketplaceFee", {}).get("value", "0")
        total_fee = float(total_fee_val) if total_fee_val else 0
        delivery_cost_val = pricing.get("deliveryCost", {}).get("value", "0")
        delivery_cost = float(delivery_cost_val) if delivery_cost_val else 0

        n_items = max(len(line_items), 1)
        fee_per_item = round(total_fee / n_items, 2)
        shipping_per_item = round(delivery_cost / n_items, 2)

        shipping_info = self._extract_shipping_info(order)

        image_url = ""
        if line_items:
            item_ref = (
                line_items[0].get("legacyVariationId")
                or line_items[0].get("legacyItemId", "")
            )
            if item_ref:
                image_url = f"https://www.ebay.es/itm/{item_ref}"

        return OrderData(
            order_id=order_id,
            request_id=request_id,
            status_id=status_id,
            buyer=buyer,
            tracking=tracking,
            line_items=line_items,
            order_date=order_date,
            ship_by=ship_by,
            order_name=order_name[:200] if order_name else "eBay Order",
            total_amount=total_amount,
            total_fee=total_fee,
            delivery_cost=delivery_cost,
            fee_per_item=fee_per_item,
            shipping_per_item=shipping_per_item,
            shipping_carrier_code=shipping_info.get("carrier_code", ""),
            shipping_service_code=shipping_info.get("service_code", ""),
            image_url=image_url,
        )

    @staticmethod
    def _extract_buyer(order: dict) -> BuyerInfo:
        """
        Extract buyer data from eBay's deeply nested order structure.

        Address comes from fulfillmentStartInstructions[0].shippingStep.shipTo,
        NOT from buyer.buyerRegistrationAddress (which may be different).
        """
        buyer_obj = order.get("buyer", {})
        instructions = order.get("fulfillmentStartInstructions") or [{}]
        ship_step = instructions[0].get("shippingStep", {}) if instructions else {}
        ship_to = ship_step.get("shipTo", {})
        contact = ship_to.get("contactAddress", {})
        phone_obj = ship_to.get("primaryPhone", {})

        full_name = ship_to.get("fullName", buyer_obj.get("username", "eBay Buyer"))
        email = ship_to.get(
            "email",
            buyer_obj.get("buyerRegistrationAddress", {}).get("email", ""),
        )

        return BuyerInfo(
            name=full_name,
            username=buyer_obj.get("username", ""),
            country=contact.get("countryCode", "ES"),
            address_line1=contact.get("addressLine1", ""),
            address_line2=contact.get("addressLine2", ""),
            city=contact.get("city", ""),
            state_or_province=contact.get("stateOrProvince", ""),
            postal_code=contact.get("postalCode", ""),
            phone=phone_obj.get("phoneNumber", ""),
            email=email,
            company_name=ship_to.get("companyName", ""),
        )

    @staticmethod
    def _extract_tracking(order: dict) -> TrackingInfo:
        """
        Extract tracking info from eBay order.

        Checks multiple locations: fulfillments array first, then
        lineItems deliveryAddress as fallback.
        """
        fulfillment_hrefs = order.get("fulfillmentHrefs", [])
        fulfillments = order.get("fulfillments", [])

        tracking_number = ""
        carrier_code = ""

        if fulfillments:
            for f in fulfillments:
                raw = f.get("shipmentTrackingNumber", [])
                if isinstance(raw, str):
                    tracking_number = raw
                elif isinstance(raw, list):
                    for sl in raw:
                        if sl:
                            tracking_number = sl
                            break
                carrier_code = f.get("shippingCarrierCode", carrier_code)

        if not tracking_number and not fulfillments:
            for li in order.get("lineItems", []):
                for delivery in li.get("deliveryAddress", {}).get("fulfillments", []):
                    tracking_number = delivery.get("shipmentTrackingNumber", "")
                    carrier_code = delivery.get("shippingCarrierCode", "")

        return TrackingInfo(
            tracking_number=tracking_number if isinstance(tracking_number, str) else "",
            carrier_code=carrier_code,
            fulfillment_hrefs=fulfillment_hrefs,
        )

    @staticmethod
    def _extract_shipping_info(order: dict) -> dict:
        """Extract carrier and service from fulfillmentStartInstructions."""
        instructions = order.get("fulfillmentStartInstructions") or [{}]
        ship_step = instructions[0].get("shippingStep", {}) if instructions else {}
        return {
            "carrier_code": ship_step.get("shippingCarrierCode", ""),
            "service_code": ship_step.get("shippingServiceCode", ""),
        }

    @staticmethod
    def resolve_lpn(sku: str, lpn_exists: Callable[[str], bool]) -> Optional[str]:
        """
        Resolve an eBay SKU to an inventory LPN.

        Fallback: strips 2-letter suffix for legacy listings created
        before the LPN system (e.g., "LPNWE001AB" -> "LPNWE001").
        """
        if not sku:
            return None

        if lpn_exists(sku):
            return sku

        if len(sku) > 2 and sku[-2:].isalpha():
            sku_trimmed = sku[:-2]
            if lpn_exists(sku_trimmed):
                logger.info("LPN %s -> %s (legacy suffix stripped)", sku, sku_trimmed)
                return sku_trimmed

        return None

    def import_orders(
        self,
        days_back: int = 3,
        on_new: Optional[Callable[[OrderData], None]] = None,
        on_update: Optional[Callable[[OrderData, Optional[int]], None]] = None,
    ) -> dict:
        """
        Fetch and parse all orders from the last N days.

        Args:
            days_back: How many days of orders to fetch.
            on_new: Callback for new orders.
            on_update: Callback(order, old_status_id) for existing orders.

        Returns:
            Stats dict with total, new, updated, skipped counts.
        """
        orders_raw = self.fetch_orders(days_back)
        stats = {"total": len(orders_raw), "new": 0, "updated": 0, "skipped": 0}

        for raw in orders_raw:
            order_data = self.parse_order(raw)
            if not order_data:
                stats["skipped"] += 1
                continue

            if on_new:
                on_new(order_data)
                stats["new"] += 1

        logger.info(
            "Import complete: %d total, %d new, %d updated, %d skipped",
            stats["total"], stats["new"], stats["updated"], stats["skipped"],
        )
        return stats
