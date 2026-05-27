"""
eBay shipping fulfillment service.

Handles marking orders as shipped via the Fulfillment API and
formatting buyer addresses for shipping labels.

Key operations:
  1. create_shipping_fulfillment: POST to eBay with tracking + carrier
  2. get_shipping_fulfillments: GET existing fulfillments for an order
  3. format_address_for_label: Format buyer address for label printing

The Fulfillment API requires line item IDs, so the service first
fetches the order to extract them before creating the fulfillment.
"""
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

FULFILLMENT_API = "https://api.ebay.com/sell/fulfillment/v1"
MARKETPLACE_ID = "EBAY_ES"

CARRIER_MAP = {
    "CORREOS_DE_ESPANA": "CORREOS",
    "CORREOS": "CORREOS",
    "CORREOS_EXPRESS": "CORREOS_EXPRESS",
    "SEUR": "SEUR",
    "MRW": "MRW",
    "GLS": "GLS",
    "DHL": "DHL",
    "UPS": "UPS",
    "NACEX": "NACEX",
}


class EbayShippingService:
    """Manages shipping fulfillments for eBay orders."""

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

    def create_shipping_fulfillment(
        self,
        request_id: str,
        tracking_number: str,
        carrier_code: str = "Correos",
        shipped_date: Optional[str] = None,
    ) -> dict:
        """
        Mark an order as shipped on eBay with tracking number.

        Args:
            request_id: Internal order ID (with EBAY- prefix).
            tracking_number: Shipping tracking code (e.g., PK123456789ES).
            carrier_code: eBay carrier code (default: Correos).
            shipped_date: ISO-8601 date (default: now).

        Returns:
            dict with success, fulfillmentId, trackingNumber, carrierCode.
        """
        ebay_order_id = request_id.replace("EBAY-", "")
        if not shipped_date:
            shipped_date = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )

        line_items = self._get_order_line_items(ebay_order_id)
        if not line_items:
            return {"success": False, "error": "No line items found for this order"}

        payload = {
            "lineItems": [
                {"lineItemId": li["lineItemId"], "quantity": li.get("quantity", 1)}
                for li in line_items
            ],
            "shippedDate": shipped_date,
            "shippingCarrierCode": carrier_code,
            "trackingNumber": tracking_number,
        }

        url = f"{FULFILLMENT_API}/order/{ebay_order_id}/shipping_fulfillment"
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=30)

        if resp.status_code == 401:
            self._refresh_token()
            resp = requests.post(
                url, json=payload, headers=self._headers(), timeout=30
            )

        if resp.status_code in (200, 201):
            location = resp.headers.get("Location", "")
            fulfillment_id = location.rstrip("/").split("/")[-1] if location else ""

            logger.info(
                "Order %s marked as shipped (tracking=%s, fulfillment=%s)",
                request_id, tracking_number, fulfillment_id,
            )
            return {
                "success": True,
                "fulfillmentId": fulfillment_id,
                "trackingNumber": tracking_number,
                "carrierCode": carrier_code,
            }

        error_data = resp.json() if resp.text else {}
        errors = error_data.get("errors", [])
        error_msg = errors[0].get("message", resp.text) if errors else resp.text
        logger.error(
            "Error marking shipment %s: %d - %s",
            request_id, resp.status_code, error_msg,
        )
        return {"success": False, "error": error_msg, "status_code": resp.status_code}

    def _get_order_line_items(self, ebay_order_id: str) -> list[dict]:
        """Fetch line items from eBay API for the given order."""
        url = f"{FULFILLMENT_API}/order/{ebay_order_id}"
        resp = requests.get(url, headers=self._headers(), timeout=30)

        if resp.status_code == 401:
            self._refresh_token()
            resp = requests.get(url, headers=self._headers(), timeout=30)

        if resp.status_code != 200:
            logger.error("Error fetching order %s: %d", ebay_order_id, resp.status_code)
            return []

        order = resp.json()
        return [
            {"lineItemId": li["lineItemId"], "quantity": li.get("quantity", 1)}
            for li in order.get("lineItems", [])
        ]

    def get_shipping_fulfillments(self, request_id: str) -> list[dict]:
        """Get existing fulfillments (shipments) for an order."""
        ebay_order_id = request_id.replace("EBAY-", "")
        url = f"{FULFILLMENT_API}/order/{ebay_order_id}/shipping_fulfillment"

        resp = requests.get(url, headers=self._headers(), timeout=30)
        if resp.status_code == 401:
            self._refresh_token()
            resp = requests.get(url, headers=self._headers(), timeout=30)

        if resp.status_code != 200:
            return []

        return resp.json().get("fulfillments", [])

    @staticmethod
    def resolve_carrier_code(ebay_carrier: str) -> str:
        """Map eBay carrier code to internal carrier name."""
        return CARRIER_MAP.get(ebay_carrier.upper(), "CORREOS") if ebay_carrier else "CORREOS"
