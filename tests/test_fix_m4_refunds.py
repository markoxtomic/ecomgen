"""M4 regression: refunds are what the customer actually paid for the line."""

from collections import defaultdict
from decimal import Decimal
from functools import cache

from ecomgen.accounting import item_paid_values
from ecomgen.pipeline import generate_dataset


@cache
def _dataset():
    return generate_dataset(markets=("de", "ch", "uk"), customers=4000, months=12)


def _paid_values(dataset) -> dict[str, Decimal]:
    items_by_order = defaultdict(list)
    for item in dataset.order_items:
        items_by_order[item.order_id].append(item)
    values: dict[str, Decimal] = {}
    for order in dataset.orders:
        values.update(item_paid_values(order, items_by_order[order.id]))
    return values


def test_refunds_equal_the_discounted_gross_line_value() -> None:
    dataset = _dataset()
    orders = {order.id: order for order in dataset.orders}
    items = {item.id: item for item in dataset.order_items}
    paid_values = _paid_values(dataset)
    refunded_per_order: defaultdict[str, Decimal] = defaultdict(Decimal)
    discounted = 0

    for returned in dataset.returns:
        order = orders[returned.order_id]
        item = items[returned.order_item_id]
        refunded_per_order[order.id] += returned.refund_amount
        if order.discount == 0:
            continue
        discounted += 1
        assert returned.refund_amount == paid_values[item.id]
        # The discount really reduces the refund below the undiscounted gross line value.
        assert returned.refund_amount < item.unit_price * item.quantity

    assert discounted > 50
    for order_id, refunded in refunded_per_order.items():
        order = orders[order_id]
        assert refunded <= order.subtotal - order.discount


def test_refunds_do_not_add_vat_to_gross_unit_prices() -> None:
    dataset = _dataset()
    paid_values = _paid_values(dataset)

    assert dataset.returns
    for returned in dataset.returns:
        expected = paid_values[returned.order_item_id]
        assert returned.refund_amount == expected
        assert returned.refund_amount.as_tuple().exponent == -2
