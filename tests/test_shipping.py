"""Tests for eBay shipping service."""
import pytest
from unittest.mock import patch, MagicMock

from src.shipping.shipping_service import EbayShippingService, CARRIER_MAP


@pytest.fixture
def service():
    return EbayShippingService(
        get_token=lambda: "test_token",
        refresh_token=lambda: None,
    )


class TestCarrierMapping:
    def test_correos(self):
        assert EbayShippingService.resolve_carrier_code("Correos") == "CORREOS"

    def test_correos_de_espana(self):
        assert EbayShippingService.resolve_carrier_code("CORREOS_DE_ESPANA") == "CORREOS"

    def test_seur(self):
        assert EbayShippingService.resolve_carrier_code("SEUR") == "SEUR"

    def test_mrw(self):
        assert EbayShippingService.resolve_carrier_code("MRW") == "MRW"

    def test_gls(self):
        assert EbayShippingService.resolve_carrier_code("GLS") == "GLS"

    def test_dhl(self):
        assert EbayShippingService.resolve_carrier_code("DHL") == "DHL"

    def test_ups(self):
        assert EbayShippingService.resolve_carrier_code("UPS") == "UPS"

    def test_nacex(self):
        assert EbayShippingService.resolve_carrier_code("NACEX") == "NACEX"

    def test_correos_express(self):
        assert EbayShippingService.resolve_carrier_code("CORREOS_EXPRESS") == "CORREOS_EXPRESS"

    def test_unknown_defaults_to_correos(self):
        assert EbayShippingService.resolve_carrier_code("RANDOM_CARRIER") == "CORREOS"

    def test_empty_defaults_to_correos(self):
        assert EbayShippingService.resolve_carrier_code("") == "CORREOS"

    def test_case_insensitive(self):
        assert EbayShippingService.resolve_carrier_code("seur") == "SEUR"
        assert EbayShippingService.resolve_carrier_code("Gls") == "GLS"


class TestCreateShippingFulfillment:
    @patch("src.shipping.shipping_service.requests.post")
    @patch("src.shipping.shipping_service.requests.get")
    def test_successful_fulfillment(self, mock_get, mock_post, service):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "lineItems": [
                    {"lineItemId": "LI001", "quantity": 1},
                    {"lineItemId": "LI002", "quantity": 1},
                ],
            },
        )

        mock_post.return_value = MagicMock(
            status_code=201,
            headers={"Location": "/fulfillment/FUL-001"},
        )

        result = service.create_shipping_fulfillment(
            "EBAY-12345", "PK123456789ES", "Correos",
        )
        assert result["success"] is True
        assert result["fulfillmentId"] == "FUL-001"
        assert result["trackingNumber"] == "PK123456789ES"

    @patch("src.shipping.shipping_service.requests.get")
    def test_no_line_items_returns_error(self, mock_get, service):
        mock_get.return_value = MagicMock(
            status_code=404,
        )

        result = service.create_shipping_fulfillment(
            "EBAY-99999", "PK000000000ES",
        )
        assert result["success"] is False
        assert "No line items" in result["error"]

    @patch("src.shipping.shipping_service.requests.post")
    @patch("src.shipping.shipping_service.requests.get")
    def test_ebay_error_response(self, mock_get, mock_post, service):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"lineItems": [{"lineItemId": "LI1", "quantity": 1}]},
        )
        mock_post.return_value = MagicMock(
            status_code=400,
            text='{"errors": [{"message": "Invalid tracking"}]}',
            json=lambda: {"errors": [{"message": "Invalid tracking"}]},
        )

        result = service.create_shipping_fulfillment("EBAY-123", "INVALID")
        assert result["success"] is False
        assert "Invalid tracking" in result["error"]

    @patch("src.shipping.shipping_service.requests.post")
    @patch("src.shipping.shipping_service.requests.get")
    def test_strips_ebay_prefix(self, mock_get, mock_post, service):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"lineItems": [{"lineItemId": "LI1", "quantity": 1}]},
        )
        mock_post.return_value = MagicMock(
            status_code=201, headers={"Location": "/f/1"},
        )

        service.create_shipping_fulfillment("EBAY-ORDER-123", "T1")
        get_url = mock_get.call_args[0][0]
        assert "ORDER-123" in get_url
        assert "EBAY-" not in get_url.split("/order/")[1]


class TestGetFulfillments:
    @patch("src.shipping.shipping_service.requests.get")
    def test_returns_fulfillments(self, mock_get, service):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "fulfillments": [
                    {"fulfillmentId": "F1", "trackingNumber": "PK111"},
                    {"fulfillmentId": "F2", "trackingNumber": "PK222"},
                ],
            },
        )

        result = service.get_shipping_fulfillments("EBAY-123")
        assert len(result) == 2
        assert result[0]["fulfillmentId"] == "F1"

    @patch("src.shipping.shipping_service.requests.get")
    def test_returns_empty_on_error(self, mock_get, service):
        mock_get.return_value = MagicMock(status_code=404)
        result = service.get_shipping_fulfillments("EBAY-999")
        assert result == []
