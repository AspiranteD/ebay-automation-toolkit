"""Tests for eBay orders service: buyer extraction, tracking, LPN resolution."""
import pytest

from src.orders.orders_service import EbayOrdersService, BuyerInfo, TrackingInfo
from src.orders.status import ST_POR_ENVIAR, ST_ENVIADO, ST_ENTREGADO, ST_CANCELADO


def make_service():
    return EbayOrdersService(
        get_token=lambda: "mock_token",
        refresh_token=lambda: None,
    )


SAMPLE_ORDER = {
    "orderId": "12-34567-89012",
    "orderPaymentStatus": "PAID",
    "orderFulfillmentStatus": "NOT_STARTED",
    "cancelStatus": {"cancelState": "NONE_REQUESTED"},
    "creationDate": "2024-06-15T10:30:00.000Z",
    "buyer": {
        "username": "buyer_user",
        "buyerRegistrationAddress": {"email": "fallback@test.com"},
    },
    "fulfillmentStartInstructions": [{
        "shippingStep": {
            "shippingCarrierCode": "Correos",
            "shippingServiceCode": "ES_Otros",
            "shipTo": {
                "fullName": "Juan Garcia Lopez",
                "companyName": "Acme SL",
                "contactAddress": {
                    "addressLine1": "Calle Mayor 15",
                    "addressLine2": "2B",
                    "city": "Madrid",
                    "stateOrProvince": "Madrid",
                    "postalCode": "28001",
                    "countryCode": "ES",
                },
                "primaryPhone": {"phoneNumber": "+34600123456"},
                "email": "juan@test.com",
            },
        },
    }],
    "lineItems": [{
        "lineItemId": "LI001",
        "title": "Samsung Galaxy S21 Ultra",
        "sku": "LPNWE001AB",
        "quantity": 1,
        "legacyItemId": "123456789",
        "lineItemCost": {"value": "45.00", "currency": "EUR"},
        "lineItemFulfillmentInstructions": {
            "shipByDate": "2024-06-18T23:59:59.000Z",
        },
    }],
    "pricingSummary": {
        "total": {"value": "49.99", "currency": "EUR"},
        "deliveryCost": {"value": "4.99", "currency": "EUR"},
    },
    "totalMarketplaceFee": {"value": "6.50", "currency": "EUR"},
    "fulfillmentHrefs": [],
}


class TestBuyerExtraction:
    """Verify deep extraction of buyer data from nested eBay JSON."""

    def test_full_name_from_shipto(self):
        buyer = EbayOrdersService._extract_buyer(SAMPLE_ORDER)
        assert buyer.name == "Juan Garcia Lopez"

    def test_username(self):
        buyer = EbayOrdersService._extract_buyer(SAMPLE_ORDER)
        assert buyer.username == "buyer_user"

    def test_address_fields(self):
        buyer = EbayOrdersService._extract_buyer(SAMPLE_ORDER)
        assert buyer.address_line1 == "Calle Mayor 15"
        assert buyer.address_line2 == "2B"
        assert buyer.city == "Madrid"
        assert buyer.state_or_province == "Madrid"
        assert buyer.postal_code == "28001"
        assert buyer.country == "ES"

    def test_phone(self):
        buyer = EbayOrdersService._extract_buyer(SAMPLE_ORDER)
        assert buyer.phone == "+34600123456"

    def test_email_from_shipto(self):
        buyer = EbayOrdersService._extract_buyer(SAMPLE_ORDER)
        assert buyer.email == "juan@test.com"

    def test_email_fallback_to_registration(self):
        order = {
            "buyer": {"username": "u", "buyerRegistrationAddress": {"email": "fb@test.com"}},
            "fulfillmentStartInstructions": [{
                "shippingStep": {"shipTo": {"contactAddress": {}}}
            }],
        }
        buyer = EbayOrdersService._extract_buyer(order)
        assert buyer.email == "fb@test.com"

    def test_company_name(self):
        buyer = EbayOrdersService._extract_buyer(SAMPLE_ORDER)
        assert buyer.company_name == "Acme SL"

    def test_empty_order_fallbacks(self):
        buyer = EbayOrdersService._extract_buyer({})
        assert buyer.name == "eBay Buyer"
        assert buyer.country == "ES"

    def test_to_dict(self):
        buyer = EbayOrdersService._extract_buyer(SAMPLE_ORDER)
        d = buyer.to_dict()
        assert d["fullName"] == "Juan Garcia Lopez"
        assert d["postalCode"] == "28001"
        assert d["phone"] == "+34600123456"


class TestBuyerAddressFormatting:
    def test_full_address_format(self):
        buyer = BuyerInfo(
            name="Ana Perez",
            address_line1="Av. Diagonal 100",
            city="Barcelona",
            state_or_province="Barcelona",
            postal_code="08019",
            country="ES",
            phone="+34612345678",
        )
        label = buyer.format_for_label()
        assert "Ana Perez" in label
        assert "Av. Diagonal 100" in label
        assert "08019 Barcelona" in label
        assert "Tel: +34612345678" in label

    def test_foreign_address_includes_country(self):
        buyer = BuyerInfo(name="John", country="FR", city="Paris", postal_code="75001")
        label = buyer.format_for_label()
        assert "FR" in label

    def test_spanish_address_omits_country(self):
        buyer = BuyerInfo(name="Juan", country="ES", city="Madrid")
        label = buyer.format_for_label()
        assert "ES" not in label

    def test_company_name_included(self):
        buyer = BuyerInfo(name="Juan", company_name="Acme SL")
        label = buyer.format_for_label()
        assert "Acme SL" in label


