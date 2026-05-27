"""Tests for eBay order status mapping."""
import pytest

from src.orders.status import (
    map_ebay_status,
    ST_POR_ENVIAR, ST_ENVIADO, ST_ENTREGADO, ST_COMPLETADO,
    ST_CANCELADO, ST_REEMBOLSADO,
    CANCEL_STATES, REFUND_PAYMENTS,
    SALE_ELIGIBLE_STATUSES, ALL_SALE_STATUSES,
)


class TestMapEbayStatus:
    """Verify the 3-field status mapping decision tree."""

    def test_default_is_por_enviar(self):
        order = {}
        assert map_ebay_status(order) == ST_POR_ENVIAR

    def test_paid_not_started(self):
        order = {
            "orderPaymentStatus": "PAID",
            "orderFulfillmentStatus": "NOT_STARTED",
        }
        assert map_ebay_status(order) == ST_POR_ENVIAR

    def test_in_progress_maps_to_enviado(self):
        order = {
            "orderPaymentStatus": "PAID",
            "orderFulfillmentStatus": "IN_PROGRESS",
        }
        assert map_ebay_status(order) == ST_ENVIADO

    def test_fulfilled_maps_to_entregado(self):
        order = {
            "orderPaymentStatus": "PAID",
            "orderFulfillmentStatus": "FULFILLED",
        }
        assert map_ebay_status(order) == ST_ENTREGADO

    # --- Cancel states ---

    def test_canceled_maps_to_cancelado(self):
        order = {
            "cancelStatus": {"cancelState": "CANCELED"},
            "orderPaymentStatus": "PAID",
            "orderFulfillmentStatus": "NOT_STARTED",
        }
        assert map_ebay_status(order) == ST_CANCELADO

    def test_cancel_complete(self):
        order = {"cancelStatus": {"cancelState": "CANCEL_COMPLETE"}}
        assert map_ebay_status(order) == ST_CANCELADO

    def test_cancel_closed(self):
        order = {"cancelStatus": {"cancelState": "CANCEL_CLOSED"}}
        assert map_ebay_status(order) == ST_CANCELADO

    def test_cancel_requested(self):
        order = {"cancelStatus": {"cancelState": "CANCEL_REQUESTED"}}
        assert map_ebay_status(order) == ST_CANCELADO

    def test_cancel_overrides_fulfillment(self):
        """Cancel state takes priority over fulfillment status."""
        order = {
            "cancelStatus": {"cancelState": "CANCELED"},
            "orderFulfillmentStatus": "FULFILLED",
        }
        assert map_ebay_status(order) == ST_CANCELADO

    def test_none_requested_is_not_cancelled(self):
        order = {
            "cancelStatus": {"cancelState": "NONE_REQUESTED"},
            "orderFulfillmentStatus": "IN_PROGRESS",
        }
        assert map_ebay_status(order) == ST_ENVIADO

    # --- Refund states ---

    def test_refunded_and_fulfilled_maps_to_reembolsado(self):
        order = {
            "orderPaymentStatus": "FULLY_REFUNDED",
            "orderFulfillmentStatus": "FULFILLED",
        }
        assert map_ebay_status(order) == ST_REEMBOLSADO

    def test_refunded_not_fulfilled_maps_to_cancelado(self):
        order = {
            "orderPaymentStatus": "FULLY_REFUNDED",
            "orderFulfillmentStatus": "NOT_STARTED",
        }
        assert map_ebay_status(order) == ST_CANCELADO

    def test_refunded_in_progress_maps_to_cancelado(self):
        order = {
            "orderPaymentStatus": "REFUNDED",
            "orderFulfillmentStatus": "IN_PROGRESS",
        }
        assert map_ebay_status(order) == ST_CANCELADO

    def test_cancel_priority_over_refund(self):
        """Cancel overrides refund when both present."""
        order = {
            "cancelStatus": {"cancelState": "CANCELED"},
            "orderPaymentStatus": "FULLY_REFUNDED",
            "orderFulfillmentStatus": "FULFILLED",
        }
        assert map_ebay_status(order) == ST_CANCELADO

    # --- All cancel states are covered ---

    def test_all_cancel_states_recognized(self):
        for cancel_state in CANCEL_STATES:
            order = {"cancelStatus": {"cancelState": cancel_state}}
            assert map_ebay_status(order) == ST_CANCELADO

    # --- All refund payments are covered ---

    def test_all_refund_payments_recognized(self):
        for payment in REFUND_PAYMENTS:
            order = {
                "orderPaymentStatus": payment,
                "orderFulfillmentStatus": "FULFILLED",
            }
            assert map_ebay_status(order) == ST_REEMBOLSADO


class TestStatusConstants:
    def test_sale_eligible_includes_por_enviar(self):
        assert ST_POR_ENVIAR in SALE_ELIGIBLE_STATUSES

    def test_sale_eligible_includes_entregado(self):
        assert ST_ENTREGADO in SALE_ELIGIBLE_STATUSES

    def test_all_sale_includes_reembolsado(self):
        assert ST_REEMBOLSADO in ALL_SALE_STATUSES

    def test_all_statuses_are_unique(self):
        values = [
            ST_POR_ENVIAR, ST_ENVIADO, ST_ENTREGADO, ST_COMPLETADO,
            ST_CANCELADO, ST_REEMBOLSADO,
        ]
        assert len(values) == len(set(values))

    def test_status_ids_are_in_100_range(self):
        for st in [ST_POR_ENVIAR, ST_ENVIADO, ST_ENTREGADO, ST_COMPLETADO,
                    ST_CANCELADO, ST_REEMBOLSADO]:
            assert 100 <= st <= 199
