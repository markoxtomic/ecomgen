from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ecomgen.schemas import (
    Customer,
    MarketingSpend,
    Order,
    OrderItem,
    Product,
    Return,
    Variant,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def test_all_record_schemas_accept_the_specified_fields() -> None:
    records = [
        Product(
            id="p1",
            title="Stone Planter",
            category="planters",
            description_short="A planter.",
            price_eur=Decimal("49.90"),
            cost_eur=Decimal("20.00"),
            markets=["de", "at"],
        ),
        Variant(
            id="v1",
            product_id="p1",
            sku="PLAN-S",
            option_name="size",
            option_value="Small",
            price_eur=Decimal("49.90"),
            inventory=12,
        ),
        Customer(
            id="c1",
            market="de",
            email="ada@example.test",
            first_name="Ada",
            last_name="Lovelace",
            city="Berlin",
            created_at=NOW,
            acquisition_channel="google",
        ),
        Order(
            id="o1",
            customer_id="c1",
            market="de",
            created_at=NOW,
            currency="EUR",
            subtotal=Decimal("49.90"),
            discount=Decimal(0),
            shipping=Decimal("4.90"),
            tax=Decimal("10.41"),
            total=Decimal("65.21"),
            discount_code=None,
            is_repeat=False,
        ),
        OrderItem(
            id="oi1",
            order_id="o1",
            variant_id="v1",
            quantity=1,
            unit_price=Decimal("49.90"),
        ),
        Return(
            id="r1",
            order_id="o1",
            order_item_id="oi1",
            reason="damaged",
            refund_amount=Decimal("49.90"),
            created_at=NOW,
        ),
        MarketingSpend(
            date=date(2026, 1, 2),
            market="de",
            channel="google",
            spend=Decimal(100),
            impressions=10000,
            clicks=300,
            attributed_orders=5,
        ),
    ]

    assert [type(record).__name__ for record in records] == [
        "Product",
        "Variant",
        "Customer",
        "Order",
        "OrderItem",
        "Return",
        "MarketingSpend",
    ]


def test_record_schemas_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Product.model_validate(
            {
                "id": "p1",
                "title": "Planter",
                "category": "planters",
                "description_short": "A planter.",
                "price_eur": "10",
                "cost_eur": "4",
                "markets": ["de"],
                "unexpected": True,
            }
        )


def test_non_negative_inventory_is_enforced() -> None:
    with pytest.raises(ValidationError):
        Variant(
            id="v1",
            product_id="p1",
            sku="SKU",
            option_name="size",
            option_value="S",
            price_eur=Decimal(10),
            inventory=-1,
        )
