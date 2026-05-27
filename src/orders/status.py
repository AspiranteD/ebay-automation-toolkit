"""
eBay order status mapping to unified internal states.

eBay uses three independent status fields:
  - orderPaymentStatus: PENDING | PAID | FAILED | FULLY_REFUNDED
  - orderFulfillmentStatus: NOT_STARTED | IN_PROGRESS | FULFILLED
  - cancelStatus.cancelState: NONE_REQUESTED | CANCEL_REQUESTED | CANCELED | ...

These three fields are combined into 9 unified internal states shared
across all marketplace platforms (eBay, Wallapop, etc.).
"""

ST_POR_ENVIAR = 101
ST_ENVIADO = 102
ST_ENTREGADO = 103
ST_COMPLETADO = 104
ST_EN_DEVOLUCION = 105
ST_DEVUELTO = 106
ST_CANCELADO = 107
ST_REEMBOLSADO = 108
ST_INCIDENCIA = 109

EBAY_STATUS_CONSTANTS = {
    "POR_ENVIAR": ST_POR_ENVIAR,
    "ENVIADO": ST_ENVIADO,
    "ENTREGADO": ST_ENTREGADO,
    "COMPLETADO": ST_COMPLETADO,
    "EN_DEVOLUCION": ST_EN_DEVOLUCION,
    "DEVUELTO": ST_DEVUELTO,
    "CANCELADO": ST_CANCELADO,
    "REEMBOLSADO": ST_REEMBOLSADO,
    "INCIDENCIA": ST_INCIDENCIA,
}

CANCEL_STATES = {"CANCELED", "CANCEL_COMPLETE", "CANCEL_CLOSED", "CANCEL_REQUESTED"}
REFUND_PAYMENTS = {"FULLY_REFUNDED", "REFUNDED"}

SALE_ELIGIBLE_STATUSES = {ST_POR_ENVIAR, ST_ENVIADO, ST_ENTREGADO, ST_COMPLETADO}
REFUND_SALE_STATUSES = {ST_REEMBOLSADO}
ALL_SALE_STATUSES = SALE_ELIGIBLE_STATUSES | REFUND_SALE_STATUSES
CANCEL_STATUSES = {ST_CANCELADO}

PAYMENT_METHOD_PLATAFORMA = 4
PAYMENT_STATUS_PAGADO = 3
PAYMENT_STATUS_REEMBOLSADO = 5
PAYMENT_STATUS_CANCELADO = 6


def map_ebay_status(order: dict) -> int:
    """
    Map eBay API fields to unified internal status.

    Decision tree:
      1. cancelState in CANCEL_STATES -> CANCELADO
      2. payment REFUNDED + FULFILLED -> REEMBOLSADO
      3. payment REFUNDED (not fulfilled) -> CANCELADO
      4. FULFILLED -> ENTREGADO
      5. IN_PROGRESS -> ENVIADO
      6. default -> POR_ENVIAR
    """
    fulfillment = order.get("orderFulfillmentStatus", "NOT_STARTED")
    payment = order.get("orderPaymentStatus", "PENDING")
    cancel_state = order.get("cancelStatus", {}).get("cancelState", "NONE_REQUESTED")

    is_cancelled = cancel_state in CANCEL_STATES
    is_refunded = payment in REFUND_PAYMENTS

    if is_cancelled:
        return ST_CANCELADO

    if is_refunded and fulfillment == "FULFILLED":
        return ST_REEMBOLSADO
    if is_refunded:
        return ST_CANCELADO

    if fulfillment == "FULFILLED":
        return ST_ENTREGADO
    if fulfillment == "IN_PROGRESS":
        return ST_ENVIADO

    return ST_POR_ENVIAR
