"""M4 regression: refunds are what the customer actually paid for the line."""

from collections import defaultdict
from decimal import Decimal
from functools import cache

from ecomgen.config import load_markets
from ecomgen.pipeline import generate_dataset

CENT = Decimal("0.01")


@cache
def _dataset():
    return generate_dataset(markets=("de", "ch", "uk"), customers=4000, months=12)


def _paid_net(order, item) -> Decimal:
    """The line's net value after its pro-rata share of the order discount."""

    line = item.unit_price * item.quantity
    return line - order.discount * line / order.subtotal


def test_refunds_on_discounted_orders_never_exceed_what_was_paid() -> None:
    dataset = _dataset()
    vat = {code: market.vat_rate for code, market in load_markets().items()}
    orders = {order.id: order for order in dataset.orders}
    items = {item.id: item for item in dataset.order_items}
    refunded_per_order: defaultdict[str, Decimal] = defaultdict(Decimal)
    discounted = 0

    for returned in dataset.returns:
        order = orders[returned.order_id]
        item = items[returned.order_item_id]
        refunded_per_order[order.id] += returned.refund_amount
        if order.discount == 0:
            continue
        discounted += 1
        paid_gross = _paid_net(order, item) * (1 + vat[order.market])
        assert returned.refund_amount <= paid_gross + CENT
        # The discount really reduces the refund below the undiscounted gross line value.
        assert returned.refund_amount < item.unit_price * item.quantity * (1 + vat[order.market])

    assert discounted > 50
    for order_id, refunded in refunded_per_order.items():
        order = orders[order_id]
        assert refunded <= order.total - order.shipping * (1 + vat[order.market]) + CENT


def test_refund_vat_matches_the_order_tax_rate() -> None:
    dataset = _dataset()
    vat = {code: market.vat_rate for code, market in load_markets().items()}
    orders = {order.id: order for order in dataset.orders}
    items = {item.id: item for item in dataset.order_items}

    assert dataset.returns
    for returned in dataset.returns:
        order = orders[returned.order_id]
        net = _paid_net(order, items[returned.order_item_id])
        # refund = net paid + VAT at the market rate, to the cent.
        assert abs(returned.refund_amount - net * (1 + vat[order.market])) <= CENT
        assert returned.refund_amount.as_tuple().exponent == -2
