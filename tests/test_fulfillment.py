"""Tests for the eBay Fulfillment API client and status mapping."""

from unittest.mock import MagicMock, patch

import pytest

from src.orders.fulfillment import (
    EbayFulfillmentClient,
    UnifiedOrder,
    BuyerInfo,
    TrackingInfo,
    extract_buyer,
    extract_tracking,
    map_order_status,
)


# --- Status mapping tests ---


class TestStatusMapping:
    """Test the mapping from eBay's 3 independent fields to 9 unified states."""

    def test_pending_shipment_default(self):
        assert map_order_status("PAID", "NOT_STARTED", "NONE_REQUESTED") == "PENDING_SHIPMENT"

    def test_shipped(self):
        assert map_order_status("PAID", "IN_PROGRESS", "NONE_REQUESTED") == "SHIPPED"

    def test_completed(self):
        assert map_order_status("PAID", "FULFILLED", "NONE_REQUESTED") == "COMPLETED"

    def test_cancelled(self):
        assert map_order_status("PAID", "NOT_STARTED", "CANCELLED") == "CANCELLED"

    def test_cancelled_closed(self):
        assert map_order_status("PAID", "NOT_STARTED", "CANCEL_CLOSED") == "CANCELLED"

    def test_refunded_via_cancel(self):
        assert map_order_status("REFUND", "NOT_STARTED", "CANCELLED") == "REFUNDED"

    def test_refunded_without_cancel(self):
        assert map_order_status("REFUND", "NOT_STARTED", "NONE_REQUESTED") == "REFUNDED"

    def test_incident_cancel_requested(self):
        assert map_order_status("PAID", "NOT_STARTED", "CANCEL_REQUESTED") == "INCIDENT"

    def test_incident_payment_failed(self):
        assert map_order_status("FAILED", "NOT_STARTED", "NONE_REQUESTED") == "INCIDENT"

    def test_handles_none_values(self):
        result = map_order_status(None, None, None)
        assert result == "PENDING_SHIPMENT"

    def test_handles_empty_strings(self):
        result = map_order_status("", "", "")
        assert result == "PENDING_SHIPMENT"


# --- Buyer extraction tests ---


class TestExtractBuyer:
    def test_extracts_full_buyer_info(self):
        order = {
            "fulfillmentStartInstructions": [
                {
                    "shippingStep": {
                        "shipTo": {
                            "fullName": "Juan García",
                            "contactAddress": {
                                "addressLine1": "Calle Mayor 10",
                                "addressLine2": "2B",
                                "city": "Madrid",
                                "stateOrProvince": "Madrid",
                                "postalCode": "28001",
                                "countryCode": "ES",
                            },
                            "primaryPhone": {"phoneNumber": "+34600123456"},
                        }
                    }
                }
            ],
            "buyer": {"taxAddress": {"email": "juan@example.com"}},
        }

        buyer = extract_buyer(order)
        assert buyer is not None
        assert buyer.name == "Juan García"
        assert buyer.address_line1 == "Calle Mayor 10"
        assert buyer.city == "Madrid"
        assert buyer.postal_code == "28001"
        assert buyer.phone == "+34600123456"

    def test_returns_none_when_no_instructions(self):
        assert extract_buyer({}) is None
        assert extract_buyer({"fulfillmentStartInstructions": []}) is None

    def test_handles_missing_optional_fields(self):
        order = {
            "fulfillmentStartInstructions": [
                {
                    "shippingStep": {
                        "shipTo": {
                            "fullName": "Test User",
                            "contactAddress": {"addressLine1": "123 St"},
                        }
                    }
                }
            ],
        }
        buyer = extract_buyer(order)
        assert buyer is not None
        assert buyer.name == "Test User"
        assert buyer.address_line2 == ""
        assert buyer.phone == ""


# --- Tracking extraction tests ---