class TestTrackingExtraction:
    def test_tracking_from_fulfillments(self):
        order = {
            "fulfillmentHrefs": ["/fulfillment/123"],
            "fulfillments": [{
                "shipmentTrackingNumber": ["PK123456789ES"],
                "shippingCarrierCode": "Correos",
            }],
        }
        tracking = EbayOrdersService._extract_tracking(order)
        assert tracking.tracking_number == "PK123456789ES"
        assert tracking.carrier_code == "Correos"

    def test_tracking_fallback_to_line_items(self):
        order = {
            "fulfillmentHrefs": [],
            "lineItems": [{
                "deliveryAddress": {
                    "fulfillments": [{
                        "shipmentTrackingNumber": "SEUR123",
                        "shippingCarrierCode": "SEUR",
                    }]
                }
            }],
        }
        tracking = EbayOrdersService._extract_tracking(order)
        assert tracking.tracking_number == "SEUR123"

    def test_empty_tracking(self):
        tracking = EbayOrdersService._extract_tracking({})
        assert tracking.tracking_number == ""
        assert tracking.carrier_code == ""

    def test_string_tracking_number(self):
        order = {
            "fulfillmentHrefs": [],
            "fulfillments": [{
                "shipmentTrackingNumber": "DIRECT_STRING",
                "shippingCarrierCode": "MRW",
            }],
        }
        tracking = EbayOrdersService._extract_tracking(order)
        assert tracking.tracking_number == "DIRECT_STRING"


class TestShippingInfoExtraction:
    def test_carrier_and_service(self):
        info = EbayOrdersService._extract_shipping_info(SAMPLE_ORDER)
        assert info["carrier_code"] == "Correos"
        assert info["service_code"] == "ES_Otros"

    def test_empty_instructions(self):
        info = EbayOrdersService._extract_shipping_info({})
        assert info["carrier_code"] == ""
        assert info["service_code"] == ""


class TestLPNResolution:
    def test_exact_match(self):
        lpn = EbayOrdersService.resolve_lpn("LPNWE001", lambda x: x == "LPNWE001")
        assert lpn == "LPNWE001"

    def test_legacy_suffix_stripped(self):
        lpn = EbayOrdersService.resolve_lpn(
            "LPNWE001AB",
            lambda x: x == "LPNWE001",
        )
        assert lpn == "LPNWE001"

    def test_no_match_returns_none(self):
        lpn = EbayOrdersService.resolve_lpn("UNKNOWN", lambda x: False)
        assert lpn is None

    def test_empty_sku_returns_none(self):
        lpn = EbayOrdersService.resolve_lpn("", lambda x: True)
        assert lpn is None

    def test_suffix_only_stripped_if_alpha(self):
        lpn = EbayOrdersService.resolve_lpn("LPN12345", lambda x: x == "LPN123")
        assert lpn is None  # "45" are digits, not stripped

    def test_short_sku_no_strip(self):
        lpn = EbayOrdersService.resolve_lpn("AB", lambda x: x == "AB")
        assert lpn == "AB"

    def test_3char_sku_with_alpha_suffix(self):
        lpn = EbayOrdersService.resolve_lpn("XYZ", lambda x: x == "X")
        assert lpn == "X"


class TestOrderParsing:
    def test_parse_full_order(self):
        svc = make_service()
        order = svc.parse_order(SAMPLE_ORDER)
        assert order is not None
        assert order.order_id == "12-34567-89012"
        assert order.request_id == "EBAY-12-34567-89012"
        assert order.status_id == ST_POR_ENVIAR
        assert order.buyer.name == "Juan Garcia Lopez"
        assert order.order_name == "Samsung Galaxy S21 Ultra"

    def test_parse_fee_splitting(self):
        svc = make_service()
        order = svc.parse_order(SAMPLE_ORDER)
        assert order.total_fee == 6.50
        assert order.delivery_cost == 4.99
        assert order.fee_per_item == 6.50  # single item
        assert order.shipping_per_item == 4.99

    def test_parse_multi_item_fee_splitting(self):
        multi_order = dict(SAMPLE_ORDER)
        multi_order["lineItems"] = [
            {"lineItemId": "LI001", "title": "Item A", "sku": "A", "quantity": 1,
             "lineItemCost": {"value": "20.00"}, "legacyItemId": "111"},
            {"lineItemId": "LI002", "title": "Item B", "sku": "B", "quantity": 1,
             "lineItemCost": {"value": "25.00"}, "legacyItemId": "222"},
        ]
        svc = make_service()
        order = svc.parse_order(multi_order)
        assert order.fee_per_item == 3.25  # 6.50 / 2
        assert order.shipping_per_item == 2.50  # 4.99 / 2 = 2.495 -> 2.50

    def test_parse_returns_none_for_no_order_id(self):
        svc = make_service()
        assert svc.parse_order({}) is None

    def test_parse_image_url(self):
        svc = make_service()
        order = svc.parse_order(SAMPLE_ORDER)
        assert "ebay.es/itm/123456789" in order.image_url

    def test_parse_ship_by_date(self):
        svc = make_service()
        order = svc.parse_order(SAMPLE_ORDER)
        assert order.ship_by is not None
        assert order.ship_by.year == 2024
        assert order.ship_by.month == 6
        assert order.ship_by.day == 18

    def test_parse_cancelled_order(self):
        cancelled = dict(SAMPLE_ORDER)
        cancelled["cancelStatus"] = {"cancelState": "CANCELED"}
        svc = make_service()
        order = svc.parse_order(cancelled)
        assert order.status_id == ST_CANCELADO

    def test_parse_fulfilled_order(self):
        fulfilled = dict(SAMPLE_ORDER)
        fulfilled["orderFulfillmentStatus"] = "FULFILLED"
        svc = make_service()
        order = svc.parse_order(fulfilled)
        assert order.status_id == ST_ENTREGADO
