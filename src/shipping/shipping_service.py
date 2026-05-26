"""
eBay Shipping Service.

Creates shipping fulfillments, retrieves fulfillment details,
and formats addresses for shipping label generation.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

FULFILLMENT_API_BASE = "https://api.ebay.com/sell/fulfillment/v1"


@dataclass
class ShippingFulfillment:
    fulfillment_id: str
    tracking_number: str
    carrier_code: str
    shipped_date: str
    line_items: list[dict]


class EbayShippingService:
    """Handles shipping fulfillment creation and address formatting."""

    def __init__(self, auth_client, marketplace_id: Optional[str] = None):
        self.auth = auth_client
        self.marketplace_id = marketplace_id or os.getenv(
            "EBAY_MARKETPLACE_ID", "EBAY_ES"
        )
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.auth.get_valid_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("headers", self._headers())
        response = self.session.request(method, url, **kwargs)

        if response.status_code == 401:
            self.auth.refresh_access_token()
            kwargs["headers"] = self._headers()
            response = self.session.request(method, url, **kwargs)

        if response.status_code >= 400:
            raise requests.HTTPError(
                f"Shipping API error: {response.status_code}",
                response=response,
            )
        return response

    def create_shipping_fulfillment(
        self,
        order_id: str,
        tracking_number: str,
        carrier_code: str,
        line_item_ids: Optional[list[str]] = None,
        shipped_date: Optional[str] = None,
    ) -> str:
        """
        Mark an order as shipped by creating a shipping fulfillment.

        Returns the fulfillment_id from the Location header.
        """
        url = f"{FULFILLMENT_API_BASE}/order/{order_id}/shipping_fulfillment"

        if shipped_date is None:
            shipped_date = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )

        payload = {
            "trackingNumber": tracking_number,
            "shippingCarrierCode": carrier_code,
            "shippedDate": shipped_date,
        }

        if line_item_ids:
            payload["lineItems"] = [
                {"lineItemId": lid, "quantity": 1} for lid in line_item_ids
            ]

        response = self._request_with_retry("POST", url, json=payload)

        location = response.headers.get("Location", "")
        fulfillment_id = location.rstrip("/").split("/")[-1] if location else ""
        return fulfillment_id

    def get_shipping_fulfillments(self, order_id: str) -> list[ShippingFulfillment]:
        """Retrieve all shipping fulfillments for an order."""
        url = f"{FULFILLMENT_API_BASE}/order/{order_id}/shipping_fulfillment"
        response = self._request_with_retry("GET", url)
        data = response.json()

        fulfillments = []
        for item in data.get("fulfillments", []):
            fulfillments.append(
                ShippingFulfillment(
                    fulfillment_id=item.get("fulfillmentId", ""),
                    tracking_number=item.get("shipmentTrackingNumber", ""),
                    carrier_code=item.get("shippingCarrierCode", ""),
                    shipped_date=item.get("shippedDate", ""),
                    line_items=item.get("lineItems", []),
                )
            )
        return fulfillments

    @staticmethod
    def format_address_for_label(address: dict) -> str:
        """
        Format an address dictionary into a multi-line string
        suitable for copy/paste onto a shipping label.

        Expected keys: name, address_line1, address_line2,
                        city, state_or_province, postal_code, country_code
        """
        lines = []

        name = address.get("name", "").strip()
        if name:
            lines.append(name)

        line1 = address.get("address_line1", "").strip()
        if line1:
            lines.append(line1)

        line2 = address.get("address_line2", "").strip()
        if line2:
            lines.append(line2)

        city = address.get("city", "").strip()
        state = address.get("state_or_province", "").strip()
        postal = address.get("postal_code", "").strip()

        city_line_parts = []
        if city:
            city_line_parts.append(city)
        if state:
            city_line_parts.append(state)
        if postal:
            city_line_parts.append(postal)
        if city_line_parts:
            lines.append(", ".join(city_line_parts))

        country = address.get("country_code", "").strip()
        if country:
            lines.append(country)

        return "\n".join(lines)