class TestExtractTracking:
    def test_extracts_tracking_info(self):
        order = {
            "fulfillments": [
                {
                    "shipmentTrackingNumber": "1Z999AA10123456784",
                    "shippingCarrierCode": "UPS",
                }
            ]
        }
        tracking = extract_tracking(order)
        assert tracking is not None
        assert tracking.tracking_number == "1Z999AA10123456784"
        assert tracking.carrier == "UPS"

    def test_returns_none_when_no_fulfillments(self):
        assert extract_tracking({}) is None
        assert extract_tracking({"fulfillments": []}) is None

    def test_returns_none_when_no_tracking_number(self):
        order = {"fulfillments": [{"shipmentTrackingNumber": "", "shippingCarrierCode": "CORREOS"}]}
        assert extract_tracking(order) is None


# --- Fulfillment client tests ---


@pytest.fixture
def mock_auth():
    auth = MagicMock()
    auth.get_valid_token.return_value = "test_token"
    return auth


@pytest.fixture
def fulfillment_client(mock_auth):
    return EbayFulfillmentClient(auth_client=mock_auth, marketplace_id="EBAY_ES")


class TestFetchOrders:
    def test_fetches_and_maps_orders(self, fulfillment_client):
        raw_response = {
            "total": 1,
            "orders": [
                {
                    "orderId": "ORD-001",
                    "orderPaymentStatus": "PAID",
                    "orderFulfillmentStatus": "NOT_STARTED",
                    "cancelStatus": {"cancelState": "NONE_REQUESTED"},
                    "pricingSummary": {"total": {"value": "29.99", "currency": "EUR"}},
                    "lineItems": [{"title": "Product A"}],
                    "creationDate": "2025-01-15T10:00:00.000Z",
                    "fulfillmentStartInstructions": [],
                }
            ],
        }

        with patch.object(fulfillment_client.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=lambda: raw_response,
            )

            orders = fulfillment_client.fetch_orders(days_back=7)
            assert len(orders) == 1
            assert orders[0].order_id == "ORD-001"
            assert orders[0].status == "PENDING_SHIPMENT"
            assert orders[0].total_amount == "29.99"

    def test_handles_pagination(self, fulfillment_client):
        page1 = {
            "total": 3,
            "orders": [
                {
                    "orderId": f"ORD-{i}",
                    "orderPaymentStatus": "PAID",
                    "orderFulfillmentStatus": "NOT_STARTED",
                    "cancelStatus": {"cancelState": "NONE_REQUESTED"},
                    "pricingSummary": {"total": {"value": "10.00", "currency": "EUR"}},
                    "lineItems": [],
                    "fulfillmentStartInstructions": [],
                }
                for i in range(2)
            ],
        }
        page2 = {
            "total": 3,
            "orders": [
                {
                    "orderId": "ORD-2",
                    "orderPaymentStatus": "PAID",
                    "orderFulfillmentStatus": "FULFILLED",
                    "cancelStatus": {"cancelState": "NONE_REQUESTED"},
                    "pricingSummary": {"total": {"value": "10.00", "currency": "EUR"}},
                    "lineItems": [],
                    "fulfillmentStartInstructions": [],
                }
            ],
        }

        with patch.object(fulfillment_client.session, "request") as mock_req:
            mock_req.side_effect = [
                MagicMock(status_code=200, json=lambda: page1),
                MagicMock(status_code=200, json=lambda: page2),
            ]

            orders = fulfillment_client.fetch_orders(days_back=7, limit=2)
            assert len(orders) == 3

    def test_empty_response(self, fulfillment_client):
        with patch.object(fulfillment_client.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=lambda: {"total": 0, "orders": []},
            )

            orders = fulfillment_client.fetch_orders()
            assert orders == []


class TestGetOrder:
    def test_fetches_single_order(self, fulfillment_client):
        raw = {
            "orderId": "ORD-SINGLE",
            "orderPaymentStatus": "PAID",
            "orderFulfillmentStatus": "FULFILLED",
            "cancelStatus": {"cancelState": "NONE_REQUESTED"},
            "pricingSummary": {"total": {"value": "50.00", "currency": "EUR"}},
            "lineItems": [],
            "fulfillmentStartInstructions": [],
        }

        with patch.object(fulfillment_client.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=lambda: raw,
            )

            order = fulfillment_client.get_order("ORD-SINGLE")
            assert order.order_id == "ORD-SINGLE"
            assert order.status == "COMPLETED"
