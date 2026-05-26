"""Tests for the eBay Shipping Service."""

from unittest.mock import MagicMock, patch

import pytest

from src.shipping.shipping_service import EbayShippingService, ShippingFulfillment


@pytest.fixture
def mock_auth():
    auth = MagicMock()
    auth.get_valid_token.return_value = "test_token"
    return auth


@pytest.fixture
def shipping_service(mock_auth):
    return EbayShippingService(auth_client=mock_auth, marketplace_id="EBAY_ES")


class TestCreateShippingFulfillment:
    def test_creates_fulfillment_and_returns_id(self, shipping_service):
        with patch.object(shipping_service.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=201,
                headers={
                    "Location": "https://api.ebay.com/.../shipping_fulfillment/ful-001"
                },
            )

            fid = shipping_service.create_shipping_fulfillment(
                order_id="ORD-001",
                tracking_number="1Z999",
                carrier_code="UPS",
            )
            assert fid == "ful-001"

    def test_sends_correct_payload(self, shipping_service):
        with patch.object(shipping_service.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=201,
                headers={"Location": "/ful-001"},
            )

            shipping_service.create_shipping_fulfillment(
                order_id="ORD-001",
                tracking_number="TRACK123",
                carrier_code="CORREOS",
                shipped_date="2025-01-15T10:00:00.000Z",
            )

            call_kwargs = mock_req.call_args
            payload = call_kwargs[1]["json"]
            assert payload["trackingNumber"] == "TRACK123"
            assert payload["shippingCarrierCode"] == "CORREOS"
            assert payload["shippedDate"] == "2025-01-15T10:00:00.000Z"

    def test_includes_line_items(self, shipping_service):
        with patch.object(shipping_service.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=201,
                headers={"Location": "/ful-001"},
            )

            shipping_service.create_shipping_fulfillment(
                order_id="ORD-001",
                tracking_number="TRACK",
                carrier_code="DHL",
                line_item_ids=["item1", "item2"],
            )

            payload = mock_req.call_args[1]["json"]
            assert len(payload["lineItems"]) == 2
            assert payload["lineItems"][0]["lineItemId"] == "item1"

    def test_auto_generates_shipped_date(self, shipping_service):
        with patch.object(shipping_service.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=201,
                headers={"Location": "/ful-001"},
            )

            shipping_service.create_shipping_fulfillment(
                order_id="ORD-001",
                tracking_number="T",
                carrier_code="C",
            )

            payload = mock_req.call_args[1]["json"]
            assert "shippedDate" in payload
            assert "T" in payload["shippedDate"]


class TestGetShippingFulfillments:
    def test_returns_fulfillment_list(self, shipping_service):
        with patch.object(shipping_service.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "fulfillments": [
                        {
                            "fulfillmentId": "ful-001",
                            "shipmentTrackingNumber": "1Z999",
                            "shippingCarrierCode": "UPS",
                            "shippedDate": "2025-01-15T10:00:00.000Z",
                            "lineItems": [{"lineItemId": "li1"}],
                        },
                        {
                            "fulfillmentId": "ful-002",
                            "shipmentTrackingNumber": "TRACK2",
                            "shippingCarrierCode": "CORREOS",
                            "shippedDate": "2025-01-16T10:00:00.000Z",
                            "lineItems": [],
                        },
                    ]
                },
            )

            results = shipping_service.get_shipping_fulfillments("ORD-001")
            assert len(results) == 2
            assert results[0].fulfillment_id == "ful-001"
            assert results[1].carrier_code == "CORREOS"

    def test_empty_fulfillments(self, shipping_service):
        with patch.object(shipping_service.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=lambda: {"fulfillments": []},
            )

            results = shipping_service.get_shipping_fulfillments("ORD-001")
            assert results == []


class TestFormatAddressForLabel:
    def test_formats_complete_address(self):
        address = {
            "name": "Juan García López",
            "address_line1": "Calle Mayor 10",
            "address_line2": "2º B",
            "city": "Madrid",
            "state_or_province": "Madrid",
            "postal_code": "28001",
            "country_code": "ES",
        }

        result = EbayShippingService.format_address_for_label(address)
        lines = result.split("\n")

        assert lines[0] == "Juan García López"
        assert lines[1] == "Calle Mayor 10"
        assert lines[2] == "2º B"
        assert "Madrid" in lines[3]
        assert "28001" in lines[3]
        assert lines[4] == "ES"

    def test_skips_empty_fields(self):
        address = {
            "name": "Test User",
            "address_line1": "123 Main St",
            "address_line2": "",
            "city": "Barcelona",
            "state_or_province": "",
            "postal_code": "08001",
            "country_code": "ES",
        }

        result = EbayShippingService.format_address_for_label(address)
        lines = result.split("\n")

        assert len(lines) == 4
        assert "Test User" in result
        assert "Barcelona" in result
        assert "08001" in result

    def test_handles_empty_address(self):
        result = EbayShippingService.format_address_for_label({})
        assert result == ""

    def test_handles_only_name(self):
        result = EbayShippingService.format_address_for_label({"name": "Solo Nombre"})
        assert result == "Solo Nombre"
