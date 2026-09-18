"""Shared accounting formulas used by generators and validation."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ecomgen.schemas import Order, OrderItem

CENT = Decimal("0.01")


def item_paid_value(order: Order, item: OrderItem) -> Decimal:
    """Return what the customer paid for one order line, VAT included, in cents.

    The formula mirrors how the order's tax was computed::

        line       = unit_price × quantity
        net_paid   = line − discount × line / subtotal
        line_tax   = tax × net_paid / (subtotal − discount + shipping)
        paid_value = ROUND_HALF_UP(net_paid + line_tax, 0.01)

    The line carries its pro-rata share of the order discount and of the tax
    actually charged on the order. Shipping is not part of any line. This is
    the refund for a fully returned line and the cap for its cumulative refunds.
    """

    line = item.unit_price * item.quantity
    discount_share = order.discount * line / order.subtotal if order.subtotal else Decimal(0)
    net_paid = line - discount_share
    taxable = order.subtotal - order.discount + order.shipping
    line_tax = order.tax * net_paid / taxable if taxable else Decimal(0)
    return (net_paid + line_tax).quantize(CENT, rounding=ROUND_HALF_UP)
