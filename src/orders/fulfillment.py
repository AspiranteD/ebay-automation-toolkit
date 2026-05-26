"""
eBay Fulfillment API integration.

Fetches orders with pagination, maps eBay's three independent status fields
to a unified internal state model, and extracts buyer/tracking details.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

FULFILLMENT_API_BASE = "https://api.ebay.com/sell/fulfillment/v1"


@dataclass
class BuyerInfo:
    name: str
    address_line1: str
    address_line2: str
    city: str
    state_or_province: str
    postal_code: str
    country_code: str
    phone: str
    email: str


@dataclass
class TrackingInfo:
    tracking_number: str
    carrier: str


@dataclass
class UnifiedOrder:
    order_id: str
    status: str
    buyer: Optional[BuyerInfo]
    tracking: Optional[TrackingInfo]
    total_amount: str
    currency: str
    line_items: list[dict] = field(default_factory=list)
    creation_date: str = ""


# eBay uses three independent status fields. This mapping combines them
# into a single internal status for cross-platform compatibility.
STATUS_PRIORITY = [
    "CANCELLED",
    "REFUNDED",
    "INCIDENT",
    "RETURNING",
    "RETURNED",
    "DELIVERED",
    "SHIPPED",
    "COMPLETED",
    "PENDING_SHIPMENT",
]


def map_order_status(
    payment_status: str,
    fulfillment_status: str,
    cancel_state: str,
) -> str:
    """
    Map eBay's 3 independent status fields to one of 9 unified states.

    eBay fields:
      - orderPaymentStatus: PAID, PENDING, FAILED
      - orderFulfillmentStatus: NOT_STARTED, IN_PROGRESS, FULFILLED
      - cancelStatus.cancelState: NONE_REQUESTED, CANCEL_REQUESTED,
                                   CANCEL_PENDING, CANCEL_CLOSED, CANCELLED

    Unified states:
      PENDING_SHIPMENT, SHIPPED, DELIVERED, COMPLETED,
      RETURNING, RETURNED, CANCELLED, REFUNDED, INCIDENT
    """
    cancel_state = (cancel_state or "NONE_REQUESTED").upper()
    payment_status = (payment_status or "").upper()
    fulfillment_status = (fulfillment_status or "").upper()

    if cancel_state in ("CANCELLED", "CANCEL_CLOSED"):
        if payment_status == "REFUND":
            return "REFUNDED"
        return "CANCELLED"

    if cancel_state == "CANCEL_REQUESTED":
        return "INCIDENT"

    if payment_status == "FAILED":
        return "INCIDENT"

    if payment_status == "REFUND":
        return "REFUNDED"

    if fulfillment_status == "FULFILLED":
        return "COMPLETED"

    if fulfillment_status == "IN_PROGRESS":
        return "SHIPPED"

    return "PENDING_SHIPMENT"


def extract_buyer(order: dict) -> Optional[BuyerInfo]:
    """Extract buyer details from fulfillmentStartInstructions."""
    instructions = order.get("fulfillmentStartInstructions", [])
    if not instructions:
        return None

    ship_to = instructions[0].get("shippingStep", {}).get("shipTo", {})
    contact = ship_to.get("contactAddress", {})
    full_name = ship_to.get("fullName", "")
    phone_obj = ship_to.get("primaryPhone", {})

    return BuyerInfo(
        name=full_name,
        address_line1=contact.get("addressLine1", ""),
        address_line2=contact.get("addressLine2", ""),
        city=contact.get("city", ""),
        state_or_province=contact.get("stateOrProvince", ""),
        postal_code=contact.get("postalCode", ""),
        country_code=contact.get("countryCode", ""),
        phone=phone_obj.get("phoneNumber", ""),
        email=order.get("buyer", {}).get("taxAddress", {}).get("email", ""),
    )


def extract_tracking(order: dict) -> Optional[TrackingInfo]:
    """Extract tracking number and carrier from fulfillments."""
    fulfillments = order.get("fulfillments", [])
    if not fulfillments:
        return None

    shipment = fulfillments[0].get("shipmentTrackingNumber", "")
    carrier = fulfillments[0].get("shippingCarrierCode", "")

    if not shipment:
        return None

    return TrackingInfo(tracking_number=shipment, carrier=carrier)


class EbayFulfillmentClient:
    """Client for the eBay Fulfillment API — order fetching and status mapping."""

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
                f"Fulfillment API error: {response.status_code}",
                response=response,
            )
        return response

    def fetch_orders(
        self,
        days_back: int = 3,
        limit: int = 50,
    ) -> list[UnifiedOrder]:
        """Fetch orders from the last N days with pagination."""
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        date_filter = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        filter_str = f"creationdate:[{date_filter}..]"

        all_orders: list[UnifiedOrder] = []
        offset = 0

        while True:
            url = f"{FULFILLMENT_API_BASE}/order"
            params = {
                "filter": filter_str,
                "limit": limit,
                "offset": offset,
            }

            response = self._request_with_retry("GET", url, params=params)
            data = response.json()

            orders = data.get("orders", [])
            if not orders:
                break

            for raw in orders:
                cancel_status = raw.get("cancelStatus", {})
                status = map_order_status(
                    payment_status=raw.get("orderPaymentStatus", ""),
                    fulfillment_status=raw.get("orderFulfillmentStatus", ""),
                    cancel_state=cancel_status.get("cancelState", "NONE_REQUESTED"),
                )
                total = raw.get("pricingSummary", {}).get("total", {})

                unified = UnifiedOrder(
                    order_id=raw.get("orderId", ""),
                    status=status,
                    buyer=extract_buyer(raw),
                    tracking=extract_tracking(raw),
                    total_amount=total.get("value", "0.00"),
                    currency=total.get("currency", "EUR"),
                    line_items=raw.get("lineItems", []),
                    creation_date=raw.get("creationDate", ""),
                )
                all_orders.append(unified)

            total_count = data.get("total", 0)
            offset += limit
            if offset >= total_count:
                break

        return all_orders

    def get_order(self, order_id: str) -> UnifiedOrder:
        """Fetch a single order by ID."""
        url = f"{FULFILLMENT_API_BASE}/order/{order_id}"
        response = self._request_with_retry("GET", url)
        raw = response.json()

        cancel_status = raw.get("cancelStatus", {})
        status = map_order_status(
            payment_status=raw.get("orderPaymentStatus", ""),
            fulfillment_status=raw.get("orderFulfillmentStatus", ""),
            cancel_state=cancel_status.get("cancelState", "NONE_REQUESTED"),
        )
        total = raw.get("pricingSummary", {}).get("total", {})

        return UnifiedOrder(
            order_id=raw.get("orderId", ""),
            status=status,
            buyer=extract_buyer(raw),
            tracking=extract_tracking(raw),
            total_amount=total.get("value", "0.00"),
            currency=total.get("currency", "EUR"),
            line_items=raw.get("lineItems", []),
            creation_date=raw.get("creationDate", ""),
        )
